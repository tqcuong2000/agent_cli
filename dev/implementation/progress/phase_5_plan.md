# Phase 5 Implementation Plan — Data Management & Persistence (Revised)

## Overview
Phase 5 transforms the Agent CLI from a transient, single-turn tool into a
durable, multi-turn, context-aware workspace. Each user message currently
starts a brand-new conversation — Phase 5 fixes this by introducing session
continuity, token-aware memory management, workspace security, and smarter
file discovery.

**Status:** Planning (Revised after architecture review)
**Reference Spec:** `roadmap/phase_5_data_persistence.md`
**Review:** `phase_5_review.md`

---

## Execution Order

> [!IMPORTANT]
> The execution order differs from the sub-phase numbering. Summarization
> depends on having a session boundary defined, so 5.2 must come before
> the second half of 5.1.

```
5.1.1  TokenCounter ABC + provider implementations
5.1.2  TokenBudget model
    ↓
5.2.0  Session Lifecycle Design (session vs task, hydration flow)
5.2.1  AbstractSessionManager + FileSessionManager + SessionRecord
5.2.2  Orchestrator refactor (multi-turn continuity)
5.2.3  /session commands + auto-save
    ↓
5.1.3  SummarizingMemoryManager (depends on session boundary)
    ↓
5.3.1  BaseWorkspaceManager ABC + StrictWorkspaceManager
5.3.2  Deny patterns + symlink detection
5.3.3  SandboxWorkspaceManager (git-based or lazy-copy)
    ↓
5.4.1  FileIndexer + gitignore
5.4.2  Fuzzy scoring upgrade
```

---

## Sub-Phase 5.1 — Memory Management (Context Optimization)
**Objective:** Replace simple message-count truncation with token-aware
budgeting and intelligent summarization.

### 5.1.1 — Token Counting Infrastructure
**File:** `agent_cli/memory/token_counter.py`

- [ ] Define `BaseTokenCounter` ABC with `count(messages, model_name) -> int`
- [ ] Implement `TiktokenCounter` for OpenAI models (cl100k_base, o200k_base)
- [ ] Implement `AnthropicTokenCounter` using Anthropic `count_tokens` API (or character heuristic fallback ~4 chars/token)
- [ ] Implement `GeminiTokenCounter` using the Gemini SDK `count_tokens()`
- [ ] Implement `HeuristicTokenCounter` as universal fallback (character-based estimation)
- [ ] Register the correct counter per provider in `ProviderManager` or `AppContext`
- [ ] Add `tiktoken` to project dependencies

### 5.1.2 — Token Budget Model
**File:** `agent_cli/memory/budget.py`

- [ ] Create `TokenBudget` dataclass:
  - `max_context: int` — model's maximum context window
  - `response_reserve: int` — tokens reserved for the LLM response (default 4096)
  - `compaction_threshold: float` — percentage at which to trigger compaction (default 0.80)
  - `available_for_context() -> int` — `max_context - response_reserve`
  - `should_compact(current_tokens: int) -> bool`
- [ ] Wire `TokenBudget` into `BaseMemoryManager` so compaction is token-driven, not message-count-driven
- [ ] Populate per-model budgets from a lookup table (e.g., GPT-4o = 128k, Claude 3.5 = 200k, Gemini 1.5 Pro = 2M)
- [ ] **Reactive update**: When the user switches models via `/model`, recalculate the budget and trigger compaction if the new model has a smaller window

### 5.1.3 — Adaptive Summarization (Depends on 5.2)
**File:** `agent_cli/memory/summarizer.py` (new), modify `agent_cli/agent/memory.py`

- [ ] Implement `SummarizingMemoryManager` (extends `WorkingMemoryManager`)
- [ ] Summarization strategy:
  - Keep system prompt intact
  - Keep the most recent N turns (configurable, default 5)
  - Summarize the "middle" messages into a single `[Context Summary]` message
- [ ] **Summarization engine**: Use a **separate, cheap model** (e.g., always GPT-4o-mini) to avoid the circular dependency of summarizing with a near-full context window
- [ ] Define explicit summarization budget: max 2000 tokens for the summary prompt + response
- [ ] Fallback: If no cheap model is available, use a **local heuristic** (extract tool names, file paths, key outcomes from each turn)
- [ ] Wire automatic compaction into the agent ReAct loop (replace the current `ContextLengthExceededError` handler)

---

## Sub-Phase 5.2 — Session Persistence & Multi-Turn Continuity
**Objective:** Make conversation history survive across multiple user messages
and CLI restarts. Define the Session lifecycle that all other components
depend on.

### 5.2.0 — Session Lifecycle Design (Architecture Decision)
**Deliverable:** Design document (not code) answering:

- [x] **Session vs Task relationship**: One `Session` wraps N `TaskRecord`s.
  A session starts when the CLI launches (or when the user runs `/session new`).
  Each user message creates a new `TaskRecord` within the active session.
