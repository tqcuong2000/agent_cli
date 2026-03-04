# Phase 3 — Agent Core Logic

## Goal
Implement the agent's reasoning engine: the ReAct loop (Think → Act → Observe), the tool execution system, and schema verification. After this phase, the agent can autonomously reason, call tools, and produce answers.

**Specs:** `01_reasoning_loop.md`, `02_schema_verification.md`, `03_tools_architecture.md`
**Depends on:** Phase 1 (Event Bus, State), Phase 2 (AI Providers)

---

## Sub-Phase 3.1 — Tool System
> Spec: `01_agent_logic/03_tools_architecture.md`

Build tools first — the agent needs tools to act.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 3.1.1 | `BaseTool` ABC | Define `name`, `description`, `parameters_schema`, `execute()` interface | 🔴 Critical |
| 3.1.2 | `ToolRegistry` | Register, lookup, and list available tools | 🔴 Critical |
| 3.1.3 | `ToolExecutor` | Centralized tool runner with path enforcement, timeout, error wrapping | 🔴 Critical |
| 3.1.4 | Core file tools | `read_file`, `write_file`, `list_directory`, `search_files` | 🔴 Critical |
| 3.1.5 | Shell tool | `run_command` with approval gate (emits HITL event) | 🟡 Medium |
| 3.1.6 | Tool schema generation | Auto-generate JSON Schema / XML from `BaseTool.parameters_schema` | 🔴 Critical |
| 3.1.7 | Tool result model | `ToolResult` with `success`, `output`, `error`, `metadata` | 🔴 Critical |
| 3.1.8 | Unit tests | Test each tool in isolation, test registry lookup | 🔴 Critical |

**Deliverable:** `agent_cli/tools/base.py`, `agent_cli/tools/registry.py`, `agent_cli/tools/executor.py`, `agent_cli/tools/file_tools.py`, `agent_cli/tools/shell_tool.py`

---

## Sub-Phase 3.2 — Schema Verification
> Spec: `01_agent_logic/02_schema_verification.md`

Parse and validate LLM responses — extract tool calls, detect malformed JSON, handle `<thinking>` tags.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 3.2.1 | `SchemaValidator` | Validate tool_call arguments against tool's parameter schema | 🔴 Critical |
| 3.2.2 | Native FC parser | Parse OpenAI/Anthropic/Google native tool call responses | 🔴 Critical |
| 3.2.3 | XML fallback parser | Parse `<tool_call>` XML from text responses (non-FC providers) | 🟡 Medium |
| 3.2.4 | `<thinking>` tag extractor | Strip `<thinking>` content from response, route separately | 🟡 Medium |
| 3.2.5 | Auto-repair | Attempt to fix malformed JSON with simple heuristics (trailing comma, missing quote) | 🟢 Low |
| 3.2.6 | Re-prompt on failure | If validation fails, append error context and re-prompt agent | 🟡 Medium |
| 3.2.7 | Tests | Test with malformed, valid, edge-case responses | 🔴 Critical |

**Deliverable:** `agent_cli/agent/schema.py`, `agent_cli/agent/parsers.py`

---

## Sub-Phase 3.3 — Reasoning Loop (ReAct)
> Spec: `01_agent_logic/01_reasoning_loop.md`

The core agent loop: Think → Act → Observe → repeat until done.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 3.3.1 | `BaseAgent` ABC | Define `process_request()`, `build_messages()`, `get_tools()` interface | 🔴 Critical |
| 3.3.2 | ReAct loop | `while not done:` think → parse tool calls → execute → append result → think again | 🔴 Critical |
| 3.3.3 | Max iterations guard | Stop after N iterations, ask user for guidance | 🔴 Critical |
| 3.3.4 | Event emission | Emit `AgentThinkingEvent`, `ToolStartEvent`, `ToolResultEvent`, `AgentMessageEvent` throughout loop | 🔴 Critical |
| 3.3.5 | Fast-path vs Plan mode | Fast: direct execution. Plan: generate plan → user approval → execute steps | 🟡 Medium |
| 3.3.6 | Token budget management | Track input/output tokens, compact history if context window near-full | 🟡 Medium |
| 3.3.7 | Working memory | Sliding window of recent messages + system prompt | 🟡 Medium |
| 3.3.8 | Integration test | End-to-end: user prompt → agent thinks → calls tool → returns answer | 🔴 Critical |

**Deliverable:** `agent_cli/agent/base.py`, `agent_cli/agent/react_loop.py`

---

## Sub-Phase 3.4 — Orchestrator
> Spec: `01_architecture_pattern.md` (§ Orchestrator)

The bridge between user requests and agents.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 3.4.1 | `Orchestrator` class | Receive `UserRequestEvent` → select agent → run reasoning loop → emit result | 🔴 Critical |
| 3.4.2 | Agent selection | Route to default agent (single agent for now, multi-agent in Phase 6) | 🔴 Critical |
| 3.4.3 | Command interception | If input starts with `/`, route to CommandParser instead of agent | 🔴 Critical |
| 3.4.4 | Task lifecycle | Create task → RUNNING → SUCCESS/FAILED → emit events | 🟡 Medium |
| 3.4.5 | Tests | Test routing, command interception, agent execution | 🔴 Critical |

**Deliverable:** `agent_cli/core/orchestrator.py`

---

## Completion Criteria

- [ ] Tools: file read/write/search/list work with path enforcement
- [ ] Schema: tool calls parsed from all provider formats
- [ ] ReAct loop: agent reasons, calls tools, and produces answers
- [ ] Events: full event trail from user request to response
- [ ] Orchestrator: routes requests to agents and commands to parser
- [ ] Integration test: "read file X and summarize it" works end-to-end
