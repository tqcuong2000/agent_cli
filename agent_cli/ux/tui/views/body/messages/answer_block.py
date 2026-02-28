from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Markdown


class AnswerBlock(Widget):
    """Final assistant response rendered as Markdown."""

    DEFAULT_CSS = """
    AnswerBlock {
        width: 100%;
        height: auto;
        margin: 1 0;
        border-left: inner $accent;
        background: $panel 40%;
        padding: 1;
    }

    AnswerBlock Markdown {
        width: 100%;
        height: auto;
    }
    """

    def __init__(self, content: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._buffer = content

    def compose(self) -> ComposeResult:
        yield Markdown(self._buffer)

    def update_content(self, text: str) -> None:
        self._buffer = text
        self._markdown.update(text)

    def append_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        self._buffer += chunk
        self.update_content(self._buffer)

    @property
    def _markdown(self) -> Markdown:
        return self.query_one(Markdown)
