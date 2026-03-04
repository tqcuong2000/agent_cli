# Registry Lifecycle Refactor — Implementation Plan

Status: Draft
Date: 2026-03-05
Owner: Agent CLI Core
Depends on: `dev/specs/00_core_engine/05_registry_lifecycle.md`
Audit ref: Codebase Audit Report (2026-03-04)

## 1. Scope

Implement the registry lifecycle refactor:

- Shared `RegistryLifecycleMixin` with validate → freeze semantics.
- Duck-type registration-time validation across all registries.
- Elimination of `_DEFAULT_REGISTRY` global via lazy discovery pattern.
- Elimination of `_OBSERVABILITY` global singleton.
- Immutable `ADAPTER_TYPES` via `MappingProxyType`.
- Data-driven `_declared_support()` in `DataRegistry`.
- Structured logging in `DataRegistry`.
- Freeze sequence at end of `create_app()`.

Out of scope:

- `SessionAgentRegistry` freeze (designed for runtime mutation).
- Changes to `DataRegistry` registration model (already read-only by design).
- New discovery/tag APIs for registries (Info-level, deferred).

## 2. Delivery Principles

1. Zero functional regression:
   - every existing command, tool, agent, and provider must work identically.
   - existing tests must continue to pass.

2. Incremental delivery:
   - each phase is independently mergeable and testable.
   - phases do not depend on later phases.

3. Test-first for new behavior:
   - freeze rejection, validation errors, and immutability are tested before wiring.

## 3. Phase Plan

### Phase 1: Shared Lifecycle Mixin

Goal:
- Create the foundation mixin that all registries will adopt.

Tasks:
1. Create `core/registry_base.py` with `RegistryLifecycleMixin`.
   - `_frozen: bool`, `_registry_name: str`
   - `freeze()` method: calls `validate()`, sets `_frozen = True`, logs.
   - `validate()` method: no-op base, override in subclasses.
   - `_assert_mutable()` guard: raises `RuntimeError` if frozen.
   - `_freeze_summary()` hook: returns human-readable summary.
   - `is_frozen` property.
   - Idempotent `freeze()`: calling twice is safe.
2. Write unit tests for the mixin in isolation.
   - freeze sets `is_frozen` to True.
   - `_assert_mutable()` raises after freeze.
   - `freeze()` is idempotent.
   - custom `validate()` runs before freeze.
   - `validate()` failure prevents freeze.

Files:
- `agent_cli/core/registry_base.py` (new)
- `tests/unit/core/test_registry_base.py` (new)

Exit criteria:
- Mixin passes all unit tests in isolation.
- No other files changed.

---

### Phase 2: ToolRegistry and AgentRegistry Adoption

Goal:
- Apply lifecycle mixin and duck-type validation to the two core registries.

Tasks:
1. Update `ToolRegistry` (`tools/registry.py`):
   - Inherit `RegistryLifecycleMixin`.
   - Set `self._registry_name = "tools"` in `__init__`.
   - Add `self._assert_mutable()` as first line of `register()`.
   - Add duck-type validation in `register()`:
     - `hasattr(tool, "name")` and non-empty.
     - `hasattr(tool, "execute")`.
     - `hasattr(tool, "get_json_schema")`.
   - Override `_freeze_summary()` → `"{n} tools"`.
2. Update `AgentRegistry` (`agent/registry.py`):
   - Inherit `RegistryLifecycleMixin`.
   - Set `self._registry_name = "agents"` in `__init__`.
   - Add `self._assert_mutable()` as first line of `register()`.
   - Add duck-type validation in `register()`:
     - `hasattr(agent, "name")` and non-empty.
     - `hasattr(agent, "handle_task")`.
   - Override `_freeze_summary()` → `"{n} agents: name1, name2"`.
3. Update `SessionAgentRegistry` (`agent/session_registry.py`):
   - **No mixin** — this registry is not freezable.
   - Add duck-type validation in `add()` only:
     - `hasattr(agent, "name")` and non-empty.
4. Write tests:
   - `ToolRegistry`: freeze blocks `register()`, validation rejects bad tools.
   - `AgentRegistry`: freeze blocks `register()`, validation rejects bad agents.
   - `SessionAgentRegistry`: always allows mutations, validates on `add()`.
   - Existing tests still pass with mocks (duck-type friendly).

Files:
- `agent_cli/tools/registry.py`
- `agent_cli/agent/registry.py`
- `agent_cli/agent/session_registry.py`
- `tests/unit/tools/test_registry.py` (new or extend)
- `tests/unit/agent/test_registry.py` (new or extend)

Exit criteria:
- Both registries reject mutations after `freeze()`.
- Duck-type validation catches missing attributes.
- Existing mock-based tests pass without changes.

---

### Phase 3: Command System Redesign

Goal:
- Eliminate `_DEFAULT_REGISTRY` global, remove `@command` decorator, switch to explicit command construction.

#### Phase 3a: CommandRegistry Mixin and Duplicate Guard

