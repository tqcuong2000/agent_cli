from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding

from agent_cli.core.bootstrap import create_app
from agent_cli.ux.tui.views.body.body import BodyContainer
from agent_cli.ux.tui.views.common.command_popup import CommandPopup
from agent_cli.ux.tui.views.common.error_popup import ErrorPopup
from agent_cli.ux.tui.views.common.file_popup import FileDiscoveryPopup
from agent_cli.ux.tui.views.common.popup_list import BasePopupListView
from agent_cli.ux.tui.views.footer.footer import FooterContainer
from agent_cli.ux.tui.views.header.header import HeaderContainer

if TYPE_CHECKING:
    from agent_cli.core.bootstrap import AppContext


class AgentCLIApp(App):
    """A minimal Textual TUI for agent_cli."""

    BINDINGS = [
        Binding("ctrl+p", "open_command_palette", "Commands", show=True),
        Binding("ctrl+e", "cycle_effort", "Effort", show=True),
        Binding("ctrl+m", "toggle_mode", "Mode", show=False),
        Binding("ctrl+l", "clear_context", "Clear", show=False),
        Binding("ctrl+q", "quit_app", "Quit", show=False),
        ("shift+up", "show_error_popup", "Show Error Popup (Temp)"),
    ]

    CSS = """
    #content {
        height: 1fr;
        padding: 1 2;
        content-align: center middle;
        text-align: center;
    }
    """

    def __init__(
        self,
        root_folder: str,
        app_context: Optional["AppContext"] = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.root_folder = root_folder
        self.app_context = app_context or create_app(root_folder=root_folder)

        # Build popups — use live CommandRegistry if available
        registry = getattr(self.app_context, "command_registry", None)
        self.command_popup = CommandPopup(registry=registry)
        self.file_popup = FileDiscoveryPopup(app_context=self.app_context)
        self.error_popup = ErrorPopup(id="error_popup")
        self._bind_command_parser_context()

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield HeaderContainer()
        yield BodyContainer(app_context=self.app_context)
        # Popups at App level — true floating overlays
        yield self.command_popup
        yield self.file_popup
        yield self.error_popup
        yield FooterContainer()

    def on_mount(self) -> None:
        # Set workspace root for file discovery
        if self.root_folder:
            self.file_popup.set_workspace_root(self.root_folder)

        # Ensure command handlers can access this app instance.
        self._bind_command_parser_context()
        self._bind_interaction_handler()

        # Initialize status bar from settings
        self._init_status_bar()

    def _bind_command_parser_context(self) -> None:
        parser = getattr(self.app_context, "command_parser", None)
        if parser is not None:
            parser.set_app(self)

    def _bind_interaction_handler(self) -> None:
        from agent_cli.core.tui_interaction_handler import TUIInteractionHandler

        handler = getattr(self.app_context, "interaction_handler", None)
        if handler is None:
            handler = TUIInteractionHandler(self)
            self.app_context.interaction_handler = handler

        tool_executor = getattr(self.app_context, "tool_executor", None)
        if tool_executor is not None:
            tool_executor.set_interaction_handler(handler)

    def _init_status_bar(self) -> None:
        """Push current settings into the reactive status bar."""
        try:
            from agent_cli.ux.tui.views.header.status import StatusContainer

            status = self.query_one(StatusContainer)
            s = self.app_context.settings
            status.update_mode(getattr(s, "execution_mode", "plan"))
            status.update_model(s.default_model)
            status.update_effort(s.default_effort_level.value)
        except Exception:
            pass  # StatusContainer may not be mounted

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

    # ── Keyboard shortcut actions ────────────────────────────────

    async def action_open_command_palette(self) -> None:
        """Focus input bar and insert '/' to open the command popup."""
        try:
            footer = self.query_one(FooterContainer)
            footer.input_comp.text = "/"
            footer.input_comp.focus()
        except Exception:
            pass

    async def action_cycle_effort(self) -> None:
        """Cycle through effort levels: LOW → MEDIUM → HIGH → XHIGH → LOW."""
        levels = ["LOW", "MEDIUM", "HIGH", "XHIGH"]
        current = self.app_context.settings.default_effort_level.value
        try:
            idx = levels.index(current)
        except ValueError:
            idx = 0
        next_level = levels[(idx + 1) % len(levels)]

        parser = self.app_context.command_parser
        if parser:
            await parser.execute(f"/effort {next_level}")

    async def action_toggle_mode(self) -> None:
        """Toggle between fast and plan mode."""
        current = getattr(self.app_context.settings, "execution_mode", "plan")
        new_mode = "plan" if current == "fast" else "fast"

        parser = self.app_context.command_parser
        if parser:
            await parser.execute(f"/mode {new_mode}")

    async def action_clear_context(self) -> None:
        """Clear working memory."""
        parser = self.app_context.command_parser
        if parser:
            result = await parser.execute("/clear")
            self.notify(result.message)

    async def action_quit_app(self) -> None:
        """Exit the application."""
        self.exit()

    # ── Legacy actions ───────────────────────────────────────────

    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        if self.theme != "textual-light":
            self.theme = "textual-light"
        else:
            self.theme = "textual-dark"

    def action_show_error_popup(self) -> None:
        """Temporary debug action to preview the error popup UI."""
        self.error_popup.show_error(
            title="Temporary Popup Test",
            message=(
                "This is a temporary trigger for validating ErrorPopup position, "
                "style, and auto-dismiss behavior."
            ),
            error_type="error",
        )
