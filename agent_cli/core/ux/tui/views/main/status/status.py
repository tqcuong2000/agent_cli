from __future__ import annotations

from typing import List, Optional, Set

from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import Static

from agent_cli.core.infra.config.config_models import EffortLevel, normalize_effort
from agent_cli.core.infra.events.events import (
    BaseEvent,
    SettingsChangedEvent,
    StateChangeEvent,
)


class StatusContainer(Container):
    """A container to display reactive runtime status information."""

    DEFAULT_CSS = ""

    active_agent: reactive[str] = reactive("default")
    model: reactive[str] = reactive("gemini-3.1-pro-preview")
    effort: reactive[str] = reactive(EffortLevel.AUTO.value)
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
                self.agent_indicator,
                id="agent_indicator",
                classes="agent_indicator -hidden",
            )
            yield Static(" ", id="agent_sep_1", classes="shortcut_separator -hidden")
            yield Static(
                self.agent_state, id="agent_state", classes="agent_state -hidden"
            )
            yield Static(" ", id="agent_sep_2", classes="shortcut_separator -hidden")

            yield Static(self.active_agent, id="active_agent", classes="active_agent")
            yield Static(" ● ", classes="shortcut_separator")
            yield Static(self.model, id="model", classes="model")

            yield Static(" ● ", id="effort_sep", classes="shortcut_separator -hidden")
            yield Static("", id="effort_values", classes="effort_values -hidden")

            yield Static(" ", id="spacer", classes="spacer")
            yield Static("tab ", classes="shortcut_key")
            yield Static("agent", classes="shortcut_action")
            yield Static(" | ", classes="shortcut_separator")
            yield Static("ctrl+p ", classes="shortcut_key")
            yield Static("commands", classes="shortcut_action")
            yield Static(" | ", classes="shortcut_separator")
            yield Static("ctrl+e ", classes="shortcut_key")
            yield Static("effort", classes="shortcut_action")

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
        self.call_after_refresh(self._sync_effort)

    def on_unmount(self) -> None:
        self._stop_spinner()

        app_context = getattr(self.app, "app_context", None)
        event_bus = getattr(app_context, "event_bus", None)
        if event_bus is None:
            return

        for subscription_id in self._subscriptions:
            event_bus.unsubscribe(subscription_id)
        self._subscriptions.clear()

    async def on_click(self, event: events.Click) -> None:
        widget_id = getattr(event.widget, "id", "") if event.widget is not None else ""
        if widget_id not in {"effort_values", "effort_sep"}:
            return
        event.stop()

        app_context = getattr(self.app, "app_context", None)
        command_parser = getattr(app_context, "command_parser", None)
        if command_parser is None:
            return

        from agent_cli.core.ux.commands.handlers.core import cycle_effort

        await cycle_effort(command_parser.context)

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

    def watch_effort(self, value: str) -> None:
        try:
            normalized = normalize_effort(value).value
        except Exception:
            normalized = EffortLevel.AUTO.value

        hidden = normalized == EffortLevel.AUTO.value
        display_value = "" if hidden else f"{normalized}"
        try:
            effort_widget = self.query_one("#effort_values", Static)
            effort_widget.update(display_value)
            effort_widget.set_class(hidden, "-hidden")
            self.query_one("#effort_sep", Static).set_class(hidden, "-hidden")
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

    def update_model(self, value: str) -> None:
        """Update the displayed model name."""
        self.model = value

    def update_active_agent(self, value: str) -> None:
        """Update the displayed active agent name."""
        self.active_agent = value

    def update_effort(self, value: str | EffortLevel | None) -> None:
        """Update the displayed desired effort value."""
        try:
            self.effort = normalize_effort(value).value
        except Exception:
            self.effort = EffortLevel.AUTO.value

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
        if event.setting_name == "default_model":
            self.update_model(str(event.new_value))
            return
        if event.setting_name in {"effort", "default_effort"}:
            self.update_effort(str(event.new_value))
            return
        if event.setting_name == "active_agent":
            agent_name = str(event.new_value)
            self.update_active_agent(agent_name)

            app_context = getattr(self.app, "app_context", None)
            orchestrator = getattr(app_context, "orchestrator", None)
            if orchestrator is not None:
                agent = orchestrator.active_agent
                if agent is not None:
                    model_name = getattr(
                        agent.provider, "model_name", agent.config.model
                    )
                    self.update_model(str(model_name))

    def _sync_active_agent(self) -> None:
        app_context = getattr(self.app, "app_context", None)
        orchestrator = getattr(app_context, "orchestrator", None)
        if orchestrator is None:
            return
        try:
            agent = orchestrator.active_agent
            self.update_active_agent(agent.name)
            model_name = getattr(agent.provider, "model_name", agent.config.model)
            self.update_model(str(model_name))
        except Exception:
            return

    def _sync_effort(self) -> None:
        app_context = getattr(self.app, "app_context", None)
        if app_context is None:
            self.update_effort(EffortLevel.AUTO.value)
            return

        session_manager = getattr(app_context, "session_manager", None)
        if session_manager is not None:
            try:
                active = session_manager.get_active()
                if active is not None:
                    self.update_effort(getattr(active, "desired_effort", None))
                    return
            except Exception:
                pass

        settings = getattr(app_context, "settings", None)
        if settings is not None:
            self.update_effort(getattr(settings, "default_effort", None))
            return

        self.update_effort(EffortLevel.AUTO.value)

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