Tasks:
1. Update `CommandRegistry` (`commands/base.py`):
   - Inherit `RegistryLifecycleMixin`.
   - Set `self._registry_name = "commands"` in `__init__`.
   - Add `self._assert_mutable()` in `register()`.
   - Add duplicate-key guard: raise `ValueError` on conflict unless `override=True`.
2. Write tests for duplicate guard and freeze.

Files:
- `agent_cli/commands/base.py`
- `tests/unit/commands/test_registry.py` (new or extend)

Exit criteria:
- `CommandRegistry` rejects duplicates by default.
- Freeze blocks `register()`.

#### Phase 3b: Remove `@command` Decorator and Global

Tasks:
1. Remove `_DEFAULT_REGISTRY = CommandRegistry()` from `commands/base.py`.
2. Remove the `command()` decorator function from `commands/base.py`.
3. Remove the `absorb()` method from `CommandRegistry`.
4. Remove all `@command(...)` decorators from handler files:
   - `commands/handlers/core.py`
   - `commands/handlers/agent.py`
   - `commands/handlers/sandbox.py`
   - `commands/handlers/session.py`
5. Handler functions remain as plain `async def` functions (no decorator).

Files:
- `agent_cli/commands/base.py`
- `agent_cli/commands/handlers/core.py`
- `agent_cli/commands/handlers/agent.py`
- `agent_cli/commands/handlers/sandbox.py`
- `agent_cli/commands/handlers/session.py`

Exit criteria:
- No module-level `_DEFAULT_REGISTRY` exists anywhere.
- No `@command` decorator exists anywhere.
- Handler functions are plain async functions.

#### Phase 3c: Explicit Command Registration in Bootstrap

Tasks:
1. Rewrite `_build_command_registry()` in `core/bootstrap.py`:
   - Import all handler functions explicitly.
   - Construct `CommandDef` objects with name, description, usage, shortcut, category, handler.
   - Register each via `registry.register(cmd_def)`.
2. Remove the old `import agent_cli.commands.handlers.* # noqa` pattern.
3. Remove the `from agent_cli.commands.base import _DEFAULT_REGISTRY` import.
4. Remove the `registry.absorb(_DEFAULT_REGISTRY)` call.
5. Fix `cmd_help` handler to use `ctx.app_context.command_registry` instead of importing `_DEFAULT_REGISTRY`.

Files:
- `agent_cli/core/bootstrap.py`
- `agent_cli/commands/handlers/core.py` (`cmd_help` fix)

Exit criteria:
- `_build_command_registry()` constructs all commands explicitly.
- `cmd_help` uses the injected registry.
- All `/help`, `/exit`, `/model`, etc. commands work as before.

---

### Phase 4: Observability Global Elimination

Goal:
- Remove `_OBSERVABILITY` module-level global and `get_observability()` function.

Tasks:
1. Remove `_OBSERVABILITY` variable from `core/logging.py`.
2. Remove `get_observability()` function from `core/logging.py`.
3. Update `configure_observability()`: return the instance without storing globally.
4. Update `ObservabilityManager.shutdown()`: remove `global _OBSERVABILITY` reference.
5. Update `cmd_cost` in `commands/handlers/core.py`:
   - Replace `get_observability()` → `ctx.app_context.observability`.
6. Update `cmd_debug` in `commands/handlers/core.py`:
   - Replace `get_observability()` → `ctx.app_context.observability`.
7. Search for any other `get_observability()` call sites and update them.
8. Write test confirming `cmd_cost` and `cmd_debug` use injected observability.

Files:
- `agent_cli/core/logging.py`
- `agent_cli/commands/handlers/core.py`
- Any other files importing `get_observability`

Exit criteria:
- No `_OBSERVABILITY` module-level variable exists.
- No `get_observability()` function exists.
- `/cost` and `/debug` commands work via DI.

---

### Phase 5: ADAPTER_TYPES Immutability and DataRegistry Improvements

Goal:
- Make `ADAPTER_TYPES` immutable, add startup validation, improve `DataRegistry` observability.

#### Phase 5a: ADAPTER_TYPES

Tasks:
1. Replace `Dict` with `MappingProxyType` for `ADAPTER_TYPES` in `providers/manager.py`.
2. Add startup validation in `ProviderManager.__init__()`:
   - Each key is non-empty.
   - Each value has `safe_generate` attribute (duck-type check).
3. Write test confirming `ADAPTER_TYPES` rejects mutation.

Files:
- `agent_cli/providers/manager.py`
- `tests/unit/providers/test_manager.py` (new or extend)

Exit criteria:
- `ADAPTER_TYPES["custom"] = X` raises `TypeError`.
- Bad adapter class detected at startup, not runtime.

#### Phase 5b: DataRegistry Improvements

Tasks:
1. Add `logger = logging.getLogger(__name__)` to `core/registry.py`.
2. Log offering count after `_load_offerings()`.
3. Log `resolve_model_spec()` hits/misses at `DEBUG` level.
4. Log capability cache operations at `DEBUG` level.
5. Replace `_declared_support()` if/elif chain with `_CAPABILITY_ACCESSORS` dict.

