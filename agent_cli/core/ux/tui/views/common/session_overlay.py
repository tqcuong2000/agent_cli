from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Input, Static

from agent_cli.core.infra.events.events import SessionLoadedEvent, SettingsChangedEvent
from agent_cli.core.runtime.session.base import SessionSummary

if TYPE_CHECKING:
    from textual.widget import Widget

    from agent_cli.core.infra.registry.bootstrap import AppContext
    from agent_cli.core.runtime.session.base import AbstractSessionManager


class SessionOverlay(Container):
    can_focus = True

    DEFAULT_CSS = """
    SessionOverlay {
        width: 100%;
        height: 100%;
        dock: bottom;
        background: transparent;
        align: center middle;
        layer: overlay;
        display: none;
    }

    SessionOverlay.visible {
        display: block;
    }

    SessionOverlay .session-popup {
        width: 70;
        min-width: 40;
        height: 24;
        background: $panel 40%;
        border: solid $panel;
        padding: 1 2;
    }

    SessionOverlay .title-row {
        layout: grid;
        grid-size: 2 1;
        grid-columns: 1fr auto;
        height: 1;
    }

    SessionOverlay .session-area {
        margin-top: 1;
        height: 1fr;
        overflow-y: auto;
        scrollbar-size: 0 0;
    }

    SessionOverlay .session-row {
        layout: grid;
        grid-size: 2 1;
        grid-columns: 1fr auto;
        height: 3;
        border: solid $panel;
        padding: 0 1;
    }

    SessionOverlay .session-row:hover {
        border: solid $primary;
    }

    SessionOverlay .session-row.selected {
        border: solid $primary;
        background: $boost;
    }

    SessionOverlay .title-left {
        text-style: bold;
        color: $primary;
    }

    SessionOverlay .title-right {
        text-align: right;
        color: $text-muted;
    }

    SessionOverlay .title-right:hover {
        color: $warning;
        text-style: bold;
    }

    SessionOverlay .session-time {
        color: $text-muted;
        text-align: right;
    }

    SessionOverlay .session-date {
        color: $accent-lighten-1;
    }

    SessionOverlay .session-name {
        color: $text;
    }

    SessionOverlay #session-rename-input {
        margin-top: 1;
        display: none;
    }

    SessionOverlay #session-rename-input.visible {
        display: block;
    }

    SessionOverlay .footer-hint {
        margin-top: 1;
        color: $text-muted;
    }

    SessionOverlay .empty-state {
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._summaries: List[SessionSummary] = []
        self._selected_index = 0
        self._renaming_session_id: Optional[str] = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="session-popup"):
            with Container(classes="title-row"):
                yield Static("Session Manager", classes="title-left")
                yield Static("esc", id="session-overlay-esc", classes="title-right")
            yield Container(id="session-list", classes="session-area")
            yield Input(
                placeholder="New session name (Enter to save, Esc to cancel)",
                id="session-rename-input",
            )
            yield Static(
                "[b]enter [dimgrey]load[/dimgrey] | ctrl+d [dimgrey]delete[/dimgrey] | "
                "ctrl+r [dimgrey]rename[/dimgrey][/b]",
                classes="footer-hint",
            )

    def show_overlay(self) -> None:
        self.refresh_sessions()
        self._hide_rename_input()
        self.add_class("visible")
        self.focus()

    def hide_overlay(self) -> None:
        self._hide_rename_input()
        self.remove_class("visible")

    async def on_key(self, event: events.Key) -> None:
        key = event.key.lower()
        if key == "escape":
            event.stop()
            if self._renaming_session_id is not None:
                self._hide_rename_input()
                self.focus()
                return
            self.hide_overlay()
            return

        if self._renaming_session_id is not None:
            return

        if key == "up":
            event.stop()
            self._move_selection(-1)
            return

        if key == "down":
            event.stop()
            self._move_selection(1)
            return

        if key == "enter":
            event.stop()
            await self._restore_selected_session()
            return

        if key == "ctrl+d":
            event.stop()
            self._delete_selected_session()
            return

        if key == "ctrl+r":
            event.stop()
            self._begin_rename_selected_session()
            return

    async def on_click(self, event: events.Click) -> None:
        widget = event.widget
        if widget is not None and widget.id == "session-overlay-esc":
            event.stop()
            self.hide_overlay()
            return

        row_id = self._extract_row_id(widget)
        if row_id is None:
            return

        event.stop()
        for idx, summary in enumerate(self._summaries):
            if summary.session_id == row_id:
                self._selected_index = idx
                self._update_selected_row_styles()
                await self._restore_selected_session()
                return

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "session-rename-input":
            return
        if self._renaming_session_id is None:
            return

        new_name = event.value.strip() or None
        if await self._rename_session(self._renaming_session_id, new_name):
            label = new_name or "(unnamed)"
            self._notify(f"Renamed session: {label}")
        self._hide_rename_input()
        self.refresh_sessions()
        self.focus()

    def refresh_sessions(self) -> None:
        manager = self._get_session_manager()
        if manager is None:
            self._summaries = []
            self._selected_index = 0
            self._render_session_rows(error="Session manager not configured.")
            return

        self._summaries = manager.list()
        if not self._summaries:
            self._selected_index = 0
        else:
            self._selected_index = max(
                0,
                min(self._selected_index, len(self._summaries) - 1),
            )
        self._render_session_rows()

    def _render_session_rows(self, error: Optional[str] = None) -> None:
        area = self.query_one("#session-list", Container)
        area.remove_children()

        if error:
            area.mount(Static(error, classes="empty-state"))
            return

        if not self._summaries:
            area.mount(Static("No saved sessions.", classes="empty-state"))
            return

        for idx, summary in enumerate(self._summaries):
            display_name = summary.display_name
            date_text = _format_date(summary.last_activity_at)
            left = Static(
                f"{display_name}\n[{'cyan'}] {date_text}[/]",
                classes="session-name",
            )
            active_dot = "[lime]●[/lime]" if summary.is_active else "[dim]●[/dim]"
            right = Static(
                f"{_relative_time(summary.last_activity_at)}  {active_dot}",
                classes="session-time",
            )
            row_classes = (
                "session-row selected" if idx == self._selected_index else "session-row"
            )
            row = Container(
                left,
                right,
                classes=row_classes,
            )
            setattr(row, "_session_id", summary.session_id)
            area.mount(row)
        
        # Ensure the selected row is visible after rendering
        self.call_after_refresh(self._update_selected_row_styles)

    def _update_selected_row_styles(self) -> None:
        area = self.query_one("#session-list", Container)
        rows = [child for child in area.children if isinstance(child, Container)]
        for i, row in enumerate(rows):
            if i == self._selected_index:
                row.add_class("selected")
                area.scroll_to_widget(row, animate=False)
            else:
                row.remove_class("selected")

    def _move_selection(self, delta: int) -> None:
        if not self._summaries:
            return
        new_index = max(
            0,
            min(self._selected_index + delta, len(self._summaries) - 1),
        )
        if new_index == self._selected_index:
            return
        self._selected_index = new_index
        self._update_selected_row_styles()

    async def _restore_selected_session(self) -> None:
        summary = self._selected_summary()
        if summary is None:
            return

        manager = self._get_session_manager()
        app_context = self._get_app_context()
        if manager is None or app_context is None:
            return

        try:
            session = manager.load(summary.session_id)
        except Exception as exc:
            self._notify(f"Failed to restore session: {exc}", severity="error")
            return

        # Hydrate memory immediately for context visibility commands.
        app_context.memory_manager.reset_working()
        loaded_messages = []
        for message in session.messages:
            if isinstance(message, dict):
                app_context.memory_manager.add_working_event(message)
                loaded_messages.append(message)

        if loaded_messages and app_context.event_bus:
            await app_context.event_bus.publish(
                SessionLoadedEvent(
                    session_id=session.session_id,
                    messages=loaded_messages,
                    source="session_overlay",
                )
            )

        if (
            session.active_model
            and session.active_model != app_context.settings.default_model
        ):
            switch_error = await self._switch_runtime_model(session.active_model)
            if switch_error:
                self._notify(
                    f"Session restored, but model switch failed: {switch_error}",
                    severity="warning",
                )
            else:
                self._notify(f"Restored session: {summary.display_name}")
        else:
            self._notify(f"Restored session: {summary.display_name}")

        await self._publish_session_title(session.name)
        self.hide_overlay()

    def _delete_selected_session(self) -> None:
        summary = self._selected_summary()
        if summary is None:
            return

        manager = self._get_session_manager()
        app_context = self._get_app_context()
        if manager is None:
            return

        removed = manager.delete(summary.session_id)
        if not removed:
            self._notify("Could not delete selected session.", severity="warning")
            return

        if summary.is_active and app_context is not None:
            app_context.memory_manager.reset_working()

        self._notify(f"Deleted session: {summary.display_name}")
        self.refresh_sessions()

    def _begin_rename_selected_session(self) -> None:
        summary = self._selected_summary()
        if summary is None:
            return

        rename_input = self.query_one("#session-rename-input", Input)
        rename_input.value = summary.name or ""
        rename_input.add_class("visible")
        rename_input.focus()
        self._renaming_session_id = summary.session_id

    def _hide_rename_input(self) -> None:
        rename_input = self.query_one("#session-rename-input", Input)
        rename_input.remove_class("visible")
        rename_input.value = ""
        self._renaming_session_id = None

    async def _rename_session(self, session_id: str, new_name: Optional[str]) -> bool:
        manager = self._get_session_manager()
        if manager is None:
            return False

        try:
            session = manager.load(session_id)
            session.name = new_name
            manager.save(session)
            await self._publish_session_title(new_name)
            return True
        except Exception as exc:
            self._notify(f"Failed to rename session: {exc}", severity="error")
            return False

    async def _publish_session_title(self, title: Optional[str]) -> None:
        app_context = self._get_app_context()
        if app_context is None:
            return
        event_bus = getattr(app_context, "event_bus", None)
        if event_bus is None:
            return
        await event_bus.publish(
            SettingsChangedEvent(
                setting_name="session_title",
                new_value=title or "Untitled session",
                source="session_overlay",
            )
        )

    async def _switch_runtime_model(self, model_name: str) -> Optional[str]:
        app_context = self._get_app_context()
        if app_context is None:
            return "app context unavailable"

        app_context.settings.default_model = model_name

        if app_context.orchestrator:
            try:
                agent = app_context.orchestrator.active_agent
                agent.provider = app_context.providers.get_provider(model_name)
                agent.config.model = model_name
            except Exception as exc:
                return str(exc)

        try:
            context_budget = app_context.data_registry.get_context_budget()
            token_counter = app_context.providers.get_token_counter(model_name)
            token_budget = app_context.providers.get_token_budget(
                model_name,
                response_reserve=4096,
                compaction_threshold=float(
                    context_budget.get("compaction_threshold", 0.80)
                ),
            )
            await app_context.memory_manager.on_model_changed(
                model_name,
                token_counter=token_counter,
                token_budget=token_budget,
            )
        except Exception as exc:
            return str(exc)

        try:
            await app_context.event_bus.publish(
                SettingsChangedEvent(
                    setting_name="default_model",
                    new_value=model_name,
                    source="session_overlay_restore",
                )
            )
        except Exception:
            pass

        self._update_status_bar(model=model_name)
        return None

    def _update_status_bar(self, *, model: Optional[str] = None) -> None:
        try:
            from agent_cli.core.ux.tui.views.header.status import StatusContainer

            status = self.app.query_one(StatusContainer)
            if model is not None:
                status.update_model(model)
        except Exception:
            pass

    def _selected_summary(self) -> Optional[SessionSummary]:
        if not self._summaries:
            return None
        return self._summaries[self._selected_index]

    def _get_app_context(self) -> Optional[AppContext]:
        app_context = getattr(self.app, "app_context", None)
        if app_context is None:
            return None
        return app_context

    def _get_session_manager(self) -> Optional[AbstractSessionManager]:
        app_context = self._get_app_context()
        if app_context is None:
            return None
        return app_context.session_manager

    def _extract_row_id(self, widget: Optional[Widget]) -> Optional[str]:
        current = widget
        while current is not None and current is not self:
            row_id = getattr(current, "_session_id", None)
            if isinstance(row_id, str) and row_id:
                return row_id
            current = current.parent
        return None

    def _notify(self, message: str, severity: str = "information") -> None:
        try:
            self.app.notify(message, severity=severity)
        except Exception:
            pass


def _format_date(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def _relative_time(value: datetime) -> str:
    now = datetime.now(timezone.utc)
    delta = now - value.astimezone(timezone.utc)
    seconds = max(int(delta.total_seconds()), 0)

    if seconds < 60:
        return "just now"

    minutes = seconds // 60
    if minutes < 60:
        unit = "minute" if minutes == 1 else "minutes"
        return f"{minutes} {unit} ago"

    hours = minutes // 60
    if hours < 24:
        unit = "hour" if hours == 1 else "hours"
        return f"{hours} {unit} ago"

    days = hours // 24
    if days < 30:
        unit = "day" if days == 1 else "days"
        return f"{days} {unit} ago"

    months = days // 30
    if months < 12:
        unit = "month" if months == 1 else "months"
        return f"{months} {unit} ago"

    years = months // 12
    unit = "year" if years == 1 else "years"
    return f"{years} {unit} ago"
