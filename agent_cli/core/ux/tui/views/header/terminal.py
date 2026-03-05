from textual.widgets import Static


class TerminalComponent(Static):
    """Component to display terminal output."""

    DEFAULT_CSS = """
    TerminalComponent {
        height: 1;
        content-align: center middle;
        background: $panel;
        color: $text;
        padding: 0 1;
        width: auto;
    }

    TerminalComponent:hover {
        background: $panel-lighten-1;
        color: $text;
    }
    """

    def __init__(self, content: str = ">_", **kwargs):
        comp_id = kwargs.pop("id", "terminal")
        super().__init__(content, id=comp_id, **kwargs)
