from textual.widgets import Static


class SubmitButtonComponent(Static):
    """A custom styled submit button for the agent orchestrator."""

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
