# In-Depth Evaluation: Agent ↔ System Communication Format

## Session Overview

| Attribute | Value |
|-----------|-------|
| **Session ID** | `6ded69d1-a2dd-44de-b595-af112ac4bb80` |
| **Model** | Kimi-K2.5 |
| **Total Cost** | $0.052638 |
| **Duration** | ~11 minutes (15:28 → 15:39 UTC) |
| **Tasks** | 6 task IDs across ~8 user turns |
| **Format Variant** | `output_format_multi` (JSON-only, multi-action enabled) |

---

## 1. Architecture of the Communication Protocol

The system implements a **structured JSON protocol** layered on top of the standard `user / assistant / tool` role triplet. Both sides — the agent (LLM) and the system (runtime) — speak JSON exclusively, with no free-form prose permitted in the assistant's responses.

### 1.1 Agent → System (Assistant Messages)

Every assistant message is a single JSON object with three mandatory fields:

```json
{
  "title": "short title (2-15 words)",
  "thought": "the agent's chain-of-thought reasoning",
  "decision": {
    "type": "reflect | execute_action | execute_actions | notify_user | yield",
    "tool": "...",
    "args": {},
    "actions": [],
    "message": "..."
  }
}
```

**Decision types observed in this session:**
- `execute_action` — single tool call (lines 43, 91, 122, 147)
- `execute_actions` — parallel multi-tool calls (lines 23, 51, 79)
- `notify_user` — final answer to user (lines 35, 71, 99, 115, 139, 155)
- `reflect` — internal reasoning without action (line 107)

### 1.2 System → Agent (Tool Results)

Every tool result is wrapped in a **JSON envelope**:

```json
{
  "id": "msg_<uuid_hex>",
  "type": "tool_result",
  "version": "1.0",
  "timestamp": "2026-03-05T15:29:44Z",
  "payload": {
    "tool": "ask_user",
    "status": "success | error",
    "truncated": false,
    "truncated_chars": 0,
    "output": "..."
  },
  "metadata": {
    "task_id": "...",
    "action_id": "act_0",
    "native_call_id": "..."
  }
}
```

### 1.3 System → Agent (System Messages)

Minimal, directive system messages like:
```
"Reasoning noted. Continue planning or execute an action."
```

---

## 2. Strengths

### ✅ 2.1 Structured, Machine-Parseable Protocol

