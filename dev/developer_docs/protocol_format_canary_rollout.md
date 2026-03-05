# Protocol Format Canary Rollout Plan

Status: Draft (Sprint 5)
Date: 2026-03-06
Owner: Agent CLI Runtime

## 1. Feature Scope

Rollout target:
- Lean tool-result envelope (`output_formatter.lean_envelope`)

Already-on additive features (no default flip required):
- dual-format stuck normalization
- error taxonomy in tool results
- content references
- truncation planning metadata
- reflect/resource summary
- `batch_id` grouping
- optional `notify_user.intent`
- optional `title`

## 2. Guardrails

- Keep default:
  - `output_formatter.lean_envelope = true`
- Preserve dual-format parser support at all times during canary.
- Keep replay compatibility test in required CI suite.

## 3. Entry Criteria

- CI green on regression gates:
  - `python -m pytest dev/tests/tools/ dev/tests/agent/test_schema.py dev/tests/core/ -q`
  - `python -m pytest dev/tests/agent/test_react_loop.py dev/tests/agent/test_batch_executor.py dev/tests/agent/test_protocol_format_integration.py -q`
- No unresolved replay regressions.
- No unresolved schema validation spikes in pre-canary logs.

## 4. Canary Phases

## Phase 0: Baseline (Current)

- Environment emits legacy JSON envelope.
- Record baseline for:
  - task success rate
  - tool error rate
  - schema validation error count
  - median/p95 task latency

## Phase 1: Internal Dogfood

- Enable lean envelope for internal test workloads only.
- Suggested traffic/time:
  - internal-only for 24h
- Verify:
  - no replay breakages
  - no stuck-detector regressions
  - no consumer/parser failures

Rollback:
- immediate config revert to `lean_envelope=false`

## Phase 2: Limited Canary

- Enable lean envelope for 5-10% traffic for 24h.
- Then 25% traffic for 24h if stable.

Monitor deltas versus baseline:
- task success/failure ratio
- tool error ratio
- schema validation failures
- p95 task latency
- memory/replay continuity issues

Exit to next phase only if deltas are within accepted thresholds.

## Phase 3: Broad Enablement

- Increase to 50%, then 100% after two stable windows.
- Keep dual-format parser and replay tests active.

## 5. Monitoring Signals

Primary runtime health:
- Task success rate
- Tool execution error rate
- Schema validation failure rate
- Max-iterations exits
- Replay/load failures

Secondary protocol checks:
- Presence/parseability of lean envelopes in tool messages
- Correct `truncated` + `total_chars`/`total_lines` metadata on large outputs
- Correct `batch_id` grouping for multi-action results

## 6. Rollback Playbook

1. Set `output_formatter.lean_envelope=false`.
2. Redeploy config/runtime.
3. Re-run mandatory regression suite.
4. Compare post-rollback health to pre-canary baseline.

Rollback is non-destructive:
- old and new sessions remain replay-compatible via dual parser support.

## 7. Post-Rollout Hardening

- Keep `test_legacy_json_tool_result_replay_remains_compatible` required in CI.
- Keep integration protocol test required in CI.
- Review parser logs for malformed lean-envelope edge cases.
- Only after stable period consider removing legacy emitter mode (not in current scope).

## 8. Release Decision Record

Default flip recommendation requires:
- 2 consecutive stable canary windows
- replay test pass in CI and staging
- no statistically meaningful failure-rate regression

Until legacy replay confidence is sustained, keep dual-parser compatibility enabled.
