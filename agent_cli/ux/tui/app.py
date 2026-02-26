from textual.app import App, ComposeResult

from agent_cli.ux.tui.widgets.body import BodyWidget
from agent_cli.ux.tui.widgets.footer import FooterWidget
from agent_cli.ux.tui.widgets.header import HeaderWidget


class AgentCLIApp(App):
    """A minimal Textual TUI for agent_cli."""

    CSS = """
    #content {
        height: 1fr;
        padding: 1 2;
        content-align: center middle;
        text-align: center;
    }
    """

    def __init__(self, root_folder: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.root_folder = root_folder

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield HeaderWidget()
        yield BodyWidget()
        yield FooterWidget()

    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        # Fixed the dark mode error (in newer textual versions, theme is used instead of self.dark)
        if self.theme != "textual-light":
            self.theme = "textual-light"
        else:
            self.theme = "textual-dark"
