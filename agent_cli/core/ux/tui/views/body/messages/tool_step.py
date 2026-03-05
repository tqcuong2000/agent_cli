from __future__ import annotations

from typing import Optional

from rich.markup import escape
from textual.app import ComposeResult
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static


class ToolStepWidget(Widget):
    """Animated status row for a single tool execution."""

    SPINNER_FRAMES = [
        "\u280b",
        "\u2819",
        "\u2839",
        "\u2838",
        "\u283c",
        "\u2834",
        "\u2826",
        "\u2827",
        "\u2807",
        "\u280f",
    ]

    DEFAULT_CSS = ""

    def __init__(self, tool_name: str, args: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self.args = args
        self._frame_index = 0
        self._timer: Optional[Timer] = None
        self._status: str = "running"
        self._duration_ms: Optional[int] = None
        self._error: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Static(self._render_row(), classes="tool_step_label")

    def on_mount(self) -> None:
        self._label.update(self._render_row())
        if self._status == "running":
            self._timer = self.set_interval(0.1, self._spin)

    def on_unmount(self) -> None:
        self._stop_timer()

    def _spin(self) -> None:
        if self._status != "running" or not self.is_mounted:
            return
        self._frame_index = (self._frame_index + 1) % len(self.SPINNER_FRAMES)
        self._label.update(self._render_row())

    def mark_success(self, duration_ms: int) -> None:
        self._status = "success"
        self._duration_ms = duration_ms
        self._stop_timer()
        if self.is_mounted:
            self._label.update(self._render_row())

    def mark_failed(self, error: str) -> None:
        self._status = "failed"
        self._error = self._truncate(error, 80)
        self._stop_timer()
        if self.is_mounted:
            self._label.update(self._render_row())

    def _render_row(self) -> str:
        args_text = self._format_args(self.args)
        tool_call = f"{self.tool_name}({args_text})"
        safe_tool_call = escape(tool_call)

        if self._status == "success":
            duration = self._duration_ms if self._duration_ms is not None else 0
            return (
                f"[green]\u2713[/green] [b]{safe_tool_call}[/b] "
                f"[dim]({duration} ms)[/dim]"
            )

        if self._status == "failed":
            error = self._error or "Tool execution failed."
            safe_error = escape(error)
            return (
                f"[red]\u2717[/red] [b]{safe_tool_call}[/b] "
                f"[dim]- {safe_error}[/dim]"
            )

        spinner = self.SPINNER_FRAMES[self._frame_index]
        return f"[cyan]{spinner}[/cyan] [b]{safe_tool_call}[/b]"

    def _format_args(self, args: dict) -> str:
        if not args:
            return ""
        parts = [f"{key}={value!r}" for key, value in args.items()]
        joined = ", ".join(parts)
        return self._truncate(joined, 60)

    def _truncate(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return f"{value[: max(0, limit - 3)]}..."

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    @property
    def _label(self) -> Static:
        return self.query_one(".tool_step_label", Static)
