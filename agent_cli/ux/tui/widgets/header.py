from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from agent_cli.ux.tui.components.agent_badge import AgentBadgeComponent
from agent_cli.ux.tui.components.terminal import TerminalComponent
from agent_cli.ux.tui.components.title import TitleComponent
from agent_cli.ux.tui.widgets.base import BaseWidget


class HeaderWidget(BaseWidget):
    """The main header widget containing the title, quick actions, and agent badge."""

    DEFAULT_CSS = """
    HeaderWidget {
        dock: top;
        height: 3;
        width: 100%;
    }

    HeaderWidget Horizontal {
        width: 100%;
        height: 100%;
        align: left middle;
        padding: 0 1;
    }

    HeaderWidget .spacer-left {
        width: 1fr;
    }

    HeaderWidget .spacer-right {
        width: 1;
    }
    """

    def __init__(self):
        self.title_comp = TitleComponent()
        self.badge_comp = AgentBadgeComponent()
        self.terminal_menu = TerminalComponent()

        super().__init__(
            id="header",
            components=[self.title_comp, self.badge_comp, self.terminal_menu],
        )

    def compose(self) -> ComposeResult:
        """Compose the horizontal layout and mount the hidden action menu."""
        with Horizontal():
            # Left aligned components
            yield self.title_comp
            yield Static(" ", id="spacer_left", classes="spacer-left")
            yield self.terminal_menu
            yield Static(" ", id="spacer_right", classes="spacer-right")
            yield self.badge_comp
