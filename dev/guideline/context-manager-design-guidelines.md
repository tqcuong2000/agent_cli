# Context Manager Design Guidelines
> Effective Context Management for CLI Agent Systems

---

## Table of Contents

1. [The Core Problem](#1-the-core-problem)
2. [Mental Model: Layers of Decay](#2-mental-model-layers-of-decay)
3. [The State Object](#3-the-state-object)
4. [The Four Core Operations](#4-the-four-core-operations)
   - 4.1 [Extract](#41-extract)
   - 4.2 [Score](#42-score)
   - 4.3 [Compress](#43-compress)
   - 4.4 [Budget](#44-budget)
5. [The Sliding Window + Summary Pattern](#5-the-sliding-window--summary-pattern)
6. [Handling Tool Results](#6-handling-tool-results)
7. [Failure Modes](#7-failure-modes)
8. [Pre-Call Checklist](#8-pre-call-checklist)
9. [Core Principle](#9-core-principle)

---

## 1. The Core Problem

A context manager must solve a relevance problem: *"what does the agent need to know, right now, to perform this specific step?"*

Getting this wrong in either direction has a direct cost:

| Too Little Context | Too Much Context |
|---|---|
| Agent fills gaps by guessing | Agent attention diluted across irrelevant content |
| Confident but incorrect behavior | Higher token cost per turn |
| Errors that are hard to trace | Agent takes detours or addresses things it wasn't asked |

The goal is not to remember everything — it is to **reconstruct the minimal sufficient context for the next step**, assembled fresh before every agent call.

---

## 2. Mental Model: Layers of Decay

Not all context ages the same way. Design your storage around how quickly each type of information becomes irrelevant.

```
┌──────────────────────────────────────────────────────┐
│  PERMANENT LAYER                                     │
│  Never changes. Identity, rules, tool definitions.   │
│  → Always injected in full. No management needed.    │
├──────────────────────────────────────────────────────┤
│  SESSION LAYER                                       │
│  Stable for the life of a task. Goals, constraints,  │
│  discovered facts, completed steps.                  │
│  → Maintained as a structured state object.          │
├──────────────────────────────────────────────────────┤
│  WORKING MEMORY LAYER                                │
│  Relevant for a few turns. Recent tool results,      │
│  current sub-task, intermediate reasoning.           │
│  → Sliding window. Summarized when it ages out.      │
├──────────────────────────────────────────────────────┤
│  IMMEDIATE LAYER                                     │
│  Relevant for exactly this turn. Current tool        │
│  result, the specific instruction right now.         │
│  → Injected fresh each turn. Never persisted raw.    │
└──────────────────────────────────────────────────────┘
```

**The most common failure:** treating all layers the same — either keeping everything forever (too much) or summarizing everything aggressively (too little).

---

## 3. The State Object

The state object is your primary context primitive. Instead of managing raw conversation history, maintain a structured object that your manager updates after every turn.

```json
{
  "task": {
    "goal": "Refactor all axios calls to use the internal http client",
    "exit_condition": "All .ts files in ./src have been updated",
    "constraints": [
      "Do not modify test files",
      "Preserve existing error handling"
    ]
  },
  "progress": {
    "completed_steps": [
      "Identified 6 files with axios imports",
      "Refactored src/api/user.ts"
    ],
    "remaining": ["src/api/post.ts", "src/api/auth.ts", "...3 more"],
    "current_step": "Refactoring src/api/post.ts"
  },
  "discovered_facts": {
    "http_client_path": "src/lib/http.ts",
    "http_client_interface": "get(url, options?), post(url, body, options?)",
    "pattern_to_replace": "axios.get(url) → httpClient.get(url)"
  },
  "last_result": { }
}
```

**Why this outperforms raw history:**
- Dense with signal, free of noise
- Easy to update incrementally after each turn
- A compact state object (~40 tokens) replaces the equivalent raw history (~2,000 tokens)
- The agent acts on structured facts, not a transcript it must re-interpret

---

## 4. The Four Core Operations

### 4.1 Extract

After every agent turn, extract reusable facts from the raw output before discarding it.

```
Raw tool result:  [500 lines of file content]
        ↓
Extracted fact:   { "http_client_interface": "get, post, put, delete" }
        ↓
Raw result:       discarded after this turn
```

The raw output is used exactly once, then replaced by a compact extracted fact stored in the session layer. **What you extract and how you represent it is the most important design decision in your context manager.** Everything downstream depends on extraction quality.

---

### 4.2 Score

Before injecting context, score each item in your state object against the current step. Not every fact is needed for every action.

```
Current step: "Refactoring src/api/post.ts"

Score HIGH  → http_client_interface       (directly needed)
Score HIGH  → pattern_to_replace          (directly needed)
Score LOW   → completed steps > 3 turns ago
Score ZERO  → facts about unrelated files
```

**Simple heuristics that work well:**
- **Recency:** more recent = higher score
- **Keyword overlap:** does the fact share terms with the current instruction?
- **Last-used:** was this fact referenced in the previous turn?

Inject HIGH items in full, LOW items as a one-line summary, ZERO items not at all.

---

### 4.3 Compress

When working memory items age out of the sliding window, compress them rather than discarding them. Compression preserves the signal without the tokens.

```
Turn 3 raw:   "Read src/api/user.ts (340 lines). Found 4 axios.get calls
               on lines 12, 45, 67, 89. Replaced all with httpClient.get.
               File saved successfully."
       ↓
Compressed:   "✓ user.ts — 4 axios.get calls replaced"
```

~60 tokens becomes ~10 tokens. The compressed form contains everything the agent needs to know about that step going forward — what happened and whether it succeeded.

**Compression rules:**
- Preserve outcome (success / failure / partial)
- Preserve any facts that might be referenced later
- Discard step-by-step reasoning and intermediate output
- Use a consistent, scannable format (e.g., `✓ filename — action taken`)

---

### 4.4 Budget

Always work backwards from a fixed token ceiling. Define explicit allocations per layer before writing any context assembly code.

```
Total context budget:          8,000 tokens
├─ System prompt (static):     1,500 tokens  ← fixed
├─ Current instruction:          200 tokens  ← fixed
├─ Current tool result:        1,000 tokens  ← variable, isolated
└─ Context manager budget:     5,300 tokens  ← what you control
    ├─ Task + constraints:       300 tokens
    ├─ Discovered facts:         500 tokens
    ├─ Progress state:           200 tokens
    └─ Working memory window:  4,300 tokens  ← sliding window lives here
```

When the working memory slot fills, compress the oldest entries automatically. The budget enforces the right behavior — context never accumulates passively.

---

## 5. The Sliding Window + Summary Pattern

This is the core pattern for working memory management. Only the most recent turns stay in raw form; everything older is compressed into the session layer.

```
Turn N-4:  [compressed] → stored in progress.completed_steps
Turn N-3:  [compressed] → stored in progress.completed_steps
Turn N-2:  [raw]        → in working memory window
Turn N-1:  [raw]        → in working memory window
Turn N:    [raw]        → current turn, full fidelity
```

**Recommended window size:** 2–3 raw turns. Beyond that, compression quality is usually good enough that the agent doesn't need the verbatim record.

**What goes into the window:** the agent's reasoning, the action taken, and the result received — kept together as a unit. Don't split them across layers.

---

## 6. Handling Tool Results

Tool results are the largest token offender and require individual treatment strategies.

| Tool Type | Raw Size | Strategy |
|---|---|---|
| `read_file` | Large | Extract only relevant lines or sections. Never re-inject the full file after the current turn. |
| `search_code` | Medium | Keep file paths + line numbers. Discard surrounding code context. |
| `run_command` | Variable | Keep exit code + last N lines of output. Discard verbose middle output. |
| `list_dir` | Small | Keep as-is. Already compact. |
| `write_file` | N/A | Record only `"✓ written"` + path. Content lives on disk. |

**The general rule:** the agent needs to know *what happened*, not re-read everything it already processed. A tool result is consumed once at full fidelity, then replaced by an extracted fact.

---

## 7. Failure Modes

| Failure Mode | Symptom | Cause |
|---|---|---|
| **Under-compression** | Agent repeats completed work or contradicts earlier decisions | Manager retains too much raw history; stale context conflicts with current state |
| **Over-compression** | Agent is confident but wrong in ways that trace back to older turns | Summaries discarded facts that were still needed downstream |
| **Relevance scoring failure** | Agent addresses things it wasn't asked about; takes detours | Facts injected that don't apply to the current step |
| **State object drift** | Agent believes a step is complete when it isn't, or vice versa | Manager failed to update state correctly after an error or partial execution |
| **Tool result accumulation** | Token cost grows quickly; agent becomes unfocused | Raw tool outputs re-injected across multiple turns instead of being extracted and replaced |

---

## 8. Pre-Call Checklist

Before assembling the context for each agent call, the context manager must be able to answer all of the following:

- [ ] What is the agent trying to accomplish **right now** (not the overall goal)?
- [ ] What facts are **directly needed** for this specific step?
- [ ] What is the result of the **last action** (injected at full fidelity for this turn only)?
- [ ] What has been **completed** so far (compact summary only)?
- [ ] What **constraints** are still active?
- [ ] Is the assembled context **within the token budget**?

If any answer requires re-injecting raw history, that is a signal that the extraction step is not aggressive enough.

---

## 9. Core Principle

> The context manager's job is not to remember everything — it is to **reconstruct the minimal sufficient context for the next step**.

Think of it less like a database and more like a briefing document written fresh before each agent call. A good briefing is short, specific, and contains exactly what the agent needs to act — nothing more, nothing less.

Signal density is the metric that matters. Every token injected should earn its place by being directly relevant to what the agent is about to do.

---

*This document is a living reference. Update extraction strategies and budget allocations as the system matures.*
