from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding

from agent_cli.core.infra.config.config_models import normalize_effort
from agent_cli.core.infra.registry.bootstrap import create_app
from agent_cli.core.infra.events.events import BaseEvent, SettingsChangedEvent
from agent_cli.core.ux.tui.views.body.body import BodyContainer
from agent_cli.core.ux.tui.views.common.command_popup import CommandPopup
from agent_cli.core.ux.tui.views.common.error_popup import ErrorPopup
from agent_cli.core.ux.tui.views.common.file_popup import FileDiscoveryPopup
from agent_cli.core.ux.tui.views.common.popup_list import BasePopupListView
from agent_cli.core.ux.tui.views.common.session_overlay import SessionOverlay
from agent_cli.core.ux.tui.views.footer.footer import FooterContainer
from agent_cli.core.ux.tui.views.header.header import HeaderContainer
from agent_cli.core.ux.tui.views.header.title import TitleComponent

if TYPE_CHECKING:
    from agent_cli.core.infra.registry.bootstrap import AppContext


class AgentCLIApp(App):
    """A minimal Textual TUI for agent_cli."""

    BINDINGS = [
        Binding("ctrl+p", "open_command_palette", "Commands", show=True),
        Binding("ctrl+e", "cycle_effort", "Effort", show=True),
        Binding("escape", "interrupt_agent", "Stop", show=False),
        Binding("ctrl+l", "clear_context", "Clear", show=False),
        Binding("ctrl+q", "quit_app", "Quit", show=False),
        ("shift+up", "show_error_popup", "Show Error Popup (Temp)"),
    ]

    CSS_PATH = "../../../assets/app.tcss"

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
        self.session_overlay = SessionOverlay()
        self._settings_subscription_id: Optional[str] = None
        self._bind_command_parser_context()

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield HeaderContainer()
        yield BodyContainer(app_context=self.app_context)
        yield FooterContainer()
        # Popups at App level — true floating overlays
        yield self.command_popup
        yield self.file_popup
        yield self.error_popup
        yield self.session_overlay

    async def on_mount(self) -> None:
        await self.app_context.startup()

        # Set workspace root for file discovery
        if self.root_folder:
            self.file_popup.set_workspace_root(self.root_folder)

        # Ensure command handlers can access this app instance.
        self._bind_command_parser_context()
        self._bind_interaction_handler()
        self._bind_settings_events()

        # Initialize status bar from settings
        self._init_status_bar()
        self._init_session_title()

    def _bind_command_parser_context(self) -> None:
        parser = getattr(self.app_context, "command_parser", None)
        if parser is not None:
            parser.set_app(self)

    def _bind_interaction_handler(self) -> None:
        from agent_cli.core.ux.interaction.tui_interaction_handler import TUIInteractionHandler

        handler = getattr(self.app_context, "interaction_handler", None)
        if handler is None:
            handler = TUIInteractionHandler(self)
            self.app_context.interaction_handler = handler

        tool_executor = getattr(self.app_context, "tool_executor", None)
        if tool_executor is not None:
            tool_executor.set_interaction_handler(handler)

    def _bind_settings_events(self) -> None:
        event_bus = getattr(self.app_context, "event_bus", None)
        if event_bus is None or self._settings_subscription_id is not None:
            return
        self._settings_subscription_id = event_bus.subscribe(
            "SettingsChangedEvent",
            self._on_settings_changed,
            priority=40,
        )

    def _init_status_bar(self) -> None:
        """Push current settings into the reactive status bar."""
        try:
            from agent_cli.core.ux.tui.views.header.agent_badge import AgentBadgeComponent
            from agent_cli.core.ux.tui.views.footer.status import StatusContainer

            status = self.query_one(StatusContainer)
            s = self.app_context.settings
            status.update_model(s.default_model)
            active_name = "default"
            if self.app_context.orchestrator is not None:
                active_name = self.app_context.orchestrator.active_agent_name
            status.update_active_agent(active_name)
            desired_effort = normalize_effort(getattr(s, "default_effort", None)).value
            session_manager = getattr(self.app_context, "session_manager", None)
            if session_manager is not None:
                active_session = session_manager.get_active()
                if active_session is not None:
                    desired_effort = normalize_effort(
                        getattr(active_session, "desired_effort", None)
                    ).value
            status.update_effort(desired_effort)
            badge = self.query_one(AgentBadgeComponent)
            badge.update(active_name)
        except Exception:
            pass  # StatusContainer may not be mounted

    def _init_session_title(self) -> None:
        manager = getattr(self.app_context, "session_manager", None)
        if manager is None:
            return
        try:
            active = manager.get_active()
        except Exception:
            return
        if active is None:
            return
        self._apply_session_title(active.name or "Untitled session")

    async def _on_settings_changed(self, event: BaseEvent) -> None:
        if not isinstance(event, SettingsChangedEvent):
            return
        if event.setting_name != "session_title":
            return
        self._apply_session_title(str(event.new_value or "Untitled session"))

    def _apply_session_title(self, title: str) -> None:
        cleaned = " ".join(str(title).split()).strip() or "Untitled session"
        try:
            title_widget = self.query_one(TitleComponent)
            title_widget.update_title(cleaned)
        except Exception:
            pass
        self.title = f"Engine CLI - {cleaned}"

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

    async def action_clear_context(self) -> None:
        """Clear working memory."""
        parser = self.app_context.command_parser
        if parser:
            result = await parser.execute("/clear")
            self.notify(result.message)

    async def action_cycle_effort(self) -> None:
        """Cycle desired reasoning effort for the active session/default."""
        parser = getattr(self.app_context, "command_parser", None)
        if parser is None:
            return

        from agent_cli.core.ux.commands.handlers.core import cycle_effort

        await cycle_effort(parser.context)

    async def action_quit_app(self) -> None:
        """Exit the application."""
        self.exit()

    async def action_interrupt_agent(self) -> None:
        """Interrupt the currently running agent task."""
        orchestrator = getattr(self.app_context, "orchestrator", None)
        if orchestrator is None:
            return
        interrupted = await orchestrator.interrupt_active_task()
        if interrupted:
            self.notify("Stopping current task...")

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

    async def on_unmount(self) -> None:
        """Flush and release app context resources on TUI shutdown."""
        if self._settings_subscription_id is not None:
            event_bus = getattr(self.app_context, "event_bus", None)
            if event_bus is not None:
                event_bus.unsubscribe(self._settings_subscription_id)
            self._settings_subscription_id = None
        await self.app_context.shutdown()
