from agent_cli.ux.tui.widgets.base import BaseWidget


class BodyWidget(BaseWidget):
    """The main body widget containing the content area."""

    DEFAULT_CSS = """
    BodyWidget {
        dock: none;
        width: 100%;
        height: 100%;
        border: solid #2a2f35;
        }
    """

    def __init__(self):
        super().__init__(id="body", components=[])
