# Protocol Format Improvements – Implementation Plan (v1)

## Epic: PF-EPIC-001 – Agent ↔ System Communication Protocol Improvements

**Objective**
Resolve structural inefficiencies, ambiguities, and fragilities in the JSON communication protocol between the agent (LLM) and the system runtime. The improvements target token efficiency, error recovery speed, auditability, and protocol consistency — while maintaining full backward compatibility with the existing single-action and multi-action behaviors.

**Architecture Decisions (locked)**

| # | Decision | Choice |
|---|----------|--------|
| 1 | Backward compatibility | All changes default-off or additive; existing session replay unaffected |
| 2 | Tool result format | Structured envelope with separated metadata vs content; stringified JSON remains as fallback |
| 3 | Schema unification | Normalize `execute_action`/`execute_actions` into a single dispatch path internally |
| 4 | Content referencing | Hash-based reference for previously emitted content in same session |
| 5 | Error taxonomy | Enum-based error codes coexisting with human-readable messages |
| 6 | Reflect feedback | Enriched system response with cycle count and memory state summary |
| 7 | Batch result grouping | Explicit `batch_id` envelope wrapping correlated tool results |
| 8 | `notify_user` subtyping | Optional `intent` field for downstream rendering hints |

**Success Metrics**
- ≥15% token reduction on tool-heavy sessions (measured via before/after on 10 representative sessions)
- ≥50% reduction in recovery turns after truncated file reads
- 0 regressions in existing session replay, schema validation, or agent behavior
- All new protocol fields validated by SchemaValidator with backward-compat defaults

---

## Plan Verification (2026-03-05)

Validation was run against the current repository state (`agent_cli/...` package layout) and baseline tests.

**Verified with no blockers**
- Stories PF-01 through PF-09 map cleanly to existing modules.
- The following files are the primary modification targets and have been verified to exist:
  - `agent_cli/core/runtime/tools/output_formatter.py` (149 lines)
  - `agent_cli/core/runtime/agents/schema.py` (480 lines)
  - `agent_cli/core/runtime/agents/parsers.py`
  - `agent_cli/core/runtime/agents/react_loop.py` (301 lines)
  - `agent_cli/core/runtime/agents/base.py` (large, 52KB)
  - `agent_cli/data/prompts/output_format*.txt` (4 variants)
  - `agent_cli/data/schema.json`, `agent_cli/data/tools.json`

**Required corrections for execution**
- Output formatter changes must coordinate with multi-action plan (MA-06-02) which already added `action_id` to the envelope.
- Schema unification (PF-02) is orthogonal to multi-action parsing (MA-03) but both touch `schema.py` — changes should be to different regions.
- Content reference system (PF-04) requires a session-scoped content store that doesn't currently exist — new module needed.

---

## Current Architecture Reference

```
core/runtime/
├── agents/
│   ├── base.py               ← BaseAgent + handle_task() loop + AgentConfig
│   ├── parsers.py             ← ParsedAction, AgentDecision, AgentResponse
│   ├── schema.py              ← SchemaValidator (JSON + native FC parsing)
│   ├── react_loop.py          ← StuckDetector, PromptBuilder
│   ├── memory.py              ← WorkingMemoryManager
│   ├── batch_executor.py      ← BatchExecutor (multi-action)
│   └── multi_action_validator.py ← MultiActionValidator
├── tools/
│   ├── base.py                ← BaseTool ABC, ToolResult, ToolCategory
│   ├── executor.py            ← ToolExecutor
│   ├── output_formatter.py    ← ToolOutputFormatter (JSON envelope)
│   └── registry.py            ← ToolRegistry
└── orchestrator/
    └── ...

data/
├── prompts/
│   ├── output_format.txt            ← JSON-only single-action
│   ├── output_format_multi.txt      ← JSON-only multi-action
│   ├── output_format_native.txt     ← Native FC single-action
│   └── output_format_multi_native.txt ← Native FC multi-action
├── schema.json                      ← title constraints, validation limits
└── tools.json                       ← executor, output_formatter defaults
```

**Weakness Inventory (consolidated from both audit reports):**

