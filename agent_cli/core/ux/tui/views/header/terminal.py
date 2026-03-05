from textual.widgets import Static


class TerminalComponent(Static):
    """Component to display terminal output."""

    DEFAULT_CSS = ""

    def __init__(self, content: str = ">_", **kwargs):
        comp_id = kwargs.pop("id", "terminal")
        super().__init__(content, id=comp_id, **kwargs)
