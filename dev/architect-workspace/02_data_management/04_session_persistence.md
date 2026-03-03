# Session Persistence & Restoration Architecture

## Overview
When a user closes the CLI and returns later, their conversation history, completed tasks, and execution plan progress should not vanish. This architecture defines how **session state is persisted to SQLite**, how users **browse and restore sessions** via a dedicated TUI container, and how sessions are **scoped to workspaces** so that project-specific conversations never bleed into unrelated projects.

A restored session provides **conversation context continuity** — the LLM knows what was discussed and what tasks completed — without attempting the impossible task of resuming a mid-flight reasoning loop.

---

## 1. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Restore Scope** | Conversation context only | Active tasks are cancelled on restore. User re-issues interrupted work naturally. No impossible mid-loop state restoration. |
| **Persisted Data** | Conversation + Task Records + Session Metrics | User sees full history: chat messages, task states, plan progress, token usage. |
| **Storage** | SQLite primary + JSON export | Crash-safe, queryable, transactional. JSON export for sharing/debugging. |
| **Retention** | Configurable auto-prune (default 30 days) | Self-managing. No disk bloat. |
| **Session Selection** | `/session` command → TUI session management container | Visual session browser scoped to the current workspace. |
| **Workspace Scoping** | `workspace` field on every session | Sessions are tied to their project directory. `/session` only shows sessions for the active workspace. |

---

## 2. What Gets Persisted vs. What Doesn't

| Data | Persisted? | Rationale |
|---|---|---|
| Conversation messages (user + assistant + tool results) | ✅ Yes | Core context for the LLM on restore |
| Task records (state, description, history, parent-child) | ✅ Yes | Shows plan progress, completed/failed tasks |
| Execution plans (goal, sub-task list) | ✅ Yes | User sees the checklist on restore |
| Session metrics (tokens, duration, task counts) | ✅ Yes | Cost awareness |
| Workspace path | ✅ Yes | Scopes sessions to projects |
| Working Memory internal state | ❌ No | Rebuilt from conversation messages on restore |
| Agent loop iteration counter | ❌ No | Agent starts fresh |
| Event Bus subscriptions | ❌ No | Re-wired on startup |
| asyncio.Lock states | ❌ No | Runtime-only |

---

## 3. Restoration Behavior

When a user loads a previous session:

1. **Conversation messages** are loaded into the Memory Manager as the initial Working Memory context. The LLM sees the full prior conversation.
2. **Terminal tasks** (`SUCCESS`, `FAILED`, `CANCELLED`) are loaded as-is for display in the TUI.
3. **Non-terminal tasks** (`PENDING`, `ROUTING`, `WORKING`, `AWAITING_INPUT`) are automatically transitioned to `CANCELLED` with a note: `"Session interrupted — restored from saved state."`
4. **The user is shown a summary:**
   ```
   ── Session Restored ──────────────────────────────
   Session: "Refactor auth module" (2026-02-27 19:42)
   Messages: 24  │  Tasks: 3 ✓  1 ✗  1 cancelled
   Tokens used: 58,000
   ──────────────────────────────────────────────────
   ```
5. The input bar is focused. The user can now type a new prompt that benefits from the restored context.

---

## 4. SQLite Database Schema

The session database lives at `.agent_cli/sessions.db` — inside the project's `.agent_cli/` directory.

