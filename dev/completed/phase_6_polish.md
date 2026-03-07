# Phase 6 — Multi-Agent System, Observability & Hardening

## Goal
Add a user-driven multi-agent system, observability/logging, and overall hardening. This is the "production-ready" phase.

**Spec:** `01_agent_logic/04_multi_agent_definitions.md` (rewritten), `04_utilities/03_observability.md`
**Depends on:** All prior phases (including Phase 7 — Data-Driven System)
**Supersedes:** Old routing-LLM / PLAN mode architecture (archived to `architect-workspace/old/`)

> [!IMPORTANT]
> Phase 7 (Data-Driven System) has already been implemented. All hard-coded values now live in `agent_cli/data/*.toml` and prompt templates in `agent_cli/data/prompts/*.txt`, accessed via `DataRegistry`. This phase should use `DataRegistry` for any new configurable values.
>
> The old LLM-routing and FAST/PLAN mode architecture has been removed. The new multi-agent design is fully user-driven via `!mention` tags and `/agent` commands.

---

## Sub-Phase 6.1 — Multi-Agent System (User-Driven)
> Spec: `01_agent_logic/04_multi_agent_definitions.md`

User-controlled agent management with `!mention` switching and `/agent` commands.

| # | Task | Description | Reuses | Priority |
|---|------|-------------|--------|----------|
| 6.1.1 | `AgentRegistry` (global) | Register and lookup all available agent implementations by name | — | 🔴 Critical |
| 6.1.2 | `SessionAgentRegistry` | Track agents in the current session with `Active/Idle/Inactive` status management | — | 🔴 Critical |
| 6.1.3 | Agent definitions | Define built-in agents: `default`, `coder`, `researcher` — each with a persona loaded from `DataRegistry.get_prompt_template()` | `DefaultAgent`, `BaseAgent`, `AgentConfig` | 🔴 Critical |
| 6.1.4 | Agent persona templates | Add `prompts/coder_persona.txt`, `prompts/researcher_persona.txt` to `agent_cli/data/prompts/` | `DataRegistry.get_prompt_template()` | 🔴 Critical |
| 6.1.5 | `!mention` tag parser | Parse `!agent_name` from start of user input for inline agent switching | — | 🔴 Critical |
| 6.1.6 | `/agent` command | Implement `/agent [list\|add\|remove\|enable\|disable\|default]` subcommands | `@command` decorator, `CommandRegistry`, `CommandParser` | 🔴 Critical |
| 6.1.7 | Session summary on switch | Generate LLM summary of conversation when switching agents (context handoff) | **`SummarizingMemoryManager._summarize_middle_messages()`** + `_heuristic_summary()` fallback | 🟡 Medium |
| 6.1.8 | Default agent config | Support `default_agent` in `AgentSettings`, replace `execution_mode` | `AgentSettings` (modify) | 🟡 Medium |
| 6.1.9 | Orchestrator extension | Add `_session_agents`, `_agent_registry`, mention parsing, agent switching to **existing** `Orchestrator` | Existing `handle_request()`, `_route_to_agent()`, session persistence | 🔴 Critical |
| 6.1.10 | Per-agent memory isolation | Each agent gets its own `SummarizingMemoryManager` instance (not shared) | `SummarizingMemoryManager`, `_create_agent_memory()` factory | 🔴 Critical |
| 6.1.11 | Agent badge update | TUI header badge shows the active agent name and status | — | 🟡 Medium |
| 6.1.12 | User-defined agents | Load user agents from `[agents.*]` in config.toml into global registry | `AgentConfig` fields | 🟡 Medium |
| 6.1.13 | Bootstrap refactor | Replace single-agent step 11 in `create_app()` with multi-agent setup | Steps 1-10 unchanged | 🔴 Critical |
| 6.1.14 | Remove `/mode` command | Delete `cmd_mode` from `commands/handlers/core.py` | — | 🟡 Medium |
| 6.1.15 | Tests | Test session registry, mention parsing, agent switching, context summary | — | 🔴 Critical |

**Deliverable:** `agent_cli/agent/registry.py`, `agent_cli/agent/session_registry.py`, `agent_cli/agent/agents/coder.py`, `agent_cli/agent/agents/researcher.py`, `agent_cli/data/prompts/coder_persona.txt`, etc.

---

## Sub-Phase 6.2 — Effort & Model Management
> Cross-cutting: affects all agents
> **Note:** Effort resolution is already implemented in `BaseAgent.effort` and `/effort` command. This sub-phase adds the per-agent model override.

