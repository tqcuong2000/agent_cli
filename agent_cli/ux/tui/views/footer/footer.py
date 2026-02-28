from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import TextArea

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
