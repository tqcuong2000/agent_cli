from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static


class SystemMessageContainer(Container):
    """Container for command/system feedback shown in the conversation stream."""

    DEFAULT_CSS = """
    SystemMessageContainer {
        layout: vertical;
        width: 100%;
        height: auto;
        padding: 0 2;
        margin: 1 0;
    }

    SystemMessageContainer .system_bubble {
        width: 100%;
        height: auto;
        background: $panel 60%;
        border-left: inner fuchsia;
        padding: 1;
    }

    SystemMessageContainer .system_title {
        width: 100%;
        height: auto;
        color: $warning;
        text-style: bold;
    }

    """

    def __init__(self, message_text: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.message_text = message_text

    def compose(self) -> ComposeResult:
        with Container(classes="system_bubble"):
            yield Static(self.message_text, classes="system_text")