| # | Task | Description | Reuses | Priority |
|---|------|-------------|--------|----------|
| 6.2.1 | Global effort level | `/effort <low\|medium\|high>` — already works | ✅ `cmd_effort` in `core.py` | � Done |
| 6.2.2 | Per-agent effort override | `AgentConfig.effort_level` takes priority over global | ✅ `BaseAgent.effort` property | � Done |
| 6.2.3 | `/model` command update | `/model <name>` sets the **active** agent's model (not just global) | Modify existing `cmd_model` | 🟡 Medium |
| 6.2.4 | Tests | Test effort resolution order, model persistence | — | 🟡 Medium |

**Deliverable:** Updated command system, config persistence

---

## Sub-Phase 6.3 — Observability & Logging
> Spec: `04_utilities/03_observability.md`

Structured logging, metrics, and debugging tools.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 6.3.1 | Structured logging | `structlog` setup with JSON output, correlation IDs per task | 🔴 Critical |
| 6.3.2 | Log levels | Configured via `AgentSettings.log_level` | 🔴 Critical |
| 6.3.3 | Request tracing | Each user request gets a trace ID carried through all events | 🟡 Medium |
| 6.3.4 | Token usage metrics | Log per-request and per-session token usage using `DataRegistry.get_pricing()` | 🟡 Medium |
| 6.3.5 | Debug mode | `/debug` command or `--debug` flag for verbose output | 🟡 Medium |
| 6.3.6 | Log file rotation | Write logs to `AgentSettings.log_directory` with rotation | 🟡 Medium |
| 6.3.7 | Tests | Test log output formatting, correlation IDs | 🟡 Medium |

**Deliverable:** `agent_cli/core/logging.py`, `agent_cli/core/tracing.py`

---

## Sub-Phase 6.4 — Hardening & Edge Cases
> Cross-cutting concerns from all specs

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 6.4.1 | Graceful shutdown | Clean `ctrl+c` handling: cancel running tasks, save session, close connections | 🔴 Critical |
| 6.4.2 | Concurrent request guard | Prevent new requests while agent is working | 🟡 Medium |
| 6.4.3 | Large file handling | Truncate/skip files over size limit — via `DataRegistry` | 🟡 Medium |
| 6.4.4 | Unicode / encoding | Handle non-UTF-8 files gracefully | 🟡 Medium |
| 6.4.5 | Terminal resize | Re-flow chat content on terminal resize | 🟡 Medium |
| 6.4.6 | Empty state UX | Welcome message, onboarding hints when no session exists | 🟢 Low |
| 6.4.7 | Performance profiling | Identify and fix slow paths (file scanning, token counting) | 🟢 Low |
| 6.4.8 | End-to-end tests | Full scenario tests: start → chat → switch agent → save → restore | 🔴 Critical |

**Deliverable:** Various fixes across the codebase

---

## Sub-Phase 6.5 — Packaging & Distribution

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 6.5.1 | `pyproject.toml` finalization | Dependencies, entry points, version, metadata — include `agent_cli/data/` files | 🔴 Critical |
| 6.5.2 | CLI entry point | `python -m agent_cli` and `agent-cli` command via `[project.scripts]` | 🔴 Critical |
| 6.5.3 | README | Installation, quickstart, configuration guide | 🟡 Medium |
| 6.5.4 | First-run setup | Interactive config wizard for API keys and default provider | 🟡 Medium |
| 6.5.5 | CI/CD | GitHub Actions: lint, test, type-check on PR — include `tests/data/` validation | 🟡 Medium |

**Deliverable:** Updated `pyproject.toml`, `README.md`, `.github/workflows/`

---

## Phase 7 Integration Notes

The following Phase 7 components are already available and should be used throughout Phase 6:

| Component | Usage in Phase 6 |
|---|---|
| `DataRegistry` (via `AppContext.data_registry`) | All new configurable defaults should go in TOML data files |
| `agent_cli/data/prompts/*.txt` | New agent personas (6.1) should be added as prompt templates |
| `DataRegistry.get_pricing()` | Token usage metrics (6.3.4) can get per-model pricing from the registry |
| `DataRegistry.get_tool_defaults()` | Any new tool limits (6.4.3) should be added to `tools.toml` |

---

## Completion Criteria

- [ ] Default agent starts active on session start
- [ ] `/agent add/remove/list/enable/disable/default` commands work
- [ ] `!mention` tag switches active agent with context summary
- [ ] Agent personas loaded from `data/prompts/` templates
- [ ] User-defined agents loadable from config.toml
- [ ] Effort: global `/effort` + per-agent override in config
- [ ] `/model` sets active agent's model (persisted)
- [ ] Structured logging with trace IDs throughout
- [ ] Graceful shutdown works cleanly from any state
- [ ] End-to-end tests pass for full conversation flows including agent switching
- [ ] Packaged and installable via `pip install -e .` (including data files)
- [ ] README with quickstart guide
