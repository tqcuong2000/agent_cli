from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Static


class StatusContainer(Container):
    """A container to display status information."""

    DEFAULT_CSS = """
    StatusContainer {
        height: 1;
        width: 100%;
        background: $background;
        color: $text;
    }

    StatusContainer Horizontal {
        padding: 0 1;
        width: 100%;
        height: 100%;
        align: left middle;
    }

    StatusContainer .spacer {
        width: 1fr;
    }

    StatusContainer #shortcuts {
        width: auto;
        color: $panel-lighten-1;
    }

    StatusContainer .shortcut_key {
        color: $text;
        width: auto;
    }

    StatusContainer .shortcut_action {
        color: $panel-lighten-2;
        width: auto;
    }

    StatusContainer .shortcut_separator {
        color: $panel-lighten-1;
        width: auto;
    }

    StatusContainer .mode {
        color: $accent;
        width: auto;
    }

    StatusContainer .model {
        color: $text;
        width: auto;
    }

    StatusContainer .effort {
        color: $accent;
        width: auto;
    }
    """

    def __init__(self, **kwargs):
        if "id" not in kwargs:
            kwargs["id"] = "status_bar"
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static("Plan", id="mode", classes="mode")
            yield Static(" ● ", classes="shortcut_separator")
            yield Static("gemini-3.1-pro-preview", id="model", classes="model")
            yield Static(" ● ", classes="shortcut_separator")
            yield Static("xHigh", id="effort", classes="effort")
            yield Static(" ", id="spacer", classes="spacer")
            yield Static("tab ", classes="shortcut_key")
            yield Static("mode", classes="shortcut_action")
            yield Static(" | ", classes="shortcut_separator")
            yield Static("ctrl+p ", classes="shortcut_key")
            yield Static("commands", classes="shortcut_action")
            yield Static(" | ", classes="shortcut_separator")
            yield Static("ctrl+e ", classes="shortcut_key")
            yield Static("efforts", classes="shortcut_action")