| # | Weakness | Source | Severity | Story |
|---|----------|--------|----------|-------|
| W1 | Double-escaped JSON in tool results | Both | Critical | PF-01 |
| W2 | `execute_action` vs `execute_actions` schema divergence | Agent 2 | High | PF-02 |
| W3 | Large content re-emitted in `write_file` args | Agent 2 | High | PF-04 |
| W4 | No structured error codes in tool results | Agent 1 | Medium | PF-03 |
| W5 | Truncation recovery lacks file metadata | Agent 1 | Medium | PF-05 |
| W6 | `reflect` system response is a no-op | Both | Medium | PF-06 |
| W7 | Parallel tool results not explicitly grouped | Agent 2 | Medium | PF-07 |
| W8 | `notify_user` has no intent subtyping | Agent 2 | Low | PF-08 |
| W9 | `title` field adds overhead without proportional value | Agent 1 | Low | PF-09 |
| W10 | No token/cost budget visibility for the agent | Agent 1 | Medium | PF-06 |
| W11 | Native FC format slip (inconsistent schema) | Agent 1 | Medium | PF-02 |
| W12 | File content in `output` lacks structural metadata | Agent 1 | Low | PF-05 |

---

## Story: PF-01 – Reduce Tool Result Serialization Overhead

> **Addresses:** W1 (Double-escaped JSON tokens)

### PF-01-01: Restructure `ToolOutputFormatter` envelope to separate metadata from content

**File:** `agent_cli/core/runtime/tools/output_formatter.py`
**Priority:** Highest
**Estimate:** 5 SP

**Problem:**
Currently `_to_json_envelope()` produces a single compact JSON string via `json.dumps(..., separators=(",", ":"))`. This string is then assigned to `{"role": "tool", "content": <string>}`, creating double-serialized JSON. Every `"` in file content costs an extra `\` escape token.

**Changes:**
```python
class ToolOutputFormatter:
    # ... existing __init__ ...

    def format(
        self,
        tool_name: str,
        raw_output: str,
        success: bool = True,
        *,
        task_id: str = "",
        native_call_id: str = "",
        action_id: str = "",
    ) -> str:
        # ... existing truncation logic unchanged ...

        # NEW: Use a lightweight text-based format instead of nested JSON
        return self._to_lean_envelope(
            tool_name=tool_name,
            status="success" if success else "error",
            output=output_text,  # raw text, NOT json-escaped
            truncated=truncated,
            truncated_chars=truncated_chars,
            task_id=task_id,
            native_call_id=native_call_id,
            action_id=action_id,
        )

    @staticmethod
    def _to_lean_envelope(
        *,
        tool_name: str,
        status: str,
        output: str,
        truncated: bool,
        truncated_chars: int,
        task_id: str,
        native_call_id: str,
        action_id: str,
    ) -> str:
        """Render tool result in a lean text format that avoids double-escaping.

        Format:
            [tool_result tool=<name> status=<status> truncated=<bool> ...]
            <raw output content — no escaping needed>
            [/tool_result]
        """
        meta_parts = [
            f"tool={tool_name}",
            f"status={status}",
        ]
        if truncated:
            meta_parts.append(f"truncated_chars={truncated_chars}")
        if task_id:
            meta_parts.append(f"task_id={task_id}")
        if action_id:
            meta_parts.append(f"action_id={action_id}")
        if native_call_id:
            meta_parts.append(f"native_call_id={native_call_id}")

        header = f"[tool_result {' '.join(meta_parts)}]"
        return f"{header}\n{output}\n[/tool_result]"

    # Keep _to_json_envelope as fallback for backward compat
    @staticmethod
    def _to_json_envelope(...) -> str:
        # ... unchanged, kept for legacy mode ...
```

**Acceptance Criteria:**
- New `_to_lean_envelope()` method produces a tag-delimited format with raw content (no double-escaping)
- Old `_to_json_envelope()` retained as `_to_json_envelope_legacy()` behind a config flag
- Metadata (tool name, status, truncation info) lives in the header tag attributes
- File content, code outputs, etc. appear verbatim between tags — zero escape overhead
- Toggle via `tools.json`: `output_formatter.lean_envelope: true` (default `true`)
- SchemaValidator can parse both old and new formats for session replay

**Dependencies:** None

---

### PF-01-02: Update `SchemaValidator` to parse lean envelope format

**File:** `agent_cli/core/runtime/agents/schema.py`
**Priority:** Highest
**Estimate:** 3 SP

**Changes:**
The `_normalize_result_for_stuck_check()` and any internal parsing that reads tool result content must handle both the new lean format and the legacy JSON envelope.

```python
@staticmethod
def _parse_tool_result_metadata(content: str) -> dict:
    """Parse tool result metadata from either lean or JSON envelope format.

    Lean format:  [tool_result tool=X status=Y ...] \n content \n [/tool_result]
    JSON format:  {"id":"...", "payload":{"tool":"X", ...}}
    """
    if content.startswith("[tool_result "):
        # Lean format parsing
        header_end = content.index("]\n")
        header = content[len("[tool_result "):header_end]
        attrs = dict(part.split("=", 1) for part in header.split())
        body_end = content.rindex("[/tool_result]")
        body = content[header_end + 2:body_end].rstrip("\n")
        return {
            "tool": attrs.get("tool", ""),
            "status": attrs.get("status", "success"),
            "output": body,
            "truncated": "truncated_chars" in attrs,
            "truncated_chars": int(attrs.get("truncated_chars", 0)),
        }
    else:
        # Legacy JSON envelope parsing
        data = json.loads(content)
        payload = data.get("payload", {})
        return {
            "tool": payload.get("tool", ""),
            "status": payload.get("status", "success"),
            "output": payload.get("output", ""),
            "truncated": payload.get("truncated", False),
            "truncated_chars": payload.get("truncated_chars", 0),
        }
