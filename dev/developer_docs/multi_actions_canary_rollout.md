# Multi-Action Canary Rollout Plan

## Scope
- Feature: multi-action ReAct loop (`decision.type=execute_actions`)
- Guard: `AgentConfig.multi_action_enabled` (default `false`)
- Runtime safety: `parallel_safe` tool metadata + `ask_user` singleton enforcement

## Preconditions
- CI green with full regression suite.
- Multi-action metrics available in structured logs:
  - `multi_action.batch_size`
  - `multi_action.parallel_count`
  - `multi_action.sequential_count`
  - `multi_action.batch_duration_ms`
  - `multi_action.ask_user_strip_count`
  - `multi_action.stuck_batch_count`

## Phase 1: Internal Validation
- Enable `multi_action_enabled=true` only on internal test agents.
- Tool set: read-only (`read_file`, `search_files`, `list_directory`).
- Run focused scenarios:
  - 3-way read fan-out
  - mixed safe/unsafe batch
  - `ask_user` singleton strip behavior
- Exit criteria:
  - No increase in fatal errors
  - `stuck_batch_count` near zero
  - p95 `batch_duration_ms` lower than sequential baseline for read fan-out

## Phase 2: Production Canary
- Enable one production agent with read-only tools and limited traffic.
- Suggested blast radius:
  - 5-10% traffic for 24h
  - then 25% traffic for 24h
- Monitor:
  - task success/failure ratio
  - tool error ratio
  - multi-action metrics above
  - latency deltas vs single-action baseline
- Roll-forward criteria:
  - Stable success rate
  - No abnormal increase in tool errors
  - Positive latency impact on I/O-heavy tasks

## Phase 3: Broad Enablement
- Expand to additional agents gradually.
- Add non-read-only tools only after canary confidence is established.
- Keep `max_concurrent_actions` conservative per agent and raise gradually.

## Rollback
- Immediate rollback: set `multi_action_enabled=false` in agent config.
- Optional containment:
  - reduce `max_concurrent_actions`
  - temporarily remove write/exec tools from multi-action agents

## Post-Rollout
- Keep alerting on `multi_action.stuck_batch_count` and tool error drift.
- Review logs for recurring `ask_user` strips (prompt quality feedback loop).
