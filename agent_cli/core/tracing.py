"""Trace and span context utilities for observability."""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Dict, Iterator, Optional

_TRACE_ID: ContextVar[str] = ContextVar("agent_cli_trace_id", default="")
_TASK_ID: ContextVar[str] = ContextVar("agent_cli_task_id", default="")
_SPAN_ID: ContextVar[str] = ContextVar("agent_cli_span_id", default="")
_SPAN_TYPE: ContextVar[str] = ContextVar("agent_cli_span_type", default="")


def new_trace_id() -> str:
    """Return a short trace/span identifier suitable for logs."""
    return uuid.uuid4().hex[:8]


def get_trace_fields() -> Dict[str, str]:
    """Return the currently bound trace fields."""
    return {
        "trace_id": _TRACE_ID.get(),
        "task_id": _TASK_ID.get(),
        "span_id": _SPAN_ID.get(),
        "span_type": _SPAN_TYPE.get(),
    }


@contextmanager
def bind_trace(
    *,
    trace_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Iterator[None]:
    """Temporarily bind trace/task IDs for downstream logs."""
    tokens: list[tuple[ContextVar[str], Token[str]]] = []

    if trace_id is not None:
        tokens.append((_TRACE_ID, _TRACE_ID.set(trace_id)))
    if task_id is not None:
        tokens.append((_TASK_ID, _TASK_ID.set(task_id)))

    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


@dataclass
class Span:
    """Represents a traced sub-operation."""

    span_id: str
    span_type: str
    task_id: str
    trace_id: str
    _start: float
    _span_id_token: Token[str]
    _span_type_token: Token[str]
    _task_id_token: Optional[Token[str]] = None
    _trace_id_token: Optional[Token[str]] = None
    _closed: bool = False
    _duration_ms: int = 0

    def finish(self) -> Dict[str, str | int]:
        """Close the span and return timing/context fields."""
        if not self._closed:
            self._duration_ms = int((time.monotonic() - self._start) * 1000)
            _SPAN_TYPE.reset(self._span_type_token)
            _SPAN_ID.reset(self._span_id_token)
            if self._task_id_token is not None:
                _TASK_ID.reset(self._task_id_token)
            if self._trace_id_token is not None:
                _TRACE_ID.reset(self._trace_id_token)
            self._closed = True

        return {
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "span_id": self.span_id,
            "span_type": self.span_type,
            "duration_ms": self._duration_ms,
        }


def start_span(span_type: str, *, task_id: str = "") -> Span:
    """Start a span and bind it into contextvars."""
    span_id = new_trace_id()
    resolved_task_id = task_id or _TASK_ID.get()
    resolved_trace_id = _TRACE_ID.get() or resolved_task_id or new_trace_id()

    trace_token: Optional[Token[str]] = None
    task_token: Optional[Token[str]] = None
    if not _TRACE_ID.get():
        trace_token = _TRACE_ID.set(resolved_trace_id)
    if resolved_task_id and _TASK_ID.get() != resolved_task_id:
        task_token = _TASK_ID.set(resolved_task_id)

    span_id_token = _SPAN_ID.set(span_id)
    span_type_token = _SPAN_TYPE.set(span_type)

    return Span(
        span_id=span_id,
        span_type=span_type,
        task_id=resolved_task_id,
        trace_id=resolved_trace_id,
        _start=time.monotonic(),
        _span_id_token=span_id_token,
        _span_type_token=span_type_token,
        _task_id_token=task_token,
        _trace_id_token=trace_token,
    )