```

**Acceptance Criteria:**
- Both lean and JSON envelope formats parsed correctly
- `StuckDetector._normalize_result_for_stuck_check()` handles both formats
- Session replay with old JSON-envelope sessions still works
- Unit tests for both parsing paths

**Dependencies:** PF-01-01

---

### PF-01-03: Add `lean_envelope` config flag to `tools.json`

**File:** `agent_cli/data/tools.json`
**Priority:** High
**Estimate:** 1 SP

**Changes:**
```json
{
  "output_formatter": {
    "error_truncation_chars": 2000,
    "lean_envelope": true
  }
}
```

**Acceptance Criteria:**
- `lean_envelope: true` uses new format (default)
- `lean_envelope: false` falls back to legacy JSON envelope
- `ToolOutputFormatter.__init__()` reads this flag from `DataRegistry`

**Dependencies:** PF-01-01

---

## Story: PF-02 – Unify Action Schema and Normalize Native FC Fallbacks

> **Addresses:** W2 (schema divergence), W11 (native FC format slip)

### PF-02-01: Internal normalization of `execute_action` and `execute_actions` dispatch

**File:** `agent_cli/core/runtime/agents/schema.py`
**Priority:** High
**Estimate:** 5 SP

**Problem:**
`execute_action` places `tool`/`args` as siblings of `type` in `decision`. `execute_actions` nests them inside an `actions` array. The parser must branch on `type` with different extraction logic. A silent misroute is possible.

**Changes:**
Add an internal normalization step after parsing either variant:

```python
def _normalize_action_response(self, response: AgentResponse) -> AgentResponse:
    """Ensure consistent internal representation regardless of input variant.

    If decision is EXECUTE_ACTION (singular), ensure response.action is set
    and response.actions is None.
    If decision is EXECUTE_ACTIONS (plural), ensure response.actions is set
    and response.action is None.

    Additionally, if EXECUTE_ACTIONS contains exactly 1 action, downgrade
    to EXECUTE_ACTION for simpler downstream handling.
    """
    if response.decision == AgentDecision.EXECUTE_ACTIONS:
        if response.actions and len(response.actions) == 1:
            # Downgrade single-element batch to singular
            response = AgentResponse(
                decision=AgentDecision.EXECUTE_ACTION,
                title=response.title,
                thought=response.thought,
                action=response.actions[0],
                actions=None,
                final_answer=response.final_answer,
            )
    return response
```

**Acceptance Criteria:**
- Called at the end of both `_parse_json_response()` and `_parse_native_fc()`
- Single-action `execute_actions` batches automatically downgraded
- No behavioral change for existing `execute_action` responses
- Prevents silent misroute between singular/plural paths
- Unit tests for normalization edge cases

**Dependencies:** None

---

### PF-02-02: Normalize native FC format slips into JSON contract format

**File:** `agent_cli/core/runtime/agents/schema.py`
**Priority:** High
**Estimate:** 3 SP

**Problem:**
In the session, the model emitted a raw native function call at line 131 (`{"type":"tool_call","version":"1.0","payload":{...}}`) instead of the `{title, thought, decision}` contract. The `title` and `thought` audit trail was lost.

**Changes:**
In `parse_and_validate()`, when the response is detected as native FC but protocol mode is `JSON_ONLY`, reconstruct the missing fields:

```python
def parse_and_validate(self, response: LLMResponse) -> AgentResponse:
    # ... existing logic ...

    # If we got a native FC response but expected JSON, reconstruct audit trail
    if result.decision in (AgentDecision.EXECUTE_ACTION, AgentDecision.EXECUTE_ACTIONS):
        if not result.title:
            tool_names = []
            if result.action:
                tool_names = [result.action.tool_name]
            elif result.actions:
                tool_names = [a.tool_name for a in result.actions]
            result = AgentResponse(
                decision=result.decision,
                title=f"Call {', '.join(tool_names)}",  # auto-generated
                thought="[Auto-reconstructed from native function call]",
                action=result.action,
                actions=result.actions,
                final_answer=result.final_answer,
            )
            logger.warning(
                "Native FC format slip detected; reconstructed title/thought "
                "for audit trail: %s",
                result.title,
            )

    return result