The strict JSON contract makes parsing deterministic. Every response has a predictable shape that can be validated with a JSON Schema. The [SchemaValidator](file:///x:/agent_cli/agent_cli/core/runtime/agents/schema.py) class has robust parsing including:
- Balanced-brace extraction for malformed JSON
- Repair candidates for common model artifacts
- Unbalanced brace closing for truncated outputs

**Why this matters**: Unlike free-text protocols (e.g., `THOUGHT: ... ACTION: ...` with regex parsing), this JSON approach eliminates an entire class of parsing ambiguities.

### ✅ 2.2 Explicit Chain-of-Thought via `title` + `thought`

The `title` field acts as a **headline** for the current step, while `thought` contains the full reasoning. This separation serves multiple purposes:
- **TUI streaming**: The title can be rendered immediately as a progress indicator
- **Session preview**: The `last_message_preview` in session metadata comes from this
- **Auditability**: Every decision is accompanied by a rationale

The session excerpt demonstrates excellent use of this pattern — e.g., on the `reflect` decision (line 107), the agent writes two full paragraphs of structured reasoning about framework selection before concluding.

### ✅ 2.3 Multi-Action Support with Parallel Execution

The `execute_actions` decision type allows batching independent operations:

```json
"decision": {
  "type": "execute_actions",
  "actions": [
    {"tool": "read_file", "args": {"path": "react-framework.md"}},
    {"tool": "read_file", "args": {"path": "agent-frameworks.md"}}
  ]
}
```

This is **well-designed** because:
- It reduces round-trip latency (2 reads in 1 turn vs. 2 turns)
- The system correctly returns **separate** tool messages for each action (lines 27-31)
- Each action gets a unique `action_id` (`act_0`, `act_1`) for correlation
- The constraint that [ask_user](file:///x:/agent_cli/agent_cli/core/runtime/agents/react_loop.py#275-278) must be the only action prevents ambiguous user interactions

### ✅ 2.4 Truncation Awareness is First-Class

The `truncated` and `truncated_chars` fields in the tool result envelope let the agent know **definitively** whether it received the full output. The session shows this working well:
- First read of `agent-frameworks.md` → `"truncated": true, "truncated_chars": 5061`
- Agent correctly recognizes truncation and reads specific line ranges (lines 51, 63)
- The truncation message includes a recovery hint: `"Use read_file with line range for full content."`

### ✅ 2.5 The `reflect` Decision Type

Having an explicit "think more without acting" primitive is valuable. In the session, the user explicitly requested reflection (line 103), and the agent used it properly:
1. Agent emits `"type": "reflect"` with deep reasoning in `thought`
2. System responds with a continuation prompt: `"Reasoning noted. Continue planning or execute an action."`
3. Agent then delivers the polished answer via `notify_user`

This prevents unnecessary tool calls when the agent needs to organize its thinking.

### ✅ 2.6 The `yield` Decision Type

Having a graceful abort mechanism is architecturally sound. It signals that the agent is stopping but has partial progress to report, which prevents silent failures.

### ✅ 2.7 Tool Result Envelope Metadata

The `task_id` and `action_id` fields create an audit trail connecting requests to responses. This is especially important for multi-action batches where results arrive out of order.

### ✅ 2.8 Clarification Policy via [ask_user](file:///x:/agent_cli/agent_cli/core/runtime/agents/react_loop.py#275-278)

Forcing all questions through a dedicated tool (rather than inline in `notify_user`) is a good design:
- The system can intercept and render the question in a special UI widget
- It provides structured options for the user
- It prevents the agent from ending a task prematurely with a question embedded in the "final answer"

---

## 3. Weaknesses

### ⚠️ 3.1 Tool Results Are Double-Escaped JSON Strings (Critical)

This is the **single biggest problem** in the format. Look at any tool result in the session:

```json
{
  "role": "tool",
  "content": "{\"id\":\"msg_37c59e92...\",\"type\":\"tool_result\",\"version\":\"1.0\",\"timestamp\":\"...\",\"payload\":{\"tool\":\"ask_user\",\"status\":\"success\",\"truncated\":false,\"truncated_chars\":0,\"output\":\"User replied: ...\"},\"metadata\":{\"task_id\":\"...\",\"action_id\":\"act_0\"}}"
}
```

The tool result is a **JSON string containing stringified JSON** inside the `content` field. This means:
- Every `"` inside becomes `\"`, every `\n` becomes `\\n`, every `\` becomes `\\\\`
- The file content inside `output` is **triple-escaped** (original content → JSON string → JSON string again)
- **Token waste**: Each escape character (the `\` before every `"`) consumes a token. For the `agent-frameworks.md` content, this adds hundreds of unnecessary tokens per response.

**Impact**: The [ToolOutputFormatter._to_json_envelope](file:///x:/agent_cli/agent_cli/core/runtime/tools/output_formatter.py#L107-L148) uses `json.dumps()` with `separators=(",", ":")` (no spaces), then this compact JSON is itself stored as the string value of `content`. The model must mentally parse through layers of escaping, which degrades comprehension and increases cost.

**Recommendation**: If the API supports structured tool results (as most modern LLM APIs do), pass the envelope as a native object rather than a serialized string. At minimum, consider using the raw file content directly in `output` and separating the metadata into a different field.

### ⚠️ 3.2 No Error Code / Error Category in Tool Results

Tool errors only report `"status": "error"` with the error text in `output`. There's no machine-readable error classification like:

```json
{
  "status": "error",
  "error_code": "FILE_NOT_FOUND",
  "error_category": "recoverable",
  "output": "File self-ask.md does not exist"
}
```

This means the agent must **parse the error message text** to decide how to recover, which is fragile and model-dependent.

### ⚠️ 3.3 The `title` Field Adds Overhead Without Proportional Value

Every single response requires a `title` field with constraints (`min_words: 2, max_words: 15` per [schema.json](file:///x:/agent_cli/agent_cli/data/schema.json)). While useful for UI display, it:
- Adds tokens to every response (especially costly in long sessions)
- Creates a validation failure point (if the title is 1 word or 16 words, the schema rejects)
- Rarely contains information not already in `thought`

In the session, titles like `"Read both framework files"` and `"Provide summaries of both files"` are reasonable but could be auto-generated from the first few words of `thought`.

### ⚠️ 3.4 Inconsistent Schema Between Native FC and JSON-Only Modes

The session shows a **format inconsistency** at line 131:

```json
{"type":"tool_call","version":"1.0","payload":{"tool":"run_command","args":{"command":"python hello_world.py","timeout":10},"action_id":"functions.run_command:0"},"metadata":{"native_call_id":"functions.run_command:0"}}
```

This is a **completely different schema** from the standard `{title, thought, decision}` format. It appears the model switched to a native function-calling format mid-conversation. This indicates:
- The model wasn't fully constrained by the JSON schema prompt
- The system accepted it anyway (since the tool executed successfully)
- But the `title` and `thought` fields were lost, breaking the audit trail

This is likely a model-level issue with Kimi-K2.5 slipping into native tool call format, but the system should either reject or normalize this.

### ⚠️ 3.5 Truncation Recovery Is Inefficient

When `agent-frameworks.md` was truncated, the agent needed **3 additional turns** to read the full file:
1. Turn 1: Read full file → truncated (line 43-47)
2. Turn 2: Read lines 70-150, 151-230 → got parts (lines 51-59)
3. Turn 3: Read lines 231-310 → got remaining (lines 62-67)

The system's truncation at `max_output_length: 5000` characters (with head+tail preservation) means:
- The agent can see the beginning and end but not the middle
- It must guess line ranges for the middle section
- The hint says "Use read_file with line range" but doesn't tell the agent the **total line count** on truncation (only revealed when line-range reads succeed: "Showing lines 70-150 of 374 total lines")

**Recommendation**: Include `total_lines` or `total_chars` in the truncation metadata so the agent can plan reads optimally.

### ⚠️ 3.6 The `reflect` System Response Is Too Terse

When the agent chooses `reflect`, the system responds with only:
```
"Reasoning noted. Continue planning or execute an action."
```

This wastes a turn with minimal guidance. Better alternatives:
- Suppress the system message entirely and just let the agent continue
- Include useful context like `"Reasoning noted. You have used 1/3 reflect turns. Continue."`
- Or: provide a summary of the current working memory state

### ⚠️ 3.7 No Token/Cost Budget Visibility for the Agent

The session cost $0.053, but the agent has **no visibility** into its own token consumption. The tool result envelope carries no token count, and there's no system message like:
```
"Context: 4,200 tokens used of 128,000 limit. Budget: $0.04 of $0.10 spent."
```

This means the agent can't optimize its behavior (e.g., preferring summary over full-content dumps) based on resource awareness.

### ⚠️ 3.8 File Content in `output` Lacks Structure

When file content is returned, it's a flat string with `\\n` escaped newlines:

```
"output": "# The ReAct Framework: Reason and Act\\n\\n## Overview\\nReAct is a paradigm..."
```

There's no metadata about the file itself:
- No `file_size_bytes`
- No `total_lines` (until you use line-range reads)
- No `file_type` or `encoding`
- No `last_modified` timestamp

The agent must infer these properties from the content, which is unreliable.

---

## 4. Comparison to Industry Patterns

| Aspect | This System | OpenAI Assistants API | Anthropic MCP | LangChain/LangGraph |
|--------|-------------|----------------------|---------------|---------------------|
| **Agent output format** | Strict JSON contract | Free text + native FC | Free text + native FC | Flexible (model-driven) |
| **Tool results** | JSON envelope (stringified) | Native structured | Native structured | Model-specific |
| **Multi-action** | `execute_actions` batch | Parallel tool calls | Sequential | Graph edges |
| **Thinking** | `thought` field (forced) | Not structured | `<thinking>` tags | Not structured |
| **Reflection** | Explicit `reflect` type | N/A | N/A | N/A |
| **Truncation handling** | Head+tail + char count | Varies per tool | Varies per tool | Varies |

The explicit `reflect` type and forced `thought` field are **unique** advantages over industry-standard approaches. However, the stringified JSON-in-JSON pattern and lack of native structured results are behind industry best practices.

---

## 5. Specific Recommendations

### 🔧 5.1 De-escape Tool Results (High Priority)

Instead of:
```json
{"role": "tool", "content": "{\"id\":\"msg_...\",\"payload\":{\"output\":\"...\"}}"}
```

Use (if API supports):
```json
{"role": "tool", "content": {"id": "msg_...", "payload": {"output": "..."}}}
```

Or at minimum, separate metadata from content:
```json
{"role": "tool", "tool_meta": {"id": "msg_...", "status": "success", "truncated": false}, "content": "# The ReAct Framework\n\n## Overview\n..."}
```

**Expected savings**: 15-25% token reduction on tool-heavy sessions.

### 🔧 5.2 Add Truncation Planning Data (Medium Priority)

When truncating, include recovery metadata:
```json
{
  "truncated": true,
  "truncated_chars": 5061,
  "total_chars": 10200,
  "total_lines": 374,
  "suggested_ranges": [[1, 120], [121, 250], [251, 374]]
}
```

This would have reduced the agent's 3-turn recovery in this session to 1-2 turns.

### 🔧 5.3 Structured Error Codes (Medium Priority)

Add machine-readable error classification:
```json
{
  "status": "error",
  "error_code": "TRUNCATED_OUTPUT",
  "retryable": true,
  "suggestion": "Use start_line/end_line to read in chunks"
}
```

### 🔧 5.4 Make `title` Optional or Auto-Generated (Low Priority)

Either:
- Make `title` optional (system auto-generates from first N words of `thought`)
- Or generate it server-side after receiving the response

This saves ~5-10 tokens per turn and removes a validation failure point.

### 🔧 5.5 Add Context Budget Awareness (Medium Priority)

Inject periodic system messages with resource state:
```json
{
  "role": "system",
  "content": {
    "type": "resource_update",
    "context_tokens": 4200,
    "context_limit": 128000,
    "session_cost": 0.035,
    "cost_budget": 0.10,
    "turns_remaining_estimate": 15
  }
}
```

### 🔧 5.6 Normalize Native FC Fallbacks (Medium Priority)

When the model emits a native function call instead of the JSON contract (as happened at line 131), the system should:
1. **Accept** the tool call (it did)
2. **Reconstruct** a normalized `{title, thought, decision}` record for the session log
3. **Log a warning** for diagnostics

### 🔧 5.7 Enrich File Tool Metadata (Low Priority)

Include file metadata in read_file results:
```json
{
  "output": "...",
  "file_meta": {
    "total_lines": 374,
    "total_bytes": 10200,
    "encoding": "utf-8",
    "last_modified": "2026-03-05T10:00:00Z"
  }
}
```

---

## 6. Agent Behavioral Observations

Beyond the format itself, the **agent** (Kimi-K2.5) demonstrates several notable behaviors in this session:

### ✅ Good Behaviors
- **Parallel reads**: Correctly batched two independent file reads (line 23)
- **Truncation awareness**: Recognized truncation and read line ranges
- **Verification**: After write+delete, listed directory to confirm (line 91)
- **Memory**: Correctly remembered that `self-ask.md` was deleted earlier when the user asked for it again (line 147)
- **Ask-user with options**: Provided structured options when clarification was needed (line 15)
- **Rich formatting**: Used tables and markdown in `notify_user` messages

### ⚠️ Concerning Behaviors
- **Re-read the same truncated content**: The second `read_file` without line ranges (line 43) returned the same truncated content — the agent should have immediately used line ranges
- **Format slip**: Emitted native FC format at line 131 instead of the JSON contract
- **Trailing comma**: The JSON at line 63 has a trailing comma after the closing `}`, which is invalid JSON — suggests the model sometimes struggles with strict JSON adherence

---

## 7. Summary Verdict

| Category | Rating | Notes |
|----------|--------|-------|
| **Reliability** | ⭐⭐⭐⭐ | Robust schema validation with repair, balanced-brace extraction |
| **Token Efficiency** | ⭐⭐ | Double-escaped JSON, mandatory `title`, verbose envelopes |
| **Expressiveness** | ⭐⭐⭐⭐⭐ | `reflect`, `yield`, `execute_actions`, `ask_user` — rich decision vocabulary |
| **Error Recovery** | ⭐⭐⭐ | Good truncation awareness but no structured error codes |
| **Auditability** | ⭐⭐⭐⭐ | Full chain-of-thought, task_id/action_id correlation |
| **Developer Experience** | ⭐⭐⭐⭐ | Clean separation of concerns, well-documented prompts |

> [!IMPORTANT]
> The protocol is well-designed at the **semantic** level — the decision types, the thinking structure, and the multi-action batching are all superior to most agent frameworks. The main weaknesses are at the **efficiency** level: the double-serialized JSON consumes unnecessary tokens and reduces model comprehension, and the lack of structured error/truncation metadata forces the agent into extra recovery turns.

The single highest-ROI improvement would be **de-escaping tool results** (§5.1), which would simultaneously reduce costs, improve model comprehension, and simplify the codebase.
