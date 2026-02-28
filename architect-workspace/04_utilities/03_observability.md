# Observability & Logging Architecture

## Overview
A multi-agent system with external LLM APIs, sandboxed tool execution, and asynchronous event dispatch is **impossible to debug** without structured observability. When a task fails after 15 reasoning iterations, you need to answer: *"Which tool call failed? How many tokens were burned? Was the LLM rate-limited?"*

This architecture defines structured JSON logging, session-level token tracking, trace/span correlation, and sensitive data sanitization — all writing to file for external analysis.

---

## 1. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Log Format** | Structured JSON Lines (`.jsonl`) to file | Machine-parseable, queryable with `jq`, filterable by task/agent/level |
| **Token Tracking** | Per-session aggregate | Simple. Total tokens/cost visible at session end. |
| **Trace Correlation** | `task_id` (trace) + `span_id` (sub-operation) | Can answer "which specific tool call in Task 3 took 12s?" without full distributed tracing overhead |
| **Sanitization** | At source (Provider/Event Bus) + regex safety net at logger | Defense in depth. Provider knows its own secrets; logger catches stragglers. |
| **TUI Debug Panel** | No. Logs to file only. | YAGNI. User reads logs externally with `cat`, `jq`, or a text editor. |

---

## 2. Log File Structure

All logs are written into the `.agent_cli/` project directory:

```
.agent_cli/
├── logs/
│   ├── session_2026-02-27_19-42-00.jsonl   ← Structured event log
│   ├── session_2026-02-27_19-42-00.summary  ← Session summary (tokens, duration, tasks)
│   └── ...
├── config.toml
├── memory.db
└── artifacts/
```

### Log Rotation
- One `.jsonl` file per session (created when the CLI starts).
- Old sessions are **not** auto-deleted. The user manages cleanup.
- Configurable max log file size in `AgentSettings` (default: 50MB). If exceeded, old entries are dropped from the head (FIFO) while the session continues.

---

## 3. Structured Log Entry Format

Every log entry is a single JSON object on one line:

```json
{
  "timestamp": "2026-02-27T19:42:15.123Z",
  "level": "INFO",
  "source": "coder_agent",
  "task_id": "a1b2c3d4",
  "span_id": "e5f6g7h8",
  "span_type": "llm_call",
  "message": "LLM response received",
  "data": {
    "model": "claude-3-5-sonnet",
    "input_tokens": 1250,
    "output_tokens": 340,
    "duration_ms": 2150,
    "tool_mode": "NATIVE"
  }
}
```

### Field Definitions

| Field | Type | Required | Description |
|---|---|---|---|
| `timestamp` | ISO 8601 | ✅ | When the event occurred |
| `level` | string | ✅ | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `source` | string | ✅ | Component that emitted the log (e.g., `orchestrator`, `coder_agent`, `event_bus`) |
| `task_id` | string | ❌ | Trace ID — which task this log belongs to. Empty for system-level logs. |
| `span_id` | string | ❌ | Sub-operation ID within a task (see Section 5) |
| `span_type` | string | ❌ | Category of the span: `llm_call`, `tool_exec`, `event_dispatch`, `state_transition` |
| `message` | string | ✅ | Human-readable log message |
| `data` | object | ❌ | Structured payload (tokens, duration, tool args, etc.) |

---

## 4. Log Levels and What They Capture

| Level | When Used | Examples |
|---|---|---|
| `DEBUG` | Verbose internal state (disabled by default) | Event Bus dispatch details, memory compaction steps, raw LLM prompt |
| `INFO` | Normal operation milestones | Task created, state transition, LLM call completed, tool executed |
| `WARNING` | Recoverable issues | Schema validation error (self-correcting), context summarization triggered, transient retry |
| `ERROR` | Failed operations | Task failed, max iterations exceeded, all retries exhausted |
| `CRITICAL` | System-level failures | Event Bus subscriber crash, unhandled exception, shutdown error |

### Configuration

```python
class AgentSettings(BaseSettings):
    # ... existing fields ...
    
    # Logging settings
    log_level: str = Field(
        default="INFO",
        description="Minimum log level written to the session log file."
    )
    log_max_file_size_mb: int = Field(
        default=50,
        description="Maximum log file size in megabytes before head truncation."
    )
```

---

## 5. Trace and Span Model

### Trace ID = `task_id`
Every log entry within a task's lifecycle carries the same `task_id`. This is the top-level correlation key.

```
Filter all logs for a task:
$ cat session.jsonl | jq 'select(.task_id == "a1b2c3d4")'
```