- [x] **Memory hydration flow**:
  1. User sends message → Orchestrator creates TaskRecord
  2. Instead of `memory.reset_working()`, Orchestrator calls `session.get_messages()` to reload the conversation thread
  3. Agent receives the full thread (system + history + new message) as its working context
  4. After the agent completes, the new messages (assistant + tool results) are appended to the session
- [x] **System prompt refresh strategy**: `build_system_prompt()` is called
  **once per task** (not once per session) so the tool list and persona stay
  current. The refreshed system prompt replaces the old one in the hydrated
  context.
- [x] **Compaction integration**: If hydrated context exceeds `TokenBudget`,
  trigger `summarize_and_compact()` *before* the first LLM call of the task.
  - **Design doc:** `roadmap/progress/phase_5_session_lifecycle_design.md`

### 5.2.1 — Session Storage Engine
**Files:** `agent_cli/session/base.py`, `agent_cli/session/file_store.py`

- [x] Define `AbstractSessionManager` ABC:
  - `create_session(name?) -> Session`
  - `save(session) -> None`
  - `load(session_id) -> Session`
  - `list() -> List[SessionSummary]`
  - `delete(session_id) -> bool`
  - `get_active() -> Optional[Session]`
- [x] Create `Session` dataclass:
  - `session_id: str` (UUID)
  - `name: Optional[str]`
  - `created_at: datetime`
  - `updated_at: datetime`
  - `messages: List[Dict[str, Any]]` — the full conversation thread
  - `active_model: str`
  - `total_cost: float`
  - `task_ids: List[str]`
- [x] Implement `FileSessionManager(AbstractSessionManager)`:
  - Store as JSON in `~/.agent_cli/sessions/{session_id}.json`
  - Atomic writes (write to temp file, then rename)
- [x] Add `session_manager` field to `AppContext`
- [x] Auto-create a session on CLI startup if none is active

### 5.2.2 — Orchestrator Refactor (Multi-Turn Continuity)
**File:** `agent_cli/core/orchestrator.py`, `agent_cli/agent/base.py` (modify)

- [x] Refactor `Orchestrator._route_to_agent()`:
  - Before calling `agent.handle_task()`, hydrate working memory from `session.messages`
  - After task completion, append the new messages to the session and save
- [x] Refactor `BaseAgent.handle_task()`:
  - Accept an optional `session_messages: List[Dict]` parameter
  - If provided, skip `reset_working()` and instead load session messages + fresh system prompt
  - If not provided, behave as before (backward compatible for tests and single-turn use)