```sql
-- ── Sessions ──────────────────────────────────────────────
CREATE TABLE sessions (
    session_id    TEXT PRIMARY KEY,
    workspace     TEXT NOT NULL,          -- Absolute path of the workspace root
    title         TEXT DEFAULT '',        -- Auto-generated or user-set title
    created_at    REAL NOT NULL,          -- Unix timestamp
    updated_at    REAL NOT NULL,          -- Last activity timestamp
    status        TEXT DEFAULT 'active',  -- 'active', 'closed', 'archived'
    
    -- Session-level metrics snapshot
    total_input_tokens   INTEGER DEFAULT 0,
    total_output_tokens  INTEGER DEFAULT 0,
    total_llm_calls      INTEGER DEFAULT 0,
    total_tasks_created  INTEGER DEFAULT 0,
    total_tasks_succeeded INTEGER DEFAULT 0,
    total_tasks_failed   INTEGER DEFAULT 0,
    
    -- Duration
    duration_seconds     REAL DEFAULT 0
);

CREATE INDEX idx_sessions_workspace ON sessions(workspace);
CREATE INDEX idx_sessions_updated ON sessions(updated_at DESC);

-- ── Conversation Messages ─────────────────────────────────
CREATE TABLE messages (
    message_id    TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    sequence      INTEGER NOT NULL,        -- Ordering within the session
    role          TEXT NOT NULL,            -- 'user', 'assistant', 'system', 'tool'
    content       TEXT NOT NULL,            -- The message text
    metadata      TEXT DEFAULT '{}',        -- JSON: agent_name, is_monologue, tool_name, etc.
    created_at    REAL NOT NULL
);

CREATE INDEX idx_messages_session ON messages(session_id, sequence);

-- ── Task Records ──────────────────────────────────────────
CREATE TABLE tasks (
    task_id       TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    parent_id     TEXT,                     -- NULL for top-level tasks
    state         TEXT NOT NULL,            -- TaskState enum value
    description   TEXT DEFAULT '',
    assigned_agent TEXT DEFAULT '',
    result        TEXT,                     -- Final output on SUCCESS
    error         TEXT,                     -- Error message on FAILED
    history       TEXT DEFAULT '[]',        -- JSON: list of state transition records
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE INDEX idx_tasks_session ON tasks(session_id);
CREATE INDEX idx_tasks_parent ON tasks(parent_id);
```

---

## 5. The SessionManager Interface

```python
from abc import ABC, abstractmethod
from typing import Optional, List
from dataclasses import dataclass, field
import time
import uuid


@dataclass
class SessionRecord:
    """Serializable session metadata."""
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    workspace: str = ""
    title: str = ""
    status: str = "active"       # "active", "closed", "archived"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    
    # Metrics
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_llm_calls: int = 0
    total_tasks_created: int = 0
    total_tasks_succeeded: int = 0
    total_tasks_failed: int = 0
    duration_seconds: float = 0.0
    
    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens
    
    @property
    def display_title(self) -> str:
        """Human-readable title for the session list."""
        return self.title or f"Session {self.session_id[:8]}"


@dataclass
class MessageRecord:
    """A single conversation message."""
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    sequence: int = 0
    role: str = ""         # "user", "assistant", "system", "tool"
    content: str = ""
    metadata: dict = field(default_factory=dict)  # agent_name, is_monologue, etc.
    created_at: float = field(default_factory=time.time)


class AbstractSessionManager(ABC):
    """
    Manages session persistence: saving, loading, listing, and pruning.
    Backed by SQLite for crash safety and queryability.
    """
    
    # ── Session Lifecycle ─────────────────────────────────────
    
    @abstractmethod
    async def create_session(self, workspace: str, title: str = "") -> SessionRecord:
        """
        Create a new session for the given workspace.
        Called on CLI startup if no --session flag is provided.
        """
        pass
    
    @abstractmethod
    async def close_session(self, session_id: str, metrics: "SessionMetrics") -> None:
        """
        Mark a session as 'closed' and persist final metrics.
        Called during graceful shutdown.
        """
        pass
    
    # ── Persistence ───────────────────────────────────────────
    
    @abstractmethod
    async def save_message(self, message: MessageRecord) -> None:
        """
        Persist a conversation message. Called after every user input,
        agent response, and tool result.
        """
        pass
    
    @abstractmethod
    async def save_task(self, task: "TaskRecord") -> None:
        """
        Persist or update a task record. Called after every state transition.
        """
        pass
    
    @abstractmethod
    async def update_metrics(self, session_id: str, metrics: "SessionMetrics") -> None:
        """Periodically flush session metrics to disk (e.g., every 30s)."""
        pass
    
    # ── Queries ───────────────────────────────────────────────
    
    @abstractmethod
    async def list_sessions(
        self,
        workspace: str,
        limit: int = 20
    ) -> List[SessionRecord]:
        """
        List recent sessions for a specific workspace.
        Ordered by updated_at DESC.
        Only returns sessions matching the given workspace path.
        """
        pass
    
    @abstractmethod
    async def load_session(self, session_id: str) -> dict:
        """
        Load a complete session for restoration.
        Returns:
            {
                "session": SessionRecord,
                "messages": List[MessageRecord],  (ordered by sequence)
                "tasks": List[TaskRecord]
            }
        """
        pass
    
    # ── Maintenance ───────────────────────────────────────────
    
    @abstractmethod
    async def prune_old_sessions(self, retention_days: int) -> int:
        """
        Delete sessions older than retention_days.
        Called on startup. Returns the number of sessions pruned.
        """
        pass
    
    @abstractmethod
    async def delete_session(self, session_id: str) -> None:
        """Manually delete a specific session (CASCADE deletes messages + tasks)."""
        pass
    
    @abstractmethod
    async def export_session(self, session_id: str) -> str:
        """
        Export a session as a formatted JSON string.
        For sharing or debugging.
        """
        pass
```

