from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional

from textual import events
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from agent_cli.core.infra.events.events import (
    BaseEvent,
    ChangedFileDetailEvent,
    ChangedFileDiffLine,
    ChangedFileReviewActionEvent,
    ChangedFileSelectedEvent,
    FileChangedEvent,
)

if TYPE_CHECKING:
    from agent_cli.core.infra.registry.bootstrap import AppContext


@dataclass
class _ChangedFileRowData:
    file_path: str
    change_type: str  # "created" | "modified" | "deleted"


class ChangedFileRow(Static):
    """Clickable row representing a single changed file."""

    DEFAULT_CSS = ""

    class Selected(Message):
        def __init__(self, sender: "ChangedFileRow", data: _ChangedFileRowData) -> None:
            super().__init__()
            self.sender = sender
            self.data = data

    def __init__(self, data: _ChangedFileRowData, **kwargs) -> None:
        super().__init__(**kwargs)
        self.data = data

    def on_mount(self) -> None:
        self.update(self._render_text())

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Selected(self, self.data))

    def _render_text(self) -> str:
        icon = self._icon_markup(self.data.change_type)
        return f"{icon} {self.data.file_path}"

    @staticmethod
    def _icon_markup(change_type: str) -> str:
        normalized = (change_type or "").lower()
        if normalized == "created":
            return "[green]+[/green]"
        if normalized == "modified":
            return "[yellow]~[/yellow]"
        if normalized == "deleted":
            return "[red]-[/red]"
        return "[grey50]?[/grey50]"


