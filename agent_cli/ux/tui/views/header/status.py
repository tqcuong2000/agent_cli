from __future__ import annotations

from typing import List, Optional, Set

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import Static

from agent_cli.core.events.events import BaseEvent, StateChangeEvent


class StatusContainer(Container):
    """A container to display status information.

    Mode, model, and effort are **reactive** — changing them
    automatically updates the corresponding ``Static`` widget.
    """

    DEFAULT_CSS = """
    StatusContainer {
        height: 1;
        width: 100%;
        background: $background;
        color: $text;
    }

    StatusContainer Horizontal {
        padding: 0 1;
        width: 100%;
        height: 100%;
        align: left middle;
    }

    StatusContainer .spacer {
        width: 1fr;
    }

    StatusContainer #shortcuts {
        width: auto;
        color: $panel-lighten-1;
    }

    StatusContainer .shortcut_key {
        color: $text;
        width: auto;
    }

    StatusContainer .shortcut_action {
        color: $panel-lighten-2;
        width: auto;
    }

    StatusContainer .shortcut_separator {
        color: $panel-lighten-1;
        width: auto;
    }

    StatusContainer .mode {
        color: $accent;
        width: auto;
    }

    StatusContainer .model {
        color: $text;
        width: auto;
    }

    StatusContainer .effort {
        color: $accent;
        width: auto;
    }

    StatusContainer .agent_indicator {
        color: $accent;
        width: auto;
    }

    StatusContainer .agent_state {
        color: $text;
        width: auto;
    }

    StatusContainer .-hidden {
        display: none;
    }
    """

    # ── Reactive state ───────────────────────────────────────────

    mode: reactive[str] = reactive("Plan")
    model: reactive[str] = reactive("gemini-3.1-pro-preview")
    effort: reactive[str] = reactive("xHigh")
    agent_state: reactive[str] = reactive("Idle")
    agent_indicator: reactive[str] = reactive(".")

    SPINNER_FRAMES = ["|", "/", "-", "\\"]

    def __init__(self, **kwargs):
        if "id" not in kwargs:
            kwargs["id"] = "status_bar"
        super().__init__(**kwargs)
        self._subscriptions: List[str] = []
        self._working_task_ids: Set[str] = set()
        self._paused_task_ids: Set[str] = set()
        self._frame_index = 0
        self._spinner_timer: Optional[Timer] = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static(
                self.agent_indicator, id="agent_indicator", classes="agent_indicator"
            )
            yield Static(" ", id="agent_sep_1", classes="shortcut_separator")
            yield Static(self.mode, id="mode", classes="mode")
            yield Static(" ● ", classes="shortcut_separator")
            yield Static(self.model, id="model", classes="model")
            yield Static(" ● ", classes="shortcut_separator")
            yield Static(self.effort, id="effort", classes="effort")
            yield Static(" ● ", id="agent_sep_2", classes="shortcut_separator")
            yield Static(self.agent_state, id="agent_state", classes="agent_state")
            yield Static(" ", id="spacer", classes="spacer")
            yield Static("tab ", classes="shortcut_key")
            yield Static("mode", classes="shortcut_action")
            yield Static(" | ", classes="shortcut_separator")
            yield Static("ctrl+p ", classes="shortcut_key")
            yield Static("commands", classes="shortcut_action")
            yield Static(" | ", classes="shortcut_separator")
            yield Static("ctrl+e ", classes="shortcut_key")
            yield Static("efforts", classes="shortcut_action")

    def on_mount(self) -> None:
        app_context = getattr(self.app, "app_context", None)
        event_bus = getattr(app_context, "event_bus", None)
        if event_bus is None:
            return

        self._subscriptions.append(
            event_bus.subscribe("StateChangeEvent", self._on_state_change, priority=40)
        )
        self.call_after_refresh(self._sync_agent_status)

    def on_unmount(self) -> None:
        self._stop_spinner()

        app_context = getattr(self.app, "app_context", None)
        event_bus = getattr(app_context, "event_bus", None)
        if event_bus is None:
            return

        for subscription_id in self._subscriptions:
            event_bus.unsubscribe(subscription_id)
        self._subscriptions.clear()

    # ── Watchers ─────────────────────────────────────────────────

    def watch_mode(self, value: str) -> None:
        try:
            self.query_one("#mode", Static).update(value)
        except Exception:
            pass  # Widget not mounted yet

    def watch_model(self, value: str) -> None:
        try:
            self.query_one("#model", Static).update(value)
        except Exception:
            pass

    def watch_effort(self, value: str) -> None:
        try:
            self.query_one("#effort", Static).update(value)
        except Exception:
            pass

    def watch_agent_state(self, value: str) -> None:
        try:
            self.query_one("#agent_state", Static).update(value)
        except Exception:
            pass

    def watch_agent_indicator(self, value: str) -> None:
        try:
            self.query_one("#agent_indicator", Static).update(value)
        except Exception:
            pass

    # ── Public API (called by command handlers) ──────────────────

    def update_mode(self, value: str) -> None:
        """Update the displayed execution mode."""
        self.mode = value.capitalize()

    def update_model(self, value: str) -> None:
        """Update the displayed model name."""
        self.model = value

    def update_effort(self, value: str) -> None:
        """Update the displayed effort level."""
        self.effort = value.upper()

    # ── Event handlers ───────────────────────────────────────────

    async def _on_state_change(self, event: BaseEvent) -> None:
        if not isinstance(event, StateChangeEvent):
            return

        task_id = event.task_id
        next_state = (event.to_state or "").upper()

        if next_state == "WORKING" and task_id:
            self._working_task_ids.add(task_id)
            self._paused_task_ids.discard(task_id)
        elif next_state == "AWAITING_INPUT" and task_id:
            self._working_task_ids.discard(task_id)
            self._paused_task_ids.add(task_id)
        elif next_state in {"SUCCESS", "FAILED", "CANCELLED"} and task_id:
            self._working_task_ids.discard(task_id)
            self._paused_task_ids.discard(task_id)

        self._sync_agent_status()

    def _sync_agent_status(self) -> None:
        working_count = len(self._working_task_ids)
        paused_count = len(self._paused_task_ids)

        if working_count > 0:
            self.agent_state = f"Working ({working_count})"
            self._set_agent_visible(True)
            self._start_spinner()
            return

        if paused_count > 0:
            self.agent_state = f"Awaiting input ({paused_count})"
            self._set_agent_visible(True)
            self._stop_spinner()
            self.agent_indicator = "!"
            return

        self.agent_state = "Idle"
        self._stop_spinner()
        self.agent_indicator = "."
        self._set_agent_visible(False)

    def _set_agent_visible(self, visible: bool) -> None:
        hidden = not visible
        if not self.is_mounted:
            return
        try:
            self.query_one("#agent_indicator", Static).set_class(hidden, "-hidden")
            self.query_one("#agent_state", Static).set_class(hidden, "-hidden")
            self.query_one("#agent_sep_1", Static).set_class(hidden, "-hidden")
            self.query_one("#agent_sep_2", Static).set_class(hidden, "-hidden")
        except Exception:
            pass

    def _start_spinner(self) -> None:
        if self._spinner_timer is not None:
            return

        self._frame_index = 0
        self.agent_indicator = self.SPINNER_FRAMES[self._frame_index]
        self._spinner_timer = self.set_interval(0.12, self._tick_spinner)

    def _tick_spinner(self) -> None:
        if not self._working_task_ids:
            return
        self._frame_index = (self._frame_index + 1) % len(self.SPINNER_FRAMES)
        self.agent_indicator = self.SPINNER_FRAMES[self._frame_index]

    def _stop_spinner(self) -> None:
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
