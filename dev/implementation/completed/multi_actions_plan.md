# Multi-Action ReAct Loop â€” Implementation Plan (v2)

## Epic: MA-EPIC-001 â€” Multi-Action Support in ReAct Loop

**Objective**
Enable the ReAct runtime to execute multiple tool actions per loop iteration
with safe parallelism, sequential fallback, robust validation, and full
backward compatibility with the existing single-action behavior.

**Architecture Decisions (locked)**

| # | Decision | Choice |
|---|----------|--------|
| 1 | Decision enum | New `EXECUTE_ACTIONS` value â€” coexists with singular `EXECUTE_ACTION` |
| 2 | Action ID ownership | Runtime-generated (`act_0`, `act_1`, â€¦); native FC uses `native_call_id` |
| 3 | Execution strategy | Runtime-inferred from `BaseTool.parallel_safe` â€” no LLM-specified `depends_on`/`mode` |
| 4 | LLM output schema | Array of `{tool, args}` inside `decision.actions` |
| 5 | ToolExecutor return | Return `ToolResult` dataclass (structured), format at loop level |
| 6 | Memory injection | One `{"role": "tool"}` message per action result |
| 7 | `ask_user` singleton | Strip other actions, execute only `ask_user`, log warning |
| 8 | Agent hooks | Both per-action `on_tool_result()` AND batch `on_batch_complete()` |

**Success Metrics**
- â‰¥30% latency reduction in I/O-heavy multi-read scenarios
- 0 regression in single-action agent behavior
- 100% pass rate on new validation/executor/stuck-detector test suites
- Deterministic handling of `ask_user` and mixed read/write batches

---

## Plan Verification (2026-03-05)

Validation was run against the current repository state (`agent_cli/...` package layout) and baseline tests.

**Verified with no blockers**
- Sprint 1 stories MA-01-01..MA-01-05 and MA-02-01 map cleanly to existing modules.
- Baseline regression suite passed before Sprint 1 kickoff:
  - `python -m pytest dev/tests/tools/test_base.py dev/tests/agent/test_schema.py dev/tests/core/test_bootstrap.py dev/tests/core/test_orchestrator.py -q`
  - Result: `44 passed`

**Required corrections for execution**
- Path normalization: all implementation targets should use `agent_cli/core/...` and `agent_cli/data/...` (instead of unprefixed `core/...` and `data/...`).
- Multi-action flag scope: `SchemaValidator` is currently instantiated once in bootstrap and shared across agents. Any per-agent multi-action behavior must be gated in the agent loop/config path, or validator instantiation must be refactored to per-agent instances before MA-03-02.
- Action ID timing: in Sprint 1, `ParsedAction.action_id` is introduced as a contract field with a backward-compatible default. It becomes populated on multi-action paths when MA-03 stories land.

---

## Current Architecture Reference

```
core/runtime/
â”œâ”€â”€ agents/
â”‚   â”œâ”€â”€ base.py           â† BaseAgent + handle_task() loop + AgentConfig
â”‚   â”œâ”€â”€ parsers.py         â† ParsedAction, AgentDecision, AgentResponse
â”‚   â”œâ”€â”€ schema.py          â† SchemaValidator (JSON + native FC parsing)
â”‚   â”œâ”€â”€ react_loop.py      â† StuckDetector, PromptBuilder
â”‚   â””â”€â”€ memory.py          â† WorkingMemoryManager
â”œâ”€â”€ tools/
â”‚   â”œâ”€â”€ base.py            â† BaseTool ABC, ToolResult, ToolCategory
â”‚   â”œâ”€â”€ executor.py        â† ToolExecutor (execute â†’ returns str today)
â”‚   â”œâ”€â”€ output_formatter.pyâ† ToolOutputFormatter (JSON envelope)
â”‚   â”œâ”€â”€ registry.py        â† ToolRegistry
â”‚   â”œâ”€â”€ file_tools.py      â† read_file, write_file, str_replace, insert_lines,
â”‚   â”‚                         list_directory, search_files
â”‚   â”œâ”€â”€ shell_tool.py      â† run_command
â”‚   â””â”€â”€ ask_user_tool.py   â† ask_user
â””â”€â”€ orchestrator/
    â””â”€â”€ ...

data/
â”œâ”€â”€ prompts/
â”‚   â”œâ”€â”€ output_format.txt       â† JSON prompt mode template
â”‚   â””â”€â”€ output_format_native.txtâ† Native FC prompt mode template
â”œâ”€â”€ memory.json                 â† stuck_detector defaults here
â”œâ”€â”€ tools.json                  â† executor + output_formatter defaults
â””â”€â”€ schema.json                 â† validation defaults
```

**Tool Safety Map (current `is_safe` values):**

| Tool | `is_safe` | Category | Parallel-safe? |
|------|-----------|----------|----------------|
| `read_file` | `True` | FILE | âœ… Yes |
| `write_file` | `False` | FILE | âŒ No |
| `str_replace` | `False` | FILE | âŒ No |
| `insert_lines` | `False` | FILE | âŒ No |
| `list_directory` | `True` | FILE | âœ… Yes |
| `search_files` | `True` | SEARCH | âœ… Yes |
| `run_command` | `False` | EXECUTION | âŒ No |
| `ask_user` | `True` | UTILITY | ðŸš« Singleton |

---

## Story: MA-01 â€” Contracts & Data Model Changes

### MA-01-01: Add `parallel_safe` property to `BaseTool`

**File:** `agent_cli/core/runtime/tools/base.py`
**Priority:** Highest
**Estimate:** 1 SP

**Changes:**
```python
class BaseTool(ABC):
    name: str
    description: str
    is_safe: bool = False
    category: ToolCategory = ToolCategory.UTILITY
    parallel_safe: bool = True  # NEW â€” runtime uses this for execution strategy
```