```

**Acceptance Criteria:**
- Native FC slips are accepted and executed (no rejection)
- Missing `title` and `thought` fields are auto-generated for audit trail
- Warning logged for diagnostics
- Session history includes the reconstructed fields
- Unit test: native FC response in JSON_ONLY mode → reconstructed AgentResponse

**Dependencies:** None

---

## Story: PF-03 – Structured Error Codes in Tool Results

> **Addresses:** W4 (no machine-readable error classification)

### PF-03-01: Define error code taxonomy

**File:** New file `agent_cli/core/runtime/tools/error_codes.py`
**Priority:** Medium
**Estimate:** 2 SP

**Changes:**
```python
from enum import Enum

class ToolErrorCode(str, Enum):
    """Machine-readable error codes for tool results."""

    # File operations
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    ENCODING_ERROR = "ENCODING_ERROR"

    # Command execution
    COMMAND_TIMEOUT = "COMMAND_TIMEOUT"
    COMMAND_FAILED = "COMMAND_FAILED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    APPROVAL_TIMEOUT = "APPROVAL_TIMEOUT"

    # Validation
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"

    # Content
    OUTPUT_TRUNCATED = "OUTPUT_TRUNCATED"

    # Generic
    INTERNAL_ERROR = "INTERNAL_ERROR"
    UNKNOWN = "UNKNOWN"

    @property
    def retryable(self) -> bool:
        """Whether an error with this code is generally retryable."""
        return self in {
            ToolErrorCode.COMMAND_TIMEOUT,
            ToolErrorCode.APPROVAL_TIMEOUT,
            ToolErrorCode.OUTPUT_TRUNCATED,
        }
```

**Acceptance Criteria:**
- All error codes documented with clear semantics
- `retryable` property guides agent recovery behavior
- Enum string values match what appears in tool result envelopes

**Dependencies:** None

---

### PF-03-02: Integrate error codes into `ToolOutputFormatter`

**Files:**
- `agent_cli/core/runtime/tools/output_formatter.py` → add `error_code` to envelope
- `agent_cli/core/runtime/tools/executor.py` → classify errors on output

**Priority:** Medium
**Estimate:** 3 SP

**Changes to lean envelope format:**
```
[tool_result tool=read_file status=error error_code=FILE_NOT_FOUND retryable=false]
File 'self-ask.md' does not exist.
[/tool_result]
```

**Changes to `ToolExecutor`:**
Map common exceptions to error codes:
```python
except FileNotFoundError:
    error_code = ToolErrorCode.FILE_NOT_FOUND
except PermissionError:
    error_code = ToolErrorCode.PERMISSION_DENIED
except TimeoutError:
    error_code = ToolErrorCode.COMMAND_TIMEOUT
```

**Acceptance Criteria:**
- Error results include `error_code` and `retryable` fields
- Agent can read error codes without parsing free-text messages
- Backward compat: JSON legacy envelope includes `error_code` in payload when available
- Unit tests for each error code classification

**Dependencies:** PF-01-01, PF-03-01

---

## Story: PF-04 – Content Reference System for `write_file`

> **Addresses:** W3 (large content re-emitted inline)

### PF-04-01: Implement session-scoped content store

**File:** New file `agent_cli/core/runtime/agents/content_store.py`
**Priority:** High
**Estimate:** 5 SP

**Problem:**
When the agent calls `write_file`, it must re-emit the full file content inside its decision JSON — even if that content was generated in a previous `notify_user` turn. This doubles output token cost and introduces drift risk.

**Changes:**
```python
import hashlib
from typing import Optional


class ContentStore:
    """Session-scoped store for referenceable content blocks.

    Captures large text outputs (from notify_user, read_file results, etc.)
    and allows the agent to reference them by hash instead of re-emitting.

    Usage in agent decision:
        {"tool": "write_file", "args": {"path": "summary.md", "content_ref": "sha256:abc123..."}}

    The system resolves content_ref to the stored content before executing.
    """

    def __init__(self, max_entries: int = 50) -> None:
        self._store: dict[str, str] = {}  # hash → content
        self._max_entries = max_entries

    def store(self, content: str) -> str:
        """Store content and return its reference hash."""
        content_hash = f"sha256:{hashlib.sha256(content.encode()).hexdigest()[:16]}"
        self._store[content_hash] = content
        if len(self._store) > self._max_entries:
            # Evict oldest entry
            oldest_key = next(iter(self._store))
            del self._store[oldest_key]
        return content_hash

    def resolve(self, ref: str) -> Optional[str]:
        """Resolve a content reference to its stored content."""
        return self._store.get(ref)

    def has(self, ref: str) -> bool:
        return ref in self._store
