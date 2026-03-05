from __future__ import annotations

from typing import List, Optional, Set

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import Static

from agent_cli.core.infra.events.events import (
    BaseEvent,
    SettingsChangedEvent,
    StateChangeEvent,
)


class StatusContainer(Container):
    """A container to display status information.

    Active agent and model are **reactive** — changing them
    automatically updates the corresponding ``Static`` widget.
    """

    DEFAULT_CSS = ""

    # ── Reactive state ───────────────────────────────────────────

    active_agent: reactive[str] = reactive("default")
    model: reactive[str] = reactive("gemini-3.1-pro-preview")
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
            yield Static(" ", classes="shortcut_separator")
            yield Static(self.active_agent, id="active_agent", classes="active_agent")
            yield Static(" ● ", classes="shortcut_separator")
            yield Static(self.model, id="model", classes="model")
            yield Static(" ● ", classes="shortcut_separator")
            yield Static(self.agent_state, id="agent_state", classes="agent_state")
            yield Static(" ● ", classes="shortcut_separator")
            yield Static(id="effort_values", classes="effort_values")
            yield Static(" ", id="spacer", classes="spacer")
            yield Static("tab ", classes="shortcut_key")
            yield Static("agent", classes="shortcut_action")
            yield Static(" | ", classes="shortcut_separator")
            yield Static("ctrl+p ", classes="shortcut_key")
            yield Static("commands", classes="shortcut_action")

    def on_mount(self) -> None:
        app_context = getattr(self.app, "app_context", None)
        event_bus = getattr(app_context, "event_bus", None)
        if event_bus is None:
            return

        self._subscriptions.append(
            event_bus.subscribe("StateChangeEvent", self._on_state_change, priority=40)
        )
        self._subscriptions.append(
            event_bus.subscribe(
                "SettingsChangedEvent",
                self._on_settings_changed,
                priority=40,
            )
        )
        self.call_after_refresh(self._sync_agent_status)
        self.call_after_refresh(self._sync_active_agent)

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

    def watch_model(self, value: str) -> None:
        try:
            self.query_one("#model", Static).update(value)
        except Exception:
            pass

    def watch_active_agent(self, value: str) -> None:
        try:
            self.query_one("#active_agent", Static).update(value)
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

    def update_model(self, value: str) -> None:
        """Update the displayed model name."""
        self.model = value

    def update_active_agent(self, value: str) -> None:
        """Update the displayed active agent name."""
        self.active_agent = value

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

    async def _on_settings_changed(self, event: BaseEvent) -> None:
        if not isinstance(event, SettingsChangedEvent):
            return
        if event.setting_name == "active_agent":
            agent_name = str(event.new_value)
            self.update_active_agent(agent_name)

            # Sync model name from the new active agent
            app_context = getattr(self.app, "app_context", None)
            orchestrator = getattr(app_context, "orchestrator", None)
            if orchestrator is not None:
                agent = orchestrator.active_agent
                if agent is not None:
                    # Use the model name from the agent's provider for the most accurate display
                    model_name = getattr(agent.provider, "model_name", agent.config.model)
                    self.update_model(str(model_name))

    def _sync_active_agent(self) -> None:
        app_context = getattr(self.app, "app_context", None)
        orchestrator = getattr(app_context, "orchestrator", None)
        if orchestrator is None:
            return
        try:
            agent = orchestrator.active_agent
            self.update_active_agent(agent.name)

            # Sync model name from the active agent's provider
            # This ensures that even if we initialize with a non-default agent,
            # we show the correct model name.
            model_name = getattr(agent.provider, "model_name", agent.config.model)
            self.update_model(str(model_name))
        except Exception:
            return

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
