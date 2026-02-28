# Phase 3 — Agent Core Logic: Implementation Plan

> **Goal:** Build the agent's reasoning engine — the ReAct loop (Think → Act → Observe), the tool execution system, schema verification, and the orchestrator. After this phase, the agent can autonomously reason, call tools, and produce answers.

> [!IMPORTANT]
> **Specs:** [01_reasoning_loop.md](file:///x:/agent_cli/architect-workspace/01_agent_logic/01_reasoning_loop.md), [02_schema_verification.md](file:///x:/agent_cli/architect-workspace/01_agent_logic/02_schema_verification.md), [03_tools_architecture.md](file:///x:/agent_cli/architect-workspace/01_agent_logic/03_tools_architecture.md)
> **Depends on:** Phase 1 (Event Bus, State, Config, Errors) ✅, Phase 2 (AI Providers) ✅

---

## Current State Summary

| Component | Location | Status |
|---|---|---|
| Event Bus | `agent_cli/core/events/event_bus.py` | ✅ Stable |
| Events (18 types) | `agent_cli/core/events/events.py` | ✅ Stable |
| State Manager | `agent_cli/core/state/state_manager.py` | ✅ Stable |
| Config | `agent_cli/core/config.py` | ✅ Stable |
| Error Taxonomy | `agent_cli/core/error_handler/errors.py` | ✅ Has `SchemaValidationError`, `ToolExecutionError`, `MaxIterationsExceededError` |
| Provider Models | `agent_cli/providers/models.py` | ✅ Has `LLMResponse`, `ToolCall`, `ToolCallMode` |
| Provider Base | `agent_cli/providers/base.py` | ✅ `BaseLLMProvider` with `generate()`, `stream()`, `safe_generate()` |
| Provider Manager | `agent_cli/providers/manager.py` | ✅ Prefix routing, adapter caching |
| Bootstrap / DI | `agent_cli/core/bootstrap.py` | ✅ `AppContext` + `create_app()` — needs Phase 3 fields |
| Agent Directory | `agent_cli/core/agent/` | ⬜ **Empty** — ready for new files |
| Tests | `tests/` | ✅ 78 tests passing |

---

## Implementation Order

We build **bottom-up** within Phase 3, following the roadmap's sub-phase ordering:

```
Sub-Phase 3.1 (Tool System) ─── Sub-Phase 3.2 (Schema) ─── Sub-Phase 3.3 (ReAct Loop) ─── Sub-Phase 3.4 (Orchestrator)
         ▲                              ▲                           ▲                              ▲
      Tools first                Parse LLM output          Use tools + schema           Bridge user → agent
```

---

## Sub-Phase 3.1 — Tool System

> Build tools first — the agent needs tools to act.

### Files Created

| File | Purpose |
|---|---|
| `agent_cli/tools/__init__.py` | Package init |
| `agent_cli/tools/base.py` | `BaseTool` ABC, `ToolCategory` enum, `ToolResult` model |
| `agent_cli/tools/registry.py` | `ToolRegistry` — register, lookup, list, schema gen |
| `agent_cli/tools/executor.py` | `ToolExecutor` — safety checks, output formatting, event emission |
| `agent_cli/tools/output_formatter.py` | `ToolOutputFormatter` — truncation, consistent formatting |
| `agent_cli/tools/file_tools.py` | `ReadFileTool`, `WriteFileTool`, `ListDirectoryTool`, `SearchFilesTool` |
| `agent_cli/tools/shell_tool.py` | `RunCommandTool` with approval gate |
| `agent_cli/tools/workspace.py` | Minimal workspace context implementation |
| `tests/tools/test_base.py` | `BaseTool` and `ToolResult` tests |
| `tests/tools/test_registry.py` | `ToolRegistry` tests |
| `tests/tools/test_executor.py` | `ToolExecutor` tests (safety, formatting) |
| `tests/tools/test_file_tools.py` | File tool unit tests |
| `tests/tools/test_shell_tool.py` | Shell tool unit tests |

### Task Breakdown

| # | Task | Description | Key Decisions |
|---|---|---|---|
| 3.1.1 | **`BaseTool` ABC** | `name`, `description`, `args_schema` (Pydantic), `is_safe`, `category`, `execute()`, `validate_args()`, `get_json_schema()` | ✅ Pydantic for arg validation; auto JSON Schema generation for native FC compatibility |
| 3.1.2 | **`ToolResult` model** | Dataclass with `success: bool`, `output: str`, `error: str`, `metadata: dict` | ✅ Simple dataclass, not Pydantic — internal data, not API-facing |
| 3.1.3 | **`ToolCategory` enum** | `FILE`, `SEARCH`, `EXECUTION`, `TERMINAL`, `UTILITY` | ✅ Grouping for agent tool filtering |
| 3.1.4 | **`ToolRegistry`** | `register()`, `get()`, `get_by_category()`, `get_for_agent()`, `get_all_names()`, `get_definitions_for_llm()` | ✅ Central catalog; raises on duplicate names; agents receive filtered subsets |
| 3.1.5 | **`ToolOutputFormatter`** | `format(tool_name, raw_output, success)` — truncate with head+tail, prefix with tool name | ✅ Max 5000 chars default (configurable); head+tail truncation preserves context |
| 3.1.6 | **`ToolExecutor`** | Validation → Safety check → Event emission → Execute → Format → Return | ✅ Wraps `BaseTool.execute()` — agent never calls tools directly |
| 3.1.7 | **Core file tools** | `read_file`, `write_file`, `list_directory`, `search_files` (grep-like) | ✅ Workspace path jailing (basic `Path.resolve()` check for now) |
| 3.1.8 | **Shell tool** | `run_command` with timeout, approval gate via event | ✅ Dynamic regex for safe commands (`ls`, `cat`, `echo`, etc.) |
| 3.1.9 | **Unit tests** | Registry, executor, each tool in isolation | ✅ Use `tmp_path` fixtures for file tools |

---

## Sub-Phase 3.2 — Schema Verification

> Parse and validate LLM responses — extract tool calls, detect malformed JSON, handle `<thinking>` tags.

### Files Created

| File | Purpose |
|---|---|
| `agent_cli/agent/schema.py` | `BaseSchemaValidator` ABC + `SchemaValidator` implementation |
| `agent_cli/agent/parsers.py` | `ParsedAction`, `AgentResponse` dataclasses |
| `tests/agent/test_schema.py` | Schema validation tests (native FC, XML, coercion, edge cases) |

### Task Breakdown

| # | Task | Description | Key Decisions |
|---|---|---|---|
| 3.2.1 | **Data models** | `ParsedAction(tool_name, arguments, native_call_id)`, `AgentResponse(thought, action, final_answer)` | ✅ Dataclasses, not Pydantic — simple internal structs |
| 3.2.2 | **`BaseSchemaValidator` ABC** | `parse_and_validate(response) → AgentResponse`, `extract_thinking(text) → str` | ✅ Abstract contract for testability |
| 3.2.3 | **Native FC parser** | Validate structured `ToolCall` objects from `LLMResponse.tool_calls` | ✅ Nearly trivial — API already enforced schema; just validate tool name exists |
| 3.2.4 | **XML fallback parser** | Parse `<action><tool>name</tool><args>{...}</args></action>` from text | ✅ Regex-based; handles most common LLM formatting issues |
| 3.2.5 | **`<thinking>` extractor** | `re.search` for `<thinking>...</thinking>` content | ✅ Consistent across both modes |
| 3.2.6 | **JSON auto-repair** | Fix trailing commas, single quotes → double quotes | ✅ Best-effort; `_attempt_json_coercion()` |
| 3.2.7 | **Re-prompt on failure** | Raises `SchemaValidationError` — the agent loop catches it and appends feedback | ✅ Error already defined in Phase 1 |
| 3.2.8 | **Tests** | Valid native FC, valid XML, malformed XML, coercion, edge cases | ✅ Include multi-`<thinking>` block tests, missing tags, etc. |

---

## Sub-Phase 3.3 — Reasoning Loop (ReAct)

> The core agent loop: Think → Act → Observe → repeat until done.

### Files Created

| File | Purpose |
|---|---|
| `agent_cli/agent/__init__.py` | Package init |
| `agent_cli/agent/base.py` | `BaseAgent` ABC, `AgentConfig`, `EffortLevel`, `EFFORT_CONSTRAINTS` |
| `agent_cli/agent/react_loop.py` | `StuckDetector`, `PromptBuilder` |
| `agent_cli/agent/memory.py` | `BaseMemoryManager` ABC + `WorkingMemoryManager` (sliding window) |
| `tests/agent/test_react_loop.py` | ReAct loop tests (mock provider, mock tools) |

### Task Breakdown

| # | Task | Description | Key Decisions |
|---|---|---|---|
| 3.3.1 | **`EffortLevel` + `AgentConfig`** | Enum (`LOW`/`MEDIUM`/`HIGH`) + dataclass with name, persona, model, tools, effort, etc. | ✅ `EFFORT_CONSTRAINTS` dict maps effort → max_iterations, model_tier, reasoning_instruction |
| 3.3.2 | **`BaseAgent` ABC** | `handle_task()` (the ReAct loop), `build_system_prompt()`, `on_tool_result()`, `on_final_answer()` | ✅ Constructor takes: config, provider, tool_executor, schema_validator, memory_manager, event_bus, state_manager |
| 3.3.3 | **ReAct loop** | `for iteration in range(1, max_iterations+1):` → generate → extract thinking → validate → execute tool or return answer | ✅ Integrated error handling: `ContextLengthExceededError` → compact; `SchemaValidationError` → re-prompt; `ToolExecutionError` → feed back |
| 3.3.4 | **Max iterations guard** | Raises `MaxIterationsExceededError` when exhausted | ✅ Already defined in Phase 1 errors |
| 3.3.5 | **Event emission** | Emit `AgentMessageEvent` for thinking + final answer; tool events via `ToolExecutor` | ✅ TUI subscribes for real-time updates |
| 3.3.6 | **`StuckDetector`** | Track last N tool calls; if same tool+result 3 times → inject hint | ✅ Reset after detection; keep last 10 entries |
| 3.3.7 | **`PromptBuilder`** | Assemble system prompt from persona + output format + tool descriptions + effort instructions | ✅ Modular sections; reusable across agents |
| 3.3.8 | **`BaseMemoryManager` + `WorkingMemoryManager`** | `reset_working()`, `add_working_event()`, `get_working_context()`, `summarize_and_compact()` | ✅ Sliding window; compact = summarize oldest messages |
| 3.3.9 | **Integration tests** | Mock provider → agent thinks → calls tool → returns answer | ✅ Full loop with mock `BaseLLMProvider` and mock tools |

---

## Sub-Phase 3.4 — Orchestrator

> The bridge between user requests and agents.

### Files Created

| File | Purpose |
|---|---|
| `agent_cli/core/orchestrator.py` | `Orchestrator` class — routes requests to agents, intercepts commands |
| `tests/core/test_orchestrator.py` | Orchestrator routing, command interception, agent execution tests |

### Task Breakdown

| # | Task | Description | Key Decisions |
|---|---|---|---|
| 3.4.1 | **`Orchestrator` class** | Subscribe to `UserRequestEvent` → select agent → run `handle_task()` → emit `TaskResultEvent` | ✅ Single agent for now (default to `coder`); multi-agent routing in Phase 6 |
| 3.4.2 | **Agent selection** | Route to a default agent | ✅ Configurable via `AgentSettings.default_agent` |
| 3.4.3 | **Command interception** | If input starts with `/` → route to `CommandParser` (stub for Phase 4) | ✅ Simple prefix check; return early without invoking agent |
| 3.4.4 | **Task lifecycle** | Create task → `PENDING` → `WORKING` → `SUCCESS`/`FAILED` via State Manager | ✅ Emit `TaskDelegatedEvent` on assignment |
| 3.4.5 | **DI integration** | Add `Orchestrator` to `AppContext` and wire in `create_app()` | ✅ Update `bootstrap.py` |
| 3.4.6 | **Tests** | Routing, command interception, task lifecycle | ✅ Mock agent and event bus |

---

## Completion Criteria

- [x] Tools: `read_file`, `write_file`, `list_directory`, `search_files`, `run_command` work with path enforcement
- [x] Tool Registry: register, lookup, schema generation functional
- [x] Tool Executor: safety checks, output formatting, event emission
- [x] Schema Validator: parses native FC + XML tool calls, extracts `<thinking>`, handles coercion
- [x] ReAct loop: agent reasons → calls tools → produces answers
- [x] Events: full event trail from `UserRequestEvent` → agent processing → `TaskResultEvent`
- [x] Orchestrator: routes user requests to agents, intercepts `/commands`
- [x] Memory: working memory with sliding window and compaction
- [x] DI: all Phase 3 components wired into `AppContext`
- [x] All new tests pass; all existing tests still pass