```

**Acceptance Criteria:**
- Content stored on every `notify_user` message and `read_file` result
- Reference hash is deterministic (SHA-256 prefix)
- Store bounded by `max_entries` with LRU-like eviction
- Thread-safe for concurrent access
- Unit tests for store/resolve/eviction

**Dependencies:** None

---

### PF-04-02: Integrate content references into `ToolExecutor`

**File:** `agent_cli/core/runtime/tools/executor.py`
**Priority:** High
**Estimate:** 3 SP

**Changes:**
Before executing a tool, check if any argument contains a `content_ref` pattern and resolve it:

```python
async def execute(self, tool_name: str, arguments: dict, ...) -> ToolResult:
    # Resolve content references in arguments
    resolved_args = self._resolve_content_refs(arguments)
    # ... existing execution logic with resolved_args ...

def _resolve_content_refs(self, args: dict) -> dict:
    """Replace content_ref values with stored content."""
    resolved = {}
    for key, value in args.items():
        if isinstance(value, str) and value.startswith("sha256:"):
            content = self._content_store.resolve(value)
            if content is not None:
                resolved[key] = content
            else:
                resolved[key] = value  # pass through if not found
        else:
            resolved[key] = value
    return resolved
```

**Acceptance Criteria:**
- `content_ref` in `write_file.args.content` resolved before execution
- If reference not found, pass through as literal string (graceful degradation)
- Logging when content reference is resolved: hash, size, tool name
- Unit test: write_file with content_ref → correct file content written

**Dependencies:** PF-04-01

---

### PF-04-03: Instruct agent about content references in output format prompts

**Files:**
- `agent_cli/data/prompts/output_format_multi.txt`
- `agent_cli/data/prompts/output_format.txt`
- `agent_cli/data/prompts/output_format_native.txt`
- `agent_cli/data/prompts/output_format_multi_native.txt`

**Priority:** Medium
**Estimate:** 2 SP

**Changes:**
Add a section to all output format prompts:

```
## Content References
When writing content that you already generated in a previous `notify_user` message,
you may use a content reference instead of re-emitting the full text:
  {"tool": "write_file", "args": {"path": "file.md", "content_ref": "<hash>"}}

The system will tell you the content hash when storing large outputs.
Only use content_ref if the system has provided the hash.
```

**Acceptance Criteria:**
- All 4 output format templates updated
- Instructions are clear and concise
- Agent can use literal `content` or `content_ref` — both work

**Dependencies:** PF-04-01, PF-04-02

---

## Story: PF-05 – Enriched Truncation and File Metadata

> **Addresses:** W5 (truncation recovery lacks metadata), W12 (file content lacks structure)

### PF-05-01: Add file metadata to `read_file` tool results

**File:** `agent_cli/core/runtime/tools/file_tools.py`
**Priority:** Medium
**Estimate:** 3 SP

**Changes:**
When `read_file` returns content, include structural metadata in the output:

```python
async def _execute(self, arguments: dict) -> ToolResult:
    # ... existing logic ...

    # Prepend file metadata header
    meta_header = (
        f"File: {path} | Lines: {total_lines} | "
        f"Size: {file_size_bytes} bytes | Encoding: {encoding}"
    )
    if start_line or end_line:
        meta_header += f" | Showing: {start_line}-{end_line}"

    output = f"{meta_header}\n{content}"
    # ...
```

**Acceptance Criteria:**
- `total_lines`, `file_size_bytes`, and `encoding` included in every `read_file` result
- When reading a line range, `Showing: X-Y of N` also included
- Metadata is a single plain-text header line (not JSON-escaped)
- Agent can use `total_lines` to plan subsequent reads without guessing
- Backward compat: metadata header is additive — existing parsing still works

**Dependencies:** None

---

### PF-05-02: Include `total_lines` and `total_chars` in truncation metadata

**File:** `agent_cli/core/runtime/tools/output_formatter.py`
**Priority:** Medium
**Estimate:** 2 SP

**Changes:**
When truncating, include recovery planning data in the envelope header:

```python
# In lean envelope format:
# [tool_result tool=read_file status=success truncated_chars=5061 total_chars=10200 total_lines=374]
# ... content (head + tail) ...
# [/tool_result]

