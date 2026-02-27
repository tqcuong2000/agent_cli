from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Static

from agent_cli.ux.tui.views.header.agent_badge import AgentBadgeComponent
from agent_cli.ux.tui.views.header.terminal import TerminalComponent
from agent_cli.ux.tui.views.header.title import TitleComponent


class HeaderContainer(Container):
    """The main header container holding the title, quick actions, and agent badge."""

    DEFAULT_CSS = """
    HeaderContainer {
        dock: top;
        height: 3;
        width: 100%;
    }

    HeaderContainer Horizontal {
        width: 100%;
        height: 100%;
        align: left middle;
        padding: 0 1;
    }

    HeaderContainer .spacer-left {
        width: 1fr;
    }

    HeaderContainer .spacer-right {
        width: 1;
    }
    """

    def __init__(self):
        self.title_comp = TitleComponent()
        self.badge_comp = AgentBadgeComponent()
        self.terminal_menu = TerminalComponent()
        super().__init__(id="header")

    def compose(self) -> ComposeResult:
        """Compose the horizontal layout."""
        with Horizontal():
            yield self.title_comp
            yield Static(" ", id="spacer_left", classes="spacer-left")
            yield self.terminal_menu
            yield Static(" ", id="spacer_right", classes="spacer-right")
            yield self.badge_comp