---

## 6. Concrete Implementation: `SQLiteSessionManager`

```python
import aiosqlite
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SQLiteSessionManager(AbstractSessionManager):
    """SQLite-backed session persistence."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None
    
    async def initialize(self) -> None:
        """Create tables if they don't exist. Called once on startup."""
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.executescript(SCHEMA_SQL)  # The CREATE TABLE statements
        await self._db.commit()
    
    # ── Session Lifecycle ─────────────────────────────────────

    async def create_session(self, workspace: str, title: str = "") -> SessionRecord:
        session = SessionRecord(workspace=workspace, title=title)
        await self._db.execute(
            """INSERT INTO sessions 
               (session_id, workspace, title, created_at, updated_at, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session.session_id, workspace, title, session.created_at, session.updated_at, "active")
        )
        await self._db.commit()
        logger.info(f"Session created: {session.session_id} for workspace {workspace}")
        return session
    
    async def close_session(self, session_id: str, metrics: "SessionMetrics") -> None:
        await self._db.execute(
            """UPDATE sessions SET 
               status='closed', updated_at=?, duration_seconds=?,
               total_input_tokens=?, total_output_tokens=?, total_llm_calls=?,
               total_tasks_created=?, total_tasks_succeeded=?, total_tasks_failed=?
               WHERE session_id=?""",
            (time.time(), metrics.duration_seconds,
             metrics.total_input_tokens, metrics.total_output_tokens, metrics.total_llm_calls,
             metrics.total_tasks_created, metrics.total_tasks_succeeded, metrics.total_tasks_failed,
             session_id)
        )
        await self._db.commit()
    
    # ── Persistence ───────────────────────────────────────────

    async def save_message(self, message: MessageRecord) -> None:
        await self._db.execute(
            """INSERT OR REPLACE INTO messages 
               (message_id, session_id, sequence, role, content, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (message.message_id, message.session_id, message.sequence,
             message.role, message.content, json.dumps(message.metadata), message.created_at)
        )
        await self._db.commit()
    
    async def save_task(self, task: "TaskRecord") -> None:
        await self._db.execute(
            """INSERT OR REPLACE INTO tasks
               (task_id, session_id, parent_id, state, description, assigned_agent,
                result, error, history, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task.task_id, self._current_session_id, task.parent_id,
             task.state.name, task.description, task.assigned_agent,
             task.result, task.error, json.dumps(task.history),
             task.created_at, task.updated_at)
        )
        await self._db.commit()
    
    # ── Queries ───────────────────────────────────────────────

    async def list_sessions(self, workspace: str, limit: int = 20) -> List[SessionRecord]:
        cursor = await self._db.execute(
            """SELECT * FROM sessions 
               WHERE workspace = ? 
               ORDER BY updated_at DESC 
               LIMIT ?""",
            (workspace, limit)
        )
        rows = await cursor.fetchall()
        return [self._row_to_session(row) for row in rows]
    
    async def load_session(self, session_id: str) -> dict:
        # Load session metadata
        cursor = await self._db.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        )
        session_row = await cursor.fetchone()
        if not session_row:
            raise KeyError(f"Session '{session_id}' not found.")
        
        # Load messages (ordered)
        cursor = await self._db.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY sequence",
            (session_id,)
        )
        message_rows = await cursor.fetchall()
        
        # Load tasks
        cursor = await self._db.execute(
            "SELECT * FROM tasks WHERE session_id = ?", (session_id,)
        )
        task_rows = await cursor.fetchall()
        
        return {
            "session": self._row_to_session(session_row),
            "messages": [self._row_to_message(r) for r in message_rows],
            "tasks": [self._row_to_task(r) for r in task_rows],
        }
    
    # ── Maintenance ───────────────────────────────────────────

    async def prune_old_sessions(self, retention_days: int) -> int:
        cutoff = time.time() - (retention_days * 86400)
        cursor = await self._db.execute(
            "DELETE FROM sessions WHERE updated_at < ? AND status = 'closed'",
            (cutoff,)
        )
        await self._db.commit()
        pruned = cursor.rowcount
        if pruned > 0:
            logger.info(f"Pruned {pruned} sessions older than {retention_days} days.")
        return pruned
    
    async def export_session(self, session_id: str) -> str:
        data = await self.load_session(session_id)
        return json.dumps({
            "session": data["session"].__dict__,
            "messages": [m.__dict__ for m in data["messages"]],
            "tasks": [{"task_id": t.task_id, "state": t.state, 
                       "description": t.description} for t in data["tasks"]],
        }, indent=2, default=str)
    
    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
```

