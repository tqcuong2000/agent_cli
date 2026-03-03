# JSON Communication and Prompting Migration Specification

Status: Draft
Date: 2026-03-03
Owner: Agent CLI Core
Reference: `dev/architect-workspace/agent-system-design-guidelines.md`

## 1. Objective

Replace XML-based agent/system communication and prompt contracts with JSON-based contracts that are:
- Typed
- Versioned
- Validated at boundaries
- Backward-compatible during migration

This includes:
- Agent output contract
- Tool call/result representation in prompt-mode flows
- Tool result formatting injected into working memory
- Error payload standardization
- Prompt structure modularization to reduce ambiguity

## 2. Non-Goals

- No redesign of Event Bus dataclasses in this phase (`core/events/events.py` remains intact).
- No provider SDK replacement.
- No change to business logic of tools themselves.

## 3. Current Architecture Snapshot

### 3.1 Runtime Flow

Current flow is:
1. `Orchestrator` routes user request to active agent (`agent_cli/core/orchestrator.py`).
2. `BaseAgent.handle_task` runs ReAct loop (`agent_cli/agent/base.py`).
3. Provider returns `LLMResponse` (`agent_cli/providers/models.py` + provider adapters).
4. `SchemaValidator` parses native tool calls or XML tags (`agent_cli/agent/schema.py`).
5. `ToolExecutor` executes tool and returns formatted output (`agent_cli/tools/executor.py`).
6. Tool output is stored in memory and sent back to model on next iteration (`agent_cli/agent/memory.py`).

### 3.2 XML Coupling Points

XML currently appears in all critical boundaries:
- Prompt output instructions:
  - `agent_cli/data/prompts/output_format.txt`
  - `agent_cli/data/prompts/output_format_native.txt`
- Validation/parsing:
  - `agent_cli/agent/schema.py` (regex and XML parsing)
- Assistant history normalization:
  - `agent_cli/agent/base.py` serializes native tool calls into XML `<action>` blocks
- Tool result envelope:
  - `agent_cli/tools/output_formatter.py` emits `<tool_result>...</tool_result>`
- Prompt-injected tool definitions for non-native providers:
  - `agent_cli/providers/xml_formatter.py`

### 3.3 Key Risks in Current Design

1. Mixed protocol modes (`NATIVE` + `XML`) increase parser and recovery complexity.
2. XML regex + XML parsing is fragile under malformed model output.
3. Important routing semantics are encoded in tag structure rather than typed fields.
4. Tool outputs are injected as XML strings, not typed data envelopes.
5. Prompt sections are not strictly modular; hard rules and output constraints are spread across templates and recovery text.

## 4. Target Contract

## 4.1 Canonical Envelope (Agent <-> System Logical Contract)

All message-like payloads that cross model/tool boundaries must use:

```json
{
  "id": "msg_uuid",
  "type": "tool_call",
  "version": "1.0",
  "timestamp": "2026-03-03T10:00:00Z",
  "payload": {},
  "metadata": {}
}
```

Decision:
- `version` is required from day one.
- Unknown fields are ignored by consumers.
- `type` is enum-backed and validated.

## 4.2 Agent Decision Payload (Prompt Mode)

For providers without native function calling, the assistant must return one JSON object:

```json
{
  "title": "Read config before edit",
  "thought": "Need current file state before patching.",
  "decision": {
    "type": "execute_action",
    "tool": "read_file",
    "args": {"path": "agent_cli/core/config.py"}
  }
}
```

Allowed `decision.type`:
- `reflect`
- `execute_action`
- `notify_user`
- `yield`

`notify_user` and `yield` include:
- `message` (string)

## 4.3 Agent Decision in Native Tool Mode

For native providers:
- Tool invocation remains native via provider `tool_calls`.
- Text content should still be JSON with `title` + `thought` + `decision.type`.
- If native tool call exists, parser treats it as authoritative action source.

Decision:
- Native tool call data remains provider-driven.
- JSON text is required for reasoning/consistency, but not for tool args in native mode.

## 4.4 Tool Result Envelope in Memory

Replace XML tool-result blob with JSON envelope string:

```json
{
  "id": "msg_uuid",
  "type": "tool_result",
  "version": "1.0",
  "timestamp": "2026-03-03T10:00:00Z",
  "payload": {
    "tool": "read_file",
    "status": "success",
    "truncated": false,
    "truncated_chars": 0,
    "output": "..."
  },
  "metadata": {
    "task_id": "task_123",
    "native_call_id": "call_abc"
  }
}
```

Decision:
- Memory continues storing `content` as string for provider compatibility.
- Content format becomes JSON string, not XML.

## 4.5 Canonical Error Shape

All parser/format/contract errors emitted back to model or logs should map to:

```json
{
  "code": "SCHEMA_VALIDATION_ERROR",
  "message": "Invalid decision payload",
  "details": {"field": "decision.type"},
  "recoverable": true
}
```

## 5. Prompting Architecture Changes

Adopt 4 explicit prompt blocks:
1. `SYSTEM_CONTRACT`
2. `CONTEXT_INJECTION`
3. `TASK`
4. `HISTORY`

Decision:
- Hard rules must appear near top and bottom of `SYSTEM_CONTRACT`.
- Output format constraints become JSON-only.
- Tool descriptions keep positive + negative usage guidance.

Implementation target:
- Refactor `PromptBuilder` (`agent_cli/agent/react_loop.py`) to compose labeled sections from dedicated templates instead of one mixed template.

## 6. Module-Level Implementation Plan

### Phase 0: Baseline and Flags

