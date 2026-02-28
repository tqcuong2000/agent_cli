# Changed Files Detection & Display Architecture

## Overview
When an agent writes or edits a file, the change must appear **immediately** in the TUI sidebar. The changed files panel is a persistent widget on the right side of the screen, positioned below the Session Info panel. It updates in real-time as the agent works — no waiting for task completion.

Detection is purely **tool-level**: the ToolExecutor records every `write_file`, `edit_file`, and `delete_file` call and emits a `FileChangedEvent` to the Event Bus. The TUI sidebar widget subscribes to this event and renders the file immediately.

---

## 1. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Detection Method** | ToolExecutor-only (no git diff) | Simpler. Immediate. Only tracks agent-initiated changes. |
| **Display Timing** | Real-time (appears instantly on each file change) | User sees changes as they happen, not after completion. |
| **Display Location** | Right sidebar, below Session Info panel | Always visible. No overlay. Doesn't interrupt the chat flow. |
| **Accept/Reject** | Batch (all or nothing) on task completion | Terminal UI constraint. Git-based revert. |

---

## 2. TUI Layout

Looking at the full screen layout with the changed files panel:

```
┌─────────────────────────────────────────────────────────────────────┐
│ Engine CLI                                    >_   Main Agent      │
├──────────────────────────────────────┬──────────────────────────────┤
│                                      │ Session ● 20260227-1036     │
│  > Hello, could you help me write    │  Context used: 12,834 (12%) │
│    a python script for my agent      │  Cost: $0.012               │
│    project?                          ├──────────────────────────────┤
│                                      │ Changed Files (3)           │
│  Agent is working...                 │                              │
│  ┌─ thinking ──────────────────┐     │  ✚ src/auth/jwt.py          │
│  │ Let me create the auth...   │     │  ✎ src/auth/middleware.py    │
│  └─────────────────────────────┘     │  ✎ tests/test_auth.py       │
│                                      │                              │
│  ┌─ write_file ────────────────┐     │                              │
│  │ Created src/auth/jwt.py     │     │                              │
│  └─────────────────────────────┘     │                              │
│                                      │                              │
│                                      │                              │
│                                      │                              │
├──────────────────────────────────────┴──────────────────────────────┤
│ [input area]                                              Submit    │
├─────────────────────────────────────────────────────────────────────┤
│ Plan ● gemini-3.1-pro ● xHigh  tab mode │ ctrl+p │ ctrl+e         │
└─────────────────────────────────────────────────────────────────────┘
```

The right sidebar has two stacked panels:
1. **Session Info** (top-right) — session ID, context usage, cost
2. **Changed Files** (bottom-right) — real-time file list

---

## 3. The `FileChangeTracker`

```python
from dataclasses import dataclass, field
from typing import List, Optional, Set
from pathlib import Path
from enum import Enum, auto
from datetime import datetime


class ChangeType(Enum):
    """Type of file modification."""
    CREATED  = auto()   # New file created (write_file to non-existing path)
    MODIFIED = auto()   # Existing file edited (edit_file or write_file to existing path)
    DELETED  = auto()   # File deleted


@dataclass
class FileChange:
    """A single file change detected from tool execution."""
    path: str                    # Relative to workspace root (for display)
    change_type: ChangeType
    tool_name: str               # "write_file", "edit_file", "delete_file"
    agent_name: str              # Which agent made the change
    timestamp: datetime = field(default_factory=datetime.now)


class FileChangeTracker:
    """
    Tracks file modifications during a user request.
    Detection is purely tool-level — records changes as they happen.
    
    Lifecycle per request:
    1. start_tracking(request_id) — on UserRequestEvent
    2. record_change() — called by ToolExecutor after every file write/edit/delete
    3. get_changes() — returns the current list (for TUI rendering)
    4. reset() — clears state for the next request
    """
    
    def __init__(self, workspace_root: Path, event_bus: "AbstractEventBus"):
        self._workspace_root = workspace_root
        self._event_bus = event_bus
        self._current_request_id: Optional[str] = None
        self._changes: List[FileChange] = []
        self._seen_paths: Set[str] = set()  # Deduplication
    
    def start_tracking(self, request_id: str) -> None:
        """Begin tracking for a new user request. Clears previous state."""
        self._current_request_id = request_id
        self._changes = []
        self._seen_paths = set()
    
    async def record_change(
        self,
        absolute_path: str,
        change_type: ChangeType,
        tool_name: str,
        agent_name: str
    ) -> None:
        """
        Record a file change and immediately emit FileChangedEvent.
        Called by ToolExecutor after write_file, edit_file, or delete operations.
        """
        # Convert to relative path for display
        try:
            rel_path = str(
                Path(absolute_path).resolve().relative_to(self._workspace_root)
            )
        except ValueError:
            rel_path = absolute_path
        
        change = FileChange(
            path=rel_path,
            change_type=change_type,
            tool_name=tool_name,
            agent_name=agent_name,
        )
        
        # Deduplicate: if same file changed again, update the entry
        if rel_path in self._seen_paths:
            # Find and update existing entry
            for i, existing in enumerate(self._changes):
                if existing.path == rel_path:
                    self._changes[i] = change
                    break
        else:
            self._changes.append(change)
            self._seen_paths.add(rel_path)
        
        # Emit event immediately → TUI updates in real-time
        await self._event_bus.emit(FileChangedEvent(
            source="file_change_tracker",
            change=change,
            total_changed=len(self._changes)
        ))
    
    def get_changes(self) -> List[FileChange]:
        """Return all tracked changes (for TUI rendering)."""
        return list(self._changes)
    
    @property
    def total_files(self) -> int:
        """Number of unique files changed."""
        return len(self._changes)
    
    @property
    def is_empty(self) -> bool:
        return len(self._changes) == 0
    
    def reset(self) -> None:
        """Clear state for the next request."""
        self._current_request_id = None
        self._changes = []
        self._seen_paths = set()
```