# In _to_lean_envelope():
if truncated and total_chars:
    meta_parts.append(f"total_chars={total_chars}")
if truncated and total_lines:
    meta_parts.append(f"total_lines={total_lines}")
```

Also add optional `total_chars` and `total_lines` parameters to `format()`:

```python
def format(
    self,
    tool_name: str,
    raw_output: str,
    success: bool = True,
    *,
    task_id: str = "",
    native_call_id: str = "",
    action_id: str = "",
    total_chars: int = 0,      # NEW
    total_lines: int = 0,      # NEW
) -> str:
```

**Acceptance Criteria:**
- Truncated results include `total_chars` and `total_lines` in metadata
- Agent can compute optimal read ranges from truncation metadata alone
- Non-truncated results omit these fields (no noise)
- Expected reduction: 3-turn file recovery → 1-2 turns

**Dependencies:** PF-01-01

---

## Story: PF-06 – Enrich `reflect` Feedback and Add Budget Awareness

> **Addresses:** W6 (`reflect` system response is a no-op), W10 (no budget visibility)

### PF-06-01: Enrich `reflect` system response with state summary

**File:** `agent_cli/core/runtime/agents/base.py`
**Priority:** Medium
**Estimate:** 3 SP

**Problem:**
The current `reflect` response is the static string: `"Reasoning noted. Continue planning or execute an action."` This provides no signal about reflect cycle count, remaining budget, or memory state.

**Changes:**
In `handle_task()`, replace the static reflect response:

```python
case AgentDecision.REFLECT:
    reflect_count += 1
    max_reflects = schema_config.get("max_consecutive_reflects", 3)

    reflect_response = (
        f"Reasoning noted ({reflect_count}/{max_reflects} reflects used). "
    )
    if reflect_count >= max_reflects - 1:
        reflect_response += "You must act or respond on your next turn. "

    # Optionally append resource state
    if self._resource_tracker:
        reflect_response += self._resource_tracker.summary()

    reflect_response += "Continue planning or execute an action."

    _append_message(
        {"role": "system", "content": reflect_response},
        track_for_session=True,
    )
```

**Acceptance Criteria:**
- Reflect response includes cycle count: `"(1/3 reflects used)"`
- Warning when approaching limit: `"You must act or respond on your next turn."`
- Resource summary appended when tracker is available
- Static fallback if tracker not available
- Unit test: verify escalating reflect messages

**Dependencies:** None

---

### PF-06-02: Add resource tracking and budget injection

**File:** New file `agent_cli/core/runtime/agents/resource_tracker.py`
**Priority:** Medium
**Estimate:** 3 SP

**Changes:**
```python
class ResourceTracker:
    """Tracks token usage and cost for budget-aware agent behavior."""

    def __init__(
        self,
        context_limit: int = 128_000,
        cost_budget: float | None = None,
    ) -> None:
        self.context_limit = context_limit
        self.cost_budget = cost_budget
        self.tokens_used = 0
        self.session_cost = 0.0
        self.turn_count = 0

    def update(self, input_tokens: int, output_tokens: int, cost: float) -> None:
        self.tokens_used += input_tokens + output_tokens
        self.session_cost += cost
        self.turn_count += 1

    def summary(self) -> str:
        parts = [f"Turn {self.turn_count}"]
        pct = (self.tokens_used / self.context_limit * 100) if self.context_limit else 0
        parts.append(f"context ~{pct:.0f}% used")
        if self.cost_budget:
            parts.append(f"cost ${self.session_cost:.4f}/${self.cost_budget:.2f}")
        return f"[{', '.join(parts)}] "
```

**Acceptance Criteria:**
- Updated after every LLM call via `on_llm_response()` hook
- Summary injected into reflect responses and periodic system messages
- Cost budget optional — omit when not configured
- No injection when tracker has no data yet

**Dependencies:** PF-06-01

---

## Story: PF-07 – Explicit Batch Result Grouping

> **Addresses:** W7 (parallel results not explicitly grouped)

### PF-07-01: Add `batch_id` to multi-action tool result envelopes

**File:** `agent_cli/core/runtime/tools/output_formatter.py`
**Priority:** Medium
**Estimate:** 2 SP

**Changes:**
When formatting results from a `BatchExecutor` run, include a shared `batch_id`:

```python
# In lean envelope:
# [tool_result tool=read_file status=success action_id=act_0 batch_id=batch_abc123]

