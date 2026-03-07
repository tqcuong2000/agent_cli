"""
Unit tests for the Error Handling Framework (Sub-Phase 1.4.5).

Tests cover:
- Error taxonomy and tier classification
- Retry engine (exponential backoff and max retries)
- Rate-limit specific backoff (retry_after)
- Event bus integration (RetryAttemptEvent, TaskErrorEvent)
- Immediate failure for non-transient errors (FATAL/RECOVERABLE)
- Recovery strategy helper functions
"""

import asyncio
import pytest

from agent_cli.core.infra.events.errors import (
    ErrorTier,
    LLMTransientError,
    LLMRateLimitError,
    CriticalError,
    SchemaValidationError,
    ToolExecutionError,
)
from agent_cli.core.infra.events.retry import retry_with_backoff, ErrorRecoveryStrategy
from agent_cli.core.infra.events.events import RetryAttemptEvent, TaskErrorEvent
from agent_cli.core.infra.events.event_bus import AsyncEventBus


# ── Error Taxonomy Tests ──────────────────────────────────────────────


def test_error_tiers():
    """Verify that different error types have the correct tier."""
    assert LLMTransientError("timeout").tier == ErrorTier.TRANSIENT
    assert LLMRateLimitError("rate limit").tier == ErrorTier.TRANSIENT
    assert SchemaValidationError("schema").tier == ErrorTier.RECOVERABLE
    assert ToolExecutionError("tool failure").tier == ErrorTier.RECOVERABLE
    assert CriticalError("critical").tier == ErrorTier.FATAL


# ── Retry Engine Tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_success_after_failures():
    """Verify the function eventually succeeds if transient errors resolve."""
    attempts = 0

    async def flaky_call():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise LLMTransientError("API down")
        return "success"

    # base_delay=0 to run tests fast
    result = await retry_with_backoff(flaky_call, max_retries=3, base_delay=0.0)
    
    assert result == "success"
    assert attempts == 3


@pytest.mark.asyncio
async def test_retry_exhausted():
    """Verify AgentCLIError is raised when max retries are exceeded."""
    attempts = 0

    async def always_fails():
        nonlocal attempts
        attempts += 1
        raise LLMTransientError("API down permanently")

    with pytest.raises(LLMTransientError):
        await retry_with_backoff(always_fails, max_retries=2, base_delay=0.0)
        
    assert attempts == 3  # 1 initial + 2 retries


@pytest.mark.asyncio
async def test_non_transient_error_fails_immediately():
    """Verify RECOVERABLE and FATAL errors do not trigger retries."""
    attempts = 0

    async def fatal_call():
        nonlocal attempts
        attempts += 1
        raise CriticalError("Fatal crash")

    with pytest.raises(CriticalError):
        await retry_with_backoff(fatal_call, max_retries=5, base_delay=0.0)
        
    assert attempts == 1  # No retries


@pytest.mark.asyncio
async def test_rate_limit_retry_after():
    """Verify LLMRateLimitError respects the retry_after hint if available."""
    
    # We will mock asyncio.sleep to verify the correct delay was requested
    sleeps = []
    
    async def mock_sleep(delay):
        sleeps.append(delay)
        
    original_sleep = asyncio.sleep
    asyncio.sleep = mock_sleep
    
    attempts = 0
    async def rate_limited_call():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise LLMRateLimitError("Limit exceeded", retry_after=42.5)
        return "success"
        
    try:
        await retry_with_backoff(rate_limited_call, max_retries=1, base_delay=1.0, max_delay=60.0)
        assert len(sleeps) == 1
        assert sleeps[0] == 42.5
    finally:
        asyncio.sleep = original_sleep  # Restore


# ── Event Bus Integration Tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_engine_publishes_events():
    """Verify RetryAttemptEvent and TaskErrorEvent are published on the bus."""
    bus = AsyncEventBus()
    retry_events = []
    error_events = []
    
    async def capture_retry(event):
        retry_events.append(event)
        
    async def capture_error(event):
        error_events.append(event)
        
    bus.subscribe("RetryAttemptEvent", capture_retry)
    bus.subscribe("TaskErrorEvent", capture_error)
    
    async def failing_call():
        raise LLMTransientError("Timeout")
        
    with pytest.raises(LLMTransientError):
        await retry_with_backoff(
            failing_call, 
            max_retries=2, 
            base_delay=0.0, 
            task_id="task-123",
            event_bus=bus
        )
        
    # Let background emit events complete
    await asyncio.sleep(0.05)
    
    assert len(retry_events) == 2
    assert retry_events[0].attempt == 1
    assert retry_events[1].attempt == 2
    assert retry_events[0].task_id == "task-123"
    
    assert len(error_events) == 1
    assert error_events[0].task_id == "task-123"
    assert error_events[0].tier == ErrorTier.TRANSIENT.value
    assert error_events[0].error_id == "provider.transient_error"
    assert error_events[0].ui_title == "Provider Error"


# ── Recovery Strategy Tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_schema_error_strategy():
    """Verify handle_schema_error manages the re-prompt budget correctly."""
    
    # Still within budget
    should_retry = await ErrorRecoveryStrategy.handle_schema_error(
        raw_response="{bad_json}", attempt=1, max_attempts=3, task_id="t1"
    )
    assert should_retry is True

    # Exhausted budget
    should_retry = await ErrorRecoveryStrategy.handle_schema_error(
        raw_response="{bad_json}", attempt=3, max_attempts=3, task_id="t1"
    )
    assert should_retry is False


@pytest.mark.asyncio
async def test_tool_error_strategy():
    """Verify handle_tool_error formats feedback correctly."""
    try:
        int("not a number")
    except ValueError as e:
        error_obj = e
        
    feedback = await ErrorRecoveryStrategy.handle_tool_error(
        error=error_obj, tool_name="calculator", task_id="t1"
    )
    
    assert "Tool 'calculator' failed with error" in feedback
    assert "invalid literal for int()" in feedback
