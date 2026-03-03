# Phase 1 — Foundation & Infrastructure

## Goal
Build the core skeleton that every other component depends on: the Event Bus for communication, the State Manager for tracking system state, the Config system for settings, and the error handling framework.

**Specs:** `00_event_bus.md`, `01_architecture_pattern.md`, `02_state_management.md`, `02_config_management.md`, `04_error_handling.md`

---

## Sub-Phase 1.1 — Event Bus
> Spec: `00_core_engine/00_event_bus.md`

The communication backbone. All components talk through events, never directly.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 1.1.1 | `AbstractEventBus` interface | Define `subscribe()`, `publish()`, `unsubscribe()` ABC | 🔴 Critical |
| 1.1.2 | `AsyncEventBus` implementation | asyncio-based in-process event bus with typed event routing | 🔴 Critical |
| 1.1.3 | Event base classes | `BaseEvent` with `event_id`, `timestamp`, `source`. Typed event subclasses | 🔴 Critical |
| 1.1.4 | Event catalogue | Define all event types: `UserRequestEvent`, `AgentThinkingEvent`, `ToolStartEvent`, `ToolResultEvent`, `AgentMessageEvent`, `StateChangeEvent`, `ErrorEvent`, `FileChangedEvent` | 🔴 Critical |
| 1.1.5 | Event filtering | Support topic-based and type-based subscription filtering | 🟡 Medium |
| 1.1.6 | Unit tests | Test pub/sub, async delivery, ordering, error isolation | 🔴 Critical |

**Deliverable:** `agent_cli/core/event_bus.py`, `agent_cli/core/events.py`

---

## Sub-Phase 1.2 — State Manager
> Spec: `00_core_engine/02_state_management.md`

Centralized state tracking for the entire system.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 1.2.1 | `AbstractStateManager` interface | Define `get_state()`, `update_state()`, `subscribe_state()` ABC | 🔴 Critical |
| 1.2.2 | `InMemoryStateManager` implementation | Thread-safe state store with reactive subscriptions | 🔴 Critical |
| 1.2.3 | State schema | Define `AppState` Pydantic model: mode, effort, model, session, agent, task status | 🔴 Critical |
| 1.2.4 | State → Event Bus bridge | State changes emit `StateChangeEvent` on the Event Bus | 🟡 Medium |
| 1.2.5 | Unit tests | Test state transitions, concurrent access, event emission | 🔴 Critical |

**Deliverable:** `agent_cli/core/state.py`, `agent_cli/core/models/app_state.py`

---

## Sub-Phase 1.3 — Configuration System
> Spec: `02_data_management/02_config_management.md`

TOML-based hierarchical config with Pydantic validation.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 1.3.1 | Refactor `core/config.py` | Migrate existing config to Pydantic Settings with TOML source | 🔴 Critical |
| 1.3.2 | Config hierarchy | Global (`~/.agent_cli/config.toml`) → Project (`.agent_cli/config.toml`) → CLI args → env vars | 🔴 Critical |
| 1.3.3 | Provider config section | `[providers]` TOML section mapping model names to adapters | 🟡 Medium |
| 1.3.4 | Secrets management | API key resolution: env var → `.env` → `keyring` (never in TOML) | 🟡 Medium |
| 1.3.5 | Config validation | Pydantic validators for all settings, clear error messages | 🟡 Medium |
| 1.3.6 | Unit tests | Test hierarchy merging, env override, missing key handling | 🔴 Critical |

**Deliverable:** `agent_cli/core/config.py` (refactored), `agent_cli/core/models/config.py`

---

## Sub-Phase 1.4 — Error Handling Framework
> Spec: `00_core_engine/04_error_handling.md`

Structured error classification and recovery strategies.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 1.4.1 | Error taxonomy | Define error classes: `RetryableError`, `FatalError`, `UserActionRequired`, `ToolError` | 🔴 Critical |
| 1.4.2 | Retry engine | Generic async retry with exponential backoff, max attempts, jitter | 🔴 Critical |
| 1.4.3 | Error → Event Bus | Errors emit `ErrorEvent` for TUI display | 🟡 Medium |
| 1.4.4 | Graceful degradation | Fallback strategies: retry → alternative provider → user notification | 🟡 Medium |
| 1.4.5 | Unit tests | Test retry logic, error classification, event emission | 🔴 Critical |

**Deliverable:** `agent_cli/core/errors.py`, `agent_cli/core/retry.py`

---

## Sub-Phase 1.5 — Dependency Injection Bootstrap
> Spec: `01_architecture_pattern.md`

Wire everything together with clean DI.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 1.5.1 | Bootstrap function | `create_app()` → instantiate Event Bus, State Manager, Config, wire them together | 🔴 Critical |
| 1.5.2 | Component registry | Central registry mapping ABCs to implementations | 🟡 Medium |
| 1.5.3 | Lifecycle management | `startup()` / `shutdown()` hooks for all components | 🟡 Medium |
| 1.5.4 | Integration test | End-to-end: config loads → bus created → state manager subscribes → event flows | 🔴 Critical |

**Deliverable:** `agent_cli/core/bootstrap.py`

---

## Completion Criteria

- [ ] Event Bus: publish/subscribe works with typed events
- [ ] State Manager: state transitions emit events
- [ ] Config: TOML → Pydantic Settings → working hierarchy
- [ ] Error handling: retry engine works with async
- [ ] Bootstrap: all components wired and lifecycle managed
- [ ] All unit tests pass
- [ ] Integration test: full round-trip event flow