- Add feature flags in config:
  - `core.protocol_mode = "xml_compat" | "json_dual" | "json_only"`
- Default: `json_dual` in dev, `xml_compat` for safe rollout.

Files:
- `agent_cli/core/config.py`
- `agent_cli/data/schema.toml` or new protocol defaults file

### Phase 1: New Protocol Models

- Add typed protocol models (`pydantic`/dataclass):
  - Envelope
  - Decision payload
  - Tool result payload
  - Error payload

Files:
- New: `agent_cli/agent/protocol.py`
- Update: `agent_cli/providers/models.py` (`ToolCallMode.XML` -> `PROMPT_JSON`)

Decision:
- Keep compatibility alias for XML mode during transition.

### Phase 2: Parser/Validator Migration

- Replace XML parsing path with JSON parser path.
- Maintain temporary fallback parser for XML when in `xml_compat`/`json_dual`.
- Add strict validation and structured recovery feedback.

Files:
- `agent_cli/agent/schema.py`
- `agent_cli/agent/parsers.py`

Decision:
- Recovery messages must include compact JSON examples, not prose-only corrections.

### Phase 3: Prompt Template Migration

- Replace `output_format*.txt` with JSON output contract templates.
- Remove XML references in prompt text and schema recovery text.

Files:
- `agent_cli/data/prompts/output_format.txt`
- `agent_cli/data/prompts/output_format_native.txt`
- `agent_cli/agent/react_loop.py`
- `agent_cli/agent/base.py` (`_build_schema_recovery_message`)

### Phase 4: Tool Formatter and Provider Prompt Injection

- Replace `XMLToolFormatter` with `JSONToolFormatter`.
- Rename injection helpers/comments from XML to prompt-json.
- Non-native providers inject JSON contract instructions, not XML tag instructions.

Files:
- Rename: `agent_cli/providers/xml_formatter.py` -> `agent_cli/providers/json_formatter.py`
- Update imports in:
  - `agent_cli/providers/provider/openai_provider.py`
  - `agent_cli/providers/provider/openai_compat.py`
  - `agent_cli/providers/provider/anthropic_provider.py`
  - `agent_cli/providers/provider/google_provider.py`
  - `agent_cli/providers/provider/ollama_provider.py`
  - `agent_cli/providers/base.py`

### Phase 5: Tool Result Formatting Migration

- `ToolOutputFormatter` emits JSON envelope string instead of XML.
- Include optional `task_id` and `native_call_id` metadata.

Files:
- `agent_cli/tools/output_formatter.py`
- `agent_cli/tools/executor.py` (pass metadata)

### Phase 6: Memory and Assistant-History Normalization

- Remove XML serialization helper methods in `BaseAgent`.
- Store native tool call traces as JSON event snippets in assistant history.
- Keep role semantics unchanged until provider message model is upgraded.

Files:
- `agent_cli/agent/base.py`

### Phase 7: Remove XML Compatibility

When metrics confirm stability:
- Delete XML parser path.
- Delete XML templates and formatter.
- Rename compatibility enums and tests.

## 7. Testing Strategy

## 7.1 Unit Tests

Update and add tests for:
- JSON parser success/failure (`dev/tests/agent/test_schema.py`)
- Prompt builder output contract (`dev/tests/agent/test_react_loop.py`)
- Tool output formatter JSON envelope (`dev/tests/tools/test_executor.py` + new formatter tests)
- Provider compatibility behavior (`dev/tests/providers/*`)

## 7.2 Contract Tests

Add a matrix of malformed payload cases:
- Missing `decision`
- Unknown `decision.type`
- `execute_action` without `tool`
- Non-object `args`
- Extra unknown fields (must not fail)

## 7.3 Integration Tests

Run end-to-end flows in all modes:
- `xml_compat`
- `json_dual`
- `json_only`

Assertions:
- Task completes with identical business behavior.
- No XML tags appear in memory/tool outputs in `json_only`.

## 8. Observability and Rollout Gates

Add protocol metrics:
- `protocol.parse.success`
- `protocol.parse.failure`
- `protocol.fallback.xml_used`
- `protocol.decision.type.count`

Rollout gates:
1. `json_dual` parse success >= 99% for 1 week
2. XML fallback < 1%
3. No increase in `MaxIterationsExceededError` or `SchemaValidationError`
4. Then switch default to `json_only`

## 9. Risks and Mitigations

1. Native providers may return text that is not strict JSON.
- Mitigation: tool call remains authoritative; text JSON parser tolerant with clear recoverable error.

2. Backward compatibility with old tests and prompts.
- Mitigation: phased flags and dual parser mode.

3. Memory token pressure from verbose JSON envelopes.
- Mitigation: compact JSON serialization for transport, pretty-print only in logs.

4. Ambiguous tool-result role mapping for providers needing `tool_call_id`.
- Mitigation: include `native_call_id` metadata now; schedule follow-up to support provider-specific message shaping.

## 10. Decisions Log

1. Keep provider-native function calling where available.
2. Remove XML tags from prompt-mode action contract; use strict JSON object.
3. Standardize tool-result payload as JSON envelope string.
4. Introduce protocol versioning at message level (`version: "1.0"`).
5. Migrate in phases with feature flag + dual parser, then remove XML.

## 11. Acceptance Criteria

- No XML-based parsing/formatting in `json_only` mode.
- All agent decisions parse through JSON schema validation.
- Tool results in memory are JSON envelopes.
- Prompt templates contain JSON contract examples only.
- Existing provider integrations remain functional across native and non-native modes.
