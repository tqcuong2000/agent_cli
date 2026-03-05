from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static


class UserMessageContainer(Container):
    """A container for displaying user messages like chat bubbles."""

    DEFAULT_CSS = ""

    def __init__(self, message_text: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.message_text = message_text

    def compose(self) -> ComposeResult:
        yield Static(self.message_text, classes="message_bubble")
