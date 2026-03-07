from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

from textual.app import ComposeResult
from textual.containers import Container

from agent_cli.core.infra.events.events import (
    BaseEvent,
    TerminalExitedEvent,
    TerminalLogEvent,
    TerminalSpawnedEvent,
)
from agent_cli.core.ux.tui.views.common.tabbed_container import TabDefinition
from agent_cli.core.ux.tui.views.common.tabbed_container import TabbedContainer
from agent_cli.core.ux.tui.views.main.panel.changed_file import ChangedFilesPanel
from agent_cli.core.ux.tui.views.main.panel.terminal_widget import TerminalOutputWidget

if TYPE_CHECKING:
    from agent_cli.core.infra.registry.bootstrap import AppContext


class PanelWindowContainer(Container):
    """Right-side shared panel container."""

    DEFAULT_CSS = ""

    def __init__(
        self,
        app_context: Optional["AppContext"] = None,
        *,
        exit_remove_delay: float = 3.0,
        **kwargs,
    ) -> None:
        if "id" not in kwargs:
            kwargs["id"] = "panel_window"
        super().__init__(**kwargs)
        self._app_context = app_context
        self._exit_remove_delay = max(float(exit_remove_delay), 0.0)
        self._subscriptions: list[str] = []
        self._terminal_tabs: dict[str, tuple[str, TerminalOutputWidget]] = {}
        self._removal_tasks: dict[str, asyncio.Task[None]] = {}
        self._changed_files_panel = ChangedFilesPanel(app_context=self._app_context)

    def compose(self) -> ComposeResult:
        yield TabbedContainer(id="panel_tabs")

    def on_mount(self) -> None:
        self._tabs().add_tab(
            TabDefinition(title="Changes", content=self._changed_files_panel),
            activate=True,
            tab_id="changes",
        )
        self._subscribe_events()

    def on_unmount(self) -> None:
        self._unsubscribe_events()
        for task in self._removal_tasks.values():
            task.cancel()
        self._removal_tasks.clear()

    def _subscribe_events(self) -> None:
        if self._app_context is None:
            return
        bus = self._app_context.event_bus
        self._subscriptions.append(
            bus.subscribe("TerminalSpawnedEvent", self._on_terminal_spawned, priority=50)
        )
        self._subscriptions.append(
            bus.subscribe("TerminalLogEvent", self._on_terminal_log, priority=50)
        )
        self._subscriptions.append(
            bus.subscribe("TerminalExitedEvent", self._on_terminal_exited, priority=50)
        )

    def _unsubscribe_events(self) -> None:
        if self._app_context is None:
            return
        bus = self._app_context.event_bus
        for sub_id in self._subscriptions:
            bus.unsubscribe(sub_id)
        self._subscriptions.clear()

    async def _on_terminal_spawned(self, event: BaseEvent) -> None:
        if not isinstance(event, TerminalSpawnedEvent):
            return
        if event.terminal_id in self._terminal_tabs:
            return

        widget = TerminalOutputWidget(
            terminal_id=event.terminal_id,
            command=event.command,
        )
        tab_id = self._tabs().add_tab(
            TabDefinition(
                title=self._build_terminal_title(event.command),
                content=widget,
            ),
            activate=True,
            tab_id=f"terminal:{event.terminal_id}",
        )
        self._terminal_tabs[event.terminal_id] = (tab_id, widget)

    async def _on_terminal_log(self, event: BaseEvent) -> None:
        if not isinstance(event, TerminalLogEvent):
            return
        tab_ref = self._terminal_tabs.get(event.terminal_id)
        if tab_ref is None:
            return
        _, widget = tab_ref
        widget.append_line(event.content)

    async def _on_terminal_exited(self, event: BaseEvent) -> None:
        if not isinstance(event, TerminalExitedEvent):
            return
        tab_ref = self._terminal_tabs.get(event.terminal_id)
        if tab_ref is None:
            return

        tab_id, widget = tab_ref
        widget.set_exited(event.exit_code)
        self._tabs().update_tab_title(
            tab_id,
            self._build_terminal_title(widget.command, exit_code=event.exit_code),
        )

        prior_task = self._removal_tasks.pop(event.terminal_id, None)
        if prior_task is not None:
            prior_task.cancel()

        self._removal_tasks[event.terminal_id] = asyncio.create_task(
            self._remove_terminal_after_delay(event.terminal_id),
            name=f"panel-terminal-remove:{event.terminal_id}",
        )

    async def _remove_terminal_after_delay(self, terminal_id: str) -> None:
        try:
            await asyncio.sleep(self._exit_remove_delay)
        except asyncio.CancelledError:
            return
        self._remove_terminal_tab(terminal_id)

    def _remove_terminal_tab(self, terminal_id: str) -> None:
        task = self._removal_tasks.pop(terminal_id, None)
        if task is not None and not task.done():
            task.cancel()

        tab_ref = self._terminal_tabs.pop(terminal_id, None)
        if tab_ref is None:
            return

        tab_id, _widget = tab_ref
        self._tabs().remove_tab(tab_id)

    def _tabs(self) -> TabbedContainer:
        return self.query_one("#panel_tabs", TabbedContainer)

    @staticmethod
    def _build_terminal_title(command: str, exit_code: int | None = None) -> str:
        compact = " ".join(str(command).split()).strip() or "terminal"
        short_command = compact if len(compact) <= 24 else f"{compact[:21]}..."
        if exit_code is None:
            return f"Terminal: {short_command}"
        return f"Terminal: {short_command} [exit: {exit_code}]"