- [x] Emit `SettingsChangedEvent` when `/model` changes (currently missing — effort changes emit it but model doesn't)

### 5.2.3 — Session Commands & Auto-Save
**Files:** `agent_cli/commands/handlers/session.py` (new), modify `bootstrap.py`

- [ ] Implement `/session` command handlers:
  - `/session save [name]` — save current session with optional name
  - `/session list` — show all saved sessions (ID, name, date, message count)
  - `/session restore <id>` — load a saved session, hydrate memory
  - `/session delete <id>` — delete a saved session file
  - `/session info` — show current session stats (messages, tokens, cost)
  - `/session new` — start a fresh session (archive current)
- [ ] Auto-save on:
  - Task completion (`TaskResultEvent`)
  - CLI shutdown (`AppContext.shutdown()`)
  - Configurable periodic interval (default: every 5 minutes)

---

## Sub-Phase 5.3 — Workspace Security (Strict Jailing)
**Objective:** Formalize path jailing with abstract interfaces and add a
practical sandbox mode.

### 5.3.1 — BaseWorkspaceManager & StrictWorkspaceManager
**Files:** `agent_cli/workspace/base.py`, `agent_cli/workspace/strict.py`

- [ ] Define `BaseWorkspaceManager` ABC:
  - `resolve_path(path, must_exist?, writable?) -> Path`
  - `is_allowed(path) -> bool`
  - `get_root() -> Path`
- [ ] Implement `StrictWorkspaceManager(BaseWorkspaceManager)`:
  - Migrate existing `WorkspaceContext.resolve_path()` logic
  - Add **symlink detection**: resolve symlinks and re-check jail after resolution
  - Add configurable **deny patterns** (globs): default deny `.env`, `.git/`, `*.pem`, `*.key`
  - Add configurable **allow overrides** (for cases where the user needs `.env` access)
- [ ] Refactor all file tools to use `BaseWorkspaceManager` instead of `WorkspaceContext`
- [ ] Deprecate `WorkspaceContext` (keep as thin wrapper for backward compat during migration)

### 5.3.2 — Sandbox Mode
**Files:** `agent_cli/workspace/sandbox.py`

- [ ] Implement `SandboxWorkspaceManager(BaseWorkspaceManager)`:
  - **Strategy: Git-based** (preferred if workspace is a git repo):
    - On `/sandbox on`: create a temporary branch from current HEAD
    - All file operations target the working tree (same as normal)
    - On `/sandbox off`: diff against the base branch, prompt user
    - "Apply" = merge/keep changes, "Discard" = `git checkout` base branch
  - **Strategy: Lazy copy** (fallback for non-git workspaces):
    - Only copy files that the agent actually modifies (intercept via `FileChangeTracker`)
    - Keep originals in a temp directory for rollback
    - On "Discard", restore originals from temp
- [ ] Wire `/sandbox on|off|ls` command handlers

---

## Sub-Phase 5.4 — Enhanced File Discovery
**Objective:** Faster, smarter `@` mentions for large projects. Lower
priority — implement after core features are stable.

### 5.4.1 — Workspace Indexing
**File:** `agent_cli/workspace/file_index.py`

- [ ] Implement `FileIndexer`:
  - Background scan on startup
  - Integrate `pathspec` for `.gitignore` parsing
  - Cache index to disk (`~/.agent_cli/cache/file_index.json`) for fast startup
  - Invalidate on `FileChangedEvent`
- [ ] Cap index at configurable limit (default 5000 files)

### 5.4.2 — Smart Fuzzy Selection
**File:** modify `ux/tui/views/common/file_popup.py`

- [ ] Replace `_scan_workspace()` with `FileIndexer` queries
- [ ] Implement weighted scoring:
  - Filename match > path match
  - Recently changed files (from `FileChangeTracker`) get a boost
  - Shorter paths rank higher (less nesting = more important)

---

## Tests

### 5.1 Tests — `tests/memory/`
- [ ] `test_token_counter.py` — verify counts match tiktoken for known strings
- [ ] `test_token_budget.py` — verify threshold triggers, reactive model switch
- [ ] `test_summarizer.py` — verify compaction preserves system prompt and recent N turns

### 5.2 Tests — `tests/session/`
- [x] `test_session_manager.py` — save/load/list/delete round-trip
- [x] `test_multi_turn.py` — verify memory persists across multiple `handle_task()` calls
- [ ] `test_session_commands.py` — `/session save`, `/session list`, etc.

### 5.3 Tests — `tests/workspace/`
- [ ] `test_strict_workspace.py` — path traversal, symlink escape, deny patterns
- [ ] `test_sandbox.py` — sandbox on/off, apply/discard flow

### 5.4 Tests — `tests/workspace/`
- [ ] `test_file_indexer.py` — index building, gitignore filtering
- [ ] `test_fuzzy_scoring.py` — weighted scoring correctness

---

## Master Checklist

- [ ] **5.1.1** Token counting — abstract + per-provider implementations
- [ ] **5.1.2** Token budget — reactive model-switch-safe budgeting
- [x] **5.2.0** Session lifecycle design doc
- [x] **5.2.1** AbstractSessionManager + FileSessionManager
- [x] **5.2.2** Orchestrator multi-turn refactor
- [ ] **5.2.3** `/session` commands + auto-save
- [ ] **5.1.3** Summarization (cheap model or heuristic)
- [ ] **5.3.1** BaseWorkspaceManager + StrictWorkspaceManager
- [ ] **5.3.2** Sandbox mode (git-based or lazy-copy)
- [ ] **5.4.1** FileIndexer + gitignore
- [ ] **5.4.2** Fuzzy scoring upgrade
- [ ] **Tests** — All sub-phases covered

---

## File Tree — New Files This Phase

```
agent_cli/
├── memory/                            # NEW package
│   ├── __init__.py
│   ├── token_counter.py               # BaseTokenCounter ABC + implementations
│   ├── budget.py                      # TokenBudget model
│   └── summarizer.py                  # SummarizingMemoryManager
├── session/                           # NEW package
│   ├── __init__.py
│   ├── base.py                        # AbstractSessionManager ABC + Session model
│   └── file_store.py                  # FileSessionManager (JSON storage)
├── workspace/                         # NEW package
│   ├── __init__.py
│   ├── base.py                        # BaseWorkspaceManager ABC
│   ├── strict.py                      # StrictWorkspaceManager (path jail + deny patterns)
│   ├── sandbox.py                     # SandboxWorkspaceManager (git-based / lazy-copy)
│   └── file_index.py                  # FileIndexer + gitignore
├── commands/handlers/
│   └── session.py                     # NEW — /session commands
├── agent/
│   ├── base.py                        # MODIFIED — session hydration in handle_task
│   └── memory.py                      # MODIFIED — SummarizingMemoryManager
├── core/
│   ├── bootstrap.py                   # MODIFIED — wire session_manager, workspace_manager
│   └── orchestrator.py                # MODIFIED — multi-turn hydration
└── tools/
    └── workspace.py                   # DEPRECATED — replaced by workspace/strict.py

tests/
├── memory/
│   ├── test_token_counter.py
│   ├── test_token_budget.py
│   └── test_summarizer.py
├── session/
│   ├── test_session_manager.py
│   ├── test_multi_turn.py
│   └── test_session_commands.py
└── workspace/
    ├── test_strict_workspace.py
    ├── test_sandbox.py
    ├── test_file_indexer.py
    └── test_fuzzy_scoring.py
```
