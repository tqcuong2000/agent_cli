from textual.containers import Container, Vertical
from agent_cli.ux.tui.views.body.panel.context_container import ContextContainer

class PanelWindowContainer(Container):
    """A blank container for the panel window."""

    DEFAULT_CSS = """
    PanelWindowContainer {
        width: 1fr;
        min-width: 30;
        height: 100%;
        border-left: solid $panel 50%;
    }
    """

    def __init__(self, **kwargs):
        if "id" not in kwargs:
            kwargs["id"] = "panel_window"
        super().__init__(**kwargs)

    def compose(self):
        with Vertical():
            yield ContextContainer()
