# Multi-Action Sprint 1 Preparation

Status: Ready  
Date: 2026-03-05  
Source plan: `dev/implementation/in_progress/multi_actions_plan.md`

## Scope (Sprint 1 Only)

- MA-01-01: `BaseTool.parallel_safe`
- MA-01-02: `ParsedAction.action_id`
- MA-01-03: `AgentDecision.EXECUTE_ACTIONS`
- MA-01-04: `AgentResponse.actions`
- MA-01-05: `ToolResult.action_id` and `tool_name`
- MA-02-01: multi-action feature flags in agent config and data defaults

Non-goal for Sprint 1:
- No multi-action execution behavior yet.
- No schema parsing for `execute_actions` yet.

## Verified Target Files

- `agent_cli/core/runtime/tools/base.py`
- `agent_cli/core/runtime/tools/file_tools.py`
- `agent_cli/core/runtime/tools/shell_tool.py`
- `agent_cli/core/runtime/tools/ask_user_tool.py`
- `agent_cli/core/runtime/agents/parsers.py`
- `agent_cli/core/runtime/agents/base.py`
- `agent_cli/core/infra/registry/bootstrap.py`
- `agent_cli/data/tools.json`
- `dev/tests/tools/test_base.py`
- `dev/tests/agent/test_schema.py`
- `dev/tests/core/test_bootstrap.py`

## Architecture Notes to Keep Sprint 1 Safe

- `SchemaValidator` is currently a shared instance from bootstrap. Do not introduce per-agent behavior in validator during Sprint 1.
- Keep `multi_action_enabled=False` as the default everywhere.
- Treat new fields as additive contract changes only; existing single-action flow must stay unchanged.

## Implementation Slices

### Slice A: Tool and parser contracts

Files:
- `agent_cli/core/runtime/tools/base.py`
- `agent_cli/core/runtime/tools/file_tools.py`
- `agent_cli/core/runtime/tools/shell_tool.py`
- `agent_cli/core/runtime/tools/ask_user_tool.py`
- `agent_cli/core/runtime/agents/parsers.py`

Deliverables:
- Add `parallel_safe` to `BaseTool` and override in concrete tools.
- Extend `ToolResult` with `action_id` and `tool_name`.
- Extend `ParsedAction` and `AgentResponse`; add `EXECUTE_ACTIONS` enum value.

### Slice B: Config and bootstrap wiring

Files:
- `agent_cli/core/runtime/agents/base.py`
- `agent_cli/core/infra/registry/bootstrap.py`
- `agent_cli/data/tools.json`

Deliverables:
- Add `multi_action_enabled` and `max_concurrent_actions` to `AgentConfig`.
- Read these values from per-agent overrides in bootstrap.
- Add executor-level defaults under `executor.multi_action`.

### Slice C: Tests and backward-compat checks

Files:
- `dev/tests/tools/test_base.py`
- `dev/tests/agent/test_schema.py`
- `dev/tests/core/test_bootstrap.py`
- optional: `dev/tests/agent/test_react_loop.py` for config plumbing assertions

Deliverables:
- Assert new fields default safely.
- Assert existing schema parsing and decision behavior are unchanged.
- Assert bootstrap sets config defaults and agent override values correctly.

## Sprint 1 Test Commands

- Baseline already validated:
  - `python -m pytest dev/tests/tools/test_base.py dev/tests/agent/test_schema.py dev/tests/core/test_bootstrap.py dev/tests/core/test_orchestrator.py -q`
  - Result recorded on 2026-03-05: `44 passed`
- Sprint 1 completion gate:
  - `python -m pytest dev/tests/tools/test_base.py dev/tests/agent/test_schema.py dev/tests/agent/test_react_loop.py dev/tests/core/test_bootstrap.py dev/tests/core/test_orchestrator.py -q`

## Definition of Ready

- [x] Target files verified in repository.
- [x] Baseline tests green.
- [x] Sprint scope constrained to additive, backward-compatible changes.
- [x] Execution slices sequenced with clear ownership boundaries.

## Definition of Done (Sprint 1)

- [ ] All six Sprint 1 stories implemented.
- [ ] Single-action behavior unchanged with default config.
- [ ] New config defaults present in code and data files.
- [ ] Sprint 1 completion gate test command passes.

