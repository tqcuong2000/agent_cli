from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from textual.app import App, ComposeResult
from textual.widgets import RichLog, Static

from agent_cli.core.infra.events.event_bus import AsyncEventBus
from agent_cli.core.infra.events.events import (
    TerminalExitedEvent,
    TerminalLogEvent,
    TerminalSpawnedEvent,
)
from agent_cli.core.ux.tui.views.common.tabbed_container import TabDefinition
from agent_cli.core.ux.tui.views.common.tabbed_container import TabbedContainer
from agent_cli.core.ux.tui.views.main.panel.panel_window import PanelWindowContainer
from agent_cli.core.ux.tui.views.main.panel.terminal_widget import TerminalOutputWidget

if TYPE_CHECKING:
    from agent_cli.core.infra.registry.bootstrap import AppContext


class _TabbedHostApp(App):
    def compose(self) -> ComposeResult:
        yield TabbedContainer(id="tabs")


class _PanelHostApp(App):
    def __init__(
        self,
        bus: AsyncEventBus,
        *args,
        exit_remove_delay: float = 3.0,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.ctx = cast("AppContext", SimpleNamespace(event_bus=bus))
        self._exit_remove_delay = exit_remove_delay

    def compose(self) -> ComposeResult:
        yield PanelWindowContainer(
            app_context=self.ctx,
            exit_remove_delay=self._exit_remove_delay,
        )


class _TerminalHostApp(App):
    def compose(self) -> ComposeResult:
        yield TerminalOutputWidget("term_wrap", "python -m demo")


@pytest.mark.asyncio
async def test_tabbed_container_uses_stable_tab_ids() -> None:
    app = _TabbedHostApp()

    async with app.run_test() as pilot:
        tabs = app.query_one(TabbedContainer)
        await pilot.pause()

        first_id = tabs.add_tab(
            TabDefinition(title="First", content=Static("one")),
            activate=True,
            tab_id="first",
        )
        second_id = tabs.add_tab(
            TabDefinition(title="Second", content=Static("two")),
            tab_id="second",
        )
        await pilot.pause()

        assert first_id == "first"
        assert second_id == "second"
        assert tabs.active_tab_id == "first"
        assert tabs.tab_count == 2

        tabs.activate_tab("second")
        await pilot.pause()
        assert tabs.active_tab_id == "second"

        tabs.update_tab_title("second", "Second Updated")
        await pilot.pause()
        assert tabs.get_tab_title("second") == "Second Updated"

        removed = tabs.remove_tab("first")
        await pilot.pause()
        assert removed is not None
        assert tabs.tab_count == 1
        assert tabs.active_tab_id == "second"


@pytest.mark.asyncio
async def test_panel_window_defaults_to_changes_tab() -> None:
    bus = AsyncEventBus()
    app = _PanelHostApp(bus)

    async with app.run_test() as pilot:
        panel = app.query_one(PanelWindowContainer)
        tabs = panel.query_one(TabbedContainer)
        await pilot.pause()

        assert tabs.tab_count == 1
        assert tabs.active_tab_id == "changes"
        assert tabs.get_tab_title("changes") == "Changes"
        title = str(tabs.query_one("#tab-title", Static).content)
        assert "Changes" in title


@pytest.mark.asyncio
async def test_terminal_spawn_and_log_stream_adds_panel_tab() -> None:
    bus = AsyncEventBus()
    app = _PanelHostApp(bus)

    async with app.run_test() as pilot:
        panel = app.query_one(PanelWindowContainer)
        tabs = panel.query_one(TabbedContainer)
        await pilot.pause()

        await bus.publish(
            TerminalSpawnedEvent(
                source="test",
                terminal_id="term_1",
                command="python -m http.server",
            )
        )
        await bus.publish(
            TerminalLogEvent(
                source="test",
                terminal_id="term_1",
                content="Serving HTTP on 0.0.0.0",
            )
        )
        await pilot.pause()

        assert tabs.tab_count == 2
        assert tabs.active_tab_id == "terminal:term_1"
        assert tabs.get_tab_title("terminal:term_1") == "Terminal: python -m http.server"

        widget = panel.query_one(TerminalOutputWidget)
        assert "Serving HTTP on 0.0.0.0" in widget.output_text

        tabs.switch_tab(-1)
        await pilot.pause()
        assert tabs.active_tab_id == "changes"

        tabs.switch_tab(1)
        await pilot.pause()
        assert tabs.active_tab_id == "terminal:term_1"


@pytest.mark.asyncio
async def test_terminal_exit_updates_title_then_removes_tab() -> None:
    bus = AsyncEventBus()
    app = _PanelHostApp(bus, exit_remove_delay=1.0)

    async with app.run_test() as pilot:
        panel = app.query_one(PanelWindowContainer)
        tabs = panel.query_one(TabbedContainer)
        await pilot.pause()

        await bus.publish(
            TerminalSpawnedEvent(
                source="test",
                terminal_id="term_2",
                command="npm run dev --watch",
            )
        )
        await pilot.pause()

        await bus.publish(
            TerminalExitedEvent(
                source="test",
                terminal_id="term_2",
                exit_code=0,
            )
        )
        await pilot.pause()

        assert tabs.get_tab_title("terminal:term_2") == "Terminal: npm run dev --watch [exit: 0]"
        panel._remove_terminal_tab("term_2")
        await pilot.pause()

        assert tabs.tab_count == 1
        assert tabs.active_tab_id == "changes"


@pytest.mark.asyncio
async def test_terminal_widget_wraps_output_in_narrow_layout() -> None:
    app = _TerminalHostApp()

    async with app.run_test(size=(30, 20)) as pilot:
        widget = app.query_one(TerminalOutputWidget)
        await pilot.pause()

        widget.append_line("[16:20:37] [Server thread/INFO]: Loading properties")
        await pilot.pause()

        log = app.query_one(RichLog)
        assert log.virtual_size.height > 1
        assert log.render_line(0).text.rstrip()
        wrapped_text = "\n".join(
            log.render_line(y).text.rstrip()
            for y in range(log.virtual_size.height)
        )
        assert "Loading" in wrapped_text
        assert "properties" in wrapped_text
