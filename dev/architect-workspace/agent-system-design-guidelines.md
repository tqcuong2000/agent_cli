# Agent-System Design Guidelines
> Communication & Prompting Reference for CLI Agent Projects

---

## Table of Contents

1. [JSON API Communication](#1-json-api-communication)
   - 1.1 [Message Envelope Schema](#11-message-envelope-schema)
   - 1.2 [Versioning](#12-versioning)
   - 1.3 [Error Handling](#13-error-handling)
   - 1.4 [Agent-Specific Message Patterns](#14-agent-specific-message-patterns)
   - 1.5 [Common Pitfalls](#15-common-pitfalls)
   - 1.6 [Tooling Tips](#16-tooling-tips)
2. [Prompting Design](#2-prompting-design)
   - 2.1 [Prompt Architecture](#21-prompt-architecture)
   - 2.2 [System Prompt Design](#22-system-prompt-design)
   - 2.3 [Instruction Design](#23-instruction-design)
   - 2.4 [Context Injection](#24-context-injection)
   - 2.5 [Tool Definitions](#25-tool-definitions)
   - 2.6 [Common Pitfalls](#26-common-pitfalls)
   - 2.7 [Agent-Specific Patterns](#27-agent-specific-patterns)
3. [Cross-Cutting Principles](#3-cross-cutting-principles)

---

## 1. JSON API Communication

### 1.1 Message Envelope Schema

Every message — in both directions — must share a consistent envelope structure. This enables generic routing, logging, and error handling without needing to inspect payload internals.

**Standard envelope:**

```json
{
  "id": "msg_abc123",
  "type": "action_request",
  "version": "1.0",
  "timestamp": "2026-03-03T10:00:00Z",
  "payload": { },
  "metadata": { }
}
```

| Field | Required | Purpose |
|---|---|---|
| `id` | ✅ | Unique message ID (UUID recommended). Used for correlation, deduplication, and tracing. |
| `type` | ✅ | Discriminator string. Drives routing and handler selection. |
| `version` | ✅ | Schema version. Include from day one, even as `"1.0"`. |
| `timestamp` | ✅ | ISO 8601 UTC. Required for ordering and debugging. |
| `payload` | ✅ | The actual message content. Shape varies by `type`. |
| `metadata` | ➖ | Optional. Tracing, session IDs, tags, environment info. |

**Rules:**
- The `type` field must always be a known, documented string — not a free-form label.
- Never put critical routing information only inside `payload`. The envelope must be self-sufficient for dispatch.
- Use strict types throughout: `123` is not `"123"`. Avoid type ambiguity at all message boundaries.

---

### 1.2 Versioning

Schema evolution is inevitable. Design for it from the start.

**Backward-compatible (safe) changes:**
- Adding new optional fields to a message
- Adding new values to an enum (with a documented unknown-value fallback)
- Adding new message `type` values

**Breaking changes (require version bump):**
- Renaming or removing fields
- Changing the type of an existing field
- Changing the semantic meaning of a field, even if the structure stays the same

**Recommendations:**
- Carry a `version` field at the message level.
- Never silently change the meaning of a field. Deprecate it, introduce a replacement, then remove the old field after a transition window.
- Consumers must ignore unknown fields gracefully — this is the single rule that makes additive changes safe.

---

### 1.3 Error Handling

Define one canonical error shape and use it everywhere, without exception.

```json
{
  "id": "msg_abc123",
  "type": "error",
  "version": "1.0",
  "timestamp": "2026-03-03T10:00:00Z",
  "payload": {
    "code": "TOOL_NOT_FOUND",
    "message": "The requested tool 'bash' is not registered",
    "details": { "tool_name": "bash" },
    "recoverable": true
  }
}
```

| Field | Purpose |
|---|---|
| `code` | Machine-readable string. The agent must be able to branch on this. |
| `message` | Human-readable description. For logs and debugging only. |
| `details` | Structured extra context relevant to the specific error. |
| `recoverable` | Boolean. Tells the agent whether to retry, escalate, or abort. |

**Rules:**
- Never put decision-critical information only in `message`. The agent reads `code`, not prose.
- Every possible error condition should have a documented `code` constant.
- `recoverable` is the minimum viable signal for agent error recovery logic. Extend it (e.g., `retry_after_ms`) as needed.

---

### 1.4 Agent-Specific Message Patterns

A typical agent-system exchange follows a tool-call loop:

```json
// Agent → System: request a tool
{
  "id": "msg_001",
  "type": "tool_call",
  "payload": {
    "tool": "read_file",
    "args": { "path": "./src/index.ts" }
  }
}

// System → Agent: return result
{
  "id": "msg_002",
  "type": "tool_result",
  "payload": {
    "ref_id": "msg_001",
    "status": "ok",
    "output": "..."
  }
}

// Agent → System: signal completion
{
  "id": "msg_003",
  "type": "completion",
  "payload": {
    "result": "...",
    "reasoning": "..."
  }
}
```

**Additional message types to plan for:**

| Type | Direction | Purpose |
|---|---|---|
| `heartbeat` | Agent → System | Keep-alive signal for long-running tasks |
| `cancel` | System → Agent | Interrupt a running action |
| `clarify` | Agent → System | Agent signals it needs more information |
| `status_update` | Agent → System | Progress reporting for multi-step tasks |

**Design decisions to make early:**

- **Request-response vs. streaming:** For token-level streaming, use Newline-Delimited JSON (NDJSON) — one JSON object per line over a stream. For atomic results, standard request-response is simpler.
- **Idempotency:** If a message is re-delivered (network retry), will processing it twice cause harm? Use `id` fields to detect and skip duplicates.
- **Timeouts:** Document the expected response latency contract. The agent and system must agree on when silence means failure.

---

### 1.5 Common Pitfalls

| Pitfall | Consequence | Remedy |
|---|---|---|
| **Null vs. missing field** | `{"key": null}` and `{}` are treated differently by parsers | Pick one convention; prefer explicit nulls |
| **Unbounded payloads** | Large tool outputs can exhaust memory or timeouts | Define a max payload size; paginate or truncate |
| **Timestamp format inconsistency** | Ordering bugs, timezone confusion | Always use ISO 8601 UTC. Never mix epoch seconds and milliseconds |
| **Floating point for exact values** | `0.1 + 0.2 !== 0.3` | Use strings or scaled integers for money, coordinates, or exact decimals |
| **No schema validation** | Silent type drift across versions | Validate at both send and receive with JSON Schema, Zod, Pydantic, etc. |
| **Circular references** | Serialization crash | Avoid serializing raw object graphs; map to safe data structures first |

---

### 1.6 Tooling Tips

- **Schema-first:** Write JSON Schema definitions for every message type before writing handler code. Use them to generate validators and documentation.
- **Log everything:** Log every message with its `id`, `type`, `timestamp`, and direction. This enables replay-based debugging.
- **Pretty-print for logs, compact for transport:** Keep them separate. Compact on the wire; human-readable in the log file.
- **Contract testing:** Test that the system correctly handles malformed, missing-field, and wrong-type messages. Agents will produce unexpected output; your parser must not crash.

---

## 2. Prompting Design

### 2.1 Prompt Architecture

A well-structured prompt has four distinct layers, each with a separate responsibility. Do not mix them.

```
┌─────────────────────────────────────────────────────┐
│  SYSTEM PROMPT                                      │
│  Identity, capabilities, hard rules, output format  │
├─────────────────────────────────────────────────────┤
│  CONTEXT INJECTION                                  │
│  Dynamic state: memory, workspace, prior results    │
├─────────────────────────────────────────────────────┤
│  TASK / INSTRUCTION                                 │
│  The specific action to perform right now           │
├─────────────────────────────────────────────────────┤
│  HISTORY / SCRATCHPAD                               │
│  Prior turns, tool results, reasoning trace         │
└─────────────────────────────────────────────────────┘
```

Each layer should be clearly labeled with delimiters (e.g., XML tags, markdown headers, or named sections) so the agent can distinguish instruction from data from history.

---

### 2.2 System Prompt Design

The system prompt is the behavioral contract for the agent. It is the highest-priority prompt layer.

**Structure it in three blocks:**

**Block 1 — Identity and Scope**
```
You are a CLI agent for [project name].
Your responsibility is to [narrow, specific scope].
You have access to these tools: [list].
You do NOT have access to [explicit exclusions].
```

**Block 2 — Output Format (with example)**
```
Always respond with a single JSON object in this exact shape:
{
  "thought": "your step-by-step reasoning",
  "action": "tool_name | done | clarify | error",
  "args": { }
}
No other text. No markdown. No preamble. No explanation outside this structure.
```

**Block 3 — Hard Rules**
```
- You must not modify files outside the ./src directory.
- You must not make more than 10 tool calls per task.
- If you are uncertain, use action: "clarify" — do not guess.
```

**Placement principle:** Put the most critical rules at the **top** and **bottom** of the system prompt. LLMs exhibit primacy and recency bias — the middle of a long prompt receives less attention.

**Tone principle:** Use "must" and "must not" for hard constraints. Use "prefer" and "avoid" for soft guidance. Never phrase a hard constraint as a suggestion.

---

### 2.3 Instruction Design

The task instruction tells the agent what to accomplish right now.

**Be concrete. Define what "done" looks like:**

```
❌  Vague:    "Analyze the codebase"

✅  Concrete: "List all .ts files in ./src that import from 'axios'.
               Output a JSON array of their relative paths.
               Stop when the list is complete."
```

**Always provide an exit condition.** An agent without a stopping criterion will loop or over-produce. Every instruction must answer: *how does the agent know it is finished?*

**Separate goal from method.** Over-specifying steps removes the agent's ability to adapt and recover from unexpected states:

```
❌  Over-specified: "First read config.json, then parse it,
                    then extract the 'database' key..."

✅  Goal-oriented:  "Return the value of the 'database' key
                    from config.json."
```

---

### 2.4 Context Injection

The system is responsible for providing all state the agent needs, since the agent has no memory between calls.

**Inject only what is relevant.** Irrelevant context is not neutral — it adds noise that degrades performance. Before injecting a piece of context, ask: *does the agent need this to complete the current task?*

**Label injected context with clear delimiters:**

```
<workspace_state>
Current directory: /project/src
Modified files this session: ["index.ts", "utils.ts"]
Last completed step: "Identified all entry points"
</workspace_state>

<task_history_summary>
The agent has read 3 files and identified the main router at router.ts.
</task_history_summary>
```

**Ordering principle:** Place the most task-relevant context closest to the instruction. Position affects attention.

**Summarize long histories.** A concise 5-line summary of 20 prior turns outperforms appending all 20 turns raw. Raw history grows unboundedly and eventually overwhelms the instruction.

---

### 2.5 Tool Definitions

A tool definition is itself a prompt. The `description` field is what teaches the agent when and how to use a tool correctly.

```json
{
  "name": "read_file",
  "description": "Read the full contents of a file. Use this to inspect source code, config files, or data files. Do NOT use this to list directory contents — use list_dir for that.",
  "parameters": {
    "path": {
      "type": "string",
      "description": "Path to the file, relative to the project root. Must include file extension."
    }
  }
}
```

**Rules:**
- The `description` must state *when* to use the tool, not just *what* it does.
- Include negative guidance ("do NOT use for...") for tools that are easily misused.
- If two tools serve similar purposes, explicitly distinguish them in their descriptions.
- Parameter `description` fields are required for any non-obvious parameter.
- Keep tool names short, lowercase, and verb-first: `read_file`, `run_command`, `search_code`.

---

### 2.6 Common Pitfalls

| Pitfall | Consequence | Remedy |
|---|---|---|
| **Prompt injection via tool output** | Malicious file content overwrites instructions | Wrap all tool output in labeled delimiters; treat it as untrusted data |
| **Ambiguous references** | Agent misinterprets "it", "that", "the previous result" | Be explicit in every turn; avoid pronouns across injected context |
| **Instruction drift** | Early system prompt rules are "forgotten" in long sessions | Re-inject critical rules into context for long-running tasks |
| **Vague stopping condition** | Agent loops or over-produces | Every instruction must have an explicit exit criterion |
| **Reward hacking** | Agent satisfies the literal instruction, not the intent | Write goal-oriented instructions; test with adversarial inputs |
| **Contradictory rules** | Agent behaves unpredictably when rules conflict | Audit system prompts for contradictions; fewer rules beat more rules |
| **One monolithic system prompt** | Hard to maintain; sections contradict each other | Modularize: identity, tool rules, and format rules assembled dynamically |

---

### 2.7 Agent-Specific Patterns

**ReAct Pattern (Reasoning + Acting)**

Interleave explicit reasoning with tool calls. This is the most reliable pattern for CLI agents:

```
thought: "I need to find where config is loaded. I'll search for 'loadConfig'."
action: search_code
args: { "query": "loadConfig" }

--- system injects result ---

thought: "Found in bootstrap.ts line 42. I'll read that file next."
action: read_file
args: { "path": "./bootstrap.ts" }
```

The `thought` field is not cosmetic — it forces the agent to reason before committing to an action, which measurably improves output quality.

**Explicit Uncertainty Signaling**

Prompt the agent to surface uncertainty rather than guess:

```
If you do not have enough information to complete the task safely,
respond with action: "clarify" and state exactly what you need.
Do not attempt to proceed with incomplete information.
```

**Confidence Tiering**

For high-stakes actions (file writes, command execution), consider a two-step confirmation pattern:

```
// Step 1 — Agent proposes
{ "action": "propose", "proposed_action": "delete_file", "args": { "path": "./old.ts" }, "reason": "..." }

// System confirms
{ "type": "confirm", "ref_id": "...", "approved": true }

// Step 2 — Agent executes
{ "action": "delete_file", "args": { "path": "./old.ts" } }
```

---

## 3. Cross-Cutting Principles

These principles apply to both the JSON communication layer and the prompting layer.

### Treat Both as Code

- **Version-control your prompts** alongside your code. They are part of the system specification.
- **Changelog prompt changes** the same way you would changelog an API change — note what changed and why.
- **Write evals (tests) for prompts.** Maintain a set of input → expected output pairs and run them on every prompt change before deploying.

### Design for Failure

- The agent will produce unexpected output. The system must not crash on malformed JSON.
- The system will return unexpected errors. The agent must have a documented recovery path for every error `code`.
- Long-running tasks will be interrupted. Both sides need cancellation and resume semantics.

### Explicit Over Implicit

- In JSON: prefer explicit `null` over missing fields. Prefer typed values over stringly-typed ones.
- In prompts: prefer "must not" over "avoid." Prefer concrete exit conditions over open-ended tasks.
- If either side has to *infer* something critical, that is a design gap.

### Minimize Surface Area

- **JSON:** Only include fields that serve a documented purpose. Every field you add becomes a field you must maintain and version.
- **Prompts:** Only inject context the agent actually needs. Every token you add is a token of attention diluted from the instruction.

### Contracts Are Bilateral

The JSON schema and the system prompt together form the communication contract between system and agent. A change to either side is a contract change. Apply the same discipline to both:

1. Define it explicitly
2. Version it
3. Test it
4. Change it deliberately

---

*This document is a living reference. Update it as the system design evolves.*
