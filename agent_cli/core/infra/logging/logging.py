"""Structured logging and session observability primitives."""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

from agent_cli.core.infra.logging.tracing import get_trace_fields
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.cost.cost import estimate_cost

SENSITIVE_PATTERNS = [
    (re.compile(r"sk-ant-[a-zA-Z0-9\-]{16,}"), "[REDACTED_ANTHROPIC_KEY]"),
    (re.compile(r"sk-[a-zA-Z0-9]{16,}"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"Bearer\s+[a-zA-Z0-9\-._~+/]+=*"), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*\S+"),
        r"\1=[REDACTED]",
    ),
]

_DEFAULT_MAX_SIZE_MB = 50
_DEFAULT_MAX_SIZE_BYTES = _DEFAULT_MAX_SIZE_MB * 1024 * 1024


def sanitize_log_line(line: str) -> str:
    """Redact secret-like strings before writing logs to disk."""
    sanitized = line
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


class JSONLineFormatter(logging.Formatter):
    """Formats a LogRecord as structured JSON."""

    def format(self, record: logging.LogRecord) -> str:
        trace_fields = get_trace_fields()
        source = getattr(record, "source", None) or record.name
        task_id = getattr(record, "task_id", None) or trace_fields.get("task_id", "")
        span_id = getattr(record, "span_id", None) or trace_fields.get("span_id", "")
        span_type = getattr(record, "span_type", None) or trace_fields.get(
            "span_type", ""
        )
        trace_id = getattr(record, "trace_id", None) or trace_fields.get("trace_id", "")

        entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "source": source,
            "message": record.getMessage(),
        }

        if trace_id:
            entry["trace_id"] = trace_id
        if task_id:
            entry["task_id"] = task_id
        if span_id:
            entry["span_id"] = span_id
        if span_type:
            entry["span_type"] = span_type

        data = getattr(record, "data", None)
        if isinstance(data, dict) and data:
            entry["data"] = data

        if record.exc_info:
            entry.setdefault("data", {})
            entry["data"]["exception"] = self.formatException(record.exc_info)

        return sanitize_log_line(json.dumps(entry, ensure_ascii=True, default=str))


@dataclass
class SessionMetrics:
    """Aggregated session metrics for summary reporting."""

    session_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_llm_calls: int = 0
    total_cost_usd: float = 0.0
    total_tasks_created: int = 0
    total_tasks_succeeded: int = 0
    total_tasks_failed: int = 0
    total_tool_calls: int = 0
    total_tool_errors: int = 0
    resolver_usage: int = 0
    capability_probe_successes: int = 0
    capability_probe_failures: int = 0
    unknown_capability_fallbacks: int = 0

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def to_summary(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        duration_seconds = max(0.0, (now - self.started_at).total_seconds())
        return {
            "session_id": self.session_id,
            "duration_seconds": round(duration_seconds, 2),
            "tokens": {
                "input": self.total_input_tokens,
                "output": self.total_output_tokens,
                "total": self.total_tokens,
            },
            "llm_calls": self.total_llm_calls,
            "tasks": {
                "created": self.total_tasks_created,
                "succeeded": self.total_tasks_succeeded,
                "failed": self.total_tasks_failed,
            },
            "tools": {
                "calls": self.total_tool_calls,
                "errors": self.total_tool_errors,
            },
            "migration": {
                "resolver_usage": self.resolver_usage,
                "capability_probe_successes": self.capability_probe_successes,
                "capability_probe_failures": self.capability_probe_failures,
                "unknown_capability_fallbacks": self.unknown_capability_fallbacks,
            },
            "cost_usd": round(self.total_cost_usd, 6),
        }


@dataclass
class TaskMetrics:
    """Per-task metrics used for request-level usage logging."""

    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0
    tool_errors: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "cost_usd": round(self.cost_usd, 6),
        }