---

## 4. Event: `FileChangedEvent`

```python
@dataclass
class FileChangedEvent(BaseEvent):
    """
    Emitted immediately when a file is written, edited, or deleted.
    The TUI sidebar subscribes to this and updates the Changed Files panel.
    """
    change: FileChange = None
    total_changed: int = 0
```

---

## 5. Integration with ToolExecutor

The ToolExecutor records the change **after** the tool executes successfully and **emits the event immediately**:

```python
class ToolExecutor:
    """Extended from 03_tools_architecture.md with change tracking."""
    
    def __init__(
        self,
        registry: "ToolRegistry",
        workspace: "BaseWorkspaceManager",
        change_tracker: FileChangeTracker,
        # ... other dependencies
    ):
        self.change_tracker = change_tracker
        # ...
    
    async def execute(
        self, action: "ParsedAction", task_id: str, agent_name: str
    ) -> str:
        # ... existing validation, safety checks ...
        
        # Determine if this is a file-creating or file-modifying operation
        is_file_write = action.tool_name in ("write_file", "edit_file", "delete_file")
        file_existed_before = False
        
        if is_file_write and action.tool_name != "delete_file":
            target_path = validated_args.get("path", "")
            file_existed_before = Path(target_path).exists()
        
        # Execute the tool
        result = await tool.execute(**validated_args)
        
        # ── Record file change (emit event immediately) ─────
        if is_file_write:
            target_path = validated_args.get("path", "")
            
            if action.tool_name == "delete_file":
                change_type = ChangeType.DELETED
            elif file_existed_before:
                change_type = ChangeType.MODIFIED
            else:
                change_type = ChangeType.CREATED
            
            await self.change_tracker.record_change(
                absolute_path=target_path,
                change_type=change_type,
                tool_name=action.tool_name,
                agent_name=agent_name
            )
        
        return self.formatter.format(tool.name, result)
```

---

## 6. TUI Changed Files Widget

```python
from textual.widget import Widget
from textual.reactive import reactive


class ChangedFilesPanel(Widget):
    """
    Right sidebar widget showing files changed during the current request.
    Updates in real-time via FileChangedEvent subscription.
    
    Positioned below the SessionInfoPanel in the right sidebar.
    """
    
    DEFAULT_CSS = """
    ChangedFilesPanel {
        width: 100%;
        height: 1fr;          /* Fill remaining space below SessionInfoPanel */
        border: solid $primary;
        padding: 0 1;
        overflow-y: auto;
    }
    
    ChangedFilesPanel .file-entry {
        height: 1;
    }
    
    ChangedFilesPanel .created {
        color: $success;       /* Green */
    }
    
    ChangedFilesPanel .modified {
        color: $warning;       /* Yellow/Orange */
    }
    
    ChangedFilesPanel .deleted {
        color: $error;         /* Red */
    }
    
    ChangedFilesPanel .header {
        text-style: bold;
        padding-bottom: 1;
    }
    
    ChangedFilesPanel .empty {
        color: $text-muted;
        text-style: italic;
    }
    """
    
    changes: reactive[list] = reactive(list, always_update=True)
    
    def __init__(self):
        super().__init__()
        self._change_list: list[FileChange] = []
    
    def on_mount(self) -> None:
        """Subscribe to FileChangedEvent on the Event Bus."""
        self.app.event_bus.subscribe(FileChangedEvent, self._on_file_changed)
    
    async def _on_file_changed(self, event: FileChangedEvent) -> None:
        """Handle real-time file change events."""
        # Rebuild the list from the tracker (handles deduplication)
        self._change_list = self.app.change_tracker.get_changes()
        self.changes = self._change_list
    
    def render(self) -> str:
        """Render the changed files list."""
        if not self._change_list:
            return ""  # Empty — widget hidden or shows placeholder
        
        lines = [f"Changed Files ({len(self._change_list)})"]
        lines.append("")
        
        # Icons: ✚ created, ✎ modified, ✖ deleted
        icons = {
            ChangeType.CREATED: "✚",
            ChangeType.MODIFIED: "✎",
            ChangeType.DELETED: "✖",
        }
        
        for change in self._change_list:
            icon = icons.get(change.change_type, "?")
            lines.append(f" {icon} {change.path}")
        
        return "\n".join(lines)
    
    def clear(self) -> None:
        """Clear the panel (called when tracker resets)."""
        self._change_list = []
        self.changes = []
```