Files:
- `agent_cli/core/registry.py`

Exit criteria:
- `DataRegistry` emits structured DEBUG logs during resolution.
- `_declared_support()` uses a data-driven accessor map.

---

### Phase 6: Bootstrap Freeze Sequence and Integration

Goal:
- Wire the freeze calls into `create_app()` and validate end-to-end.

Tasks:
1. Add freeze sequence at end of `create_app()` in `core/bootstrap.py`:
   ```python
   tool_registry.freeze()
   agent_registry.freeze()
   cmd_registry.freeze()
   ```
2. Ensure freeze happens **after** all agent/tool/command registration.
3. Ensure `SessionAgentRegistry` is **not** frozen.
4. Add integration log:
   ```python
   logger.info("All registries frozen — bootstrap complete.")
   ```
5. Run full test suite end-to-end.
6. Verify all existing functionality works post-freeze:
   - `/help`, `/model`, `/agent`, `/session`, `/sandbox` commands.
   - Tool execution via agent loop.
   - Agent switching via `/agent switch`.
   - Session management runtime mutations.

Files:
- `agent_cli/core/bootstrap.py`

Exit criteria:
- All registries frozen after bootstrap.
- `SessionAgentRegistry` allows runtime mutations.
- Full test suite green.
- Application launches and operates normally.

---

## 4. Task Breakdown by Workstream

### Workstream A: Foundation
- Phase 1

### Workstream B: Registry Hardening
- Phase 2, Phase 5a, Phase 5b

### Workstream C: Global Elimination
- Phase 3, Phase 4

### Workstream D: Integration and Validation
- Phase 6

## 5. Dependency Graph

```
Phase 1 (mixin)
   ├── Phase 2 (tool/agent registries)
   │      └── Phase 6 (freeze sequence)
   ├── Phase 3a (command registry mixin)
   │      └── Phase 3b (remove decorator + global)
   │             └── Phase 3c (explicit bootstrap)
   │                    └── Phase 6 (freeze sequence)
   └── Phase 5a (adapter types)

Phase 4 (observability) — independent, can run in parallel

Phase 5b (DataRegistry) — independent, can run in parallel

Phase 6 (integration) — depends on Phase 2, 3c, 4, 5a
```

## 6. Suggested Execution Order

1. Phase 1 — foundation, no risk
2. Phase 2 — core registries, low risk
3. Phase 3a — command duplicate guard, low risk
4. Phase 3b — remove decorator and global, medium risk
5. Phase 3c — explicit bootstrap, medium risk
6. Phase 4 — observability cleanup, low risk
7. Phase 5a — adapter immutability, low risk
8. Phase 5b — DataRegistry logging, low risk
9. Phase 6 — integration and freeze sequence, validation pass

## 7. File Impact Summary

| Phase | New Files | Modified Files |
|-------|-----------|---------------|
| 1 | `core/registry_base.py`, `tests/unit/core/test_registry_base.py` | — |
| 2 | `tests/unit/tools/test_registry.py`, `tests/unit/agent/test_registry.py` | `tools/registry.py`, `agent/registry.py`, `agent/session_registry.py` |
| 3a | `tests/unit/commands/test_registry.py` | `commands/base.py` |
| 3b | — | `commands/base.py`, `commands/handlers/core.py`, `handlers/agent.py`, `handlers/sandbox.py`, `handlers/session.py` |
| 3c | — | `core/bootstrap.py`, `commands/handlers/core.py` |
| 4 | — | `core/logging.py`, `commands/handlers/core.py` |
| 5a | — | `providers/manager.py` |
| 5b | — | `core/registry.py` |
| 6 | — | `core/bootstrap.py` |

**Totals:** 2 new source files, 4 new test files, 12 modified files.

## 8. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Mock-based tests break from duck-type validation | Medium | Low | Validation checks `hasattr` not `isinstance`; mocks with `spec=` pass |
| `/help` regression from registry switch | Low | Medium | Phase 3c includes targeted test for `cmd_help` |
| `@command` removal breaks import-time side effects | Low | High | Phase 3b removes decorators first; 3c wires explicit registration |
| Freeze order bugs in `create_app()` | Low | Medium | Phase 6 validates full lifecycle with integration test |

## 9. Definition of Done

1. All registries (except `SessionAgentRegistry`) support validate → freeze lifecycle.
2. No global singleton registries exist (`_DEFAULT_REGISTRY`, `_OBSERVABILITY` eliminated).
3. All `register()` methods validate entries via duck-type attribute checks.
4. `CommandRegistry` rejects duplicate keys by default.
5. `ADAPTER_TYPES` is immutable at module scope.
6. `DataRegistry` emits structured logs and uses data-driven capability dispatch.
7. `create_app()` freezes all registries before returning.
8. Full test suite passes with zero regressions.