**Acceptance Criteria:**
- Default `True` (read-only tools don't need to opt in)
- Override to `False` on: `write_file`, `str_replace`, `insert_lines`, `run_command`
- `ask_user` has `parallel_safe = False` (treated as singleton via separate guard)
- All existing tool subclasses audited and annotated

**Dependencies:** None

---

### MA-01-02: Extend `ParsedAction` with `action_id`

**File:** `agent_cli/core/runtime/agents/parsers.py`
**Priority:** Highest
**Estimate:** 1 SP

**Changes:**
```python
@dataclass
class ParsedAction:
    tool_name: str
    arguments: Dict[str, Any]
    native_call_id: str = ""
    action_id: str = ""          # NEW â€” runtime-assigned, e.g. "act_0"
```

**Acceptance Criteria:**
- Field is always populated by the runtime (never by the LLM)
- Default empty string preserves backward compat
- Unit tests for serialization/deserialization

**Dependencies:** None

---

### MA-01-03: Add `EXECUTE_ACTIONS` to `AgentDecision` enum

**File:** `agent_cli/core/runtime/agents/parsers.py`
**Priority:** Highest
**Estimate:** 1 SP

**Changes:**
```python
class AgentDecision(Enum):
    REFLECT = "reflect"
    EXECUTE_ACTION = "execute_action"       # existing â€” exactly one tool
    EXECUTE_ACTIONS = "execute_actions"     # NEW â€” multiple tools
    NOTIFY_USER = "notify_user"
    YIELD = "yield"
```

**Acceptance Criteria:**
- Docstring updated to describe the new value
- No existing code paths break (they match on `EXECUTE_ACTION`)

**Dependencies:** None

---

### MA-01-04: Add `actions` field to `AgentResponse`

**File:** `agent_cli/core/runtime/agents/parsers.py`
**Priority:** Highest
**Estimate:** 2 SP

**Changes:**
```python
@dataclass
class AgentResponse:
    decision: AgentDecision = AgentDecision.REFLECT
    title: str = ""
    thought: str = ""
    action: Optional[ParsedAction] = None           # existing â€” single action
    actions: Optional[List[ParsedAction]] = None     # NEW â€” multi-action list
    final_answer: Optional[str] = None
```

**Design notes:**
- `EXECUTE_ACTION` uses `action` (singular) â€” unchanged
- `EXECUTE_ACTIONS` uses `actions` (list) â€” new path
- The loop dispatches on `.decision` and reads the appropriate field
- Both fields are never populated simultaneously

**Acceptance Criteria:**
- Backward-compatible: existing tests using `action` field pass unchanged
- `actions` is `None` by default
- Unit tests for both single and multi-action `AgentResponse` construction

**Dependencies:** MA-01-02, MA-01-03

---

### MA-01-05: Extend `ToolResult` with `action_id` and `tool_name`

**File:** `agent_cli/core/runtime/tools/base.py`
**Priority:** Highest
**Estimate:** 1 SP

**Changes:**
```python
@dataclass
class ToolResult:
    success: bool = True
    output: str = ""
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    action_id: str = ""       # NEW â€” matches ParsedAction.action_id
    tool_name: str = ""       # NEW â€” for resultâ†’action mapping
```

**Acceptance Criteria:**
- Default empty strings preserve backward compat
- Unit tests for field access

**Dependencies:** MA-01-02

---

## Story: MA-02 â€” Feature Flags & Config

### MA-02-01: Add multi-action feature flags

**Files:**
- `agent_cli/core/runtime/agents/base.py` â†’ `AgentConfig`
- `agent_cli/data/tools.json` â†’ executor defaults

**Priority:** High
**Estimate:** 2 SP

**Changes to `AgentConfig`:**
```python
@dataclass
class AgentConfig:
    name: str = ""
    description: str = ""
    persona: str = ""
    model: str = ""
    tools: List[str] = field(default_factory=list)
    max_iterations_override: Optional[int] = None
    show_thinking: bool = True
    multi_action_enabled: bool = False       # NEW
    max_concurrent_actions: int = 5          # NEW
```

**Changes to `data/tools.json`:**
```json
{
  "executor": {
    "approval_timeout_seconds": 300.0,
    "multi_action": {
      "enabled": false,
      "max_concurrent_actions": 5
    }
  }
}
```

**Acceptance Criteria:**
- Default `False` preserves single-action behavior everywhere
- `max_concurrent_actions` configurable per-agent and globally
- `handle_task()` reads the flag to decide single vs multi dispatch
- Config docs updated

**Dependencies:** MA-01-04

---

## Story: MA-03 â€” SchemaValidator Multi-Action Parsing

### MA-03-01: Add `execute_actions` parsing to `_parse_json_response()`

**File:** `core/runtime/agents/schema.py`
**Priority:** Highest
**Estimate:** 5 SP

**Changes:**
Add a new branch in `_parse_json_response()` after the existing `execute_action` handling:

```python
if decision_type == AgentDecision.EXECUTE_ACTIONS.value:
    raw_actions = decision.get("actions")
    if not isinstance(raw_actions, list) or len(raw_actions) == 0:
        raise SchemaValidationError(
            "decision.actions must be a non-empty list for execute_actions.",
            raw_response=text,
        )

    parsed_actions: List[ParsedAction] = []
    for idx, raw_action in enumerate(raw_actions):
        tool_name = raw_action.get("tool", "").strip()
        if not tool_name:
            raise SchemaValidationError(
                f"Action at index {idx} missing 'tool' field.",
                raw_response=text,
            )
        if tool_name not in self._registered_tools:
            raise SchemaValidationError(
                f"Unknown tool '{tool_name}' in action[{idx}].",
                raw_response=text,
            )
        arguments = raw_action.get("args", {})
        if not isinstance(arguments, dict):
            raise SchemaValidationError(
                f"Action[{idx}].args must be an object.",
                raw_response=text,
            )
        parsed_actions.append(ParsedAction(
            tool_name=tool_name,
            arguments=arguments,
            action_id=f"act_{idx}",  # runtime-assigned
        ))

    return AgentResponse(
        decision=AgentDecision.EXECUTE_ACTIONS,
        title=title,
        thought=thought,
        actions=parsed_actions,
    )
```

**Acceptance Criteria:**
- Single-action `execute_action` path unchanged
- `execute_actions` with valid action list parsed correctly
- `action_id` assigned as `act_0`, `act_1`, etc.
- Validation errors for: empty list, missing tool, unknown tool, non-dict args
- Unit tests for all valid/invalid cases

**Dependencies:** MA-01-04

---

### MA-03-02: Lift single-tool restriction in `_parse_native_fc()`

**File:** `core/runtime/agents/schema.py`
**Priority:** High
**Estimate:** 3 SP

**Changes:**
Replace the current rejection logic:

```python
# BEFORE:
if len(response.tool_calls) > 1:
    raise SchemaValidationError("Multiple native tool calls found...")

# AFTER:
if len(response.tool_calls) > 1:
    if not multi_action_enabled:
        raise SchemaValidationError("Multiple native tool calls found...")
    # Multi-action: parse all tool calls
    parsed_actions = []
    for idx, tc in enumerate(response.tool_calls):
        if tc.tool_name not in self._registered_tools:
            raise SchemaValidationError(...)
        parsed_actions.append(ParsedAction(
            tool_name=tc.tool_name,
            arguments=tc.arguments,
            native_call_id=tc.native_call_id,
            action_id=tc.native_call_id or f"act_{idx}",
        ))
    return AgentResponse(
        decision=AgentDecision.EXECUTE_ACTIONS,
        actions=parsed_actions,
        ...)
```

**Design note:** `SchemaValidator.__init__()` needs a new `multi_action_enabled` parameter,
passed through from `AgentConfig`.

**Acceptance Criteria:**
- When `multi_action_enabled=False` (default), behavior unchanged
- When enabled, multiple native tool calls produce `EXECUTE_ACTIONS` response
- `action_id` uses `native_call_id` when available, falls back to `act_{idx}`

**Dependencies:** MA-01-04, MA-02-01

---

### MA-03-03: Add format-repair for non-compliant LLM output

**File:** `core/runtime/agents/schema.py`
**Priority:** Medium
**Estimate:** 2 SP

**Changes:**
When `multi_action_enabled=True`, if the LLM returns a single-action
`execute_action` response, optionally wrap it into an `execute_actions` list:

```python
# In _parse_json_response(), after detecting execute_action when multi_action is on:
# Auto-repair: wrap single action into list (configurable)
if self._multi_action_enabled and decision_type == "execute_action":
    # Treat as single-item execute_actions (optional repair path)
    # This is a pass-through â€” no behavioral change, just normalization
    pass  # keep as EXECUTE_ACTION, don't force wrapping
```

**Decision:** This repair is **optional and conservative**. When multi-action mode
is active, a single `execute_action` stays as `EXECUTE_ACTION` (no unnecessary wrapping).
Repair only applies if the LLM returns a malformed multi-action payload (e.g., flat object
instead of array).

**Acceptance Criteria:**
- Repair path is logged/observable
- Hard validation still enforced post-repair
- Does not change behavior of valid single-action responses

**Dependencies:** MA-03-01

---

## Story: MA-04 â€” Multi-Action Validation

### MA-04-01: Implement batch validation rules

**File:** New file `core/runtime/agents/multi_action_validator.py`
**Priority:** Highest
**Estimate:** 5 SP

**Description:**
Standalone validator called after `SchemaValidator` produces an `AgentResponse`
with `EXECUTE_ACTIONS` decision. Runs before execution.

```python
class MultiActionValidator:
    """Validates a batch of ParsedActions before execution."""

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self._registry = tool_registry

    def validate(self, actions: List[ParsedAction]) -> List[ParsedAction]:
        """Validate and potentially modify the action batch.

        Rules:
        1. Unique action_ids (should always be true since runtime-generated)
        2. All tool names exist in registry
        3. ask_user singleton guard:
           - If ask_user is in the batch with other actions,
             strip others, keep only ask_user, log warning
        4. Max batch size check

        Returns:
            Validated (possibly stripped) action list.

        Raises:
            SchemaValidationError: On unrecoverable validation failure.
        """
```

**Acceptance Criteria:**
- `ask_user` + other actions â†’ strip others, keep `ask_user` only, log warning
- `ask_user` alone â†’ pass through
- Empty action list â†’ error
- All tool names validated against registry
- Unit tests for all rules

**Dependencies:** MA-01-04, MA-01-05

---

## Story: MA-05 â€” PromptBuilder Multi-Action Contract

### MA-05-01: Add `multi_action` parameter to `PromptBuilder.build()`

**File:** `core/runtime/agents/react_loop.py`
**Priority:** High
**Estimate:** 2 SP

**Changes:**
```python
def build(
    self,
    persona: str,
    tool_names: List[str],
    *,
    workspace_context: str = "",
    extra_instructions: str = "",
    native_tool_mode: bool = False,
    multi_action: bool = False,                    # NEW
    provider_managed_capabilities: List[str] | None = None,
) -> str:
    # ...
    # 2. Output format â€” choose template based on mode
    if multi_action:
        sections.append(self._output_format_section_multi(native_tool_mode))
    else:
        sections.append(self._output_format_section(native_tool_mode))
```

**Acceptance Criteria:**
- When `multi_action=False`, prompt identical to current
- When `multi_action=True`, uses multi-action output format section
- No regression for single-action agents

**Dependencies:** MA-02-01

---

### MA-05-02: Create multi-action output format template

**Files:**
- New `data/prompts/output_format_multi.txt`
- New `data/prompts/output_format_multi_native.txt`

**Priority:** High
**Estimate:** 3 SP

**Template content (`output_format_multi.txt`):**
```
# Output Format
Return exactly ONE JSON object and no other text.
No markdown, no code fences, no legacy tag formats, no prose outside the JSON object.

The JSON object must use one of these shapes:

## Single Action
{
  "title": "short title (1 to {title_max_words} words)",
  "thought": "your reasoning for this turn",
  "decision": {
    "type": "execute_action",
    "tool": "tool_name",
    "args": {}
  }
}

## Multiple Actions (when you need to invoke multiple independent tools)
{
  "title": "short title (1 to {title_max_words} words)",
  "thought": "your reasoning for this turn",
  "decision": {
    "type": "execute_actions",
    "actions": [
      {"tool": "tool_name_1", "args": {}},
      {"tool": "tool_name_2", "args": {}}
    ]
  }
}

## Other Decisions
- `reflect`: continue reasoning only. Leave `tool` empty and `message` empty.
- `notify_user`: complete the task. Put the full response in `decision.message`.
- `yield`: graceful abort. Put reason in `decision.message`.

## Multi-Action Rules
- Use `execute_actions` when you need results from MULTIPLE INDEPENDENT tools.
- All actions in the list execute in parallel where safe.
- `ask_user` must ALWAYS be the ONLY action â€” never combine it with other tools.
- Prefer `execute_action` (singular) when only one tool is needed.

## Workflow Rules
- `Action-First`: perform tool actions before notifying the user.
- `Cycle`: Think -> Act -> Wait for ALL Results -> Think again.

## Error Recovery
- If you receive a schema/formatting error, your NEXT response must be corrected JSON.
- Never return an empty response.
- Never emit free text outside the single JSON object.
```

**Acceptance Criteria:**
- Template includes both single and multi-action schema shapes
- `ask_user` singleton constraint explicitly documented
- Few-shot examples validate against SchemaValidator
- Native FC variant also created

**Dependencies:** MA-05-01, MA-03-01

---

## Story: MA-06 â€” ToolExecutor Refactor

### MA-06-01: Change `ToolExecutor.execute()` to return `ToolResult`

**File:** `core/runtime/tools/executor.py`
**Priority:** Highest
**Estimate:** 5 SP

**Changes:**
```python
async def execute(
    self,
    tool_name: str,
    arguments: Dict[str, Any],
    task_id: str = "",
    *,
    native_call_id: str = "",
    action_id: str = "",           # NEW
) -> ToolResult:                   # CHANGED from str
    """Execute a validated tool call.
    ...
    Returns:
        ToolResult with structured data + formatted output string.
    """
    # ... existing logic ...

    # At the end, instead of returning formatted string:
    formatted = self.output_formatter.format(
        tool_name, raw_result, success,
        task_id=task_id,
        native_call_id=native_call_id,
        action_id=action_id,           # NEW
    )

    return ToolResult(
        success=success,
        output=formatted,              # formatted JSON envelope string
        error="" if success else raw_result,
        action_id=action_id,
        tool_name=tool_name,
        metadata={"duration_ms": duration_ms},
    )
```

**Backward compatibility note:**
The **single-action path** in `handle_task()` currently does:
```python
result = await self.tool_executor.execute(...)
_append_message({"role": "tool", "content": result}, ...)
```
This must change to:
```python
tool_result = await self.tool_executor.execute(...)
_append_message({"role": "tool", "content": tool_result.output}, ...)
```

**Acceptance Criteria:**
- Returns `ToolResult` with all fields populated
- `output` field contains the same JSON envelope string as before
- `action_id` passed through from caller
- Existing single-action loop updated to use `.output`
- All existing tests updated for new return type

**Dependencies:** MA-01-05

---

### MA-06-02: Add `action_id` to `ToolOutputFormatter` envelope

**File:** `core/runtime/tools/output_formatter.py`
**Priority:** High
**Estimate:** 2 SP

**Changes:**
```python
def format(
    self,
    tool_name: str,
    raw_output: str,
    success: bool = True,
    *,
    task_id: str = "",
    native_call_id: str = "",
    action_id: str = "",           # NEW
) -> str:

@staticmethod
def _to_json_envelope(
    *,
    tool_name: str,
    status: str,
    output: str,
    truncated: bool,
    truncated_chars: int,
    task_id: str,
    native_call_id: str,
    action_id: str = "",           # NEW
) -> str:
    metadata: dict[str, str] = {}
    if task_id:
        metadata["task_id"] = task_id
    if native_call_id:
        metadata["native_call_id"] = native_call_id
    if action_id:
        metadata["action_id"] = action_id     # NEW â€” in envelope
```

**Acceptance Criteria:**
- `action_id` appears in envelope metadata when non-empty
- Envelope backward-compatible when `action_id=""` (no field added)
- `StuckDetector._normalize_result_for_stuck_check()` updated to exclude `action_id`

**Dependencies:** MA-01-02

---

## Story: MA-07 â€” Batch Executor

### MA-07-01: Implement `BatchExecutor` for multi-action orchestration

**File:** New file `core/runtime/agents/batch_executor.py`
**Priority:** Highest
**Estimate:** 8 SP

```python
import asyncio
from typing import List, Tuple

from agent_cli.core.runtime.agents.parsers import ParsedAction
from agent_cli.core.runtime.tools.base import BaseTool, ToolResult
from agent_cli.core.runtime.tools.executor import ToolExecutor
from agent_cli.core.runtime.tools.registry import ToolRegistry


class BatchExecutor:
    """Orchestrates multi-action execution with safety-aware parallelism.

    Execution strategy (runtime-inferred):
    1. Partition actions into parallel-safe and sequential groups.
    2. Execute all parallel-safe actions concurrently (bounded by semaphore).
    3. Execute sequential actions one at a time, in order.

    Args:
        tool_executor:   The existing ToolExecutor for individual calls.
        tool_registry:   For looking up parallel_safe flag per tool.
        max_concurrent:  Semaphore cap for parallel execution.
    """

    def __init__(
        self,
        tool_executor: ToolExecutor,
        tool_registry: ToolRegistry,
        max_concurrent: int = 5,
    ) -> None:
        self._executor = tool_executor
        self._registry = tool_registry
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def execute_batch(
        self,
        actions: List[ParsedAction],
        task_id: str = "",
    ) -> List[ToolResult]:
        """Execute a batch of actions with safety-aware parallelism.

        Strategy:
        1. Split actions into parallel_safe and sequential groups
           (preserving original order within each group).
        2. Execute all parallel_safe actions concurrently.
        3. Execute sequential actions one at a time, in original order.
        4. Merge results in original action order.

        Returns:
            List of ToolResult, one per action, in original action order.
        """
        parallel_actions: List[Tuple[int, ParsedAction]] = []
        sequential_actions: List[Tuple[int, ParsedAction]] = []

        for idx, action in enumerate(actions):
            tool = self._registry.get(action.tool_name)
            if tool is not None and getattr(tool, "parallel_safe", True):
                parallel_actions.append((idx, action))
            else:
                sequential_actions.append((idx, action))

        results: dict[int, ToolResult] = {}

        # Phase 1: parallel-safe actions (gathered with semaphore)
        if parallel_actions:
            async def _run_with_semaphore(idx: int, act: ParsedAction) -> None:
                async with self._semaphore:
                    result = await self._executor.execute(
                        tool_name=act.tool_name,
                        arguments=act.arguments,
                        task_id=task_id,
                        native_call_id=act.native_call_id,
                        action_id=act.action_id,
                    )
                    results[idx] = result

            await asyncio.gather(
                *[_run_with_semaphore(idx, act) for idx, act in parallel_actions]
            )

        # Phase 2: sequential actions (in order)
        for idx, action in sequential_actions:
            result = await self._executor.execute(
                tool_name=action.tool_name,
                arguments=action.arguments,
                task_id=task_id,
                native_call_id=action.native_call_id,
                action_id=action.action_id,
            )
            results[idx] = result

        # Merge in original order
        return [results[i] for i in range(len(actions))]
```

**Acceptance Criteria:**
- Parallel-safe actions execute concurrently (verified via timing tests)
- Sequential actions execute in order
- Semaphore caps concurrency at `max_concurrent`
- Results returned in original action order regardless of completion order
- Mixed batch: parallel-safe run first, then sequential
- Unit tests for: all-parallel, all-sequential, mixed, single-action, empty

**Dependencies:** MA-06-01, MA-01-01

---

## Story: MA-08 â€” ReAct Loop Integration

### MA-08-01: Add `EXECUTE_ACTIONS` dispatch to `handle_task()`

**File:** `core/runtime/agents/base.py`
**Priority:** Highest
**Estimate:** 8 SP

**Changes to `handle_task()` â€” add new case in the `match response.decision:` block:**

```python
case AgentDecision.EXECUTE_ACTIONS:
    # â”€â”€ MULTI-ACTION EXECUTION PATH â”€â”€
    actions = response.actions
    if not actions:
        raise SchemaValidationError(
            "Invalid: execute_actions requires a non-empty actions list.",
            raw_response=llm_response_text,
        )

    reflect_count = 0
    _append_message(
        {
            "role": "assistant",
            "content": self._format_assistant_history(llm_response),
        },
        track_for_session=True,
    )

    # Validate batch
    validated_actions = self._multi_action_validator.validate(actions)

    # Execute batch
    batch_results = await self._batch_executor.execute_batch(
        validated_actions, task_id=task_id,
    )

    # Inject results into memory (one message per result)
    for tool_result in batch_results:
        _append_message(
            {"role": "tool", "content": tool_result.output},
            track_for_session=True,
        )

    # Per-action hooks
    for action, tool_result in zip(validated_actions, batch_results):
        await self.on_tool_result(action.tool_name, tool_result.output)

    # Batch-complete hook
    await self.on_batch_complete(validated_actions, batch_results)

    # Stuck detection (batch-level)
    if stuck_detector.is_stuck_batch(
        [(a.tool_name, r.output) for a, r in zip(validated_actions, batch_results)]
    ):
        _append_message(
            {
                "role": "system",
                "content": (
                    "âš  You appear to be repeating the same batch of "
                    "actions with the same results. "
                    "Try a completely different approach."
                ),
            },
            track_for_session=True,
        )

    continue  # Next iteration
```

**Also update the existing `EXECUTE_ACTION` path** to use `ToolResult`:
```python
case AgentDecision.EXECUTE_ACTION:
    # ... existing logic ...
    tool_result = await self.tool_executor.execute(
        tool_name=action.tool_name,
        arguments=action.arguments,
        task_id=task_id,
        native_call_id=action.native_call_id,
        action_id="act_0",  # single action gets act_0
    )
    _append_message(
        {"role": "tool", "content": tool_result.output},  # CHANGED
        track_for_session=True,
    )
    await self.on_tool_result(action.tool_name, tool_result.output)  # CHANGED

    if stuck_detector.is_stuck(action.tool_name, tool_result.output):  # CHANGED
        ...
```

**Also add initialization** of `BatchExecutor` and `MultiActionValidator`:
```python
# In handle_task(), after stuck_detector initialization:
multi_action_enabled = self.config.multi_action_enabled
batch_executor = None
multi_action_validator = None
if multi_action_enabled:
    from agent_cli.core.runtime.agents.batch_executor import BatchExecutor
    from agent_cli.core.runtime.agents.multi_action_validator import MultiActionValidator
    batch_executor = BatchExecutor(
        tool_executor=self.tool_executor,
        tool_registry=self.tool_executor.registry,
        max_concurrent=self.config.max_concurrent_actions,
    )
    multi_action_validator = MultiActionValidator(
        tool_registry=self.tool_executor.registry,
    )
```

**Acceptance Criteria:**
- Single-action path (`EXECUTE_ACTION`) works identically to current behavior
- Multi-action path (`EXECUTE_ACTIONS`) processes batches correctly
- Each result appears as a separate `{"role": "tool"}` message
- Per-action `on_tool_result()` called for each action
- `on_batch_complete()` called once with all actions + results
- Stuck detection uses batch-level fingerprinting
- Feature flag off â†’ `EXECUTE_ACTIONS` is never produced by SchemaValidator

**Dependencies:** MA-07-01, MA-04-01, MA-09-01, MA-03-01

---

### MA-08-02: Add `on_batch_complete()` hook to `BaseAgent`

**File:** `core/runtime/agents/base.py`
**Priority:** High
**Estimate:** 2 SP

**Changes:**
```python
class BaseAgent(ABC):
    # ... existing abstract hooks ...

    async def on_batch_complete(
        self,
        actions: List[ParsedAction],
        results: List[ToolResult],
    ) -> None:
        """Hook called after all actions in a multi-action batch complete.

        Receives the full list of actions and their corresponding results.
        Default implementation does nothing. Agents can override for
        aggregate analysis (e.g., "2 of 3 reads failed").
        """
        pass  # Default no-op
```

**Design note:** This is **not abstract** â€” concrete agents can optionally override.
The existing `on_tool_result()` hook remains abstract and is called per-action.

**Acceptance Criteria:**
- Default no-op implementation (no existing agent breaks)
- Called with correct action-result pairs from `handle_task()`
- Agents can override for batch analysis

**Dependencies:** MA-01-04, MA-01-05

---

### MA-08-03: Update `_format_assistant_history()` for multi-action

**File:** `core/runtime/agents/base.py`
**Priority:** High
**Estimate:** 2 SP

**Changes:**
When the LLM response contains multiple native tool calls and multi-action is active,
the history serialization must include all tool call snippets (it already handles
multiple via the `for tc in tool_calls` loop â€” just verify correctness with the
new `action_id` field in the snippets).

Add `action_id` to the tool call snippet:
```python
for idx, tc in enumerate(tool_calls):
    snippets.append(json.dumps({
        "type": "tool_call",
        "version": "1.0",
        "payload": {
            "tool": tc.tool_name,
            "args": tc.arguments,
            "action_id": tc.native_call_id or f"act_{idx}",  # NEW
        },
        ...
    }))
```

**Acceptance Criteria:**
- Single tool call history format unchanged
- Multi tool call history includes `action_id` per snippet
- Session restore correctly reconstructs multi-action context

**Dependencies:** MA-06-01

---

### MA-08-04: Update `_build_schema_recovery_message()` for multi-action

**File:** `core/runtime/agents/base.py`
**Priority:** Medium
**Estimate:** 1 SP

**Changes:**
When `multi_action_enabled=True`, include a multi-action JSON example in the
schema recovery message alongside the existing single-action examples.

**Dependencies:** MA-02-01

---

## Story: MA-09 â€” StuckDetector Refactor

### MA-09-01: Add batch-level stuck detection

**File:** `core/runtime/agents/react_loop.py`
**Priority:** Highest
**Estimate:** 5 SP

**Changes:**
```python
class StuckDetector:
    def __init__(self, threshold: int = 3, history_cap: int = 10) -> None:
        self.threshold = threshold
        self.history_cap = max(int(history_cap), 1)
        self._recent: List[tuple[str, int]] = []              # single-action history
        self._recent_batches: List[int] = []                  # NEW â€” batch fingerprints

    # Existing method â€” unchanged
    def is_stuck(self, tool_name: str, result: str) -> bool:
        ...  # unchanged

    # NEW â€” batch-level stuck detection
    def is_stuck_batch(self, action_results: List[tuple[str, str]]) -> bool:
        """Check if the agent is repeating the same batch of actions.

        Args:
            action_results: List of (tool_name, result) pairs from the batch.

        Returns:
            True if the last N batches had an identical fingerprint.
        """
        # Canonical fingerprint: sort by tool_name for determinism,
        # hash each (tool, normalized_result) pair, combine.
        fingerprint_parts = []
        for tool_name, result in sorted(action_results, key=lambda x: x[0]):
            normalized = self._normalize_result_for_stuck_check(result)
            fingerprint_parts.append((tool_name, hash(normalized)))
        batch_fingerprint = hash(tuple(fingerprint_parts))

        self._recent_batches.append(batch_fingerprint)

        if len(self._recent_batches) < self.threshold:
            return False

        last_n = self._recent_batches[-self.threshold:]
        if all(fp == last_n[0] for fp in last_n):
            self._recent_batches.clear()
            logger.warning(
                "Batch stuck detected: same %d-action batch repeated %d times",
                len(action_results),
                self.threshold,
            )
            return True

        if len(self._recent_batches) > self.history_cap:
            self._recent_batches = self._recent_batches[-self.history_cap:]

        return False

    def reset(self) -> None:
        self._recent.clear()
        self._recent_batches.clear()   # NEW
```

**Acceptance Criteria:**
- Single-action `is_stuck()` unchanged
- Batch `is_stuck_batch()` detects full batch repetition
- Fingerprint includes tool names to avoid empty-payload collisions
- Sorted by tool name for order-independent matching
- `reset()` clears both histories
- Unit tests for: repeated batches, different batches, partial overlap, reset

**Dependencies:** MA-01-05

---

## Story: MA-10 â€” Testing

### MA-10-01: Unit tests for `MultiActionValidator`

**File:** `dev/tests/runtime/test_multi_action_validator.py`
**Priority:** Highest
**Estimate:** 3 SP

**Test cases:**
- Valid batch: 2 read actions â†’ pass through
- `ask_user` + other actions â†’ strip others, keep `ask_user`
- `ask_user` alone â†’ pass through
- Empty list â†’ error
- Unknown tool name â†’ error
- Duplicate action IDs (should never happen with runtime gen, but defensive test)

**Dependencies:** MA-04-01

---

### MA-10-02: Unit tests for `SchemaValidator` multi-action parsing

**File:** `dev/tests/runtime/test_schema_multi_action.py`
**Priority:** Highest
**Estimate:** 3 SP

**Test cases:**
- Valid `execute_actions` JSON â†’ `EXECUTE_ACTIONS` response with parsed actions
- Invalid: empty actions array â†’ SchemaValidationError
- Invalid: actions not a list â†’ SchemaValidationError
- Invalid: action missing tool â†’ SchemaValidationError
- Invalid: unknown tool â†’ SchemaValidationError
- Single `execute_action` when multi enabled â†’ `EXECUTE_ACTION` (no wrapping)
- Native FC with multiple tool calls when enabled â†’ `EXECUTE_ACTIONS`
- Native FC with multiple tool calls when disabled â†’ SchemaValidationError (unchanged)

**Dependencies:** MA-03-02

---

### MA-10-03: Unit tests for `BatchExecutor`

**File:** `dev/tests/runtime/test_batch_executor.py`
**Priority:** Highest
**Estimate:** 5 SP

**Test cases:**
- All parallel-safe actions â†’ concurrent execution (verify timing)
- All sequential actions â†’ serial execution (verify ordering)
- Mixed batch â†’ parallel first, then sequential
- Semaphore cap â†’ no more than N concurrent
- Single action â†’ works correctly (edge case)
- Action failure â†’ result marked as error, other actions still complete
- Results in original action order regardless of completion order

**Dependencies:** MA-07-01

---

### MA-10-04: Unit tests for `StuckDetector` batch detection

**File:** `dev/tests/runtime/test_stuck_detector_batch.py`
**Priority:** High
**Estimate:** 3 SP

**Test cases:**
- Identical batch repeated N times â†’ stuck detected
- Different batches â†’ not stuck
- Same actions, different results â†’ not stuck
- Partial overlap with previous batch â†’ not stuck
- Reset clears batch history
- Order-independent fingerprinting

**Dependencies:** MA-09-01

---

### MA-10-05: Integration test â€” parallel read fan-out

**File:** `dev/tests/integration/test_multi_action_e2e.py`
**Priority:** Highest
**Estimate:** 5 SP

**Scenario:**
1. Configure agent with `multi_action_enabled=True`
2. Mock LLM to return `execute_actions` with 3 `read_file` calls
3. Verify all 3 execute concurrently
4. Verify 3 separate `{"role": "tool"}` messages in memory
5. Verify `on_tool_result()` called 3 times
6. Verify `on_batch_complete()` called once with 3 results  
7. Verify latency improvement vs sequential baseline

**Dependencies:** MA-08-01

---

### MA-10-06: Integration test â€” `ask_user` singleton enforcement

**File:** `dev/tests/integration/test_multi_action_e2e.py`
**Priority:** High
**Estimate:** 2 SP

**Scenario:**
1. Mock LLM returns `execute_actions` with `ask_user` + `read_file`
2. Verify `read_file` is stripped
3. Verify only `ask_user` executes
4. Verify warning logged

**Dependencies:** MA-08-01, MA-04-01

---

## Story: MA-11 â€” Observability & Ops

### MA-11-01: Add multi-action metrics and structured logs

**Priority:** High
**Estimate:** 3 SP

**Metrics to emit:**
- `multi_action.batch_size` â€” number of actions in batch
- `multi_action.parallel_count` â€” how many ran concurrently
- `multi_action.sequential_count` â€” how many ran sequentially
- `multi_action.batch_duration_ms` â€” total batch wall-clock time
- `multi_action.ask_user_strip_count` â€” times ask_user caused stripping
- `multi_action.stuck_batch_count` â€” batch stuck detections

**Structured log fields:**
- `action_ids`, `tool_names`, `batch_size` per batch execution

**Dependencies:** MA-07-01, MA-09-01

---

### MA-11-02: Canary rollout plan

**Priority:** High
**Estimate:** 2 SP

**Rollout phases:**
1. Internal testing with `multi_action_enabled=True` on test agents
2. Canary: enable on one production agent with read-only tool set
3. Broad enablement with monitoring

**Rollback:** Set `multi_action_enabled=False` in agent config â€” instant revert.

**Dependencies:** MA-10-05, MA-11-01

---

## Story: MA-12 â€” Documentation

### MA-12-01: Developer docs for multi-action authoring

**Priority:** Medium
**Estimate:** 2 SP

**Contents:**
- Multi-action JSON schema with examples
- `parallel_safe` annotation guide for new tools
- `ask_user` singleton constraint explanation
- How to override in agent config
- Troubleshooting common failures

**Dependencies:** MA-05-02, MA-04-01

---

## Sprint Plan

### Sprint 1 â€” Foundation (Data Models + Config)
| Task | SP | Description |
|------|----|-------------|
| MA-01-01 | 1 | `BaseTool.parallel_safe` property |
| MA-01-02 | 1 | `ParsedAction.action_id` |
| MA-01-03 | 1 | `AgentDecision.EXECUTE_ACTIONS` |
| MA-01-04 | 2 | `AgentResponse.actions` |
| MA-01-05 | 1 | `ToolResult.action_id` + `tool_name` |
| MA-02-01 | 2 | Feature flags in `AgentConfig` + `tools.json` |
| **Total** | **8** | |

### Sprint 1 Kickoff Package (Ready)

**Sprint goal**
Ship backward-compatible contract/config foundations for multi-action with zero runtime behavior change when disabled.

**Execution order (recommended)**
1. MA-01-01 + MA-01-05 in `agent_cli/core/runtime/tools/base.py` and tool subclasses.
2. MA-01-02 + MA-01-03 + MA-01-04 in `agent_cli/core/runtime/agents/parsers.py`.
3. MA-02-01 in `agent_cli/core/runtime/agents/base.py`, `agent_cli/core/infra/registry/bootstrap.py`, and `agent_cli/data/tools.json`.
4. Sprint 1 tests and compatibility checks.

**Definition of done for Sprint 1**
- New fields/enums exist with defaults that preserve existing single-action behavior.
- No behavioral change in `handle_task()` while `multi_action_enabled=False`.
- `agent_cli/data/tools.json` includes:
  - `executor.multi_action.enabled = false`
  - `executor.multi_action.max_concurrent_actions = 5`
- Updated tests cover new contract fields and config parsing.
- Target regression suite passes.

**Sprint 1 test suite**
- `python -m pytest dev/tests/tools/test_base.py dev/tests/agent/test_schema.py dev/tests/agent/test_react_loop.py dev/tests/core/test_bootstrap.py dev/tests/core/test_orchestrator.py -q`

**Tracking artifact**
- Detailed checklist: `dev/implementation/in_progress/multi_actions_sprint1_prep.md`

### Sprint 2 - Core Runtime
| Task | SP | Description |
|------|----|-------------|
| MA-03-01 | 5 | SchemaValidator `execute_actions` parsing |
| MA-03-02 | 3 | Native FC multi-tool parsing |
| MA-04-01 | 5 | `MultiActionValidator` |
| MA-06-01 | 5 | `ToolExecutor` â†’ return `ToolResult` |
| MA-06-02 | 2 | `ToolOutputFormatter` envelope `action_id` |
| **Total** | **20** | |

### Sprint 3 â€” Orchestration + Loop
| Task | SP | Description |
|------|----|-------------|
| MA-05-01 | 2 | `PromptBuilder.build()` multi-action flag |
| MA-05-02 | 3 | Multi-action prompt templates |
| MA-07-01 | 8 | `BatchExecutor` |
| MA-08-01 | 8 | `handle_task()` EXECUTE_ACTIONS dispatch |
| MA-08-02 | 2 | `on_batch_complete()` hook |
| MA-09-01 | 5 | StuckDetector batch detection |
| **Total** | **28** | |

### Sprint 4 â€” Testing + Polish
| Task | SP | Description |
|------|----|-------------|
| MA-03-03 | 2 | Format-repair pass |
| MA-08-03 | 2 | Session history multi-action format |
| MA-08-04 | 1 | Schema recovery message |
| MA-10-01 | 3 | Validator tests |
| MA-10-02 | 3 | Schema parsing tests |
| MA-10-03 | 5 | BatchExecutor tests |
| MA-10-04 | 3 | StuckDetector batch tests |
| MA-10-05 | 5 | E2E parallel read test |
| MA-10-06 | 2 | E2E ask_user test |
| **Total** | **26** | |

### Sprint 5 â€” Release
| Task | SP | Description |
|------|----|-------------|
| MA-11-01 | 3 | Metrics + logs |
| MA-11-02 | 2 | Canary rollout |
| MA-12-01 | 2 | Developer docs |
| **Total** | **7** | |

---

## Dependency Graph

```
MA-01-01 (BaseTool.parallel_safe)
MA-01-02 (ParsedAction.action_id)          â”€â”€â”
MA-01-03 (AgentDecision.EXECUTE_ACTIONS)     â”‚
MA-01-04 (AgentResponse.actions) â—„â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
MA-01-05 (ToolResult fields) â—„â”€â”€ MA-01-02
                â”‚
    â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
    â–¼           â–¼           â–¼
MA-02-01    MA-03-01     MA-06-01
(config)    (JSON parse) (ToolExecutorâ†’ToolResult)
    â”‚           â”‚           â”‚
    â–¼           â–¼           â–¼
MA-03-02    MA-04-01     MA-06-02
(native FC) (validator)  (envelope)
    â”‚           â”‚
    â–¼           â–¼
MA-05-01    MA-07-01 (BatchExecutor) â—„â”€â”€ MA-06-01, MA-01-01
(prompt)        â”‚
    â–¼           â–¼
MA-05-02    MA-08-01 (handle_task) â—„â”€â”€ MA-07-01, MA-04-01, MA-09-01
(templates)     â”‚
                â”œâ”€â”€ MA-08-02 (on_batch_complete)
                â”œâ”€â”€ MA-08-03 (session history)
                â–¼
            MA-10-* (tests)
                â–¼
            MA-11-* (observability) â†’ MA-11-02 (rollout)
                â–¼
            MA-12-01 (docs)
```

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Concurrency overload on filesystem tools | MA-07-01 semaphore cap + MA-11-01 monitoring |
| LLM emits non-compliant multi-action payload | MA-03-03 repair + strict validation |
| Result/action misattribution in async | MA-06-02 `action_id` in envelope + result ordering |
| StuckDetector misses batch loops | MA-09-01 batch fingerprinting + MA-10-04 tests |
| Prompt token inflation from multiple results | Existing truncation via `ToolOutputFormatter` (per-result) |
| User approval flow with parallel unsafe tools | Sequential fallback: unsafe tools always run in order with individual approval |

---

## Definition of Done (Epic-Level)

- [ ] Feature flag `multi_action_enabled` default-off merged to main
- [ ] All MA-10 tests passing in CI
- [ ] No single-action regressions in existing test suites
- [ ] Canary rollout on read-only agent with stable latency/error metrics
- [ ] Documentation and developer guide published


