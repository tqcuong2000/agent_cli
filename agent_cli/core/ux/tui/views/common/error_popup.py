from __future__ import annotations

from typing import Optional

from textual import events
from textual.timer import Timer
from textual.widget import Widget


class ErrorPopup(Widget):
    """Floating, non-blocking error popup with auto-dismiss."""

    DEFAULT_CSS = ""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._title = "Error"
        self._message = ""
        self._error_type = "error"
        self._dismiss_timer: Optional[Timer] = None

    def on_mount(self) -> None:
        self._position_bottom_right()

    def on_resize(self, event: events.Resize) -> None:
        self._position_bottom_right()

    def show_error(self, title: str, message: str, error_type: str = "error") -> None:
        self._title = title.strip() or "Error"
        self._message = message.strip()
        self._error_type = error_type.strip() or "error"
        self._position_bottom_right()
        self.add_class("visible")
        self.refresh()

        if self._dismiss_timer is not None:
            self._dismiss_timer.stop()
        self._dismiss_timer = self.set_timer(10.0, self.dismiss)

    def dismiss(self) -> None:
        if self._dismiss_timer is not None:
            self._dismiss_timer.stop()
            self._dismiss_timer = None
        self.remove_class("visible")

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.dismiss()

    def _position_bottom_right(self) -> None:
        try:
            from agent_cli.core.ux.tui.views.footer.footer import FooterContainer

            footer = self.app.query_one(FooterContainer)
            footer_height = footer.outer_size.height
            popup_width = 50
            left_spacer = max(0, self.app.size.width - popup_width - 2)
            self.styles.margin = (0, 1, footer_height, left_spacer)
        except Exception:
            self.styles.margin = (0, 1, 4, 0)

    def render(self) -> str:
        color = "yellow" if self._error_type.lower() == "warning" else "red"
        heading = f"[bold {color}]Warning: {self._title}[/]"
        body = self._message or "Unknown error."
        return f"{heading}\n{body}"
