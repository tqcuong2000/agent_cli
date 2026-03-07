from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import RichLog, Static


class TerminalOutputWidget(Widget):
    """Live terminal output widget for one managed terminal."""

    DEFAULT_CSS = ""

    def __init__(self, terminal_id: str, command: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.terminal_id = terminal_id
        self.command = command
        self._lines: list[str] = []
        self._rendered_line_count = 0
        self._exit_code: int | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="terminal_meta")
        yield RichLog(
            id="terminal_output_log",
            wrap=True,
            markup=False,
            highlight=False,
            auto_scroll=True,
        )

    def on_mount(self) -> None:
        self._sync_meta()
        self._flush_pending_lines()

    def append_line(self, text: str) -> None:
        lines = str(text).splitlines() or [str(text)]
        self._lines.extend(lines)
        self._flush_pending_lines()

    def set_exited(self, exit_code: int) -> None:
        self._exit_code = int(exit_code)
        self._sync_meta()

    @property
    def output_text(self) -> str:
        return "\n".join(self._lines)

    @property
    def status_text(self) -> str:
        if self._exit_code is None:
            return "running"
        return f"exited ({self._exit_code})"

    def _sync_meta(self) -> None:
        if not self.is_mounted:
            return
        meta = self.query_one("#terminal_meta", Static)
        meta.update(f"{self.terminal_id} [{self.status_text}] {self.command}")

    def _flush_pending_lines(self) -> None:
        if not self.is_mounted:
            return
        log = self.query_one("#terminal_output_log", RichLog)
        for line in self._lines[self._rendered_line_count :]:
            log.write(line)
        self._rendered_line_count = len(self._lines)
        log.scroll_end(animate=False)
