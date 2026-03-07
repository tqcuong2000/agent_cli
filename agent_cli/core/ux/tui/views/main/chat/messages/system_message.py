from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static


class SystemMessageContainer(Container):
    """Container for command/system feedback shown in the conversation stream."""

    DEFAULT_CSS = ""

    def __init__(self, message_text: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.message_text = message_text

    def compose(self) -> ComposeResult:
        with Container(classes="system_bubble"):
            yield Static(self.message_text, classes="system_text")