### Span ID = Sub-Operation
Within a task, each discrete operation (LLM call, tool execution, state transition) gets a unique `span_id`. This allows pinpointing exactly where time or tokens were spent.

```python
import uuid

class SpanContext:
    """Lightweight span tracker for a single sub-operation."""
    
    def __init__(self, task_id: str, span_type: str):
        self.task_id = task_id
        self.span_id = str(uuid.uuid4())[:8]  # Short ID for readability
        self.span_type = span_type
        self.start_time = time.time()
    
    def finish(self) -> dict:
        """Returns timing data for the log entry."""
        return {
            "task_id": self.task_id,
            "span_id": self.span_id,
            "span_type": self.span_type,
            "duration_ms": round((time.time() - self.start_time) * 1000)
        }
```

### Span Types

| Span Type | Created By | What It Tracks |
|---|---|---|
| `llm_call` | LLM Provider | Model name, tokens in/out, latency, tool_mode |
| `tool_exec` | Tool Executor | Tool name, arguments, success/error, execution time |
| `state_transition` | State Manager | from_state → to_state, task_id |
| `event_dispatch` | Event Bus | event_type, number of subscribers notified, dispatch mode |
| `memory_compaction` | Memory Manager | Tokens before/after, summarization trigger |

### Example: Tracing a Full Task

```
task_id=a1b2c3d4  span=1a2b  span_type=state_transition  PENDING → ROUTING
task_id=a1b2c3d4  span=3c4d  span_type=llm_call          routing model, 120 tokens, 450ms
task_id=a1b2c3d4  span=5e6f  span_type=state_transition  ROUTING → WORKING
task_id=a1b2c3d4  span=7g8h  span_type=llm_call          claude-3-5-sonnet, 2100 tokens, 3200ms
task_id=a1b2c3d4  span=9i0j  span_type=tool_exec         read_file(config.py), 45ms, success
task_id=a1b2c3d4  span=1k2l  span_type=llm_call          claude-3-5-sonnet, 3400 tokens, 4100ms
task_id=a1b2c3d4  span=3m4n  span_type=tool_exec         edit_file(config.py), 12ms, success
task_id=a1b2c3d4  span=5o6p  span_type=llm_call          claude-3-5-sonnet, 4200 tokens, 2800ms
task_id=a1b2c3d4  span=7q8r  span_type=state_transition  WORKING → SUCCESS
```

Filtering with `jq`:
```bash
# All LLM calls for a task, sorted by duration
cat session.jsonl | jq 'select(.task_id=="a1b2c3d4" and .span_type=="llm_call") | .data.duration_ms'

# Total tokens for the session
cat session.jsonl | jq 'select(.span_type=="llm_call") | .data.input_tokens + .data.output_tokens' | paste -sd+ | bc
```

---

## 6. Session-Level Token Tracking

Token usage is tracked as a simple in-memory counter at the session level. Logged at the end of the session.

```python
import threading
from dataclasses import dataclass, field


@dataclass
class SessionMetrics:
    """Aggregated metrics for the entire CLI session."""
    session_id: str = ""
    start_time: float = 0.0
    
    # Token counters
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_llm_calls: int = 0
    
    # Task counters
    total_tasks_created: int = 0
    total_tasks_succeeded: int = 0
    total_tasks_failed: int = 0
    
    # Tool counters
    total_tool_calls: int = 0
    total_tool_errors: int = 0
    
    def record_llm_call(self, input_tokens: int, output_tokens: int) -> None:
        """Called by the LLM Provider after each API response."""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_llm_calls += 1
    
    def record_tool_call(self, success: bool) -> None:
        """Called by the Tool Executor after each tool execution."""
        self.total_tool_calls += 1
        if not success:
            self.total_tool_errors += 1
    
    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens
    
    @property
    def duration_seconds(self) -> float:
        return time.time() - self.start_time
    
    def to_summary(self) -> dict:
        """Generates the session summary for the .summary file."""
        return {
            "session_id": self.session_id,
            "duration_seconds": round(self.duration_seconds, 1),
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
            }
        }
```

### Session Summary File

Written to `.agent_cli/logs/session_<timestamp>.summary` on shutdown:

```json
{
  "session_id": "sess_abc123",
  "duration_seconds": 342.5,
  "tokens": {
    "input": 45200,
    "output": 12800,
    "total": 58000
  },
  "llm_calls": 14,
  "tasks": {
    "created": 3,
    "succeeded": 2,
    "failed": 1
  },
  "tools": {
    "calls": 28,
    "errors": 2
  }
}
```

---

## 7. Sensitive Data Sanitization

### Layer 1: At the Source (Primary Defense)

Each component sanitizes its own output before logging:

