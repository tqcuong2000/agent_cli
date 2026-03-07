from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from textual.app import ComposeResult
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Static


@dataclass(frozen=True)
class DiffLine:
    """Single changed-file diff line for UI rendering."""

    kind: str  # "added" | "removed" | "context"
    text: str


class ChangedFileDetailBlock(Widget):
    """Changed-file detail preview rendered as plain Static rows (no Markdown)."""

    DEFAULT_CSS = ""

    def __init__(
        self,
        title: str = "",
        summary: str = "",
        diff_lines: Sequence[DiffLine] | None = None,
        file_path: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.file_path = file_path
        self._title = title
        self._summary = summary
        self._diff_lines: list[DiffLine] = list(diff_lines or [])

    def compose(self) -> ComposeResult:
        yield Static(self._title, classes="changed_file_detail_title")
        yield Static(self._summary, classes="changed_file_detail_summary")
        with Container(classes="changed_file_diff_container"):
            for line in self._diff_lines:
                yield Static(
                    self._render_line_text(line), classes=self._line_classes(line)
                )

    def update_content(
        self,
        *,
        title: str,
        summary: str = "",
        diff_lines: Sequence[DiffLine] | None = None,
        file_path: str | None = None,
    ) -> None:
        """Replace widget data and refresh title/summary/diff rows."""
        if file_path is not None:
            self.file_path = file_path
        self._title = title
        self._summary = summary
        self._diff_lines = list(diff_lines or [])

        self.query_one(".changed_file_detail_title", Static).update(self._title)
        self.query_one(".changed_file_detail_summary", Static).update(self._summary)

        diff_container = self.query_one(".changed_file_diff_container", Container)
        diff_container.remove_children()
        for line in self._diff_lines:
            diff_container.mount(
                Static(self._render_line_text(line), classes=self._line_classes(line))
            )

    @staticmethod
    def _render_line_text(line: DiffLine) -> str:
        if line.kind == "added":
            prefix = "+ "
        elif line.kind == "removed":
            prefix = "- "
        else:
            prefix = "  "
        return f"{prefix}{line.text}" if line.text else prefix.rstrip()

    @staticmethod
    def _line_classes(line: DiffLine) -> str:
        normalized = (line.kind or "context").lower()
        if normalized not in {"added", "removed", "context"}:
            normalized = "context"
        return f"changed_file_diff_line -{normalized}"
