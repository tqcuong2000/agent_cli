from __future__ import annotations

from textual import events
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import TextArea

from agent_cli.core.ux.tui.views.common.popup_list import BasePopupListView
import json
from pathlib import Path


class UserInputComponent(TextArea):
    """
    A custom multi-line input component for the agent orchestrator.
    Supports `\\` + Enter and Shift+Down for new lines, and Enter for submission.

    Popup integration:
    - Typing '/' triggers the CommandPopup
    - Typing '@' triggers the FileDiscoveryPopup
    - The popup intercepts ↑/↓/Tab/Enter/Esc while visible
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

        # Popup state
        self._active_popup: BasePopupListView | None = None
        self._trigger_char: str = ""
        self._trigger_pos: int = 0

        # History state
        self._history_file = Path.home() / ".agent_cli" / "history.json"
        self._history: list[str] = []
        self._history_index: int = -1
        self._history_draft: str = ""
        self._load_history()

    def _load_history(self) -> None:
        """Load input history from disk."""
        if self._history_file.exists():
            try:
                data = json.loads(self._history_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._history = data
            except Exception:
                pass
        self._history_index = len(self._history)

    def _save_history(self) -> None:
        """Save input history to disk, keeping the last 100 entries."""
        max_items = 100
        if len(self._history) > max_items:
            self._history = self._history[-max_items:]
        
        try:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            self._history_file.write_text(json.dumps(self._history), encoding="utf-8")
        except Exception:
            pass

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
        """Recompute height and manage popup visibility based on text."""
        self._update_height()
        self.call_after_refresh(lambda: self.scroll_end(animate=False))

        # Check for popup triggers
        self._check_popup_triggers()

    def _check_popup_triggers(self) -> None:
        """Detect '/' or '@' and show/update the relevant popup."""
        text = self.text

        # --- '/' commands: only at the very start of input ---
        if text.startswith("/"):
            query = text[1:]  # Text after '/'

            # If the command name is complete (has a space after it),
            # dismiss the popup — the user is done picking a command.
            if " " in query:
                popup = self._find_popup("command_popup")
                if popup is not None and popup.is_visible:
                    popup.hide_popup()
                    self._active_popup = None
                    self._trigger_char = ""
                    self._trigger_pos = 0
                return

            popup = self._find_popup("command_popup")
            if popup is not None:
                if not popup.is_visible:
                    popup.show_popup(query)
                    self._active_popup = popup
                    self._trigger_char = "/"
                    self._trigger_pos = 0
                else:
                    popup.update_filter(query)
                return

        # --- '@' file mentions: anywhere in the text ---
        # Find the last '@' that isn't followed by a space (completed mention)
        at_pos = text.rfind("@")
        if at_pos >= 0:
            after_at = text[at_pos + 1 :]
            # Only trigger if there's no space yet after @ (still typing the path)
            if " " not in after_at:
                popup = self._find_popup("file_popup")
                if popup is not None:
                    query = after_at
                    if not popup.is_visible:
                        popup.show_popup(query)
                        self._active_popup = popup
                        self._trigger_char = "@"
                        self._trigger_pos = at_pos
                    else:
                        popup.update_filter(query)
                    return

        # No trigger matched — hide any active popup
        if self._active_popup and self._active_popup.is_visible:
            self._active_popup.hide_popup()
            self._active_popup = None
            self._trigger_char = ""
            self._trigger_pos = 0

    def _find_popup(self, popup_id: str) -> BasePopupListView | None:
        """Find a popup widget by ID from anywhere in the app DOM."""
        try:
            return self.app.query_one(f"#{popup_id}", BasePopupListView)
        except NoMatches:
            return None

    async def _on_key(self, event: events.Key) -> None:
        """Handle submit/newline keyboard behavior for chat input."""
        key = event.key.lower()

        # ── Popup intercept: let the active popup handle keys first ──
        if self._active_popup and self._active_popup.is_visible:
            consumed = self._active_popup.handles_key(event)
            if consumed:
                event.stop()
                event.prevent_default()

                # If the popup selected an item (Tab/Enter), replace input text
                if key in ("tab", "enter") and not self._active_popup.is_visible:
                    # The popup was just hidden by selection — get the selected item
                    # The ItemSelected message will be handled by FooterContainer
                    pass
                return

        if key == "shift+down":
            event.stop()
            event.prevent_default()
            start, end = self.selection
            self.replace("\n", start, end, maintain_selection_offset=False)
            return

        if key == "up":
            if self.selection.start[0] == 0 and self.selection.start == self.selection.end:
                if self._history and self._history_index > 0:
                    event.stop()
                    event.prevent_default()
                    if self._history_index == len(self._history):
                        self._history_draft = self.text
                    self._history_index -= 1
                    self.text = self._history[self._history_index]
                    self.move_cursor((self.document.line_count - 1, len(self.document.get_line(self.document.line_count - 1))))
                return

        if key == "down":
            if self.selection.start[0] == self.document.line_count - 1 and self.selection.start == self.selection.end:
                if self._history_index < len(self._history):
                    event.stop()
                    event.prevent_default()
                    self._history_index += 1
                    if self._history_index == len(self._history):
                        self.text = self._history_draft
                    else:
                        self.text = self._history[self._history_index]
                    self.move_cursor((len(self.document.lines) - 1, len(self.document.get_line(self.document.line_count - 1))))
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
        # Hide any active popup on submit
        if self._active_popup and self._active_popup.is_visible:
            self._active_popup.hide_popup()
            self._active_popup = None

        val = self.text.strip()
        if val:
            # Don't add to history if it's the exact same as the last submitted line
            if not self._history or self._history[-1] != val:
                self._history.append(val)
                self._save_history()

        self._history_index = len(self._history)
        self._history_draft = ""

        self.post_message(self.Submitted(self, self.text))
        self.text = ""
        self._update_height()
