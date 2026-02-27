from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import TextArea


class UserInputComponent(TextArea):
    """
    A custom multi-line input component for the agent orchestrator.
    Supports `\\` + Enter and Shift+Down for new lines, and Enter for submission.
    """

    DEFAULT_CSS = """
    UserInputComponent {
        width: 1fr;
        background: transparent;
        min-height: 1;
        color: $text;
        border: none;
        padding: 0 1;
        overflow-y: hidden;
    }

    UserInputComponent:focus {
        outline: none;
        border: none;
    }

    UserInputComponent:disabled {
        opacity: 0.5;
    }
    """

    class Submitted(Message):
        """Emitted when the user presses Enter."""

        def __init__(self, input_comp: UserInputComponent, value: str):
            self.input_comp = input_comp
            self.value = value
            super().__init__()

    def __init__(self, text: str = "", **kwargs):
        comp_id = kwargs.pop("id", "input_field")
        # TextArea uses 'text' instead of 'value'
        super().__init__(text=text, id=comp_id, **kwargs)
        # Hide the gutter (line numbers area) for a cleaner "input box" look
        self.show_line_numbers = False
        self.show_vertical_scrollbar = False
        self.highlight_cursor_line = False
        self._max_visible_lines = 5
        self._update_height()

    @property
    def max_visible_lines(self) -> int:
        """Maximum number of visible lines before scrolling."""
        return self._max_visible_lines

    @property
    def visible_line_count(self) -> int:
        """Current visible line count clamped to the configured maximum."""
        return min(self.max_visible_lines, max(1, self.text.count("\n") + 1))

    def _update_height(self) -> None:
        """Resize the input to match content lines, capped to max lines."""
        self.styles.height = self.visible_line_count

    def on_text_area_changed(self, _: TextArea.Changed) -> None:
        """Recompute height whenever the text changes."""
        self._update_height()
        self.call_after_refresh(lambda: self.scroll_end(animate=False))

    async def _on_key(self, event: events.Key) -> None:
        """Handle submit/newline keyboard behavior for chat input."""
        key = event.key.lower()

        if key == "shift+down":
            event.stop()
            event.prevent_default()
            start, end = self.selection
            self.replace("\n", start, end, maintain_selection_offset=False)
            return

        if key == "enter":
            start, end = self.selection

            # If the cursor is after a trailing '\' and no text is selected,
            # replace the '\' with a newline instead of submitting.
            if start == end:
                row, column = start
                if column > 0:
                    prev = (row, column - 1)
                    if self.get_text_range(prev, start) == "\\":
                        event.stop()
                        event.prevent_default()
                        self.replace("\n", prev, start, maintain_selection_offset=False)
                        return

            event.stop()
            event.prevent_default()
            self.submit()
            return

        await super()._on_key(event)

    def submit(self) -> None:
        """Emit a Submitted message with the current input value."""
        self.post_message(self.Submitted(self, self.text))
        self.text = ""
        self._update_height()
