from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from agent_cli.core.infra.events.event_bus import AsyncEventBus
from agent_cli.core.infra.events.events import SettingsChangedEvent, StateChangeEvent
from agent_cli.core.ux.tui.views.main.status.status import StatusContainer


class _StatusHostApp(App):
    def __init__(self, bus: AsyncEventBus | None = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if bus is not None:
            self.app_context = SimpleNamespace(event_bus=bus)

    def compose(self) -> ComposeResult:
        yield StatusContainer()


@pytest.mark.asyncio
async def test_status_bar_manual_update_helpers():
    app = _StatusHostApp()

    async with app.run_test() as pilot:
        status = app.query_one(StatusContainer)

        status.update_active_agent("coder")
        status.update_model("gpt-4o")
        await pilot.pause()

        assert str(status.query_one("#active_agent", Static).content) == "coder"
        assert str(status.query_one("#model", Static).content) == "gpt-4o"


@pytest.mark.asyncio
async def test_status_bar_tracks_working_paused_and_idle_from_state_events():
    bus = AsyncEventBus()
    app = _StatusHostApp(bus=bus)

    async with app.run_test() as pilot:
        status = app.query_one(StatusContainer)
        await pilot.pause()

        assert str(status.query_one("#agent_state", Static).content) == "Idle"
        assert str(status.query_one("#agent_indicator", Static).content) == "."
        assert status.query_one("#agent_state", Static).has_class("-hidden")
        assert status.query_one("#agent_indicator", Static).has_class("-hidden")

        await bus.publish(
            StateChangeEvent(
                source="state_manager",
                task_id="task-1",
                from_state="ROUTING",
                to_state="WORKING",
            )
        )
        await pilot.pause()

        assert str(status.query_one("#agent_state", Static).content) == "Working (1)"
        assert status._spinner_timer is not None
        assert not status.query_one("#agent_state", Static).has_class("-hidden")
        assert not status.query_one("#agent_indicator", Static).has_class("-hidden")

        first = str(status.query_one("#agent_indicator", Static).content)
        await asyncio.sleep(0.15)
        await pilot.pause()
        second = str(status.query_one("#agent_indicator", Static).content)
        assert first != second

        await bus.publish(
            StateChangeEvent(
                source="state_manager",
                task_id="task-1",
                from_state="WORKING",
                to_state="AWAITING_INPUT",
            )
        )
        await pilot.pause()

        assert (
            str(status.query_one("#agent_state", Static).content)
            == "Awaiting input (1)"
        )
        assert str(status.query_one("#agent_indicator", Static).content) == "!"
        assert status._spinner_timer is None

        await bus.publish(
            StateChangeEvent(
                source="state_manager",
                task_id="task-2",
                from_state="ROUTING",
                to_state="WORKING",
            )
        )
        await bus.publish(
            StateChangeEvent(
                source="state_manager",
                task_id="task-3",
                from_state="ROUTING",
                to_state="WORKING",
            )
        )
        await pilot.pause()

        assert str(status.query_one("#agent_state", Static).content) == "Working (2)"

        await bus.publish(
            StateChangeEvent(
                source="state_manager",
                task_id="task-2",
                from_state="WORKING",
                to_state="SUCCESS",
            )
        )
        await pilot.pause()

        assert str(status.query_one("#agent_state", Static).content) == "Working (1)"

        await bus.publish(
            StateChangeEvent(
                source="state_manager",
                task_id="task-3",
                from_state="WORKING",
                to_state="FAILED",
            )
        )
        await bus.publish(
            StateChangeEvent(
                source="state_manager",
                task_id="task-1",
                from_state="AWAITING_INPUT",
                to_state="SUCCESS",
            )
        )
        await pilot.pause()

        assert str(status.query_one("#agent_state", Static).content) == "Idle"
        assert str(status.query_one("#agent_indicator", Static).content) == "."
        assert status._spinner_timer is None
        assert status.query_one("#agent_state", Static).has_class("-hidden")
        assert status.query_one("#agent_indicator", Static).has_class("-hidden")


@pytest.mark.asyncio
async def test_status_bar_updates_model_from_settings_event():
    bus = AsyncEventBus()
    app = _StatusHostApp(bus=bus)

    async with app.run_test() as pilot:
        status = app.query_one(StatusContainer)
        await pilot.pause()

        await bus.publish(
            SettingsChangedEvent(
                source="cmd_model",
                setting_name="default_model",
                new_value="gpt-4o-mini",
            )
        )
        await pilot.pause()

        assert str(status.query_one("#model", Static).content) == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_status_bar_effort_value_shows_and_hides_from_settings_event():
    bus = AsyncEventBus()
    app = _StatusHostApp(bus=bus)

    async with app.run_test() as pilot:
        status = app.query_one(StatusContainer)
        await pilot.pause()

        effort_widget = status.query_one("#effort_values", Static)
        assert effort_widget.has_class("-hidden")
        assert str(effort_widget.content) == ""

        await bus.publish(
            SettingsChangedEvent(
                source="shortcut_ctrl_e",
                setting_name="effort",
                new_value="high",
            )
        )
        await pilot.pause()

        assert not effort_widget.has_class("-hidden")
        assert str(effort_widget.content) == "high"

        await bus.publish(
            SettingsChangedEvent(
                source="shortcut_ctrl_e",
                setting_name="effort",
                new_value="auto",
            )
        )
        await pilot.pause()

        assert effort_widget.has_class("-hidden")
        assert str(effort_widget.content) == ""


@pytest.mark.asyncio
async def test_status_bar_click_effort_value_cycles_effort(monkeypatch: pytest.MonkeyPatch):
    bus = AsyncEventBus()
    parser = SimpleNamespace(context=SimpleNamespace())
    app = _StatusHostApp(bus=bus)
    app.app_context = SimpleNamespace(event_bus=bus, command_parser=parser)
    cycle = AsyncMock()
    monkeypatch.setattr("agent_cli.core.ux.commands.handlers.core.cycle_effort", cycle)

    async with app.run_test() as pilot:
        status = app.query_one(StatusContainer)
        status.update_effort("high")
        await pilot.pause()

        effort_widget = status.query_one("#effort_values", Static)
        fake_event = SimpleNamespace(widget=effort_widget, stop=lambda: None)
        await status.on_click(fake_event)  # type: ignore[arg-type]

        cycle.assert_awaited_once_with(parser.context)