# In _to_lean_envelope():
if batch_id:
    meta_parts.append(f"batch_id={batch_id}")
```

The `batch_id` is generated once per `execute_batch()` call and passed to all result formatters in that batch.

**Acceptance Criteria:**
- All results from the same `execute_batch()` call share the same `batch_id`
- Single-action results have no `batch_id` (or empty)
- `batch_id` is a short unique identifier (e.g., `batch_{uuid4_hex[:8]}`)
- Agent and downstream systems can reliably group correlated results

**Dependencies:** PF-01-01

---

## Story: PF-08 – `notify_user` Intent Subtyping

> **Addresses:** W8 (`notify_user` has no intent classification)

### PF-08-01: Add optional `intent` field to `notify_user` decisions

**Files:**
- `agent_cli/core/runtime/agents/parsers.py` → add `intent` to `AgentResponse`
- `agent_cli/core/runtime/agents/schema.py` → parse `intent` field
- `agent_cli/data/prompts/output_format*.txt` → document `intent` as optional

**Priority:** Low
**Estimate:** 3 SP

**Changes to schema contract:**
```json
{
  "title": "...",
  "thought": "...",
  "decision": {
    "type": "notify_user",
    "message": "...",
    "intent": "confirmation | report | error_explanation | question_answer"
  }
}
```

**Parsing changes:**
```python
# In _parse_json_response():
if decision_type == "notify_user":
    intent = decision.get("intent", "")  # optional, defaults to empty
    # ... existing logic ...
```

**Acceptance Criteria:**
- `intent` is fully optional — omitting it changes nothing
- When present, stored in `AgentResponse` for downstream rendering
- Valid intents: `confirmation`, `report`, `error_explanation`, `question_answer` (not enforced — free string)
- TUI/UI can use intent to choose rendering style (e.g., brief toast vs. full document)
- Unit test: parse with and without intent field

**Dependencies:** None

---

## Story: PF-09 – Make `title` Field Optional

> **Addresses:** W9 (title overhead)

### PF-09-01: Make `title` optional in schema validation with auto-generation fallback

**File:** `agent_cli/core/runtime/agents/schema.py`
**Priority:** Low
**Estimate:** 2 SP

**Changes:**
```python
# In _parse_json_response():
title = data.get("title", "")
if not title:
    # Auto-generate from first words of thought
    thought = data.get("thought", "")
    title = " ".join(thought.split()[:5]) + "..." if thought else "Untitled"
    logger.debug("Title auto-generated: %s", title)
