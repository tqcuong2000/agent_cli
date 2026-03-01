from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from textual import events
from textual.containers import Container, Horizontal
from textual.css.query import NoMatches

from agent_cli.ux.tui.views.body.panel_window import PanelWindowContainer
from agent_cli.ux.tui.views.body.text_window import TextWindowContainer

if TYPE_CHECKING:
    from agent_cli.core.bootstrap import AppContext


class BodyContainer(Container):
    """The main body container holding the content area."""

    DEFAULT_CSS = """
    BodyContainer {
        dock: none;
        width: 100%;
        height: 100%;
        border: solid #2a2f35;
    }
    """

    def __init__(self, app_context: Optional["AppContext"] = None):
        super().__init__(id="body")
        self._app_context = app_context

    def compose(self):
        with Horizontal():
            yield TextWindowContainer(app_context=self._app_context)
            yield PanelWindowContainer(app_context=self._app_context)

    def on_resize(self, event: events.Resize) -> None:
        try:
            panel = self.query_one(PanelWindowContainer)
            if event.size.width < 100:
                panel.display = False
            else:
                panel.display = True
        except NoMatches:
            pass