---

## 7. Automatic Session Auto-Save

Session data is saved **incrementally** — not on shutdown only. This ensures crash recovery:

### When Data Is Persisted

| Event | What Is Saved | Method |
|---|---|---|
| User submits a prompt | User message | `save_message()` |
| Agent produces a response | Assistant message | `save_message()` |
| Tool returns a result | Tool message | `save_message()` |
| Task state transitions | Updated task record | `save_task()` |
| Every 30 seconds (timer) | Session metrics | `update_metrics()` |
| Graceful shutdown | Final metrics + status='closed' | `close_session()` |
| CLI crash (no graceful shutdown) | Everything up to the last write | Automatic — SQLite is ACID |

### Integration with Existing Components

```python
# In the Orchestrator — auto-save on every agent message
async def on_agent_message(self, event: AgentMessageEvent):
    if not event.is_monologue:  # Don't save internal thinking
        await self.session_manager.save_message(MessageRecord(
            session_id=self.current_session.session_id,
            sequence=self._message_counter,
            role="assistant",
            content=event.content,
            metadata={"agent_name": event.agent_name}
        ))
        self._message_counter += 1

# In the State Manager — auto-save on every transition
async def transition(self, task_id, to_state, ...):
    # ... existing transition logic ...
    # After successful transition:
    await self.session_manager.save_task(task)
```

---

## 8. The `/session` TUI Container

When the user types `/session`, a **session management container** opens in the TUI. It replaces the main chat area temporarily (like a modal/screen).

### Container Layout

