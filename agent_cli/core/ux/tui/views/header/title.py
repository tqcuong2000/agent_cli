from textual.widgets import Static


class TitleComponent(Static):
    """Component to display the application or conversation title."""

    DEFAULT_CSS = ""

    def __init__(self, content: str = "Engine CLI", **kwargs):
        comp_id = kwargs.pop("id", "title")
        super().__init__(content, id=comp_id, **kwargs)

    def update_title(self, content: str) -> None:
        """Update the rendered title text."""
        self.update(content)
