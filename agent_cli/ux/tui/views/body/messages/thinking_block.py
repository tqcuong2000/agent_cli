from __future__ import annotations

import re

from textual import events
from textual.app import ComposeResult
from textual.containers import ScrollableContainer
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Markdown, Static


class ThinkingBlock(Widget):
    """Collapsible block that shows the agent's internal thinking stream."""

    is_expanded = reactive(True)

    DEFAULT_CSS = """
    ThinkingBlock {
        layout: vertical;
        width: 100%;
        height: 1;
        color: $text-muted;
        margin: 1 0 0 0;
        background: $panel 20%;
    }

    ThinkingBlock.-expanded {
        height: auto;
    }

    ThinkingBlock .thinking_header {
        width: 100%;
        height: 1;
        margin-left: 1;
        color: $accent;
    }

    ThinkingBlock .thinking_content_container {
        width: 100%;
        height: auto;
        min-height: 1;
        max-height: 12;
        padding: 1;
        display: none;
        overflow-y: auto;
    }

    ThinkingBlock.-expanded .thinking_content_container {
        display: block;
    }

    ThinkingBlock .thinking_content {
        width: 100%;
        height: auto;
        color: $text-muted 30%;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.is_streaming = True
        self._raw_text = ""
        self._title = "Thinking in progress"
        self._thoughts = ""

    def compose(self) -> ComposeResult:
        yield Static(classes="thinking_header")
        with ScrollableContainer(classes="thinking_content_container"):
            yield Markdown("", classes="thinking_content")

    def on_mount(self) -> None:
        self._sync_view()

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.is_expanded = not self.is_expanded

    def watch_is_expanded(self, is_expanded: bool) -> None:
        self.set_class(is_expanded, "-expanded")
        if self.is_mounted:
            self._sync_header()
            if is_expanded:
                self.call_after_refresh(self._scroll_content_end)

    def append_chunk(self, text: str) -> None:
        if not text:
            return
        self._raw_text += text
        self._parse_thinking_payload()
        if self.is_mounted:
            self._content.update(self._render_body())
            if self.is_expanded:
                self.call_after_refresh(self._scroll_content_end)

    def finish_streaming(self) -> None:
        self.is_streaming = False
        if self.is_mounted:
            self._sync_header()

    def _sync_view(self) -> None:
        self.set_class(self.is_expanded, "-expanded")
        self._sync_header()
        self._content.update(self._render_body())
        if self.is_expanded:
            self.call_after_refresh(self._scroll_content_end)

    def _sync_header(self) -> None:
        self._header.update(self._header_text())

    def _header_text(self) -> str:
        return f"[b]{self._title}[/b]"

    def _render_body(self) -> str:
        thoughts = self._thoughts or "..."
        return thoughts

    def _parse_thinking_payload(self) -> None:
        thoughts_match = re.search(
            r"<thinking>\s*(.*?)\s*</thinking>", self._raw_text, re.DOTALL
        )
        if thoughts_match:
            thoughts = thoughts_match.group(1)
        else:
            thoughts = re.sub(
                r"<title>\s*.*?\s*</title>", "", self._raw_text, flags=re.DOTALL
            )
            thoughts = re.sub(r"</?thinking>", "", thoughts)
        thoughts = thoughts.strip()
        self._thoughts = self._normalize_space(thoughts)

        title_match = re.search(
            r"<title>\s*(.*?)\s*</title>", self._raw_text, re.DOTALL
        )
        if title_match:
            parsed_title = self._normalize_space(title_match.group(1))
            self._title = self._normalize_title(parsed_title)
        else:
            fallback = self._derive_title_from_text(self._raw_text)
            if fallback:
                self._title = self._normalize_title(fallback)

    def _derive_title_from_text(self, raw: str) -> str:
        clean = re.sub(r"<[^>]+>", " ", raw)
        words = [w for w in self._normalize_space(clean).split(" ") if w]
        if not words:
            return ""
        return " ".join(words[:8])

    def _normalize_title(self, title: str) -> str:
        words = [w for w in title.split(" ") if w]
        if not words:
            return "Thinking in progress"
        if len(words) > 12:
            words = words[:12]
        return " ".join(words)

    def _normalize_space(self, text: str) -> str:
        return re.sub(r"[ \t]+", " ", text).strip()

    def _scroll_content_end(self) -> None:
        self._content_container.scroll_end(animate=False)

    @property
    def _header(self) -> Static:
        return self.query_one(".thinking_header", Static)

    @property
    def _content(self) -> Markdown:
        return self.query_one(".thinking_content", Markdown)

    @property
    def _content_container(self) -> ScrollableContainer:
        return self.query_one(".thinking_content_container", ScrollableContainer)
