"""
Retry Engine — exponential backoff with jitter and Event Bus integration.

Provides ``retry_with_backoff``, a high-level async utility that wraps
any coroutine with automatic retry logic.  On each retry attempt it
publishes a ``RetryAttemptEvent``; when all retries are exhausted it
publishes a ``TaskErrorEvent``.

Only **TRANSIENT** errors trigger retries.  RECOVERABLE and FATAL
errors propagate immediately — their handling is the caller's
responsibility (context compaction, schema repair, etc.).
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, Optional, TypeVar

from agent_cli.core.error_handler.errors import (
    AgentCLIError,
    ErrorTier,
    LLMRateLimitError,
)
from agent_cli.core.events.event_bus import AbstractEventBus
from agent_cli.core.events.events import RetryAttemptEvent, TaskErrorEvent

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ══════════════════════════════════════════════════════════════════════
# Retry Engine (1.4.2)
# ══════════════════════════════════════════════════════════════════════


async def retry_with_backoff(
    func: Callable[..., Awaitable[T]],
    *args,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    task_id: str = "",
    event_bus: Optional[AbstractEventBus] = None,
    **kwargs,
) -> T:
    """Execute *func* with automatic retry for TRANSIENT errors.

    Backoff formula:  ``min(base_delay * 2^attempt + jitter, max_delay)``
    where jitter is uniform random in ``[0, base_delay)``.

    If the error is a ``LLMRateLimitError`` with a ``retry_after`` hint,
    that value is used instead of the computed delay.

    Args:
        func:        The async callable to execute.
        max_retries: Maximum number of retry attempts (0 = no retries).
        base_delay:  Initial delay in seconds.
        max_delay:   Maximum delay cap in seconds.
        task_id:     Associated task ID (for event publishing).
        event_bus:   If provided, publishes RetryAttemptEvent / TaskErrorEvent.
        *args, **kwargs: Forwarded to *func*.

    Returns:
        The return value of *func* on success.

    Raises:
        AgentCLIError: Re-raised after all retries are exhausted, or
                       immediately for non-TRANSIENT errors.
    """
    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)

        except AgentCLIError as e:
            last_error = e

            # ── Non-transient errors propagate immediately ──
            if e.tier != ErrorTier.TRANSIENT:
                raise

            # ── Last attempt — no more retries ──
            if attempt >= max_retries:
                break

            # ── Compute delay ──
            delay = _compute_delay(e, attempt, base_delay, max_delay)

            logger.warning(
                "Transient error (attempt %d/%d): %s — retrying in %.1fs",
                attempt + 1,
                max_retries,
                e.user_message,
                delay,
            )

            # ── Publish retry event (1.4.3) ──
            if event_bus:
                await event_bus.emit(
                    RetryAttemptEvent(
                        source="retry_engine",
                        task_id=task_id,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        delay_seconds=delay,
                        error_message=e.user_message,
                    )
                )

            await asyncio.sleep(delay)

        except Exception as e:
            # Non-AgentCLIError exceptions are always fatal
            last_error = e
            break

    # ── All retries exhausted — publish failure and re-raise (1.4.3) ──
    if event_bus and task_id:
        tier = (
            last_error.tier
            if isinstance(last_error, AgentCLIError)
            else ErrorTier.FATAL
        )
        await event_bus.emit(
            TaskErrorEvent(
                source="retry_engine",
                task_id=task_id,
                tier=tier.value,
                error_message=getattr(last_error, "user_message", str(last_error)),
                technical_detail=str(last_error),
            )
        )

    raise last_error  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════
# Graceful Degradation Strategies (1.4.4)
# ══════════════════════════════════════════════════════════════════════


class ErrorRecoveryStrategy:
    """Namespace for recovery actions invoked by the Agent Loop or
    Orchestrator when a RECOVERABLE error is caught.

    These are *static helpers* — they don't hold state.  The caller
    decides which strategy to apply based on the error type.
    """

    @staticmethod
    async def handle_context_overflow(
        event_bus: Optional[AbstractEventBus] = None,
        task_id: str = "",
    ) -> None:
        """Trigger context compaction when the LLM reports context overflow.

        The actual summarization logic lives in the Memory Manager
        (Phase 3).  This method exists so Phase 1 has a clearly
        defined hook ready for integration.
        """
        logger.info(
            "Context overflow for task %s — compaction requested", task_id
        )
        # Phase 3 will wire this to the Memory Manager's compact()

    @staticmethod
    async def handle_schema_error(
        raw_response: str,
        attempt: int,
        max_attempts: int,
        event_bus: Optional[AbstractEventBus] = None,
        task_id: str = "",
    ) -> bool:
        """Decide whether to re-prompt after a malformed LLM response.

        Returns:
            ``True`` if a re-prompt should be attempted.
            ``False`` if the error budget is exhausted (caller should fail).
        """
        if attempt >= max_attempts:
            logger.error(
                "Schema error budget exhausted (%d/%d) for task %s",
                attempt,
                max_attempts,
                task_id,
            )
            return False

        logger.warning(
            "Schema error (attempt %d/%d) for task %s — will re-prompt",
            attempt,
            max_attempts,
            task_id,
        )
        return True

    @staticmethod
    async def handle_tool_error(
        error: Exception,
        tool_name: str,
        event_bus: Optional[AbstractEventBus] = None,
        task_id: str = "",
    ) -> str:
        """Format a tool error as feedback to inject into the conversation.

        The agent loop appends this to the working memory so the LLM
        can see what went wrong and try a different approach.
        """
        feedback = (
            f"Tool '{tool_name}' failed with error: {error}\n"
            f"Please try a different approach or use a different tool."
        )
        logger.warning("Tool error in task %s: %s — %s", task_id, tool_name, error)
        return feedback


# ══════════════════════════════════════════════════════════════════════
# Internal Helpers
# ══════════════════════════════════════════════════════════════════════


def _compute_delay(
    error: AgentCLIError,
    attempt: int,
    base_delay: float,
    max_delay: float,
) -> float:
    """Compute backoff delay with jitter.

    If the error is a ``LLMRateLimitError`` with ``retry_after``,
    that value takes precedence.
    """
    if isinstance(error, LLMRateLimitError) and error.retry_after:
        return min(error.retry_after, max_delay)

    jitter = random.uniform(0, base_delay)
    delay = base_delay * (2 ** attempt) + jitter
    return min(delay, max_delay)
