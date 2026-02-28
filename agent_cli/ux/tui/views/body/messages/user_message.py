from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static


class UserMessageContainer(Container):
    """A container for displaying user messages like chat bubbles."""

    DEFAULT_CSS = """
    UserMessageContainer {
        layout: vertical;
        width: 100%;
        height: auto;
        padding: 0 2;
        margin: 2 0;
    }

    UserMessageContainer .message_bubble {
        width: auto;
        max-width: 90%;
        background: $panel 80%;
        border-left: inner $primary;
        color: $text;
        padding: 1 1;
    }

    """

    def __init__(self, message_text: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.message_text = message_text

    def compose(self) -> ComposeResult:
        yield Static(self.message_text, classes="message_bubble")
