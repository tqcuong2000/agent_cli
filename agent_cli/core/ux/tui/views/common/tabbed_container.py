from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from textual import events
from textual.app import ComposeResult
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Static


@dataclass
class TabDefinition:
    """Contract for a single tab inside a TabbedContainer.

    Attributes:
        title:   Display label shown in the tab header (required).
        content: Widget rendered in the content area when this tab is active (required).
        actions: Widget rendered in the actions area when this tab is active (optional).
    """

    title: str
    content: Widget
    actions: Optional[Widget] = field(default=None)


class TabbedContainer(Container):
    """A generic tabbed container that cycles through ``TabDefinition`` items.

    Layout (3 sections):
        ┌─────────────────────────┐
        │  < ── tab-title ── >    │  ← header (nav + active tab title)
        ├─────────────────────────┤
        │       content           │  ← content area (active tab's widget)
        ├─────────────────────────┤
        │       actions           │  ← actions area (active tab's actions, if any)
        └─────────────────────────┘

    Usage:
        tabs = [
            TabDefinition(title="Terminal", content=MyTerminalWidget()),
            TabDefinition(title="Files", content=MyFilesWidget(), actions=MyActionsWidget()),
        ]
        container = TabbedContainer(tabs=tabs)
    """

    DEFAULT_CSS = ""

    def __init__(
        self,
        tabs: Optional[List[TabDefinition]] = None,
        *,
        active_index: int = 0,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._tabs: List[TabDefinition] = list(tabs or [])
        self._active_index: int = max(0, min(active_index, max(len(self._tabs) - 1, 0)))

    # ── Compose ─────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Container(classes="tab-header"):
            yield Static("‹", id="tab-nav-prev", classes="tab-navigate")
            yield Static("", id="tab-title", classes="tab-title")
            yield Static("›", id="tab-nav-next", classes="tab-navigate")
        yield Container(id="tab-content", classes="tab-content")
        yield Container(id="tab-actions", classes="tab-actions")

    def on_mount(self) -> None:
        self._render_active_tab()

    # ── Navigation ──────────────────────────────────────────────

    def on_click(self, event: events.Click) -> None:
        target = event.widget
        if target is None:
            return

        target_id = getattr(target, "id", None)
        if target_id == "tab-nav-prev":
            event.stop()
            self.switch_tab(-1)
        elif target_id == "tab-nav-next":
            event.stop()
            self.switch_tab(1)

    def switch_tab(self, delta: int) -> None:
        """Move to the tab at *current + delta*, wrapping around."""
        if not self._tabs:
            return
        self._active_index = (self._active_index + delta) % len(self._tabs)
        self._render_active_tab()

    def activate_tab(self, index: int) -> None:
        """Jump directly to the tab at *index*."""
        if not self._tabs:
            return
        self._active_index = max(0, min(index, len(self._tabs) - 1))
        self._render_active_tab()

    # ── Dynamic tab management ──────────────────────────────────

    def add_tab(self, tab: TabDefinition, *, activate: bool = False) -> int:
        """Append a tab and optionally activate it.  Returns the new tab index."""
        self._tabs.append(tab)
        idx = len(self._tabs) - 1
        if activate:
            self._active_index = idx
            self._render_active_tab()
        return idx

    def remove_tab(self, index: int) -> Optional[TabDefinition]:
        """Remove the tab at *index*.  Returns the removed definition or ``None``."""
        if index < 0 or index >= len(self._tabs):
            return None
        removed = self._tabs.pop(index)
        # Adjust active index after removal
        if not self._tabs:
            self._active_index = 0
            self._render_active_tab()
        else:
            if self._active_index >= len(self._tabs):
                self._active_index = len(self._tabs) - 1
            self._render_active_tab()
        return removed

    # ── Properties ──────────────────────────────────────────────

    @property
    def active_index(self) -> int:
        return self._active_index

    @property
    def active_tab(self) -> Optional[TabDefinition]:
        if not self._tabs:
            return None
        return self._tabs[self._active_index]

    @property
    def tab_count(self) -> int:
        return len(self._tabs)

    # ── Internal rendering ──────────────────────────────────────

    def _render_active_tab(self) -> None:
        """Swap the content & actions areas to reflect the active tab."""
        title_widget = self.query_one("#tab-title", Static)
        content_area = self.query_one("#tab-content", Container)
        actions_area = self.query_one("#tab-actions", Container)

        # Clear old content
        for child in list(content_area.children):
            child.remove()
        for child in list(actions_area.children):
            child.remove()

        if not self._tabs:
            title_widget.update("")
            return

        tab = self._tabs[self._active_index]

        # Header title  (e.g. "Terminal (1/3)")
        if len(self._tabs) > 1:
            title_widget.update(f"{tab.title} ({self._active_index + 1}/{len(self._tabs)})")
        else:
            title_widget.update(tab.title)

        # Content
        content_area.mount(tab.content)

        # Actions (optional)
        if tab.actions is not None:
            actions_area.mount(tab.actions)
