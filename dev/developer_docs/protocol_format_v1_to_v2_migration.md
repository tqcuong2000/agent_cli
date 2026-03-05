# Protocol Format Migration Guide (v1 -> v2)

Status: Draft (Sprint 5)
Date: 2026-03-06
Owner: Agent CLI Runtime

## 1. Who Should Use This Guide

Use this guide if you maintain:
- Prompt templates or agent authoring rules
- Tool-output consumers (parsers, analytics, replay tooling)
- Session/replay compatibility workflows

## 2. Migration Summary

Protocol v2 is additive and compatibility-first.

What changed:
- Optional lean tool envelope mode
- New tool-result metadata fields (`error_code`, `retryable`, `total_chars`, `total_lines`, `content_ref`, `batch_id`)
- Optional `notify_user.intent`
- Optional `title` (auto-generated when absent)
- Single-item `execute_actions` normalized to `execute_action`

What did not break:
- Legacy JSON tool envelopes still parse
- Session replay for old tool results remains valid
- Existing `execute_action` paths remain valid

## 3. Configuration Rollout Steps

## Step 1: Confirm default-on and dual-parser safety

`agent_cli/data/tools.json`:
- `output_formatter.lean_envelope: true`

Runtime still parses both lean and legacy JSON envelopes, so replay compatibility remains intact.

## Step 2: Validate compatibility in your environment

Run:
- `python -m pytest dev/tests/tools/ dev/tests/agent/test_schema.py dev/tests/core/ -q`
- `python -m pytest dev/tests/agent/test_react_loop.py dev/tests/agent/test_batch_executor.py -q`
- `python -m pytest dev/tests/agent/test_protocol_format_integration.py -q`

## Step 3: Canary flip (optional)

After canary confidence:
- set `output_formatter.lean_envelope: true`
- keep dual-format parser support enabled

## 4. Contract-Level Deltas

## 4.1 Tool Result Envelopes

v1 (legacy default):
- JSON envelope string in `role=tool` content

v2 (optional lean mode):
- tag envelope with raw body content

Consumer requirement:
- accept both envelope formats
- normalize to stable shape for hashing/comparison

## 4.2 Action Schema

v1:
- `execute_action` primary
- `execute_actions` optional multi path

v2:
- same public schema
- runtime normalization:
  - one-item `execute_actions` -> `execute_action`

## 4.3 notify_user

v1:
- `decision.message` only

v2:
- `decision.message` plus optional `decision.intent`

## 4.4 title

v1:
- practically required by prompt conventions

v2:
- optional (`schema.json`: `required=false`)
- runtime fallback generation if omitted

## 5. Prompt Migration Checklist

Update output templates to include:
- optional `title`
- optional `decision.intent` for notify_user
- content-ref usage guidance

Required templates:
- `agent_cli/data/prompts/output_format.txt`
- `agent_cli/data/prompts/output_format_multi.txt`
- `agent_cli/data/prompts/output_format_native.txt`
- `agent_cli/data/prompts/output_format_multi_native.txt`

## 6. Tooling/Parser Migration Checklist

- Parse both envelope formats.
- Read new metadata fields defensively (treat missing as default values).
- Do not assume `batch_id` on single-action results.
- Do not require `intent`.
- Do not require non-empty `title`.

## 7. Replay Migration (Old Sessions)

Replay requirement:
- historical JSON `tool_result` strings must remain processable without mutation.

Verification:
- include a replay fixture with legacy JSON tool content in `session_messages`.
- assert runtime hydrates and continues task execution normally.

Reference test:
- `dev/tests/agent/test_protocol_format_integration.py::test_legacy_json_tool_result_replay_remains_compatible`

## 8. Rollback Strategy

If issues appear after enabling lean envelope:
1. Set `output_formatter.lean_envelope` back to `false`.
2. Re-run gate suite.
3. Keep dual parser support active.

No data migration rollback is required because replay accepts legacy envelopes.

## 9. Acceptance Criteria for Complete Migration

- Gate tests pass in CI.
- Replay test passes with legacy sessions.
- Canary metrics stable after lean-envelope enablement.
- No increase in schema/tool failure rates attributable to format changes.
