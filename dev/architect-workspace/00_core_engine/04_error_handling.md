# Error Handling & Recovery Strategy

## Overview
In a multi-agent system with external LLM APIs, sandboxed tool execution, and real-time TUI rendering, errors are not exceptional — they are **routine**. Network requests fail. LLMs hallucinate malformed XML. Users hit `Ctrl+C` at the worst possible moment.

This architecture defines a **layered error handling strategy** with three severity tiers, clear escalation paths, and proportional user notification. Every error is classified, handled at the appropriate layer, and surfaced to the user only when necessary.

---

## 1. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Error Classification** | Three-Tier (Transient / Recoverable / Fatal) | Captures the full spectrum: auto-retryable, adaptable, and unrecoverable |
| **LLM API Retry** | Exponential backoff with jitter | Industry standard. Respects rate limits. Prevents thundering herd. |
| **Provider Fallback** | No automatic fallback (fail fast) | Simplicity and predictability. One provider fails → task fails after retries. |
| **Context Length Exceeded** | Auto-summarize and retry | Graceful degradation. Agent keeps working when possible. |
| **TUI Notification** | Tiered (status bar / inline / modal) | User attention is proportional to error severity. |

---

## 2. The Three-Tier Error Classification

Every error in the system is classified into one of three tiers. The tier determines the **handling strategy** and **user notification level**.

### Tier 1: Transient (Auto-Retry)
Errors caused by temporary external conditions. The system retries automatically without user intervention.

| Error | Source | Retry Behavior |
|---|---|---|
| HTTP 500 / 502 / 503 | LLM API | Exponential backoff, up to 3 retries |
| HTTP 429 (Rate Limited) | LLM API | Respect `Retry-After` header, then retry |
| Network timeout | LLM API | Backoff, up to 3 retries |
| Connection reset | LLM API | Backoff, up to 3 retries |
| Temporary file lock | Tool Executor | Fixed 1s delay, up to 2 retries |

**TUI Notification:** Subtle status bar indicator — `⟳ Retrying API call (2/3)...`
The chat log is NOT polluted with transient retry noise.

### Tier 2: Recoverable (System Adapts)
Errors the system can handle by changing its approach, without user intervention.

| Error | Source | Recovery Strategy |
|---|---|---|
| Context length exceeded | LLM Provider | Trigger Working Memory summarization ("Brain Dump"), then retry the LLM call |
| Schema validation error | Schema Validator | Inject error feedback into Working Memory, let the LLM self-correct (up to 3 consecutive failures) |
| Tool output too large | Tool Executor | Auto-truncate to configured limit, inject `[TRUNCATED]` marker |
| Agent stuck (same error 3x) | ReAct Loop | Inject a "Hint" prompt to force the LLM to change approach |

**TUI Notification:** Inline warning in the chat log — `⚠ Context too long, summarizing older steps...`

### Tier 3: Fatal (Escalate Immediately)
Errors that cannot be resolved by the system. Require user attention or task termination.

| Error | Source | Escalation |
|---|---|---|
| Invalid API key / 401 Unauthorized | LLM Provider | Task → `FAILED`. TUI modal: "API key invalid. Run `/config` to fix." |
| Max reasoning iterations exceeded | ReAct Loop | Task → `FAILED`. Inline error with summary of what was attempted. |
| Max retries exhausted (Tier 1 → Tier 3) | LLM Provider | Task → `FAILED`. Inline error: "LLM provider unreachable after 3 retries." |
| Max schema errors exhausted (Tier 2 → Tier 3) | Schema Validator | Task → `FAILED`. Inline error: "Agent unable to produce valid output." |
| Workspace security violation | Workspace Manager | Tool blocked. Inline error to Agent (not fatal to task, agent can try a different path). |
| Unknown/unhandled exception | Any component | Task → `FAILED`. TUI error banner with stack trace summary. Full trace to debug log. |

**TUI Notification:** Modal popup or prominent error banner that demands attention.

### Tier Escalation Flow

```
┌──────────────────────────────────────────────────────┐
│                    Error Occurs                      │
└──────────────────┬───────────────────────────────────┘
                   │
                   ▼
          ┌──────────────────┐
          │  Classify Error  │
          └───┬──────┬───────┘
              │      │       │
              ▼      ▼       ▼
        ┌────────┐┌────────────┐┌────────┐
        │TRANSIENT││RECOVERABLE ││ FATAL  │
        └───┬────┘└─────┬──────┘└───┬────┘
            │           │           │
            ▼           ▼           ▼
      ┌──────────┐ ┌──────────┐ ┌──────────────┐
      │ Auto     │ │ Adapt &  │ │ Task→FAILED  │
      │ Retry    │ │ Retry    │ │ Notify User  │
      │ (backoff)│ │          │ │              │
      └───┬──────┘ └────┬─────┘ └──────────────┘
          │              │
          │ exhausted    │ exhausted
          ▼              ▼
     ┌────────────────────────┐
     │  ESCALATE TO FATAL     │
     └────────────────────────┘
```

