# Protocol Format v2 Specification

Status: Draft (Sprint 5)
Date: 2026-03-06
Owner: Agent CLI Runtime

## 1. Scope

Protocol Format v2 defines runtime and prompt-side contracts for:
- Tool-result envelopes (lean + legacy compatibility)
- Action schema normalization (`execute_action`/`execute_actions`)
- Error taxonomy in tool results
- Content references for large content reuse
- Reflect feedback enrichment and budget awareness
- Batch result grouping
- Optional `notify_user.intent`
- Optional `title` with runtime fallback generation

## 2. Compatibility Model

Backward compatibility is required by default.

- Output mode toggle:
  - `tools.json` -> `output_formatter.lean_envelope`
  - `false` (default in current staged rollout): emit legacy JSON envelope
  - `true`: emit lean tag envelope
- Stuck detection and normalization parse both formats.
- Session replay remains compatible with historical JSON envelopes.

## 3. Tool Result Envelope Contract

## 3.1 Legacy JSON Envelope

```json
{
  "id": "msg_xxx",
  "type": "tool_result",
  "version": "1.0",
  "timestamp": "2026-03-06T00:00:00Z",
  "payload": {
    "tool": "read_file",
    "status": "success|error",
    "truncated": false,
    "truncated_chars": 0,
    "output": "..."
  },
  "metadata": {
    "task_id": "task_123",
    "native_call_id": "call_abc",
    "action_id": "act_0",
    "batch_id": "batch_ab12cd34",
    "content_ref": "sha256:abcd1234abcd1234"
  }
}
```

## 3.2 Lean Envelope

```text
[tool_result tool=read_file status=success truncated=false truncated_chars=0 action_id=act_0 batch_id=batch_ab12cd34 content_ref=sha256:abcd1234abcd1234]
<raw output body>
[/tool_result]
```

## 3.3 v2 Metadata Fields

- `error_code` (payload): machine-readable error type
- `retryable` (payload): whether automatic retry is reasonable
- `total_chars` (payload/header): included when truncated
- `total_lines` (payload/header): included when truncated
- `content_ref` (metadata/header): session-scoped reusable content hash
- `batch_id` (metadata/header): shared id for one multi-action batch

## 4. Error Code Taxonomy

Defined by `ToolErrorCode`:
- `FILE_NOT_FOUND`
- `PERMISSION_DENIED`
- `FILE_TOO_LARGE`
- `ENCODING_ERROR`
- `COMMAND_TIMEOUT`
- `COMMAND_FAILED`
- `APPROVAL_DENIED`
- `APPROVAL_TIMEOUT`
- `INVALID_ARGUMENTS`
- `TOOL_NOT_FOUND`
- `OUTPUT_TRUNCATED`
- `INTERNAL_ERROR`
- `UNKNOWN`

Retryable currently:
- `COMMAND_TIMEOUT`
- `APPROVAL_TIMEOUT`
- `OUTPUT_TRUNCATED`

## 5. Action and Decision Semantics

## 5.1 Action Normalization

- `execute_actions` with one action is normalized to `execute_action`.
- `execute_actions` with 2+ actions preserves batch behavior.
- Native function-call slips in JSON-only mode reconstruct missing audit fields:
  - `title`
  - `thought`

## 5.2 notify_user Intent

`decision.type=notify_user` may include optional:
- `intent` (free string, common values: `confirmation`, `report`, `error_explanation`, `question_answer`)

## 5.3 Optional Title

- `title` is optional in JSON response payloads.
- If missing, runtime auto-generates from first words of `thought` (fallback `Untitled`).

## 6. Content Reference Contract

`ToolExecutor` stores selected large outputs (notably `read_file`) and returns a session ref:
- format: `sha256:<16-hex-prefix>`
- scope: current process/session content store
- behavior:
  - if ref resolves: inject referenced content into tool args before validation/execution
  - if ref missing: pass through as literal string (graceful degradation)

Expected usage:
```json
{
  "tool": "write_file",
  "args": {
    "path": "doc.md",
    "content_ref": "sha256:abcd1234abcd1234"
  }
}
```

## 7. Reflect/Budget Feedback

On `reflect`, runtime system feedback includes:
- reflect usage count (`n/max`)
- near-limit warning before hard limit
- resource summary when token/cost data exists (`ResourceTracker`)

## 8. Batch Grouping

For one `execute_batch()` call:
- runtime generates `batch_id=batch_<8hex>`
- every tool result in that batch carries the same `batch_id`
- single-action calls omit `batch_id`

## 9. Prompt Contract Updates

All output templates document:
- optional `title`
- optional `notify_user.intent`
- content reference usage (`content_ref`) with system-provided hash only

Templates:
- `agent_cli/data/prompts/output_format.txt`
- `agent_cli/data/prompts/output_format_multi.txt`
- `agent_cli/data/prompts/output_format_native.txt`
- `agent_cli/data/prompts/output_format_multi_native.txt`

## 10. Runtime Configuration

Primary toggles and defaults:
- `tools.json`:
  - `output_formatter.lean_envelope = true` (default-on)
  - `output_formatter.error_truncation_chars = 2000`
  - `executor.multi_action.enabled = false`
  - `executor.multi_action.max_concurrent_actions = 5`
- `schema.json`:
  - `title.min_words = 0`
  - `title.max_words = 15`
  - `title.required = false`

## 11. Verification Baseline

Sprint gate commands:
- `python -m pytest dev/tests/tools/ dev/tests/agent/test_schema.py dev/tests/core/ -q`
- `python -m pytest dev/tests/agent/test_react_loop.py dev/tests/agent/test_batch_executor.py -q`
- Added integration/replay coverage:
  - `dev/tests/agent/test_protocol_format_integration.py`

## 12. Open Rollout Decision

Current production-safe posture keeps lean envelope disabled by default.
Default flip to lean should only happen after canary and replay confidence gates pass.