### Right Sidebar Layout

```python
class RightSidebar(Widget):
    """
    The right sidebar containing Session Info and Changed Files panels.
    """
    
    DEFAULT_CSS = """
    RightSidebar {
        width: 30;              /* Fixed width for sidebar */
        height: 100%;
        layout: vertical;
    }
    """
    
    def compose(self):
        yield SessionInfoPanel()      # Top: session ID, context, cost
        yield ChangedFilesPanel()     # Bottom: real-time changed files list
```

---

## 7. Accept / Reject on Task Completion

When the task finishes and there are changed files, the panel shows action buttons at the bottom:

```
┌──────────────────────────────┐
│ Changed Files (3)            │
│                              │
│  ✚ src/auth/jwt.py           │
│  ✎ src/auth/middleware.py    │
│  ✎ tests/test_auth.py       │
│                              │
│  ─────────────────────────── │
│  [Enter] Accept  [R] Reject  │
└──────────────────────────────┘
```

The action row only appears when the task reaches a terminal state. During execution, the panel just shows the file list (no buttons):

```python
class ChangedFilesPanel(Widget):
    
    task_complete: reactive[bool] = reactive(False)
    
    def on_mount(self) -> None:
        self.app.event_bus.subscribe(FileChangedEvent, self._on_file_changed)
        self.app.event_bus.subscribe(TaskStateEvent, self._on_task_state)
    
    async def _on_task_state(self, event: "TaskStateEvent") -> None:
        """Show accept/reject buttons when task completes."""
        if event.new_state in ("SUCCESS", "FAILED"):
            self.task_complete = True
    
    async def _on_accept(self) -> None:
        """User accepts all changes. Just dismiss the action row."""
        self.task_complete = False
        # Changes stay on disk. Nothing to do.
        self.app.notify("✓ Changes accepted.", severity="information")
    
    async def _on_reject(self) -> None:
        """User rejects all changes. Revert via git."""
        result = await self._revert_all()
        self.task_complete = False
        self.clear()
        self.app.notify(result, severity="warning")
    
    async def _revert_all(self) -> str:
        """Revert all changed files using git."""
        import asyncio
        
        if not (self.app.workspace_root / ".git").exists():
            return "⚠ Cannot revert: not a git repository."
        
        reverted = 0
        errors = []
        
        for change in self._change_list:
            try:
                if change.change_type == ChangeType.CREATED:
                    # New file → delete it
                    file_path = self.app.workspace_root / change.path
                    if file_path.exists():
                        file_path.unlink()
                        reverted += 1
                
                elif change.change_type == ChangeType.MODIFIED:
                    # Modified → git checkout
                    proc = await asyncio.create_subprocess_exec(
                        "git", "checkout", "HEAD", "--", change.path,
                        cwd=str(self.app.workspace_root),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await proc.communicate()
                    if proc.returncode == 0:
                        reverted += 1
                    else:
                        errors.append(change.path)
                
                elif change.change_type == ChangeType.DELETED:
                    # Deleted → git checkout (restore)
                    proc = await asyncio.create_subprocess_exec(
                        "git", "checkout", "HEAD", "--", change.path,
                        cwd=str(self.app.workspace_root),
                    )
                    await proc.communicate()
                    reverted += 1
                    
            except Exception as e:
                errors.append(f"{change.path}: {e}")
        
        msg = f"✓ Reverted {reverted} file(s)."
        if errors:
            msg += f"\n⚠ Failed: {', '.join(errors)}"
        return msg
```

---

## 8. Orchestrator Lifecycle

```python
class Orchestrator:
    
    async def on_user_request(self, event: "UserRequestEvent") -> None:
        """Start tracking at the beginning of each request."""
        
        # Reset tracker + clear TUI panel for new request
        self.change_tracker.reset()
        await self.event_bus.emit(ChangesResetEvent(source="orchestrator"))
        
        # Start tracking
        self.change_tracker.start_tracking(request_id=event.event_id)
        
        # ... routing, execution ...
```