---

## 3. Error Type Hierarchy

```python
from enum import Enum, auto


class ErrorTier(Enum):
    """Classification tier for all system errors."""
    TRANSIENT   = auto()  # Auto-retryable
    RECOVERABLE = auto()  # System can adapt
    FATAL       = auto()  # Escalate to user / fail task


class AgentCLIError(Exception):
    """Base exception for all Agent CLI errors."""
    tier: ErrorTier = ErrorTier.FATAL  # Default to fatal (safe default)
    user_message: str = "An unexpected error occurred."

    def __init__(self, message: str, user_message: str = None):
        super().__init__(message)
        if user_message:
            self.user_message = user_message


# ── Tier 1: Transient ─────────────────────────────────────────────

class LLMTransientError(AgentCLIError):
    """Temporary LLM API failure (500, timeout, rate limit)."""
    tier = ErrorTier.TRANSIENT
    user_message = "LLM provider temporarily unavailable. Retrying..."

    def __init__(self, message: str, status_code: int = None, retry_after: float = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after  # From Retry-After header (429)


# ── Tier 2: Recoverable ──────────────────────────────────────────

class ContextLengthExceededError(AgentCLIError):
    """Working Memory exceeds the LLM's token limit."""
    tier = ErrorTier.RECOVERABLE
    user_message = "Context too long. Summarizing older steps..."

    def __init__(self, message: str, current_tokens: int = 0, max_tokens: int = 0):
        super().__init__(message)
        self.current_tokens = current_tokens
        self.max_tokens = max_tokens


class SchemaValidationError(AgentCLIError):
    """LLM output doesn't match the expected XML/JSON schema."""
    tier = ErrorTier.RECOVERABLE
    user_message = "Agent produced malformed output. Requesting correction..."


class ToolExecutionError(AgentCLIError):
    """A tool encountered an OS-level error (FileNotFound, PermissionDenied, etc.)."""
    tier = ErrorTier.RECOVERABLE
    user_message = "Tool execution failed."


# ── Tier 3: Fatal ────────────────────────────────────────────────

class LLMAuthenticationError(AgentCLIError):
    """Invalid API key or unauthorized access."""
    tier = ErrorTier.FATAL
    user_message = "API key is invalid or expired. Run /config to update."


class MaxIterationsExceededError(AgentCLIError):
    """Agent hit the configured reasoning loop limit."""
    tier = ErrorTier.FATAL
    user_message = "Agent reached maximum reasoning iterations without completing the task."


class MaxRetriesExhaustedError(AgentCLIError):
    """A transient or recoverable error exhausted all retry attempts."""
    tier = ErrorTier.FATAL

    def __init__(self, message: str, original_error: AgentCLIError = None):
        super().__init__(message)
        self.original_error = original_error
        self.user_message = f"Failed after multiple retries: {message}"


class SecurityViolationError(AgentCLIError):
    """Agent attempted to access a path outside the workspace jail."""
    tier = ErrorTier.RECOVERABLE  # Not fatal to the task; agent can try a different path
    user_message = "Access denied: path is outside the workspace boundary."
```

---

## 4. Retry Engine (Exponential Backoff with Jitter)

A reusable retry utility used by the LLM Provider layer and Tool Executor.