```
┌─────────────────────────────────────────────────────────┐
│  📋 Session Manager — Workspace: x:\agent_cli           │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  #  Title                        Date          Tokens   │
│  ─────────────────────────────────────────────────────  │
│  1  Refactor auth module         Feb 27 19:42   58K     │
│  2  Fix TUI shutdown errors      Feb 26 14:20   23K     │
│  3  Add persistent terminals     Feb 25 09:15  102K     │
│  4  Debug agent monologue        Feb 24 11:30   15K     │
│  5  Initial project setup        Feb 23 16:45    8K     │
│                                                         │
│  ▸ Tasks: 3 ✓  1 ✗  0 ⊘  │  Messages: 24             │
│                                                         │
├─────────────────────────────────────────────────────────┤
│  [Enter] Load  [D] Delete  [E] Export  [Esc] Close      │
└─────────────────────────────────────────────────────────┘
```

### Container Features

1. **Workspace-Scoped List:** Only shows sessions for the current workspace path. Sessions from other projects are hidden.
2. **Session Preview:** When a session is highlighted, the bottom section shows a mini-summary (task counts, message count, token usage).
3. **Keyboard Navigation:**
   - `↑/↓` — Navigate the session list
   - `Enter` — Load the selected session (restores context)
   - `D` — Delete the selected session (with confirmation)
   - `E` — Export session to JSON file in `.agent_cli/exports/`
   - `Esc` — Close the container, return to chat

### Auto-Generated Session Titles

Sessions are automatically titled based on the first user message:

```python
def auto_title(first_user_message: str, max_length: int = 40) -> str:
    """Generate a session title from the first user prompt."""
    title = first_user_message.strip()
    # Remove common prefixes
    for prefix in ["Please ", "Can you ", "Help me "]:
        if title.lower().startswith(prefix.lower()):
            title = title[len(prefix):]
    # Truncate
    if len(title) > max_length:
        title = title[:max_length - 3] + "..."
    return title.capitalize()
```

---

## 9. Session Restoration Flow

```python
class SessionRestorer:
    """Handles the logic of loading a saved session into the active system."""
    
    def __init__(
        self,
        session_manager: AbstractSessionManager,
        state_manager: AbstractStateManager,
        memory_manager: "BaseMemoryManager",
        event_bus: AbstractEventBus
    ):
        self.session_manager = session_manager
        self.state_manager = state_manager
        self.memory_manager = memory_manager
        self.event_bus = event_bus
    
    async def restore(self, session_id: str) -> SessionRecord:
        """
        Load a previous session and restore conversation context.
        """
        data = await self.session_manager.load_session(session_id)
        session = data["session"]
        messages = data["messages"]
        tasks = data["tasks"]
        
        # ── 1. Restore conversation messages into Working Memory ──
        for msg in messages:
            self.memory_manager.add_working_event({
                "role": msg.role,
                "content": msg.content
            })
        
        # ── 2. Cancel any non-terminal tasks ──
        interrupted_count = 0
        for task in tasks:
            if task.state not in ("SUCCESS", "FAILED", "CANCELLED"):
                task.state = "CANCELLED"
                task.error = "Session interrupted — restored from saved state."
                await self.session_manager.save_task(task)
                interrupted_count += 1
        
        # ── 3. Load task records into State Manager (for TUI display) ──
        for task in tasks:
            await self.state_manager.import_task(task)
        
        # ── 4. Notify the TUI of the restoration ──
        task_summary = self._format_task_summary(tasks)
        await self.event_bus.emit(AgentMessageEvent(
            source="session_restorer",
            agent_name="system",
            content=(
                f"── Session Restored ──\n"
                f"Session: \"{session.display_title}\" ({self._format_date(session.created_at)})\n"
                f"Messages: {len(messages)}  │  {task_summary}\n"
                f"Tokens used: {session.total_tokens:,}\n"
                + (f"⚠ {interrupted_count} task(s) were interrupted and cancelled.\n" if interrupted_count else "")
                + "──────────────────────"
            ),
            is_monologue=False
        ))
        
        return session
```

---

## 10. Configuration

