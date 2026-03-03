# Phase 5.2.0 — Session Lifecycle Design

Status: Approved design for implementation
Scope: Architecture decision only (no runtime code in this task)
Depends on: 5.1.1 and 5.1.2
Unblocks: 5.2.1, 5.2.2, 5.2.3, and 5.1.3

---

## 1) Session vs Task Relationship

Decision:
- One `Session` contains many `TaskRecord`s.
- Session lifetime is longer than task lifetime.
- Each user message creates one new task within the active session.

Lifecycle:
1. CLI startup:
   - Load active session if available.
   - If none exists, create one new session and set it active.
2. User sends message:
   - Orchestrator creates `TaskRecord` via `StateManager`.
   - Task is linked to the active session (`session.task_ids.append(task_id)`).
3. Task completes/fails:
   - Task terminal state is persisted.
   - Session remains open for next user message.
4. `/session new`:
   - Current active session is saved/archived.
   - New empty session becomes active.

Invariants:
- A task belongs to exactly one session.
- Exactly zero or one active session exists at runtime.
- Session message history is append-only except controlled compaction.

---

## 2) Memory Hydration Flow

Decision:
- Orchestrator hydrates working memory from session history before agent execution.
- `memory.reset_working()` is no longer the default for multi-turn flow.

Per-request flow:
1. User submits message.
2. Orchestrator creates a `TaskRecord`.
3. Orchestrator reads `session_messages = session.get_messages()`.
4. Orchestrator invokes agent with hydrated context:
   - `agent.handle_task(..., session_messages=session_messages, ...)`
5. Agent starts with:
   - refreshed system prompt (see section 3),
   - prior session messages,
   - current user message.
6. During task:
   - assistant/tool messages are added to working memory as usual.
7. On task completion:
   - Orchestrator extracts new messages produced during the task.
   - Appends them to `session.messages`.
   - Persists session via `session_manager.save(session)`.

Message ownership:
- `WorkingMemoryManager` is per-task execution context.
- `Session.messages` is cross-task persisted thread.
- Orchestrator is the synchronization boundary between the two.

---

## 3) System Prompt Refresh Strategy

Decision:
- Rebuild system prompt once per task, not once per session.
- Replace any prior system message in hydrated context with the newly built prompt.

Why:
- Tool set may change between tasks.
- Runtime settings (`/model`, `/effort`, approvals) may change.
- Prompt must stay aligned with current agent/provider capabilities.

Rule:
- Before first model call in a task:
  - remove old leading system message(s) from hydrated session thread,
  - inject fresh `build_system_prompt()` output as the single active system message.

Result:
- Session continuity is preserved for user/assistant/tool history.
- Prompt instructions stay current without stale configuration drift.

---

## 4) Compaction Integration (Before First LLM Call)

Decision:
- Compaction check runs immediately after hydration and system-prompt replacement, before first `safe_generate()`.

Flow point:
1. Hydrate session messages.
2. Refresh system prompt.
3. Add current user message.
4. Compute token usage against current `TokenBudget`.
5. If above threshold, run `summarize_and_compact()` before first generation.

Behavior:
- Uses existing token-aware `WorkingMemoryManager` budget logic (5.1.2).
- If `/model` changed to a smaller context, budget recalculation already happened and this pre-call check enforces it.
- Fallback behavior remains: if provider still raises `ContextLengthExceededError`, compact again and retry.

---

## 5) Component Responsibilities (Implementation Contract)

Orchestrator:
- Owns session/task coordination.
- Hydrates memory from session.
- Persists task output back into session.
- Triggers save on task completion.

BaseAgent:
- Accepts optional `session_messages`.
- If provided, loads hydrated context instead of unconditional reset.
- Rebuilds system prompt once per task and replaces stale one.

MemoryManager:
- Provides token counting, threshold check, and compaction.
- Exposes pre-call compaction hook (`should_compact` + `summarize_and_compact`).

SessionManager:
- Owns persistence, active-session lookup, and CRUD.
- Guarantees atomic save and session metadata timestamps.

---

## 6) Data Shape (for 5.2.1 compatibility)

Session:
- `session_id: str`
- `name: Optional[str]`
- `created_at: datetime`
- `updated_at: datetime`
- `messages: List[Dict[str, Any]]`
- `active_model: str`
- `total_cost: float`
- `task_ids: List[str]`

Message format:
- OpenAI-style message dicts (`role`, `content`, optional metadata fields).
- Preserve tool outputs as messages so rehydration reproduces execution context.

---

## 7) Backward Compatibility

Required:
- `BaseAgent.handle_task()` remains callable without session data.
- Existing single-turn tests should continue to pass with legacy flow:
  - if `session_messages` is `None`, behavior matches current reset-per-task approach.

Migration:
- 5.2.2 introduces new optional parameter and orchestration path.
- No immediate breaking change to existing callers.

---

## 8) Open Questions (Resolved for 5.2.1+)

1. Session boundary for autosave:
   - Save on every terminal task event (`SUCCESS`/`FAILED`) and on shutdown.
2. Session message growth:
   - Allow growth; compaction policy handles token pressure at execution time.
3. Active session pointer storage:
   - Store in session manager metadata (e.g. `active_session.json`) for restart continuity.

---

## 9) Acceptance Criteria for 5.2.0

- Session/task relationship is explicitly defined and non-ambiguous.
- Hydration flow defines exact pre/post agent boundaries.
- System prompt refresh timing is fixed: once per task, replacing stale system prompt.
- Compaction timing is fixed: before first LLM call after hydration.
- Design provides a direct contract for 5.2.1 and 5.2.2 implementation.
