# Protocol Format Improvements - Sprint 1 Prep Checklist

## Sprint Scope
- PF-01-01: Dual-mode tool result envelope (`lean` + `legacy JSON`)
- PF-01-02: Dual-format stuck detection normalization
- PF-01-03: `tools.json` feature flag plumbing (`lean_envelope`)
- PF-02-01: `execute_actions` -> `execute_action` normalization for single-action batches
- PF-02-02: Native FC slip audit-trail reconstruction in JSON-only mode

## Rollout Defaults (Staged)
- `output_formatter.lean_envelope = false` in this sprint
- Keep legacy JSON envelope behavior as default for existing tests and replay
- Support lean envelopes in runtime normalization paths to enable safe future flip

## Implementation Checklist
- [x] Add lean envelope rendering to `ToolOutputFormatter`
- [x] Keep legacy JSON envelope rendering and route by config flag
- [x] Add `lean_envelope` config key in `data/tools.json`
- [x] Update `StuckDetector` to normalize both legacy JSON and lean envelopes
- [x] Add schema post-parse action normalization for single-item `execute_actions`
- [x] Add native FC audit reconstruction when JSON-only mode receives native calls
- [x] Add/adjust tests for dual-envelope parsing and schema normalization

## Test Matrix
- Formatter:
  - Legacy JSON format unchanged when `lean_envelope=false`
  - Lean format parseability and metadata coverage
- Stuck detector:
  - Same normalized fingerprint for equivalent legacy and lean tool results
- Schema validator:
  - Single-action `execute_actions` payload downgrades to `execute_action`
  - Native FC slip in JSON-only mode reconstructs title/thought and logs warning
- Regression:
  - Tool executor tests
  - Tool registry/formatter tests
  - React loop tests
  - Batch executor tests
  - Core orchestration tests

## Gate Commands
- `python -m pytest dev/tests/tools/ dev/tests/agent/test_schema.py dev/tests/core/ -q`
- `python -m pytest dev/tests/agent/test_react_loop.py dev/tests/agent/test_batch_executor.py -q`