```python
class BaseLLMProvider(ABC):
    
    def _sanitize_for_log(self, request_payload: dict) -> dict:
        """
        Strip API keys and auth headers before logging the request.
        The provider knows exactly which fields contain secrets.
        """
        sanitized = request_payload.copy()
        
        # Remove auth headers
        if "headers" in sanitized:
            headers = sanitized["headers"].copy()
            for key in ("Authorization", "x-api-key", "api-key"):
                if key in headers:
                    headers[key] = "[REDACTED]"
            sanitized["headers"] = headers
        
        return sanitized
```

The Event Bus sanitizes `SystemErrorEvent` payloads:

```python
async def _safe_invoke(self, sub, event):
    try:
        await sub.callback(event)
    except Exception as e:
        # Sanitize the error message before emitting
        sanitized_msg = self._sanitize_string(str(e))
        error_event = SystemErrorEvent(
            source="event_bus",
            error_message=sanitized_msg,
            ...
        )
```

### Layer 2: Regex Safety Net (Final Defense)

The JSON log writer applies a final regex pass before flushing to disk:

```python
import re

# Patterns that look like API keys or tokens
SENSITIVE_PATTERNS = [
    (re.compile(r'sk-[a-zA-Z0-9]{20,}'), '[REDACTED_OPENAI_KEY]'),
    (re.compile(r'sk-ant-[a-zA-Z0-9\-]{20,}'), '[REDACTED_ANTHROPIC_KEY]'),
    (re.compile(r'key-[a-zA-Z0-9]{20,}'), '[REDACTED_KEY]'),
    (re.compile(r'Bearer\s+[a-zA-Z0-9\-._~+/]+=*'), 'Bearer [REDACTED]'),
    (re.compile(r'(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*\S+'), r'\1=[REDACTED]'),
]


def sanitize_log_line(line: str) -> str:
    """Final safety net: regex-scrub any secrets that slipped through."""
    for pattern, replacement in SENSITIVE_PATTERNS:
        line = pattern.sub(replacement, line)
    return line
```

---

## 8. The Logger Implementation

```python
import json
import logging
import time
from pathlib import Path
from typing import Optional


class StructuredLogger:
    """
    Writes structured JSON log entries to a .jsonl file.
    Integrates with Python's standard logging module for compatibility.
    """
    
    def __init__(
        self,
        log_dir: Path,
        session_id: str,
        level: str = "INFO",
        max_size_mb: int = 50
    ):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        self.log_file = log_dir / f"session_{timestamp}.jsonl"
        self.summary_file = log_dir / f"session_{timestamp}.summary"
        self.session_id = session_id
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.level = getattr(logging, level.upper(), logging.INFO)
        
        self._file_handle = open(self.log_file, "a", encoding="utf-8")
        
        # Also configure Python's standard logging for stderr (development)
        logging.basicConfig(
            level=self.level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S"
        )
    
    def log(
        self,
        level: str,
        source: str,
        message: str,
        task_id: str = "",
        span_id: str = "",
        span_type: str = "",
        data: Optional[dict] = None
    ) -> None:
        """Write a structured log entry."""
        log_level = getattr(logging, level.upper(), logging.INFO)
        if log_level < self.level:
            return
        
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.") + f"{time.time() % 1:.3f}"[2:] + "Z",
            "level": level.upper(),
            "source": source,
            "message": message,
        }
        
        # Optional fields (omit if empty to keep logs compact)
        if task_id:
            entry["task_id"] = task_id
        if span_id:
            entry["span_id"] = span_id
        if span_type:
            entry["span_type"] = span_type
        if data:
            entry["data"] = data
        
        # Serialize, sanitize, and write
        line = json.dumps(entry, default=str)
        line = sanitize_log_line(line)
        self._file_handle.write(line + "\n")
        self._file_handle.flush()
    
    def write_summary(self, metrics: SessionMetrics) -> None:
        """Write the session summary file on shutdown."""
        with open(self.summary_file, "w", encoding="utf-8") as f:
            json.dump(metrics.to_summary(), f, indent=2)
    
    def close(self) -> None:
        """Flush and close the log file."""
        self._file_handle.flush()
        self._file_handle.close()
```

---

## 9. Integration Points

### A. LLM Provider Logging

