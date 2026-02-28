"""
Error Taxonomy — three-tier classification for Agent CLI.

Every error in the system inherits from ``AgentCLIError`` and carries
a ``tier`` that determines the handling strategy:

* **TRANSIENT** — Retry automatically with exponential backoff.
  Examples: network timeouts, rate-limits, LLM overload (503).
* **RECOVERABLE** — Don't retry the same call, but apply a recovery
  strategy (e.g. context compaction, schema repair, tool fallback).
* **FATAL** — Unrecoverable.  Fail the task immediately, notify the
  user, and log for post-mortem.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Optional


# ══════════════════════════════════════════════════════════════════════
# Tier Classification
# ══════════════════════════════════════════════════════════════════════


class ErrorTier(str, Enum):
    """Severity band that drives the error handling strategy."""

    TRANSIENT = "TRANSIENT"
    RECOVERABLE = "RECOVERABLE"
    FATAL = "FATAL"


# ══════════════════════════════════════════════════════════════════════
# Base Error
# ══════════════════════════════════════════════════════════════════════


class AgentCLIError(Exception):
    """Root exception for all Agent CLI errors.

    Attributes:
        tier:              Error severity (drives retry / recovery logic).
        user_message:      Short, non-technical message shown in the TUI.
        technical_detail:  Full error context for structured logs.
        task_id:           The task that was being processed (if any).
    """

    tier: ErrorTier = ErrorTier.FATAL  # Default — subclasses override

    def __init__(
        self,
        message: str,
        *,
        user_message: Optional[str] = None,
        technical_detail: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.user_message = user_message or message
        self.technical_detail = technical_detail or message
        self.task_id = task_id


# ══════════════════════════════════════════════════════════════════════
# A. TRANSIENT Errors — auto-retry with backoff
# ══════════════════════════════════════════════════════════════════════


class LLMTransientError(AgentCLIError):
    """Generic transient LLM API failure (timeout, 5xx, connection reset)."""

    tier = ErrorTier.TRANSIENT


class LLMRateLimitError(AgentCLIError):
    """HTTP 429 — rate limit exceeded.

    The retry engine inspects ``retry_after`` when available.
    """

    tier = ErrorTier.TRANSIENT

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        *,
        retry_after: Optional[float] = None,
        **kwargs,
    ) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class LLMOverloadError(AgentCLIError):
    """HTTP 529 / 503 — provider overloaded."""

    tier = ErrorTier.TRANSIENT


# ══════════════════════════════════════════════════════════════════════
# B. RECOVERABLE Errors — apply a strategy, don't blindly retry
# ══════════════════════════════════════════════════════════════════════


class ContextLengthExceededError(AgentCLIError):
    """The conversation has exceeded the model's context window.

    Recovery: trigger context compaction / summarization, then retry
    with the compacted context.
    """

    tier = ErrorTier.RECOVERABLE


class SchemaValidationError(AgentCLIError):
    """LLM returned malformed JSON / invalid tool-call schema.

    Recovery: re-prompt the LLM with the validation error message
    (up to ``max_consecutive_schema_errors``).
    """

    tier = ErrorTier.RECOVERABLE

    def __init__(
        self,
        message: str = "Schema validation failed",
        *,
        raw_response: Optional[str] = None,
        **kwargs,
    ) -> None:
        super().__init__(message, **kwargs)
        self.raw_response = raw_response


class ToolExecutionError(AgentCLIError):
    """A tool returned an error during execution.

    Recovery: feed the error back to the Agent so it can adjust its
    approach (e.g. fix a command, choose a different tool).
    """

    tier = ErrorTier.RECOVERABLE

    def __init__(
        self,
        message: str,
        *,
        tool_name: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, **kwargs)
        self.tool_name = tool_name


# ══════════════════════════════════════════════════════════════════════
# C. FATAL Errors — fail immediately, notify user
# ══════════════════════════════════════════════════════════════════════


class MaxIterationsExceededError(AgentCLIError):
    """Agent exceeded the iteration budget without completing the task.

    This is a safety guard against infinite loops.
    """

    tier = ErrorTier.FATAL

    def __init__(
        self,
        message: str = "Maximum iterations exceeded",
        *,
        iterations: int = 0,
        max_iterations: int = 0,
        **kwargs,
    ) -> None:
        super().__init__(message, **kwargs)
        self.iterations = iterations
        self.max_iterations = max_iterations


class AuthenticationError(AgentCLIError):
    """API key is missing, invalid, or expired (HTTP 401/403)."""

    tier = ErrorTier.FATAL


class ProviderNotFoundError(AgentCLIError):
    """The requested LLM provider is not registered."""

    tier = ErrorTier.FATAL


class CriticalError(AgentCLIError):
    """Catch-all for unexpected internal errors.

    Should never happen in normal operation — always indicates a bug.
    """

    tier = ErrorTier.FATAL