```python
class AgentSettings(BaseSettings):
    # ... existing fields ...
    
    # Session settings
    session_retention_days: int = Field(
        default=30, ge=1,
        description="Auto-delete sessions older than this many days."
    )
    session_auto_save: bool = Field(
        default=True,
        description="Automatically persist messages and tasks as they occur."
    )
    session_metrics_flush_interval: int = Field(
        default=30,
        description="Seconds between periodic metrics flush to DB."
    )
```

---

## 11. CLI Integration

```bash
# Start a new session (default)
agent start

# Resume the most recent session for this workspace
agent start --last

# Resume a specific session by ID
agent start --session abc123

# Inside the TUI:
/session             # Open session management container
/session export      # Export current session to JSON
```

---

## 12. File Structure Update

```
.agent_cli/
├── sessions.db          ← SQLite database (sessions, messages, tasks)
├── exports/             ← JSON exports from /session export
│   └── session_abc123_2026-02-27.json
├── logs/                ← Observability logs (separate from session data)
│   └── session_*.jsonl
├── config.toml
└── artifacts/
```

---

## 13. Testing Strategy

```python
import pytest

@pytest.mark.asyncio
async def test_create_and_load_session(tmp_path):
    sm = SQLiteSessionManager(tmp_path / "sessions.db")
    await sm.initialize()
    
    session = await sm.create_session(workspace="/project/foo", title="Test session")
    
    # Save some messages
    await sm.save_message(MessageRecord(
        session_id=session.session_id, sequence=1,
        role="user", content="Hello!"
    ))
    await sm.save_message(MessageRecord(
        session_id=session.session_id, sequence=2,
        role="assistant", content="Hi! How can I help?"
    ))
    
    # Load and verify
    data = await sm.load_session(session.session_id)
    assert len(data["messages"]) == 2
    assert data["messages"][0].content == "Hello!"
    assert data["session"].workspace == "/project/foo"
    
    await sm.close()

@pytest.mark.asyncio
async def test_workspace_scoping(tmp_path):
    sm = SQLiteSessionManager(tmp_path / "sessions.db")
    await sm.initialize()
    
    await sm.create_session(workspace="/project/foo")
    await sm.create_session(workspace="/project/bar")
    await sm.create_session(workspace="/project/foo")
    
    foo_sessions = await sm.list_sessions(workspace="/project/foo")
    bar_sessions = await sm.list_sessions(workspace="/project/bar")
    
    assert len(foo_sessions) == 2
    assert len(bar_sessions) == 1
    
    await sm.close()

@pytest.mark.asyncio
async def test_prune_old_sessions(tmp_path):
    sm = SQLiteSessionManager(tmp_path / "sessions.db")
    await sm.initialize()
    
    # Create an old session
    old_session = await sm.create_session(workspace="/project")
    await sm._db.execute(
        "UPDATE sessions SET updated_at = ?, status = 'closed' WHERE session_id = ?",
        (time.time() - 100 * 86400, old_session.session_id)  # 100 days ago
    )
    
    # Create a recent session
    await sm.create_session(workspace="/project")
    
    pruned = await sm.prune_old_sessions(retention_days=30)
    assert pruned == 1
    
    remaining = await sm.list_sessions(workspace="/project")
    assert len(remaining) == 1
    
    await sm.close()

@pytest.mark.asyncio
async def test_restoration_cancels_active_tasks(tmp_path):
    """Non-terminal tasks should be CANCELLED on restore."""
    sm = SQLiteSessionManager(tmp_path / "sessions.db")
    await sm.initialize()
    
    session = await sm.create_session(workspace="/project")
    
    # Save a WORKING task (simulate crash mid-execution)
    working_task = TaskRecord(
        task_id="t1", state=TaskState.WORKING,
        description="Mid-flight task"
    )
    # ... save task ...
    
    # Restore should cancel it
    # ... assert task state == CANCELLED after restore ...
```
