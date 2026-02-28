# Agent CLI — Phase 3 Completion Summary (Handoff)

This document summarizes the work completed in **Phase 3 — Agent Core Logic** and serves as the context transfer for **Phase 4 — TUI & Interactive Experience**.

---

## 🚀 Phase 3 Objectives Achieved

The goal was to build the agent's reasoning engine: the ReAct loop (Think → Act → Observe), the tool execution system, schema verification, and the orchestrator. **After this phase, the agent can autonomously reason, call tools, and produce answers.**

---

## Sub-Phase 3.1 — Tool System ✅

Built the entire tool subsystem from scratch — tools, registry, executor, and safety.

### New Package: `agent_cli/tools/`

| File | Key Classes | Purpose |
|---|---|---|
| [`base.py`](file:///x:/agent_cli/agent_cli/tools/base.py) | `BaseTool` ABC, `ToolCategory`, `ToolResult` | Abstract tool interface with Pydantic argument schemas and auto JSON Schema generation |
| [`registry.py`](file:///x:/agent_cli/agent_cli/tools/registry.py) | `ToolRegistry` | Central catalog: register, lookup, filter by category/agent, generate LLM definitions |
| [`executor.py`](file:///x:/agent_cli/agent_cli/tools/executor.py) | `ToolExecutor` | Single gateway: validation → safety check → event emission → execute → format → return |
| [`output_formatter.py`](file:///x:/agent_cli/agent_cli/tools/output_formatter.py) | `ToolOutputFormatter` | Head+tail truncation strategy, consistent `[Tool: name] Result:` prefix |
| [`workspace.py`](file:///x:/agent_cli/agent_cli/tools/workspace.py) | `WorkspaceContext` | Minimal path jailing (prevents path traversal outside workspace root) |
| [`file_tools.py`](file:///x:/agent_cli/agent_cli/tools/file_tools.py) | `ReadFileTool`, `WriteFileTool`, `ListDirectoryTool`, `SearchFilesTool` | Core file operations with line-range slicing, recursive listing, grep-like search |
| [`shell_tool.py`](file:///x:/agent_cli/agent_cli/tools/shell_tool.py) | `RunCommandTool`, `is_safe_command()` | Shell execution with timeout, dynamic regex for safe command auto-approval |

### Design Decisions
- **Path Jailing:** Minimal `WorkspaceContext` for Phase 3; full `BaseWorkspaceManager` sandbox deferred to Phase 5.
- **HITL Simplification:** `ToolExecutor` emits `UserApprovalRequestEvent` and awaits `UserApprovalResponseEvent` via event bus. Auto-approve flag for testing. Full TUI approval modal is Phase 4.
- **Safety:** `is_safe_command()` uses dynamic regex patterns. Commands like `ls`, `cat`, `echo`, `git status` are auto-approved. Destructive commands (`rm`, `git push`, `docker`) require user approval.

---

## Sub-Phase 3.2 — Schema Verification ✅

Dual-mode LLM response validation — handles both native function calling and XML prompting.

### New Package: `agent_cli/agent/`

| File | Key Classes | Purpose |
|---|---|---|
| [`parsers.py`](file:///x:/agent_cli/agent_cli/agent/parsers.py) | `ParsedAction`, `AgentResponse` | Unified output dataclasses — the agent loop only sees these |
| [`schema.py`](file:///x:/agent_cli/agent_cli/agent/schema.py) | `BaseSchemaValidator` ABC, `SchemaValidator` | Dual-mode validation (native FC + XML), `<thinking>` extraction, JSON auto-repair |

### Design Decisions
- **Dual-mode dispatch:** `parse_and_validate()` automatically routes based on `LLMResponse.tool_mode` (NATIVE vs XML).
- **`<thinking>` extraction:** Consistent across both modes; supports multiple `<thinking>` blocks (concatenated).
- **JSON auto-repair:** 3 coercion strategies — single→double quotes, trailing comma removal, combined fix.
- **`<final_answer>` detection:** Explicit tags preferred; implicit clean-text fallback when no tags found.
- **Error feedback loop:** Raises `SchemaValidationError` → agent loop catches it → re-prompts the LLM with correction instructions.

---

## Sub-Phase 3.3 — Reasoning Loop (ReAct) ✅

The core agent loop: Think → Act → Observe → repeat until done or exhausted.

| File | Key Classes | Purpose |
|---|---|---|
| [`base.py`](file:///x:/agent_cli/agent_cli/agent/base.py) | `BaseAgent` ABC, `AgentConfig`, `EffortLevel`, `EFFORT_CONSTRAINTS` | Full ReAct loop in `handle_task()` with 3 abstract hooks |
| [`react_loop.py`](file:///x:/agent_cli/agent_cli/agent/react_loop.py) | `StuckDetector`, `PromptBuilder` | Loop-repetition detection + dynamic prompt assembly |
| [`memory.py`](file:///x:/agent_cli/agent_cli/agent/memory.py) | `BaseMemoryManager` ABC, `WorkingMemoryManager` | Sliding-window working memory with compaction |

### Design Decisions
- **ReAct loop (`handle_task()`):** `for iteration in range(1, max+1):` → Generate → Stream thinking → Validate → Execute tool **or** return final answer.
- **3 abstract hooks:** `build_system_prompt()`, `on_tool_result()`, `on_final_answer()` — concrete agents customize without re-implementing the loop.
- **Effort levels:** `LOW` (5 iters, fast model), `MEDIUM` (15 iters, capable), `HIGH` (30 iters, premium + self-verify).
- **Error recovery:**
  - `ContextLengthExceededError` → compact memory (drop oldest, keep system + recent N).
  - `SchemaValidationError` → re-prompt with feedback (max 3 consecutive before failing).
  - `ToolExecutionError` → feed error back as tool observation.
  - `FATAL` errors → propagate to Orchestrator.
- **Stuck detection:** Hashes `(tool_name, result)` — if last 3 identical → inject nudge message.
- **Memory compaction:** Simple truncation (drop oldest middle messages). LLM-based summarization deferred to Phase 5.

---

## Sub-Phase 3.4 — Orchestrator + DI ✅

The bridge between user requests and agents.

| File | Key Classes / Changes | Purpose |
|---|---|---|
| [`orchestrator.py`](file:///x:/agent_cli/agent_cli/core/orchestrator.py) | `Orchestrator` (new) | Subscribes to `UserRequestEvent`, routes to agent, manages task lifecycle |
| [`bootstrap.py`](file:///x:/agent_cli/agent_cli/core/bootstrap.py) | `AppContext` (modified), `create_app()` (modified), `register_default_agent()`, `_build_tool_registry()` | DI container now includes all Phase 3 components |

### Design Decisions
- **Single-agent routing:** Default agent handles all requests in Phase 3. Multi-agent LLM-based routing deferred to Phase 6.
- **Slash-command interception:** `Orchestrator.register_command()` + `/` prefix check → early return without invoking agent.
- **Task lifecycle:** `create_task → PENDING → ROUTING → WORKING → SUCCESS/FAILED` with `TaskDelegatedEvent` + `TaskResultEvent` emission.
- **Error shielding:** `_safe_transition_to_failed()` catches transition errors to avoid masking the original error.
- **`register_default_agent()`:** Separate from `create_app()` because the agent requires `AppContext` components in its constructor (chicken-and-egg solved by two-phase init).

---

## 📊 Test Summary

| Test File | Tests | Covers |
|---|---|---|
| [`tests/tools/test_base.py`](file:///x:/agent_cli/tests/tools/test_base.py) | 5 | `BaseTool`, `ToolResult`, Pydantic schema generation |
| [`tests/tools/test_registry.py`](file:///x:/agent_cli/tests/tools/test_registry.py) | 6 | `ToolRegistry`, `ToolOutputFormatter` |
| [`tests/tools/test_executor.py`](file:///x:/agent_cli/tests/tools/test_executor.py) | 8 | `ToolExecutor` safety, events, approval flow |
| [`tests/tools/test_file_tools.py`](file:///x:/agent_cli/tests/tools/test_file_tools.py) | 5 | File tools + `WorkspaceContext` path jailing |
| [`tests/tools/test_shell_tool.py`](file:///x:/agent_cli/tests/tools/test_shell_tool.py) | 4 | `RunCommandTool`, `is_safe_command()` |
| [`tests/agent/test_schema.py`](file:///x:/agent_cli/tests/agent/test_schema.py) | 15 | `SchemaValidator` dual-mode, coercion, edge cases |
| [`tests/agent/test_react_loop.py`](file:///x:/agent_cli/tests/agent/test_react_loop.py) | 4 | Full ReAct integration: think → tool → answer |
| [`tests/core/test_orchestrator.py`](file:///x:/agent_cli/tests/core/test_orchestrator.py) | 6 | Orchestrator routing, lifecycle, commands, events |

**Total Phase 3 tests added:** 53
**Total project tests:** 131 ✅ all passing

---

## 🏗️ Updated `AppContext` (DI Container)

```python
@dataclass
class AppContext:
    # Phase 1 Core
    settings: AgentSettings
    event_bus: AbstractEventBus
    state_manager: AbstractStateManager

    # Phase 2 Providers
    providers: ProviderManager

    # Phase 3 Agent Core (NEW)
    tool_registry: ToolRegistry
    tool_executor: ToolExecutor
    schema_validator: BaseSchemaValidator
    memory_manager: BaseMemoryManager
    prompt_builder: PromptBuilder
    orchestrator: Optional[Orchestrator]  # None until agent registered
```

---

## 📁 New File Tree (Phase 3)

```
agent_cli/
├── agent/                          # NEW package (6 files)
│   ├── __init__.py
│   ├── base.py                     # BaseAgent ABC, AgentConfig, EffortLevel
│   ├── memory.py                   # BaseMemoryManager ABC + WorkingMemoryManager
│   ├── parsers.py                  # ParsedAction, AgentResponse
│   ├── react_loop.py               # StuckDetector, PromptBuilder
│   └── schema.py                   # BaseSchemaValidator ABC + SchemaValidator
├── tools/                          # NEW package (8 files)
│   ├── __init__.py
│   ├── base.py                     # BaseTool ABC, ToolCategory, ToolResult
│   ├── executor.py                 # ToolExecutor
│   ├── file_tools.py               # ReadFile, WriteFile, ListDir, SearchFiles
│   ├── output_formatter.py         # ToolOutputFormatter
│   ├── registry.py                 # ToolRegistry
│   ├── shell_tool.py               # RunCommandTool
│   └── workspace.py                # WorkspaceContext
├── core/
│   ├── bootstrap.py                # MODIFIED — Phase 3 wiring
│   └── orchestrator.py             # NEW — Orchestrator class
tests/
├── agent/                          # NEW (3 files)
│   ├── __init__.py
│   ├── test_react_loop.py          # Integration: mock provider + tools + full loop
│   └── test_schema.py              # Schema validator tests
├── tools/                          # NEW (6 files)
│   ├── __init__.py
│   ├── test_base.py
│   ├── test_executor.py
│   ├── test_file_tools.py
│   ├── test_registry.py
│   └── test_shell_tool.py
└── core/
    └── test_orchestrator.py        # NEW — Orchestrator tests
```

---

## ⏭️ Ready for Phase 4: TUI & Interactive Experience

Phase 4 connects the backend (built in Phases 1–3) to the existing TUI shell (partially built). Key integration points:

### What Phase 4 Needs from Phase 3

| Phase 3 Component | Phase 4 Usage |
|---|---|
| `AgentMessageEvent(is_monologue=True)` | → `ThinkingBlock` widget (4.1.2) |
| `ToolExecutionStartEvent` / `ToolExecutionResultEvent` | → `ToolStepWidget` spinner (4.1.3) |
| `AgentMessageEvent(is_monologue=False)` | → `AnswerBlock` Markdown widget (4.1.4) |
| `UserApprovalRequestEvent` / `UserApprovalResponseEvent` | → `ApprovalModal` (4.3.1) |
| `Orchestrator.register_command()` | → Wire `/mode`, `/model`, `/help`, `/exit` (4.2.4) |
| `Orchestrator._on_user_request()` subscription | → TUI publishes `UserRequestEvent` on submit |
| `TaskDelegatedEvent` / `TaskResultEvent` | → Status bar updates, task progress |

### Phase 4 Sub-Phases

1. **4.1 Response Visualization** — `ThinkingBlock`, `ToolStepWidget`, `AnswerBlock`, `ErrorPopup`
2. **4.2 Command System** — `@command` decorator, `CommandParser`, core handlers (`/mode`, `/model`, etc.)
3. **4.3 Human-in-the-Loop** — `ApprovalModal`, file change approval, shell command approval
4. **4.4 Changed Files Panel** — `FileChangeTracker`, `ChangedFilesWidget`
5. **4.5 Terminal Viewer** — Persistent terminal output widget

### Key Specs

- [05_response_visualization.md](file:///x:/agent_cli/architect-workspace/03_user_interface/05_response_visualization.md)
- [03_command_system.md](file:///x:/agent_cli/architect-workspace/03_user_interface/03_command_system.md)
- [01_human_in_loop.md](file:///x:/agent_cli/architect-workspace/03_user_interface/01_human_in_loop.md)
- [04_changed_files.md](file:///x:/agent_cli/architect-workspace/03_user_interface/04_changed_files.md)
- [02_terminal_viewer.md](file:///x:/agent_cli/architect-workspace/03_user_interface/02_terminal_viewer.md)