class ChangedFilesPanel(Widget):
    """Live changed-files list panel.

    - Subscribes to FileChangedEvent
    - Shows only when there are tracked changes
    - Emits selection and detail events on row click
    """

    DEFAULT_CSS = ""

    def __init__(self, app_context: Optional["AppContext"] = None, **kwargs) -> None:
        if "id" not in kwargs:
            kwargs["id"] = "changed_files_panel"
        super().__init__(**kwargs)
        self._app_context = app_context
        self._subscriptions: list[str] = []

        # Keyed by relative path (stable key for updates)
        self._changes: Dict[str, _ChangedFileRowData] = {}

        # Keep mounted row refs for easy update/select
        self._rows: Dict[str, ChangedFileRow] = {}
        self._selected_path: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="changed_files_list")
        yield Static("No changes yet", id="changed_files_empty")

    def on_mount(self) -> None:
        # Start hidden until we have changes
        self.add_class("-hidden")
        self._hydrate_from_tracker()
        self._update_empty_state()
        self._subscribe_events()

    def on_unmount(self) -> None:
        self._unsubscribe_events()

    # ── Public API ──────────────────────────────────────────────

    def clear(self) -> None:
        """Clear all rows and reset panel state."""
        self._changes.clear()
        self._rows.clear()
        self._selected_path = None
        list_view = self._list_view()
        for child in list(list_view.children):
            child.remove()
        self._update_empty_state()
        self._update_visibility()

    # ── Event bus wiring ────────────────────────────────────────

    def _subscribe_events(self) -> None:
        if self._app_context is None:
            return
        bus = self._app_context.event_bus
        self._subscriptions.append(
            bus.subscribe("FileChangedEvent", self._on_file_changed_event, priority=50)
        )
        self._subscriptions.append(
            bus.subscribe(
                "ChangedFileReviewActionEvent",
                self._on_changed_file_review_action,
                priority=50,
            )
        )

    def _unsubscribe_events(self) -> None:
        if self._app_context is None:
            return
        bus = self._app_context.event_bus
        for sub_id in self._subscriptions:
            bus.unsubscribe(sub_id)
        self._subscriptions.clear()

    async def _on_file_changed_event(self, event: BaseEvent) -> None:
        if not isinstance(event, FileChangedEvent):
            return
        self._upsert_change(file_path=event.file_path, change_type=event.change_type)

    # ── Row handling ────────────────────────────────────────────

    def on_changed_file_row_selected(self, event: ChangedFileRow.Selected) -> None:
        event.stop()
        self._select_path(event.data.file_path)
        self._emit_selection_events(event.data)

    async def _on_changed_file_review_action(self, event: BaseEvent) -> None:
        if not isinstance(event, ChangedFileReviewActionEvent):
            return
        if self._app_context is None:
            return

        file_path = self._normalize_path(event.file_path)
        if file_path == "<unknown>":
            return
        if file_path not in self._changes:
            return

        tracker = getattr(self._app_context, "file_tracker", None)
        if tracker is None:
            return

        action = (event.action or "").strip().lower()
        if action == "accept":
            accepted = await tracker.accept_file(file_path)
            if accepted:
                self._remove_change(file_path)
        elif action == "reject":
            reverted = await tracker.reject_file(file_path)
            if reverted:
                self._remove_change(file_path)

    def _upsert_change(self, *, file_path: str, change_type: str) -> None:
        key = self._normalize_path(file_path)
        data = _ChangedFileRowData(
            file_path=key, change_type=(change_type or "").lower()
        )
        self._changes[key] = data

        row = self._rows.get(key)
        if row is None:
            row = ChangedFileRow(data)
            self._rows[key] = row
            self._list_view().mount(row)
        else:
            row.data = data
            row.update(row._render_text())

        self._update_empty_state()
        self._update_visibility()

    def _select_path(self, file_path: str) -> None:
        normalized = self._normalize_path(file_path)

        # Remove previous selection class
        if self._selected_path is not None:
            prev = self._rows.get(self._selected_path)
            if prev is not None:
                prev.remove_class("-selected")

        self._selected_path = normalized
        current = self._rows.get(normalized)
        if current is not None:
            current.add_class("-selected")

    # ── Emit Option 2 flow events ───────────────────────────────

    def _emit_selection_events(self, data: _ChangedFileRowData) -> None:
        if self._app_context is None:
            return

        task_id = self._active_task_id()
        selected_event = ChangedFileSelectedEvent(
            source="changed_files_panel",
            task_id=task_id,
            file_path=data.file_path,
            change_type=data.change_type,
        )

        # Emit lightweight detail placeholder now; resolver can replace with richer content.
        detail_event = ChangedFileDetailEvent(
            source="changed_files_panel",
            task_id=task_id,
            file_path=data.file_path,
            change_type=data.change_type,
            detail_markdown=self._build_detail_markdown(data),
            title=self._build_detail_title(data),
            summary=self._build_detail_summary(data),
            diff_lines=self._build_detail_diff_lines(data),
        )

        # Schedule async emission correctly from Textual's callback loop.
        self.call_after_refresh(
            lambda: self.run_worker(
                self._emit_async(selected_event, detail_event),
                exclusive=False,
            )
        )

    async def _emit_async(
        self,
        selected_event: ChangedFileSelectedEvent,
        detail_event: ChangedFileDetailEvent,
    ) -> None:
        if self._app_context is None:
            return
        bus = self._app_context.event_bus
        await bus.emit(selected_event)
        await bus.emit(detail_event)

    def _build_detail_markdown(self, data: _ChangedFileRowData) -> str:
        title = self._build_detail_title(data)
        diff_lines = self._build_detail_diff_lines(data)
        if not diff_lines:
            summary = self._build_detail_summary(data)
            return f"### {title}\n\n_{summary}_"

        rendered_lines: list[str] = []
        for line in diff_lines:
            if line.kind == "added":
                rendered_lines.append(
                    f"[green]+ {line.text}[/green]" if line.text else "[green]+[/green]"
                )
            elif line.kind == "removed":
                rendered_lines.append(
                    f"[red]- {line.text}[/red]" if line.text else "[red]-[/red]"
                )

        if not rendered_lines:
            summary = self._build_detail_summary(data)
            return f"### {title}\n\n_{summary}_"

        return f"### {title}\n\n" + "\n".join(rendered_lines)

    def _build_detail_title(self, data: _ChangedFileRowData) -> str:
        label = (data.change_type or "changed").lower()
        file_name = Path(data.file_path).name or data.file_path
        return f"{file_name} ({label})"

    def _build_detail_summary(self, data: _ChangedFileRowData) -> str:
        snapshot = self._load_tracked_texts(data)
        if snapshot["error"]:
            return f"Preview unavailable ({snapshot['error']})."
        if snapshot["tracked_change"] is None:
            return "Preview unavailable (file is not tracked)."

        change = (data.change_type or "").lower()
        original_text = snapshot["original_text"]
        current_text = snapshot["current_text"]

        if change == "created":
            if current_text is None:
                return "Preview unavailable."
            return "Created file preview."
        if change == "deleted":
            if original_text is None:
                return "Preview unavailable."
            return "Deleted file preview."
        if original_text is None or current_text is None:
            return "Preview unavailable."
        return "Modified file preview."

    def _build_detail_diff_lines(
        self, data: _ChangedFileRowData
    ) -> list[ChangedFileDiffLine]:
        snapshot = self._load_tracked_texts(data)
        if snapshot["error"]:
            return []
        tracked_change = snapshot["tracked_change"]
        if tracked_change is None:
            return []

        change = (data.change_type or "").lower()
        original_text = snapshot["original_text"]
        current_text = snapshot["current_text"]

        if change == "created":
            if current_text is None:
                return []
            created = [
                ChangedFileDiffLine(kind="added", text=line)
                for line in current_text.splitlines()
            ]
            return created if created else [ChangedFileDiffLine(kind="added", text="")]

        if change == "deleted":
            if original_text is None:
                return []
            deleted = [
                ChangedFileDiffLine(kind="removed", text=line)
                for line in original_text.splitlines()
            ]
            return (
                deleted if deleted else [ChangedFileDiffLine(kind="removed", text="")]
            )

        if original_text is None or current_text is None:
            return []

        old_lines = original_text.splitlines()
        new_lines = current_text.splitlines()
        matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)

        result: list[ChangedFileDiffLine] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            if tag in {"replace", "delete"}:
                for line in old_lines[i1:i2]:
                    result.append(ChangedFileDiffLine(kind="removed", text=line))
            if tag in {"replace", "insert"}:
                for line in new_lines[j1:j2]:
                    result.append(ChangedFileDiffLine(kind="added", text=line))

        return result

    def _load_tracked_texts(self, data: _ChangedFileRowData) -> dict:
        if self._app_context is None:
            return {
                "tracked_change": None,
                "original_text": None,
                "current_text": None,
                "error": "missing app context",
            }

        tracker = getattr(self._app_context, "file_tracker", None)
        if tracker is None:
            return {
                "tracked_change": None,
                "original_text": None,
                "current_text": None,
                "error": "missing file tracker",
            }

        tracked_change = tracker.get_change(data.file_path)
        if tracked_change is None:
            return {
                "tracked_change": None,
                "original_text": None,
                "current_text": None,
                "error": "",
            }

        current_path = tracked_change.path
        original_text = tracked_change.original_content

        try:
            current_exists = current_path.exists() and current_path.is_file()
            current_text = (
                current_path.read_text(encoding="utf-8", errors="replace")
                if current_exists
                else None
            )
        except Exception as exc:
            return {
                "tracked_change": tracked_change,
                "original_text": original_text,
                "current_text": None,
                "error": f"read error: {exc}",
            }

        return {
            "tracked_change": tracked_change,
            "original_text": original_text,
            "current_text": current_text,
            "error": "",
        }

    def _remove_change(self, file_path: str) -> None:
        key = self._normalize_path(file_path)
        self._changes.pop(key, None)
        row = self._rows.pop(key, None)
        if row is not None:
            row.remove()

        if self._selected_path == key:
            self._selected_path = None

        self._update_empty_state()
        self._update_visibility()

    def _hydrate_from_tracker(self) -> None:
        if self._app_context is None:
            return
        tracker = getattr(self._app_context, "file_tracker", None)
        if tracker is None:
            return

        self.clear()
        for change in tracker.get_changes():
            rel_path = tracker.to_relative_path_str(change.path)
            self._upsert_change(
                file_path=rel_path,
                change_type=change.change_type.value,
            )

    # ── UI state helpers ────────────────────────────────────────

    def _update_empty_state(self) -> None:
        empty = self.query_one("#changed_files_empty", Static)
        has_items = len(self._changes) > 0
        empty.set_class(has_items, "-hidden")

    def _update_visibility(self) -> None:
        has_items = len(self._changes) > 0
        self.set_class(not has_items, "-hidden")

    def _list_view(self) -> VerticalScroll:
        return self.query_one("#changed_files_list", VerticalScroll)

    def _active_task_id(self) -> str:
        if self._app_context is None:
            return ""
        state_manager = getattr(self._app_context, "state_manager", None)
        if state_manager is None:
            return ""
        # Best-effort: state manager currently does not expose active task directly.
        return ""

    @staticmethod
    def _normalize_path(path: str) -> str:
        text = (path or "").strip()
        if not text:
            return "<unknown>"
        return Path(text).as_posix()
