from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.widgets import Static


class StatusContainer(Container):
    """A container to display status information.

    Mode, model, and effort are **reactive** — changing them
    automatically updates the corresponding ``Static`` widget.
    """

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

    # ── Reactive state ───────────────────────────────────────────

    mode: reactive[str] = reactive("Plan")
    model: reactive[str] = reactive("gemini-3.1-pro-preview")
    effort: reactive[str] = reactive("xHigh")

    def __init__(self, **kwargs):
        if "id" not in kwargs:
            kwargs["id"] = "status_bar"
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static(self.mode, id="mode", classes="mode")
            yield Static(" ● ", classes="shortcut_separator")
            yield Static(self.model, id="model", classes="model")
            yield Static(" ● ", classes="shortcut_separator")
            yield Static(self.effort, id="effort", classes="effort")
            yield Static(" ", id="spacer", classes="spacer")
            yield Static("tab ", classes="shortcut_key")
            yield Static("mode", classes="shortcut_action")
            yield Static(" | ", classes="shortcut_separator")
            yield Static("ctrl+p ", classes="shortcut_key")
            yield Static("commands", classes="shortcut_action")
            yield Static(" | ", classes="shortcut_separator")
            yield Static("ctrl+e ", classes="shortcut_key")
            yield Static("efforts", classes="shortcut_action")

    # ── Watchers ─────────────────────────────────────────────────

    def watch_mode(self, value: str) -> None:
        try:
            self.query_one("#mode", Static).update(value)
        except Exception:
            pass  # Widget not mounted yet

    def watch_model(self, value: str) -> None:
        try:
            self.query_one("#model", Static).update(value)
        except Exception:
            pass

    def watch_effort(self, value: str) -> None:
        try:
            self.query_one("#effort", Static).update(value)
        except Exception:
            pass

    # ── Public API (called by command handlers) ──────────────────

    def update_mode(self, value: str) -> None:
        """Update the displayed execution mode."""
        self.mode = value.capitalize()

    def update_model(self, value: str) -> None:
        """Update the displayed model name."""
        self.model = value

    def update_effort(self, value: str) -> None:
        """Update the displayed effort level."""
        self.effort = value.upper()