```python
import asyncio
import random
import logging
from typing import TypeVar, Callable, Awaitable

logger = logging.getLogger(__name__)
T = TypeVar("T")


class RetryConfig:
    """Configuration for the retry engine."""
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,      # Initial delay in seconds
        max_delay: float = 30.0,       # Cap on delay
        jitter_range: float = 0.5,     # Random jitter ±0.5s
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter_range = jitter_range


async def retry_with_backoff(
    operation: Callable[[], Awaitable[T]],
    config: RetryConfig = RetryConfig(),
    on_retry: Callable[[int, Exception, float], Awaitable[None]] = None,
) -> T:
    """
    Execute an async operation with exponential backoff on transient errors.
    
    Args:
        operation:  The async function to retry.
        config:     Retry parameters.
        on_retry:   Optional callback(attempt, error, delay) for TUI status updates.
    
    Returns:
        The result of the operation on success.
    
    Raises:
        MaxRetriesExhaustedError: If all retries fail.
        AgentCLIError:            If a non-transient error is raised.
    """
    last_error = None
    
    for attempt in range(config.max_retries + 1):  # +1 for the initial attempt
        try:
            return await operation()
            
        except AgentCLIError as e:
            if e.tier != ErrorTier.TRANSIENT:
                # Non-transient errors are NOT retried — re-raise immediately
                raise
            
            last_error = e
            
            if attempt >= config.max_retries:
                break  # Exhausted all retries
            
            # Calculate delay with exponential backoff + jitter
            if isinstance(e, LLMTransientError) and e.retry_after:
                # Respect the Retry-After header for 429 responses
                delay = e.retry_after
            else:
                delay = min(
                    config.base_delay * (2 ** attempt) + random.uniform(-config.jitter_range, config.jitter_range),
                    config.max_delay
                )
            
            logger.warning(
                f"Transient error (attempt {attempt + 1}/{config.max_retries + 1}): {e}. "
                f"Retrying in {delay:.1f}s..."
            )
            
            # Notify TUI via callback (e.g., update status bar)
            if on_retry:
                await on_retry(attempt + 1, e, delay)
            
            await asyncio.sleep(delay)
    
    # All retries exhausted — escalate to Fatal
    raise MaxRetriesExhaustedError(
        f"Operation failed after {config.max_retries + 1} attempts",
        original_error=last_error
    )
```

---

## 5. Error Handling at Each Layer

### Layer 1: LLM Provider (Network + API Errors)

The `BaseLLMProvider.generate()` and `.stream()` methods wrap raw API calls with error classification:

```python
class BaseLLMProvider(ABC):
    
    async def safe_generate(self, context: list) -> str:
        """
        Wraps generate() with retry logic and error classification.
        This is what the Agent actually calls.
        """
        async def _attempt():
            try:
                return await self.generate(context)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    raise LLMAuthenticationError(f"Invalid API key for {self.model_name}")
                elif e.response.status_code == 429:
                    retry_after = float(e.response.headers.get("Retry-After", 5))
                    raise LLMTransientError(
                        f"Rate limited by {self.model_name}",
                        status_code=429,
                        retry_after=retry_after
                    )
                elif e.response.status_code >= 500:
                    raise LLMTransientError(
                        f"Server error from {self.model_name}: {e.response.status_code}",
                        status_code=e.response.status_code
                    )
                else:
                    raise LLMTransientError(f"HTTP {e.response.status_code}: {e}")
            except (httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                raise LLMTransientError(f"Timeout connecting to {self.model_name}: {e}")
            except httpx.ConnectError as e:
                raise LLMTransientError(f"Connection failed to {self.model_name}: {e}")
        
        return await retry_with_backoff(
            _attempt,
            config=RetryConfig(max_retries=3, base_delay=1.0),
            on_retry=self._notify_retry  # Updates TUI status bar
        )
```

### Layer 2: Agent ReAct Loop (Schema + Context Errors)

The Agent loop handles Tier 2 (Recoverable) errors by adapting:

```python
class BaseAgent(ABC):
    
    async def handle_task(self, task_event):
        schema_error_count = 0
        MAX_SCHEMA_ERRORS = 3
        
        for iteration in range(self.config.max_reasoning_iterations):
            try:
                # ── Call LLM (Layer 1 handles transient retries) ──
                raw_text = await self.provider.safe_generate(
                    self.memory.get_working_context()
                )
                
                # ── Validate response schema ──
                response = self.validator.parse_and_validate(raw_text)
                schema_error_count = 0  # Reset on success
                
                # ── Process action or final answer ──
                if response.final_answer:
                    return response.final_answer
                if response.action:
                    result = await self.execute_tool(response.action)
                    self.memory.add_working_event({"role": "tool", "content": result})
                    
            except ContextLengthExceededError:
                # ── TIER 2 RECOVERY: Summarize and retry ──
                await self.event_bus.emit(AgentMessageEvent(
                    source=self.name,
                    agent_name=self.name,
                    content="⚠ Context too long, summarizing older steps...",
                    is_monologue=True
                ))
                await self.memory.summarize_and_compact()
                continue  # Retry with compacted memory
                
            except SchemaValidationError as e:
                # ── TIER 2 RECOVERY: Feedback loop ──
                schema_error_count += 1
                if schema_error_count >= MAX_SCHEMA_ERRORS:
                    raise MaxRetriesExhaustedError(
                        f"Agent produced {MAX_SCHEMA_ERRORS} consecutive malformed responses",
                        original_error=e
                    )
                # Inject correction hint into Working Memory
                self.memory.add_working_event({
                    "role": "user",
                    "content": f"Schema Error: {e}. Fix your formatting and try again."
                })
                continue  # Let the LLM self-correct
                
            except ToolExecutionError as e:
                # ── TIER 2 RECOVERY: Return error to agent as observation ──
                self.memory.add_working_event({
                    "role": "tool",
                    "content": f"Tool Error: {e}. Try a different approach."
                })
                continue  # Agent can adapt
                
            except AgentCLIError as e:
                if e.tier == ErrorTier.FATAL:
                    # ── TIER 3: Escalate ──
                    raise  # Caught by the Orchestrator
                raise  # Unknown tier — treat as fatal
        
        # Loop exhausted without final answer
        raise MaxIterationsExceededError(
            f"Agent '{self.name}' reached {self.config.max_reasoning_iterations} iterations"
        )
```

