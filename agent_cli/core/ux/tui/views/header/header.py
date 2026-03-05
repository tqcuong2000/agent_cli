from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Static

from agent_cli.core.ux.tui.views.header.agent_badge import AgentBadgeComponent
from agent_cli.core.ux.tui.views.header.terminal import TerminalComponent
from agent_cli.core.ux.tui.views.header.title import TitleComponent


class HeaderContainer(Container):
    """The main header container holding the title, quick actions, and agent badge."""

    DEFAULT_CSS = ""

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
