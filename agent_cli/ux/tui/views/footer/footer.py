from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import TextArea

from agent_cli.core.events.events import UserRequestEvent
from agent_cli.ux.tui.views.footer.submit_btn import SubmitButtonComponent
from agent_cli.ux.tui.views.footer.user_input import UserInputComponent
from agent_cli.ux.tui.views.header.status import StatusContainer


class FooterContainer(Container):
    """The footer container holding the terminal input area and status bar."""

    DEFAULT_CSS = """
    FooterContainer {
        dock: bottom;
        width: 100%;
        height: auto;
        background: transparent;
        align: left bottom;
    }

    FooterContainer .input_container {
        width: 100%;
        height: auto;
        border: solid #2a2f35;
        min-height: 1;
        align: left bottom;
    }
    """

    def __init__(self, **kwargs):
        if "id" not in kwargs:
            kwargs["id"] = "footer"
        super().__init__(**kwargs)

        self.input_comp = UserInputComponent()
        self.submit_btn = SubmitButtonComponent()

    def compose(self) -> ComposeResult:
        with Horizontal(classes="input_container"):
            yield self.input_comp
            yield self.submit_btn
        yield StatusContainer()

    def on_mount(self) -> None:
        self._sync_submit_button_offset()

    def _sync_submit_button_offset(self) -> None:
        """Keep the submit button aligned with the bottom input line."""
        visible_lines = self.input_comp.visible_line_count
        self.submit_btn.styles.offset = (0, visible_lines - 1)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area is self.input_comp:
            self._sync_submit_button_offset()

    def on_submit_button_component_pressed(
        self, _: SubmitButtonComponent.Pressed
    ) -> None:
        self.input_comp.submit()

    async def on_user_input_component_submitted(
        self, event: UserInputComponent.Submitted
    ) -> None:
        text = event.value.strip()
        if not text:
            return
        event.stop()

        app_context = getattr(self.app, "app_context", None)
        if app_context is None:
            return

        # ── Slash-command interception ────────────────────────────
        # Execute commands locally; do NOT publish a UserRequestEvent
        # so the chat window never shows the raw "/command" text.
        if text.startswith("/"):
            parser = getattr(app_context, "command_parser", None)
            if parser is not None:
                result = await parser.execute(text)
                if result.message:
                    from agent_cli.core.events.events import AgentMessageEvent

                    await app_context.event_bus.emit(
                        AgentMessageEvent(
                            source="command_system",
                            content=result.message,
                            is_monologue=False,
                        )
                    )
                return  # Do NOT publish UserRequestEvent for commands

        # ── Normal user input → event bus ────────────────────────
        await app_context.event_bus.emit(
            UserRequestEvent(
                source="tui",
                text=text,
            )
        )