### Layer 3: Orchestrator (Task-Level Recovery)

The Orchestrator catches all fatal errors from agents and manages task state:

```python
class Orchestrator:
    
    async def run_agent_task(self, task: TaskRecord, agent: BaseAgent):
        try:
            await self.state_manager.transition(task.task_id, TaskState.WORKING)
            result = await agent.handle_task(task)
            await self.state_manager.transition(
                task.task_id, TaskState.SUCCESS, result=result
            )
            
        except AgentCLIError as e:
            # ── All fatal errors land here ──
            logger.error(f"Task {task.task_id} failed: {e}", exc_info=True)
            await self.state_manager.transition(
                task.task_id, TaskState.FAILED, error=e.user_message
            )
            # Emit error event for TUI notification
            await self.event_bus.emit(TaskErrorEvent(
                source="orchestrator",
                task_id=task.task_id,
                tier=e.tier.name,
                error_message=e.user_message,
                technical_detail=str(e)
            ))
            # Handle retry logic (creates new task if retries remaining)
            await self._handle_retry_if_applicable(task, e)
            
        except Exception as e:
            # ── Completely unexpected error ──
            logger.critical(f"Unhandled exception in task {task.task_id}: {e}", exc_info=True)
            await self.state_manager.transition(
                task.task_id, TaskState.FAILED, error=f"Internal error: {type(e).__name__}"
            )
            await self.event_bus.emit(TaskErrorEvent(
                source="orchestrator",
                task_id=task.task_id,
                tier=ErrorTier.FATAL.name,
                error_message=f"Unexpected internal error: {type(e).__name__}: {str(e)[:200]}",
                technical_detail=str(e)
            ))
```

### Layer 4: TUI (User Notification)

```python
class TUIErrorHandler:
    """Subscribes to error-related events and renders appropriate notifications."""
    
    def __init__(self, app: AgentCLIApp, event_bus: AbstractEventBus):
        self.app = app
        # Subscribe to error events
        event_bus.subscribe("SystemErrorEvent", self.on_system_error, priority=50)
        event_bus.subscribe("TaskErrorEvent", self.on_task_error, priority=50)
    
    async def on_task_error(self, event: TaskErrorEvent):
        """Route errors to the appropriate TUI notification channel."""
        
        if event.tier == ErrorTier.TRANSIENT.name:
            # Status bar — subtle, non-intrusive
            self.app.status_bar.update(f"⟳ {event.error_message}")
            
        elif event.tier == ErrorTier.RECOVERABLE.name:
            # Inline warning in chat log
            self.app.chat_log.write(
                f"[yellow]⚠ {event.error_message}[/yellow]"
            )
            
        elif event.tier == ErrorTier.FATAL.name:
            # Prominent error banner or modal
            self.app.chat_log.write(
                f"[bold red]✗ {event.error_message}[/bold red]"
            )
            # For auth errors: suggest actionable fix
            if "API key" in event.error_message:
                self.app.chat_log.write(
                    "[dim]Run /config to update your API key.[/dim]"
                )
```

---

## 6. Error Events

Two new events added to the Event Bus taxonomy:

```python
@dataclass
class TaskErrorEvent(BaseEvent):
    """Published when a task encounters an error at any tier."""
    task_id: str = ""
    tier: str = ""               # "TRANSIENT", "RECOVERABLE", or "FATAL"
    error_message: str = ""      # User-friendly message
    technical_detail: str = ""   # Full error string (for debug logs only)

@dataclass
class RetryAttemptEvent(BaseEvent):
    """Published when a transient error triggers an automatic retry."""
    task_id: str = ""
    attempt: int = 0
    max_retries: int = 0
    delay_seconds: float = 0.0
    error_message: str = ""
```

