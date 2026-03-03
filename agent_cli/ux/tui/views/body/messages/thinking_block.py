from __future__ import annotations

import json
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
        parsed = self._try_parse_json_reasoning(self._raw_text)
        if parsed is not None:
            title, thoughts = parsed
        else:
            title, thoughts = self._parse_plain_reasoning(self._raw_text)

        self._thoughts = self._normalize_space(thoughts)
        normalized_title = self._normalize_space(title)
        if normalized_title:
            self._title = self._normalize_title(normalized_title)
            return

        fallback = self._derive_title_from_text(self._raw_text)
        if fallback:
            self._title = self._normalize_title(fallback)

    def _try_parse_json_reasoning(self, raw: str) -> tuple[str, str] | None:
        try:
            payload = json.loads(raw.strip())
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        title = str(payload.get("title", "")).strip()
        thought = str(payload.get("thought", "")).strip()
        if not title and not thought:
            return None
        return title, thought

    def _parse_plain_reasoning(self, raw: str) -> tuple[str, str]:
        text = raw.strip()
        if not text:
            return "", ""
        lines = text.splitlines()
        first = lines[0].strip() if lines else ""
        if first.lower().startswith("title:"):
            title = first.split(":", 1)[1].strip()
            thoughts = "\n".join(line.strip() for line in lines[1:]).strip()
            return title, thoughts
        return "", text

    def _derive_title_from_text(self, raw: str) -> str:
        words = [w for w in self._normalize_space(raw).split(" ") if w]
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
