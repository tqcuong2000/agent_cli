from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from textual.containers import Container, Vertical

from agent_cli.core.ux.tui.views.body.panel.changed_file import ChangedFilesPanel
from agent_cli.core.ux.tui.views.body.panel.context_container import ContextContainer

if TYPE_CHECKING:
    from agent_cli.core.infra.registry.bootstrap import AppContext


class PanelWindowContainer(Container):
    """Right-side panel container (context + changed files)."""

    DEFAULT_CSS = ""

    def __init__(self, app_context: Optional["AppContext"] = None, **kwargs):
        if "id" not in kwargs:
            kwargs["id"] = "panel_window"
        super().__init__(**kwargs)
        self._app_context = app_context

    def compose(self):
        with Vertical():
            yield ContextContainer()
            yield ChangedFilesPanel(app_context=self._app_context)
