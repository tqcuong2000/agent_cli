from textual.widgets import Static


class TitleComponent(Static):
    """Component to display the application or conversation title."""

    DEFAULT_CSS = """
    TitleComponent {
        height: auto;
        content-align: left middle;
        text-style: bold;
        color: $text;
        width: auto;
        min-width: 15;
    }
    """

    def __init__(self, content: str = "Engine CLI", **kwargs):
        comp_id = kwargs.pop("id", "title")
        super().__init__(content, id=comp_id, **kwargs)
