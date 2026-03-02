# Phase 6 — Polish, Multi-Agent & Observability

## Goal
Add multi-agent orchestration (specialized agents for different tasks), task planning for complex multi-step work, observability/logging, and overall hardening. This is the "production-ready" phase.

**Specs:** `04_multi_agent_definitions.md`, `03_task_planning.md`, `03_observability.md`
**Depends on:** All prior phases (including Phase 7 — Data-Driven System)

> [!IMPORTANT]
> Phase 7 (Data-Driven System) has already been implemented. All hard-coded values now live in `agent_cli/data/*.toml` and prompt templates in `agent_cli/data/prompts/*.txt`, accessed via `DataRegistry`. This phase should use `DataRegistry` for any new configurable values rather than adding constants or `AgentSettings` fields.

---

## Sub-Phase 6.1 — Multi-Agent System
> Spec: `01_agent_logic/04_multi_agent_definitions.md`

Specialized agents that the Orchestrator routes to based on the request.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 6.1.1 | `AgentRegistry` | Register and lookup agent implementations by name/capability | 🔴 Critical |
| 6.1.2 | Agent definitions | Define agents: `coder`, `researcher`, `planner`, `debugger` — each with a persona loaded from `DataRegistry.get_prompt_template()` and distinct tool sets | 🔴 Critical |
| 6.1.3 | Agent persona templates | Add `prompts/coder_persona.txt`, `prompts/researcher_persona.txt`, etc. to `agent_cli/data/prompts/` | 🔴 Critical |
| 6.1.4 | Agent selection | Orchestrator inspects request → picks best agent via LLM (used only in FAST_PATH mode to pick the worker) | 🟡 Medium |
| 6.1.5 | `/agent` and `/mode` | Manual overrides via `/agent <name>` (skip LLM agent selection) and `/mode <fast|plan>` (explicit run mode) | 🟡 Medium |
| 6.1.6 | Agent handoff | One agent can delegate to another (e.g., planner → coder) | 🟢 Low |
| 6.1.7 | Agent badge update | TUI header badge shows the active agent name | 🟡 Medium |
| 6.1.8 | Tests | Test routing, registration, handoff | 🔴 Critical |

**Deliverable:** `agent_cli/agent/registry.py`, `agent_cli/agent/agents/coder.py`, `agent_cli/agent/agents/researcher.py`, `agent_cli/data/prompts/coder_persona.txt`, etc.

---

## Sub-Phase 6.2 — Task Planning
> Spec: `00_core_engine/03_task_planning.md`

Plan mode: generate a multi-step plan before executing.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 6.2.1 | Plan generation | Agent generates a structured plan (steps, dependencies, estimates) | 🟡 Medium |
| 6.2.2 | Plan review modal | TUI shows plan to user for approval/editing before execution | 🟡 Medium |
| 6.2.3 | Step-by-step execution | Execute plan steps sequentially, track completion status | 🟡 Medium |
| 6.2.4 | Plan adaptation | If a step fails, re-plan remaining steps | 🟢 Low |
| 6.2.5 | Plan persistence | Save plans as part of session state | 🟢 Low |
| 6.2.6 | Tests | Test plan generation, step execution, failure recovery | 🟡 Medium |

**Deliverable:** `agent_cli/agent/planner.py`, `agent_cli/core/models/plan.py`

---

## Sub-Phase 6.3 — Observability & Logging
> Spec: `04_utilities/03_observability.md`

Structured logging, metrics, and debugging tools.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 6.3.1 | Structured logging | `structlog` setup with JSON output, correlation IDs per task | 🔴 Critical |
| 6.3.2 | Log levels | Configured via `AgentSettings.log_level` (user-facing preference, remains in `AgentSettings`) | 🔴 Critical |
| 6.3.3 | Request tracing | Each user request gets a trace ID carried through all events | 🟡 Medium |
| 6.3.4 | Token usage metrics | Log per-request and per-session token usage using `DataRegistry.get_pricing()` for cost calculations | 🟡 Medium |
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
| 6.4.3 | Large file handling | Truncate/skip files over size limit — add limit to `tools.toml [file_tools]` via `DataRegistry` | 🟡 Medium |
| 6.4.4 | Unicode / encoding | Handle non-UTF-8 files gracefully | 🟡 Medium |
| 6.4.5 | Terminal resize | Re-flow chat content on terminal resize | 🟡 Medium |
| 6.4.6 | Empty state UX | Welcome message, onboarding hints when no session exists | 🟢 Low |
| 6.4.7 | Performance profiling | Identify and fix slow paths (file scanning, token counting) | 🟢 Low |
| 6.4.8 | End-to-end tests | Full scenario tests: start → chat → tool → answer → save → restore | 🔴 Critical |

**Deliverable:** Various fixes across the codebase

---

## Sub-Phase 6.5 — Packaging & Distribution

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 6.5.1 | `pyproject.toml` finalization | Dependencies, entry points, version, metadata — ensure `agent_cli/data/` TOML and TXT files are included as package data | 🔴 Critical |
| 6.5.2 | CLI entry point | `python -m agent_cli` and `agent-cli` command via `[project.scripts]` | 🔴 Critical |
| 6.5.3 | README | Installation, quickstart, configuration guide — document `DataRegistry` data files for contributors | 🟡 Medium |
| 6.5.4 | First-run setup | Interactive config wizard for API keys and default provider | 🟡 Medium |
| 6.5.5 | CI/CD | GitHub Actions: lint, test, type-check on PR — include `tests/data/` validation | 🟡 Medium |

**Deliverable:** Updated `pyproject.toml`, `README.md`, `.github/workflows/`

---

## Phase 7 Integration Notes

The following Phase 7 components are already available and should be used throughout Phase 6:

| Component | Usage in Phase 6 |
|---|---|
| `DataRegistry` (via `AppContext.data_registry`) | All new configurable defaults should go in TOML data files, not as Python constants |
| `agent_cli/data/prompts/*.txt` | New agent personas (6.1) should be added as prompt templates |
| `DataRegistry.get_internal_models()` | Auto-routing (6.1.4) should use `routing_model` from the registry |
| `DataRegistry.get_pricing()` | Token usage metrics (6.3.4) can get per-model pricing from the registry |
| `DataRegistry.get_tool_defaults()` | Any new tool limits (6.4.3) should be added to `tools.toml` |

**Pattern to follow**: When adding a new configurable value:
1. Add the value to the appropriate `.toml` file in `agent_cli/data/`
2. Add an accessor method to `DataRegistry` (if a new domain)
3. Read via `context.data_registry.get_*()` — never hard-code

---

## Completion Criteria

- [ ] Multiple agents registered and auto-selected by the orchestrator
- [ ] Agent personas loaded from `data/prompts/` templates
- [ ] Plan mode: generate → review → execute → track plan
- [ ] Structured logging with trace IDs throughout
- [ ] Graceful shutdown works cleanly from any state
- [ ] End-to-end tests pass for full conversation flows
- [ ] Packaged and installable via `pip install -e .` (including data files)
- [ ] README with quickstart guide
