from textual.app import App, ComposeResult

from agent_cli.ux.tui.views.body.body import BodyContainer
from agent_cli.ux.tui.views.common.command_popup import CommandPopup
from agent_cli.ux.tui.views.common.file_popup import FileDiscoveryPopup
from agent_cli.ux.tui.views.common.popup_list import BasePopupListView
from agent_cli.ux.tui.views.footer.footer import FooterContainer
from agent_cli.ux.tui.views.header.header import HeaderContainer


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
        self.command_popup = CommandPopup()
        self.file_popup = FileDiscoveryPopup()

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield HeaderContainer()
        yield BodyContainer()
        # Popups at App level — true floating overlays
        yield self.command_popup
        yield self.file_popup
        yield FooterContainer()

    def on_mount(self) -> None:
        # Set workspace root for file discovery
        if self.root_folder:
            self.file_popup.set_workspace_root(self.root_folder)

    def on_base_popup_list_view_item_selected(
        self, event: BasePopupListView.ItemSelected
    ) -> None:
        """Handle popup item selection — insert the value into the footer input."""
        footer = self.query_one(FooterContainer)
        input_comp = footer.input_comp

        if event.trigger_char == "/":
            # Commands replace the entire input
            input_comp.text = event.item.value
        elif event.trigger_char == "@":
            # File mentions splice into existing text at the @ position
            text = input_comp.text
            trigger_pos = input_comp._trigger_pos
            before = text[:trigger_pos]
            new_text = f"{before}{event.item.value}"
            input_comp.text = new_text

        # Move cursor to end
        row = input_comp.text.count("\n")
        col = len(input_comp.text.split("\n")[-1])
        input_comp.move_cursor((row, col))
        input_comp.focus()

    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        if self.theme != "textual-light":
            self.theme = "textual-light"
        else:
            self.theme = "textual-dark"
