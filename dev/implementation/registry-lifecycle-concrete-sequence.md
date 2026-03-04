# Registry Lifecycle Refactor - Concrete Execution Sequence

Date: 2026-03-05
Source spec: `dev/specs/00_core_engine/05_registry_lifecycle.md`
Source plan: `dev/implementation/registry-lifecycle-implementation-plan.md`

## Approach

Use both documents together:

- The implementation plan remains the scope and phase contract.
- This file defines executable per-file change sets and test migration order.

Two clarifications from current codebase analysis:

- Observability migration must include `agent_cli/tools/executor.py`, `agent_cli/core/orchestrator.py`, and `agent_cli/providers/base.py` in addition to command handlers.
- Command migration must preserve current command names from handlers (`/sessions`, `/generate_title`) unless intentionally changed.

## PR Sequence

### PR1 - Lifecycle foundation

Files:

- `agent_cli/core/registry_base.py` (new)
- `dev/tests/core/test_registry_base.py` (new)

Changes:

- Add `RegistryLifecycleMixin` with `freeze()`, `validate()`, `is_frozen`, `_assert_mutable()`, `_freeze_summary()`.
- Keep `freeze()` idempotent.
- Add isolated unit tests for freeze and validate behavior.

### PR2 - Tool and agent registry adoption

Files:

- `agent_cli/tools/registry.py`
- `agent_cli/agent/registry.py`
- `agent_cli/agent/session_registry.py`
- `dev/tests/tools/test_registry.py`
- `dev/tests/agent/test_session_registry.py`

Changes:

- `ToolRegistry`: mixin + mutable guard + duck-type validation (`name`, `execute`, `get_json_schema`).
- `AgentRegistry`: mixin + mutable guard + duck-type validation (`name`, `handle_task`).
- `SessionAgentRegistry`: keep mutable, add duck-type validation in `add()`.
- Add freeze and validation tests.

### PR3 - CommandRegistry hardening (compatibility kept)

Files:

- `agent_cli/commands/base.py`
- `dev/tests/tui/test_command_system.py`

Changes:

- Add mixin to `CommandRegistry`.
- `register(..., override=False)` with duplicate guard.
- Add freeze behavior tests.
- Keep `_DEFAULT_REGISTRY`, decorator, and `absorb()` for now.

### PR4 - Explicit command wiring + test migration

Files:

- `agent_cli/core/bootstrap.py`
- `agent_cli/commands/handlers/core.py`
- `dev/tests/tui/test_command_system.py`
- `dev/tests/session/test_session_commands.py`
- `dev/tests/workspace/test_sandbox.py`

Changes:

- Build command registry explicitly in bootstrap with `CommandDef(...)`.
- `cmd_help` reads `ctx.app_context.command_registry`.
- Migrate tests off `_DEFAULT_REGISTRY`/`absorb()` to explicit builder.

### PR5 - Remove command decorator/global path

Files:

- `agent_cli/commands/base.py`
- `agent_cli/commands/handlers/core.py`
- `agent_cli/commands/handlers/agent.py`
- `agent_cli/commands/handlers/sandbox.py`
- `agent_cli/commands/handlers/session.py`

Changes:

- Remove `_DEFAULT_REGISTRY`, `@command`, and `absorb()`.
- Convert handlers to plain async functions.
- Keep command inventory unchanged unless a separate change is approved.

### PR6 - Remove observability global singleton

Files:

- `agent_cli/core/logging.py`
- `agent_cli/commands/handlers/core.py`
- `agent_cli/tools/executor.py`
- `agent_cli/core/orchestrator.py`
- `agent_cli/providers/base.py`
- tests touched by these paths

Changes:

- Remove `_OBSERVABILITY` and `get_observability()`.
- Route observability via DI (`AppContext` or constructor injection).
- Update all callsites and tests.

### PR7 - ADAPTER_TYPES immutability

Files:

- `agent_cli/providers/manager.py`
- `dev/tests/providers/test_manager.py`

Changes:

- Wrap adapter map in `MappingProxyType`.
- Add startup validation for adapter key/class shape.
- Add immutability tests.

### PR8 - DataRegistry improvements + freeze wiring

Files:

- `agent_cli/core/registry.py`
- `agent_cli/core/bootstrap.py`
- tests for data/bootstrapping

Changes:

- Add structured DataRegistry logging.
- Replace `_declared_support()` if/elif with accessor map.
- Freeze registries at end of `create_app()` in order: tools -> agents -> commands.
- Keep `SessionAgentRegistry` mutable.

## Test Migration Order

1. Add new lifecycle unit tests (PR1).
2. Update registry behavior tests for freeze/validation (PR2-PR3).
3. Migrate command tests to explicit registration before deleting decorator/global (PR4).
4. Remove decorator/global command path and complete command test cleanup (PR5).
5. Migrate observability tests after DI rewiring (PR6).
6. Add provider immutability tests (PR7).
7. Add DataRegistry accessor/logging tests and bootstrap freeze integration checks (PR8).

## Done Criteria

- All targeted globals removed (`_DEFAULT_REGISTRY`, `_OBSERVABILITY`).
- Registries (except session registry) support validate->freeze and reject post-freeze mutation.
- Full `dev/tests` suite passes.
