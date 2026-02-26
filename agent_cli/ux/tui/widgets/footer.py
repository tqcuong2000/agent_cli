from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import TextArea

from agent_cli.ux.tui.components.submit_btn import SubmitButtonComponent
from agent_cli.ux.tui.components.user_input import UserInputComponent
from agent_cli.ux.tui.widgets.base import BaseWidget


class FooterWidget(BaseWidget):
    """The footer widget containing the terminal input area."""

    DEFAULT_CSS = """
    FooterWidget {
        dock: bottom;
        width: 100%;
        height: auto;
        border: solid #2a2f35;
        background: transparent;
        align: left bottom;
    }

    FooterWidget Horizontal {
        width: 100%;
        height: auto;
        min-height: 1;
        align: left bottom;
    }
    """

    def __init__(self, **kwargs):
        # Ensure ID is set
        if "id" not in kwargs:
            kwargs["id"] = "footer"
        super().__init__(**kwargs)

        # Instantiate components
        self.input_comp = UserInputComponent()
        self.submit_btn = SubmitButtonComponent()

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield self.input_comp
            yield self.submit_btn

    def _sync_submit_button_offset(self) -> None:
        """Keep the submit button aligned with the bottom input line."""
        visible_lines = self.input_comp.visible_line_count
        self.submit_btn.styles.offset = (0, visible_lines - 1)

    def on_mount(self) -> None:
        self._sync_submit_button_offset()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area is self.input_comp:
            self._sync_submit_button_offset()

    def on_submit_button_component_pressed(
        self, _: SubmitButtonComponent.Pressed
    ) -> None:
        self.input_comp.submit()
