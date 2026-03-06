from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static


class ProviderOverlay(Container):
    def compose(self) -> ComposeResult:
        with Container(classes="provider-panel"):
            with Container(classes="header"):
                yield Static("Providers", classes="title")
                yield Static("esc", classes="key")
            with Container(classes="body"):
                with Container(classes="item"):
                    yield Static("Google", classes="provider-name")
                    yield Static("✅", classes="provider-status")
            with Container(classes="footer"):
                yield Static()
