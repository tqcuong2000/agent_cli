from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from agent_cli.core.events.event_bus import AsyncEventBus
from agent_cli.core.events.events import StateChangeEvent
from agent_cli.ux.tui.views.header.status import StatusContainer


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
        status.update_effort("high")
        await pilot.pause()

        assert str(status.query_one("#active_agent", Static).content) == "coder"
        assert str(status.query_one("#model", Static).content) == "gpt-4o"
        assert str(status.query_one("#effort", Static).content) == "HIGH"


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
