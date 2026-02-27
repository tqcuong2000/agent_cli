from textual import events
from textual.message import Message
from textual.widgets import Static


class SubmitButtonComponent(Static):
    """A custom styled submit button for the agent orchestrator."""

    can_focus = True

    class Pressed(Message):
        """Emitted when the submit button is activated."""

        def __init__(self, button: "SubmitButtonComponent") -> None:
            self.button = button
            super().__init__()

    DEFAULT_CSS = """
    SubmitButtonComponent {
        width: auto;
        min-width: 12;
        height: 1;
        background: $primary;
        color: $text;
        border: none;
        text-style: bold;
        content-align: center middle;
    }

    SubmitButtonComponent:hover {
        background: $accent;
    }

    SubmitButtonComponent.-active {
        background: $accent-darken-1;
    }

    SubmitButtonComponent:disabled {
        opacity: 0.5;
        background: $surface;
        color: $text-muted;
    }
    """

    def __init__(self, label: str = "Submit", **kwargs):
        # Default to 'submit_btn' ID if not provided
        if "id" not in kwargs:
            kwargs["id"] = "submit_btn"
        super().__init__(label, **kwargs)

    def _emit_pressed(self) -> None:
        self.post_message(self.Pressed(self))

    def on_click(self, _: events.Click) -> None:
        self._emit_pressed()

    def key_enter(self) -> None:
        self._emit_pressed()

    def key_space(self) -> None:
        self._emit_pressed()