```python
class BaseLLMProvider(ABC):
    async def safe_generate(self, context, tools=None):
        span = SpanContext(task_id=self._current_task_id, span_type="llm_call")
        
        try:
            response = await retry_with_backoff(...)
            timing = span.finish()
            
            # Log the call
            self.logger.log("INFO", self.provider_name, "LLM response received",
                task_id=timing["task_id"],
                span_id=timing["span_id"],
                span_type="llm_call",
                data={
                    "model": self.model_name,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "duration_ms": timing["duration_ms"],
                    "tool_mode": response.tool_mode.name,
                }
            )
            
            # Update session metrics
            self.session_metrics.record_llm_call(
                response.input_tokens, response.output_tokens
            )
            
            return response
        except Exception as e:
            self.logger.log("ERROR", self.provider_name, f"LLM call failed: {e}",
                task_id=span.task_id, span_id=span.span_id, span_type="llm_call")
            raise
```

### B. Tool Executor Logging

```python
class ToolExecutor:
    async def execute(self, action: ParsedAction, task_id: str) -> str:
        span = SpanContext(task_id=task_id, span_type="tool_exec")
        
        try:
            result = await tool.execute(**action.arguments)
            timing = span.finish()
            
            self.logger.log("INFO", "tool_executor", f"Tool '{action.tool_name}' completed",
                task_id=task_id,
                span_id=timing["span_id"],
                span_type="tool_exec",
                data={
                    "tool": action.tool_name,
                    "duration_ms": timing["duration_ms"],
                    "result_length": len(result),
                }
            )
            self.session_metrics.record_tool_call(success=True)
            return result
            
        except Exception as e:
            self.logger.log("ERROR", "tool_executor", f"Tool '{action.tool_name}' failed: {e}",
                task_id=task_id, span_id=span.span_id, span_type="tool_exec")
            self.session_metrics.record_tool_call(success=False)
            raise ToolExecutionError(str(e))
```

### C. Shutdown Hook

```python
async def on_shutdown(self):
    """Called during graceful shutdown."""
    self.logger.log("INFO", "system", "Session ending",
        data=self.session_metrics.to_summary()
    )
    self.logger.write_summary(self.session_metrics)
    self.logger.close()
```

---

## 10. Querying Logs (User Workflow)

Since there is no TUI debug panel, users analyze logs with external tools:

```bash
# View all errors in the last session
cat .agent_cli/logs/session_*.jsonl | jq 'select(.level == "ERROR")'

# All LLM calls with token counts
cat .agent_cli/logs/session_*.jsonl | jq 'select(.span_type == "llm_call") | {model: .data.model, tokens: (.data.input_tokens + .data.output_tokens), ms: .data.duration_ms}'

# Total tokens used in a session
cat .agent_cli/logs/session_*.summary | jq '.tokens.total'

# Trace a specific task
cat .agent_cli/logs/session_*.jsonl | jq 'select(.task_id == "a1b2c3d4")' 

# Find the slowest operations
cat .agent_cli/logs/session_*.jsonl | jq 'select(.data.duration_ms) | {span_type, message, ms: .data.duration_ms}' | sort_by(.ms)

# View the session summary
cat .agent_cli/logs/session_2026-02-27_19-42-00.summary | jq .
```

---

## 11. Testing Strategy

```python
import pytest

def test_structured_logger_writes_valid_jsonl(tmp_path):
    logger = StructuredLogger(tmp_path / "logs", "test_session")
    
    logger.log("INFO", "test", "Hello world", task_id="t1", data={"key": "value"})
    logger.close()
    
    # Read and parse
    log_file = list((tmp_path / "logs").glob("*.jsonl"))[0]
    lines = log_file.read_text().strip().split("\n")
    entry = json.loads(lines[0])
    
    assert entry["level"] == "INFO"
    assert entry["source"] == "test"
    assert entry["task_id"] == "t1"
    assert entry["data"]["key"] == "value"

def test_sanitization_redacts_api_keys():
    line = 'Authorization: Bearer sk-ant-abc123def456'
    sanitized = sanitize_log_line(line)
    assert "sk-ant-abc123def456" not in sanitized
    assert "[REDACTED" in sanitized

def test_session_metrics_accumulation():
    metrics = SessionMetrics(session_id="test", start_time=time.time())
    
    metrics.record_llm_call(1000, 500)
    metrics.record_llm_call(2000, 800)
    
    assert metrics.total_tokens == 4300
    assert metrics.total_llm_calls == 2

def test_session_summary_written_on_shutdown(tmp_path):
    logger = StructuredLogger(tmp_path / "logs", "test_session")
    metrics = SessionMetrics(session_id="test", start_time=time.time())
    metrics.record_llm_call(5000, 2000)
    
    logger.write_summary(metrics)
    logger.close()
    
    summary_file = list((tmp_path / "logs").glob("*.summary"))[0]
    summary = json.loads(summary_file.read_text())
    assert summary["tokens"]["total"] == 7000
```
