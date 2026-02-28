from textual.containers import Container, Horizontal
from textual.widgets import Static


class UserMessageContainer(Container):
    """A container for displaying user messages like chat bubbles."""

    DEFAULT_CSS = """
    UserMessageContainer {
        width: 100%;
        height: auto;
        align: left top;
        padding: 1 2 0 2;
    }

    UserMessageContainer .message_bubble {
        width: auto;
        max-width: 90%;
        background: $panel 80%;
        border-left: inner $primary;
        color: $text;
        padding: 1 1;
    }
    
    UserMessageContainer .spacer {
        width: 1fr;
    }
    """

    def __init__(self, message_text: str, **kwargs):
        super().__init__(**kwargs)
        self.message_text = message_text

    def compose(self):
        with Horizontal():
            yield Static(self.message_text, classes="message_bubble")
            yield Static(" ", classes="spacer")
