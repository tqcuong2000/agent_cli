"""Canonical error catalog, resolver, and routing helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from agent_cli.core.infra.registry.registry import DataRegistry


@dataclass
class ErrorRoute:
    """Structured routing policy for a resolved error."""

    emit_agent_memory: bool = False
    emit_agent_event: bool = False
    agent_monologue: bool = False
    emit_task_result: bool = False
    emit_task_error_event: bool = False
    emit_system_error_event: bool = False
    persist_to_session: bool = False


@dataclass
class ErrorDefinition:
    """Catalog-backed runtime error definition."""

    error_id: str
    tier: str
    user_message: str = ""
    agent_message: str = ""
    technical_detail: str = ""
    tool_message: str = ""
    ui_title: str = ""
    error_code: str = ""
    retryable: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    route: ErrorRoute = field(default_factory=ErrorRoute)


@dataclass
class ErrorRecord:
    """Canonical runtime error shape used across layers."""

    error_id: str
    source: str = ""
    message: str = ""
    tier: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    task_id: str = ""
    tool_name: str = ""
    action_id: str = ""
    batch_id: str = ""
    exception_type: str = ""
    raw_exception: str = ""


@dataclass
class ResolvedError:
    """Resolved error with rendered messages and route policy."""

    error_id: str
    tier: str
    user_message: str
    agent_message: str
    technical_detail: str
    tool_message: str
    ui_title: str
    error_code: str
    retryable: bool
    metadata: dict[str, Any]
    route: ErrorRoute


class ErrorRouter:
    """Resolve a canonical error record against the data-driven catalog."""

    def __init__(self, data_registry: DataRegistry) -> None:
        self._data_registry = data_registry

    def resolve(self, record: ErrorRecord) -> ResolvedError:
        fallback = self._data_registry.get_error_definition("generic.unexpected")
        definition = self._data_registry.get_error_definition(record.error_id) or fallback
        if definition is None:
            raise RuntimeError("generic.unexpected error catalog entry is required")

        params = self._build_params(record, definition)
        tier = str(record.tier or definition.tier or "FATAL").strip().upper()
        retryable = definition.retryable if definition.retryable is not None else tier == "TRANSIENT"
        metadata = dict(definition.metadata)
        metadata.update(record.metadata)
        metadata = self._render_value(metadata, params)
        metadata["error_id"] = definition.error_id
        if record.task_id:
            metadata["task_id"] = record.task_id
        if record.tool_name:
            metadata["tool_name"] = record.tool_name
        if record.action_id:
            metadata["action_id"] = record.action_id
        if record.batch_id:
            metadata["batch_id"] = record.batch_id

        user_message = self._render(definition.user_message, params)
        technical_detail = self._render(definition.technical_detail, params)
        tool_message = self._render(definition.tool_message or definition.user_message, params)
        agent_message = self._render(definition.agent_message, params)
        ui_title = self._render(definition.ui_title, params)

        return ResolvedError(
            error_id=definition.error_id,
            tier=tier,
            user_message=user_message or record.message or definition.error_id,
            agent_message=agent_message,
            technical_detail=technical_detail or record.raw_exception or record.message,
            tool_message=tool_message or user_message or record.message,
            ui_title=ui_title,
            error_code=definition.error_code,
            retryable=bool(retryable),
            metadata=metadata,
            route=definition.route,
        )

    @staticmethod
    def _render(template: str, params: Mapping[str, Any]) -> str:
        if not template:
            return ""
        try:
            return template.format_map(_SafeFormatDict(params))
        except ValueError:
            return template

    def _render_value(self, value: Any, params: Mapping[str, Any]) -> Any:
        if isinstance(value, str):
            return self._render(value, params)
        if isinstance(value, dict):
            return {
                str(key): self._render_value(item, params)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._render_value(item, params) for item in value]
        return value

    def _build_params(
        self,
        record: ErrorRecord,
        definition: Any,
    ) -> dict[str, Any]:
        params: dict[str, Any] = dict(definition.metadata)
        params.update(record.params)
        params.setdefault("error_id", record.error_id or definition.error_id)
        params.setdefault("message", record.message)
        params.setdefault("source", record.source)
        params.setdefault("task_id", record.task_id)
        params.setdefault("tool_name", record.tool_name)
        params.setdefault("action_id", record.action_id)
        params.setdefault("batch_id", record.batch_id)
        params.setdefault("exception_type", record.exception_type or "Error")
        params.setdefault("raw_exception", record.raw_exception or record.message)
        return params


class _SafeFormatDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