```

**Also update `schema.json`:**
```json
{
  "title": {
    "min_words": 0,
    "max_words": 15,
    "required": false
  }
}
```

**Acceptance Criteria:**
- Missing `title` no longer causes schema validation error
- Auto-generated title derived from first 5 words of `thought`
- Explicitly provided titles still validated for max length
- TUI/session preview still has a title (never empty)
- Backward compat: existing responses with titles still work identically

**Dependencies:** None

---

## Sprint Plan

### Sprint 1 – Token Efficiency (Highest Impact)
| Task | SP | Description |
|------|----|-------------|
| PF-01-01 | 5 | Lean envelope format in `ToolOutputFormatter` |
| PF-01-02 | 3 | SchemaValidator lean envelope parsing |
| PF-01-03 | 1 | Config flag in `tools.json` |
| PF-02-01 | 5 | Internal normalization of action dispatch |
| PF-02-02 | 3 | Native FC format slip normalization |
| **Total** | **17** | |

### Sprint 1 Kickoff Package (Ready)

**Sprint goal**
Ship the lean tool result envelope and action schema normalization for ≥15% token reduction, with zero behavioral change when `lean_envelope` is disabled.

**Execution order (recommended)**
1. PF-01-01: `ToolOutputFormatter._to_lean_envelope()` in `agent_cli/core/runtime/tools/output_formatter.py`
2. PF-01-03: Config flag in `agent_cli/data/tools.json`
3. PF-01-02: Lean envelope parsing in `agent_cli/core/runtime/agents/schema.py`
4. PF-02-01 + PF-02-02: Schema normalization in `agent_cli/core/runtime/agents/schema.py`
5. Tests and backward-compat verification

**Definition of done for Sprint 1**
- Lean envelope is default format for new sessions
- Legacy JSON envelope accepted for old session replay
- `execute_action`/`execute_actions` internally normalized
- Native FC slips produce reconstructed audit trail
- All existing tests pass unchanged

**Sprint 1 test suite**
- `python -m pytest dev/tests/tools/ dev/tests/agent/test_schema.py dev/tests/core/ -q`

**Tracking artifact**
- Detailed checklist: `dev/implementation/in_progress/protocol_format_sprint1_prep.md`

### Sprint 2 – Error Recovery & Content Efficiency
| Task | SP | Description |
|------|----|-------------|
| PF-03-01 | 2 | Error code taxonomy |
| PF-03-02 | 3 | Error codes in output formatter + executor |
| PF-04-01 | 5 | Session-scoped content store |
| PF-04-02 | 3 | Content reference resolution in executor |
| PF-05-01 | 3 | File metadata in `read_file` results |
| PF-05-02 | 2 | Truncation planning metadata |
| **Total** | **18** | |

### Sprint 3 – Agent Intelligence & Rendering
| Task | SP | Description |
|------|----|-------------|
| PF-04-03 | 2 | Content reference documentation in prompts |
| PF-06-01 | 3 | Enriched `reflect` system response |
| PF-06-02 | 3 | Resource tracker for budget awareness |
| PF-07-01 | 2 | Batch result grouping via `batch_id` |
| PF-08-01 | 3 | `notify_user` intent subtyping |
| PF-09-01 | 2 | Optional `title` with auto-generation |
| **Total** | **15** | |

### Sprint 4 – Testing & Polish
| Task | SP | Description |
|------|----|-------------|
| PF-T-01 | 3 | Unit tests: lean envelope format + parsing |
| PF-T-02 | 3 | Unit tests: error code classification |
| PF-T-03 | 3 | Unit tests: content store + reference resolution |
| PF-T-04 | 2 | Unit tests: truncation metadata + file metadata |
| PF-T-05 | 2 | Unit tests: reflect enrichment + resource tracker |
| PF-T-06 | 3 | Integration test: full session with all improvements |
| PF-T-07 | 2 | Regression test: old session replay with new code |
| **Total** | **18** | |

### Sprint 5 – Release & Documentation
| Task | SP | Description |
|------|----|-------------|
| PF-R-01 | 2 | Developer docs: protocol format v2 specification |
| PF-R-02 | 1 | Migration guide: v1 → v2 envelope format |
| PF-R-03 | 2 | Canary rollout plan |
| **Total** | **5** | |

---

## Dependency Graph

```
PF-01-01 (lean envelope)
    │
    ├── PF-01-02 (schema parsing) ◄── PF-01-03 (config flag)
    │       │
    │       ├── PF-03-02 (error codes in envelope) ◄── PF-03-01 (error taxonomy)
    │       ├── PF-05-02 (truncation metadata)
    │       └── PF-07-01 (batch_id grouping)
    │
PF-02-01 (action normalization)
PF-02-02 (native FC normalization)
    │
PF-04-01 (content store)
    ├── PF-04-02 (executor integration)
    │       └── PF-04-03 (prompt docs)
    │
PF-05-01 (file metadata) ── standalone
    │
PF-06-01 (reflect enrichment)
    └── PF-06-02 (resource tracker)
    │
PF-08-01 (notify_user intent) ── standalone
PF-09-01 (optional title) ── standalone
    │
    ▼
PF-T-* (tests) ── depend on respective stories
    ▼
PF-R-* (release) ── depend on all tests
```

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Lean envelope format confuses models expecting JSON tool results | PF-01-02: dual-format parser; can revert to JSON via config flag (PF-01-03) |
| Content reference hashes create security risk (hash collision) | PF-04-01: SHA-256 with 16-char prefix → collision probability negligible; session-scoped only |
| Prompt changes for content references increase prompt token count | PF-04-03: minimal 3-line instruction; net savings from avoiding re-emission dwarf prompt cost |
| Error code taxonomy is incomplete for future tools | PF-03-01: `UNKNOWN` fallback enum; taxonomy extensible without breaking changes |
| Reflect enrichment adds tokens to every reflect turn | PF-06-01: summary is ≤30 tokens; reflects are rare (max 3/task); net gain from reduced pointless reflects |
| Old sessions with JSON envelopes break on replay | PF-01-02: dual-format parser ensures both old and new formats work |

---

## Definition of Done (Epic-Level)

- [ ] Feature flag `lean_envelope` default-on merged to main
- [ ] All PF-T-* tests passing in CI
- [ ] No regressions in existing test suites (single-action and multi-action)
- [ ] Old session replay verified with new parser (PF-T-07)
- [ ] Token usage reduced ≥15% in benchmark sessions
- [ ] Truncation recovery reduced from 3 turns to ≤2 turns in benchmark
- [ ] Canary rollout completed with stable metrics
- [ ] Protocol v2 specification and migration guide published