---

## 7. Configuration (Error-Related Settings)

Added to `AgentSettings` (from `02_config_management.md`):

```python
class AgentSettings(BaseSettings):
    # ... existing fields ...
    
    # Retry settings
    llm_max_retries: int = Field(
        default=3, ge=0, le=10,
        description="Maximum retry attempts for transient LLM API errors."
    )
    llm_retry_base_delay: float = Field(
        default=1.0, ge=0.1,
        description="Base delay (seconds) for exponential backoff."
    )
    llm_retry_max_delay: float = Field(
        default=30.0,
        description="Maximum delay cap (seconds) for exponential backoff."
    )
    
    # Schema error tolerance
    max_consecutive_schema_errors: int = Field(
        default=3, ge=1, le=10,
        description="Max consecutive malformed LLM responses before failing the task."
    )
    
    # Task retry settings (Orchestrator-level)
    max_task_retries: int = Field(
        default=1, ge=0, le=5,
        description="Max times a FAILED task is retried with a fresh attempt."
    )
```

---

## 8. Error Handling Summary Matrix

| Layer | Handles | Transient Strategy | Recoverable Strategy | Fatal Strategy |
|---|---|---|---|---|
| **LLM Provider** | API/Network errors | Exponential backoff + jitter (3 retries) | — | Raise `LLMAuthenticationError` |
| **Agent Loop** | Schema, context, tool errors | — | Feedback loop / summarize / adapt | Raise to Orchestrator |
| **Orchestrator** | Task-level failures | — | — | Transition task → `FAILED`, optionally create retry task |
| **Event Bus** | Subscriber exceptions | — | — | Catch + log + emit `SystemErrorEvent` |
| **TUI** | All error events | Status bar indicator | Inline chat warning | Modal / error banner |

---

## 9. Anti-Patterns to Avoid

1. **Never swallow errors silently.** Every caught exception must either be logged, re-raised, or emitted as an event. Silent `except: pass` is forbidden.

2. **Never retry Fatal errors.** If an API key is invalid, retrying 3 times just wastes time. The error classification must be checked *before* entering the retry loop.

3. **Never expose raw stack traces to the user.** The TUI shows `user_message` (human-friendly). Full stack traces go to the debug log file only.

4. **Never let tool errors kill the agent.** A `FileNotFoundError` from `read_file` is an observation, not a crash. The error string is returned to the agent's Working Memory so the LLM can adapt.

5. **Never retry without backoff.** Immediate retries against a rate-limited API will get your key banned. Always use exponential backoff.

---

## 10. Testing Strategy

```python
import pytest

@pytest.mark.asyncio
async def test_transient_error_is_retried():
    call_count = 0
    
    async def flaky_operation():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise LLMTransientError("Server error", status_code=500)
        return "success"
    
    result = await retry_with_backoff(
        flaky_operation,
        config=RetryConfig(max_retries=3, base_delay=0.01)  # Fast for tests
    )
    assert result == "success"
    assert call_count == 3

@pytest.mark.asyncio
async def test_fatal_error_is_not_retried():
    async def auth_failure():
        raise LLMAuthenticationError("Invalid key")
    
    with pytest.raises(LLMAuthenticationError):
        await retry_with_backoff(auth_failure, config=RetryConfig(max_retries=3))

@pytest.mark.asyncio
async def test_exhausted_retries_escalate_to_fatal():
    async def always_fails():
        raise LLMTransientError("Always 500", status_code=500)
    
    with pytest.raises(MaxRetriesExhaustedError) as exc_info:
        await retry_with_backoff(
            always_fails,
            config=RetryConfig(max_retries=2, base_delay=0.01)
        )
    assert exc_info.value.original_error is not None

@pytest.mark.asyncio
async def test_schema_error_recovery_in_agent_loop():
    """Agent should self-correct after schema errors, up to the limit."""
    # Mock an agent that produces 2 bad responses then 1 good one
    # Verify: 2 feedback injections into Working Memory, then success

@pytest.mark.asyncio
async def test_context_length_triggers_summarization():
    """When context exceeds limit, memory should be compacted, not the task failed."""
    # Mock a provider that raises ContextLengthExceededError on first call
    # Verify: memory.summarize_and_compact() is called, then retry succeeds
```
