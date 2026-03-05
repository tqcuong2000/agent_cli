from textual import events
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Static

from agent_cli.core.ux.tui.views.common.kv_line import KVLine


class ContextContainer(Container):
    """A container for the context window."""

    DEFAULT_CSS = ""

    def __init__(self, **kwargs):
        if "id" not in kwargs:
            kwargs["id"] = "context_container"
        super().__init__(**kwargs)

    def compose(self):
        with Horizontal(id="context_header"):
            yield Static("Session", classes="title")
            yield Static(" ● ", classes="session_status")
            yield Static("20260227-1036", classes="session_name")
        with Vertical(id="context_content"):
            yield KVLine(" Context used", "12,834 (12%) ", ": ")
            yield KVLine(" Cost", "$0.012", ": ")

    def on_click(self, event: events.Click) -> None:
        """Toggle the visibility of the content when the header is clicked."""
        header = self.query_one("#context_header")
        content = self.query_one("#context_content")
        control = event.control
        if control is not None and (control is header or header in control.ancestors):
            content.display = not content.display
            event.prevent_default()
            event.stop()
