# Agent CLI — Project Roadmap

## Overview

This roadmap organizes the Agent CLI project into **6 progressive phases**, each building on the previous one. The architecture follows a bottom-up approach: foundational infrastructure first, then agent logic, then the user-facing TUI.

| Phase | Title | Goal | Status |
|-------|-------|------|--------|
| [Phase 1](./phase_1_foundation.md) | **Foundation & Infrastructure** | Event Bus, State Manager, Config — the skeleton | ✅ Done |
| [Phase 2](./phase_2_ai_integration.md) | **AI Provider Integration** | LLM connectivity, streaming, tool calling | ✅ Done |
| [Phase 3](./phase_3_agent_core.md) | **Agent Core Logic** | Reasoning loop, tools, schema verification | ✅ Done |
| [Phase 4](./phase_4_tui_interactive.md) | **TUI & Interactive Experience** | Response visualization, commands, HITL | ⬜ Next |
| [Phase 5](./phase_5_data_persistence.md) | **Data Management & Persistence** | Sessions, memory, workspace security | ⬜ |
| [Phase 6](./phase_6_polish.md) | **Polish, Multi-Agent & Observability** | Multi-agent orchestration, logging, hardening | ⬜ |

---

## Dependency Graph

```
Phase 1 ─────────────┬──── Phase 2 ──── Phase 3 ──── Phase 6
Foundation            │     AI Provider   Agent Core    Polish
                      │
                      └──── Phase 4 ──── Phase 5
                            TUI           Data Mgmt
```

Phases 2 and 4 can run **in parallel** after Phase 1.
Phase 3 requires Phase 2. Phase 5 requires Phase 4.
Phase 6 requires all prior phases.

---

## Current State (What's Already Built)

### Phase 1 — Foundation & Infrastructure ✅
- ✅ **Event Bus** — `AbstractEventBus` + `AsyncEventBus` with dual dispatch (publish/emit), priority routing, error isolation, graceful drain
- ✅ **Event Catalogue** — `BaseEvent` + 18 typed event subclasses across 8 domains
- ✅ **State Manager** — `AbstractStateManager` + `TaskStateManager` with FSM validation, per-task locking, parent-child hierarchy
- ✅ **Configuration** — `AgentSettings` with tri-layer TOML merge (global → local → env), Pydantic validation, provider registry, secrets management
- ✅ **Error Handling** — Three-tier taxonomy (transient/recoverable/fatal), retry engine with exponential backoff, recovery strategies
- ✅ **DI Bootstrap** — `create_app()` factory, `AppContext` component registry, startup/shutdown lifecycle
- ✅ **78 tests passing** across all phases

### Phase 2 — AI Provider Integration ✅
- ✅ **Adapters** — `OpenAIProvider`, `AnthropicProvider`, `GoogleProvider` (unified genai), `OpenAICompatibleProvider` (Ollama/vLLM)
- ✅ **Streaming** — text chunk yielding with tool call buffering and finalization assembly
- ✅ **Cost Tracking** — Real-time cost estimation using 2026 pricing tables and token usage detection
- ✅ **Provider Manager** — prefix-based model routing, adapter caching, and DI integration
- ✅ **Tool Formatting** — Native function calling + `XMLToolFormatter` for prompt-injected fallbacks

### Phase 3 — Agent Core Logic ✅
- ✅ **Tool System** — `BaseTool` ABC, `ToolRegistry`, `ToolExecutor` with safety checks, `ToolOutputFormatter`, `WorkspaceContext` path jailing
- ✅ **File Tools** — `ReadFileTool`, `WriteFileTool`, `ListDirectoryTool`, `SearchFilesTool` with workspace enforcement
- ✅ **Shell Tool** — `RunCommandTool` with timeout, dynamic safe-command regex auto-approval
- ✅ **Schema Verification** — `SchemaValidator` with dual-mode (native FC + XML), `<thinking>` extraction, JSON auto-repair
- ✅ **Reasoning Loop** — `BaseAgent` ABC with full ReAct loop, 3 effort levels, stuck detection, memory compaction
- ✅ **Orchestrator** — `Orchestrator` class with `UserRequestEvent` subscription, task lifecycle management, `/command` interception
- ✅ **DI Integration** — `AppContext` updated with all Phase 3 components + `register_default_agent()` helper
- ✅ **131 tests passing** across all phases

### TUI Shell (Partial)
- ✅ App shell (`app.py`) with Header/Body/Footer layout
- ✅ User input with multi-line support
- ✅ Header: title, terminal icon, agent badge
- ✅ Footer: input bar, submit button, status bar
- ✅ Body: text window + side panel (context container)
- ✅ User message bubble
- ✅ Popup system: BasePopupListView, CommandPopup, FileDiscoveryPopup

### Not Yet Built
- ❌ Session persistence
- ❌ Full workspace sandbox
- ❌ Response visualization (ThinkingBlock, ToolStepWidget, AnswerBlock)
- ❌ Human-in-the-loop modals
- ❌ Command execution logic (wired to handlers)
- ❌ Multi-agent routing

---

## Spec Coverage Map

| Spec File | Phase |
|---|---|
| `00_event_bus.md` | Phase 1 |
| `01_architecture_pattern.md` | Phase 1 |
| `02_state_management.md` | Phase 1 |
| `02_config_management.md` | Phase 1 |
| `04_error_handling.md` | Phase 1 |
| `01_ai_providers.md` | Phase 2 |
| `01_reasoning_loop.md` | Phase 3 |
| `02_schema_verification.md` | Phase 3 |
| `03_tools_architecture.md` | Phase 3 |
| `05_response_visualization.md` | Phase 4 |
| `03_command_system.md` | Phase 4 |
| `01_human_in_loop.md` | Phase 4 |
| `04_changed_files.md` | Phase 4 |
| `02_terminal_viewer.md` | Phase 4 |
| `01_memory_management.md` | Phase 5 |
| `04_session_persistence.md` | Phase 5 |
| `03_workspace_sandbox.md` | Phase 5 |
| `02_file_discovery.md` | Phase 5 |
| `04_multi_agent_definitions.md` | Phase 6 |
| `03_task_planning.md` | Phase 6 |
| `03_observability.md` | Phase 6 |