---

## 9. Panel States

```
New request submitted
    │
    ▼
Panel cleared (empty, title says "Changed Files")
    │
    ▼ ToolExecutor writes a file → FileChangedEvent
    │
Panel shows: ✚ src/auth/jwt.py
    │
    ▼ ToolExecutor edits another file → FileChangedEvent
    │
Panel shows: ✚ src/auth/jwt.py
              ✎ src/auth/middleware.py
    │
    ▼ Agent keeps working, more files appear instantly...
    │
    ▼ Task completes (SUCCESS or FAILED)
    │
Panel shows file list + action row:
    [Enter] Accept  │  [R] Reject
    │                    │
    ▼                    ▼
 Accept: dismiss      Reject: git revert
 action row           all files, clear panel
```

---

## 10. Cross-Reference Map

| Spec | Integration |
|---|---|
| `03_tools_architecture.md` | ToolExecutor calls `change_tracker.record_change()` after file writes |
| `00_event_bus.md` | `FileChangedEvent`, `ChangesResetEvent` |
| `04_multi_agent_definitions.md` | Orchestrator manages tracker lifecycle (start → reset) |
| `03_workspace_sandbox.md` | Paths displayed relative to workspace root |
| `02_state_management.md` | Task state transitions trigger accept/reject action row |

---

## 11. Testing Strategy

```python
import pytest

@pytest.mark.asyncio
async def test_immediate_event_emission():
    """FileChangedEvent should be emitted immediately on record_change."""
    events = []
    event_bus = MockEventBus(on_emit=lambda e: events.append(e))
    tracker = FileChangeTracker(workspace_root=Path("/project"), event_bus=event_bus)
    tracker.start_tracking("req_1")
    
    await tracker.record_change(
        absolute_path="/project/src/app.py",
        change_type=ChangeType.CREATED,
        tool_name="write_file",
        agent_name="coder"
    )
    
    assert len(events) == 1
    assert isinstance(events[0], FileChangedEvent)
    assert events[0].change.path == "src/app.py"
    assert events[0].total_changed == 1

@pytest.mark.asyncio
async def test_deduplication_on_multiple_edits():
    """Same file edited 3 times should appear once in the list."""
    event_bus = MockEventBus()
    tracker = FileChangeTracker(workspace_root=Path("/project"), event_bus=event_bus)
    tracker.start_tracking("req_1")
    
    for _ in range(3):
        await tracker.record_change(
            "/project/src/app.py", ChangeType.MODIFIED, "edit_file", "coder"
        )
    
    assert tracker.total_files == 1  # Deduplicated
    changes = tracker.get_changes()
    assert len(changes) == 1

@pytest.mark.asyncio
async def test_reset_clears_everything():
    event_bus = MockEventBus()
    tracker = FileChangeTracker(workspace_root=Path("/project"), event_bus=event_bus)
    tracker.start_tracking("req_1")
    await tracker.record_change("/project/x.py", ChangeType.CREATED, "write_file", "coder")
    
    tracker.reset()
    assert tracker.is_empty
    assert tracker.total_files == 0

@pytest.mark.asyncio
async def test_relative_path_display():
    """Paths should be relative to workspace root for clean display."""
    event_bus = MockEventBus()
    tracker = FileChangeTracker(workspace_root=Path("/project"), event_bus=event_bus)
    tracker.start_tracking("req_1")
    
    await tracker.record_change(
        "/project/src/deep/nested/file.py",
        ChangeType.CREATED, "write_file", "coder"
    )
    
    changes = tracker.get_changes()
    assert changes[0].path == "src/deep/nested/file.py"

@pytest.mark.asyncio
async def test_change_type_detection():
    """ToolExecutor should detect CREATED vs MODIFIED correctly."""
    # CREATED: file didn't exist before write_file
    # MODIFIED: file existed before edit_file
    # DELETED: delete_file called

@pytest.mark.asyncio
async def test_reject_reverts_created_files(tmp_path):
    """Reject should delete files that were created."""
    new_file = tmp_path / "new.py"
    new_file.write_text("print('hello')")
    
    panel = ChangedFilesPanel()
    panel._change_list = [
        FileChange("new.py", ChangeType.CREATED, "write_file", "coder")
    ]
    
    await panel._revert_all()
    assert not new_file.exists()

@pytest.mark.asyncio
async def test_reject_restores_modified_files(tmp_path):
    """Reject should git checkout modified files."""
    # Requires a git repo with committed files — integration test

def test_empty_panel_on_no_changes():
    """Panel should show nothing when no files have changed."""
    panel = ChangedFilesPanel()
    assert panel.render() == ""
```