class ObservabilityManager:
    """Coordinates structured logging and session/task metrics."""

    def __init__(
        self,
        *,
        log_dir: Path,
        level: str,
        max_size_mb: int,
        data_registry: Optional[DataRegistry] = None,
    ) -> None:
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.session_id = uuid.uuid4().hex[:12]
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.log_file = self.log_dir / f"session_{stamp}.jsonl"
        self.summary_file = self.log_dir / f"session_{stamp}.summary"
        self.metrics = SessionMetrics(session_id=self.session_id)
        self._task_metrics: Dict[str, TaskMetrics] = {}
        self._lock = Lock()
        self._data_registry = data_registry
        self._level = level.upper()
        self._max_size_bytes = max(int(max_size_mb), _DEFAULT_MAX_SIZE_MB) * 1024 * 1024

        self._configure_handlers(self._level)
        self._logger = logging.getLogger("agent_cli.observability")
        self._logger.info(
            "Observability initialized",
            extra={
                "source": "observability",
                "data": {
                    "session_id": self.session_id,
                    "log_file": str(self.log_file),
                },
            },
        )

    def set_level(self, level: str) -> None:
        """Update runtime logging level."""
        self._level = level.upper()
        self._configure_handlers(self._level)

    def record_task_created(self) -> None:
        with self._lock:
            self.metrics.total_tasks_created += 1

    def record_task_result(self, *, is_success: bool) -> None:
        with self._lock:
            if is_success:
                self.metrics.total_tasks_succeeded += 1
            else:
                self.metrics.total_tasks_failed += 1

    def record_migration_counter(self, name: str, count: int = 1) -> None:
        """Record migration telemetry counters for capability-registry rollout."""
        if count <= 0:
            return

        normalized = str(name).strip().lower()
        attr_map = {
            "resolver_usage": "resolver_usage",
            "probe_successes": "capability_probe_successes",
            "probe_failures": "capability_probe_failures",
            "unknown_capability_fallbacks": "unknown_capability_fallbacks",
        }
        attr_name = attr_map.get(normalized)
        if attr_name is None:
            return

        with self._lock:
            current = int(getattr(self.metrics, attr_name, 0))
            setattr(self.metrics, attr_name, current + int(count))

        self._logger.info(
            "Migration telemetry updated",
            extra={
                "source": "observability",
                "data": {
                    "counter": normalized,
                    "delta": int(count),
                    "value": int(getattr(self.metrics, attr_name, 0)),
                },
            },
        )

    def record_llm_call(
        self,
        *,
        task_id: str,
        model: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: int,
        cost_usd: Optional[float] = None,
        desired_effort: Optional[str] = None,
        effective_effort: Optional[str] = None,
    ) -> None:
        resolved_cost = (
            cost_usd
            if cost_usd is not None
            else estimate_cost(
                model,
                input_tokens,
                output_tokens,
                data_registry=self._data_registry,
            )
        )

        with self._lock:
            self.metrics.total_input_tokens += int(input_tokens)
            self.metrics.total_output_tokens += int(output_tokens)
            self.metrics.total_llm_calls += 1
            self.metrics.total_cost_usd += float(resolved_cost)
            task = self._task_metrics.setdefault(task_id, TaskMetrics())
            task.input_tokens += int(input_tokens)
            task.output_tokens += int(output_tokens)
            task.llm_calls += 1
            task.cost_usd += float(resolved_cost)

        self._logger.info(
            "LLM response received",
            extra={
                "source": provider or "llm_provider",
                "task_id": task_id,
                "span_type": "llm_call",
                "data": {
                    "model": model,
                    "provider": provider,
                    "input_tokens": int(input_tokens),
                    "output_tokens": int(output_tokens),
                    "duration_ms": int(duration_ms),
                    "cost_usd": round(float(resolved_cost), 6),
                    "desired_effort": str(desired_effort or ""),
                    "effective_effort": str(effective_effort or ""),
                },
            },
        )

    def record_tool_call(
        self,
        *,
        task_id: str,
        tool_name: str,
        success: bool,
        duration_ms: int,
        result_length: int,
    ) -> None:
        with self._lock:
            self.metrics.total_tool_calls += 1
            if not success:
                self.metrics.total_tool_errors += 1

            if task_id:
                task = self._task_metrics.setdefault(task_id, TaskMetrics())
                task.tool_calls += 1
                if not success:
                    task.tool_errors += 1

        self._logger.info(
            "Tool execution completed" if success else "Tool execution failed",
            extra={
                "source": "tool_executor",
                "task_id": task_id,
                "span_type": "tool_exec",
                "data": {
                    "tool": tool_name,
                    "success": bool(success),
                    "duration_ms": int(duration_ms),
                    "result_length": int(result_length),
                },
            },
        )

    def get_task_metrics(self, task_id: str) -> Dict[str, Any]:
        task = self._task_metrics.get(task_id)
        if task is None:
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "llm_calls": 0,
                "tool_calls": 0,
                "tool_errors": 0,
                "cost_usd": 0.0,
            }
        return task.to_dict()

    def log_task_summary(self, task_id: str, *, is_success: bool) -> None:
        self._logger.info(
            "Task metrics summary",
            extra={
                "source": "orchestrator",
                "task_id": task_id,
                "data": {
                    "is_success": is_success,
                    **self.get_task_metrics(task_id),
                },
            },
        )

    def write_summary(self) -> None:
        summary = self.metrics.to_summary()
        payload = json.dumps(summary, ensure_ascii=True, indent=2)
        self.summary_file.write_text(payload, encoding="utf-8")

    def shutdown(self) -> None:
        self._logger.info(
            "Observability shutdown",
            extra={
                "source": "observability",
                "data": self.metrics.to_summary(),
            },
        )
        self.write_summary()
        _remove_managed_handlers()

    def _configure_handlers(self, level: str) -> None:
        numeric_level = getattr(logging, level.upper(), logging.INFO)
        root = logging.getLogger()
        root.setLevel(numeric_level)
        _remove_managed_handlers()

        file_handler = RotatingFileHandler(
            self.log_file,
            maxBytes=self._max_size_bytes
            if self._max_size_bytes > 0
            else _DEFAULT_MAX_SIZE_BYTES,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(JSONLineFormatter())
        file_handler._agent_cli_managed = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)


def _remove_managed_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_agent_cli_managed", False):
            root.removeHandler(handler)
            handler.close()


def configure_observability(
    settings: Any,
    *,
    data_registry: Optional[DataRegistry] = None,
) -> ObservabilityManager:
    """Create a fresh observability manager for the current app session."""

    log_dir = Path(
        str(getattr(settings, "log_directory", "~/.agent_cli/logs"))
    ).expanduser()
    level = str(getattr(settings, "log_level", "INFO"))
    max_size_mb = int(getattr(settings, "log_max_file_size_mb", _DEFAULT_MAX_SIZE_MB))
    return ObservabilityManager(
        log_dir=log_dir,
        level=level,
        max_size_mb=max_size_mb,
        data_registry=data_registry,
    )
