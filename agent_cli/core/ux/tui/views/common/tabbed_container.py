from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from uuid import uuid4

from textual import events
from textual.app import ComposeResult
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Static


@dataclass
class TabDefinition:
    """Contract for one tab inside a TabbedContainer."""

    title: str
    content: Widget
    actions: Optional[Widget] = field(default=None)


class TabbedContainer(Container):
    """A generic tabbed container with stable string tab IDs."""

    DEFAULT_CSS = ""

    def __init__(
        self,
        tabs: Optional[list[TabDefinition]] = None,
        *,
        active_index: int = 0,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._tabs: dict[str, TabDefinition] = {}
        self._active_tab_id: str | None = None
        self._initial_active_index = max(active_index, 0)

        for tab in tabs or []:
            self.add_tab(tab)

        if self._tabs:
            tab_ids = list(self._tabs.keys())
            self._active_tab_id = tab_ids[
                min(self._initial_active_index, len(tab_ids) - 1)
            ]

    def compose(self) -> ComposeResult:
        with Container(classes="tab-header header"):
            yield Static("‹", id="tab-nav-prev", classes="tab-navigate navigate")
            yield Static("", id="tab-title", classes="tab-title title")
            yield Static("›", id="tab-nav-next", classes="tab-navigate navigate")
        yield Container(id="tab-content", classes="tab-content content")
        yield Container(id="tab-actions", classes="tab-actions actions")

    def on_mount(self) -> None:
        self._render_active_tab()

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
        """Move to the tab at current + delta, wrapping around."""
        if not self._tabs:
            return
        tab_ids = list(self._tabs.keys())
        current_index = (
            tab_ids.index(self._active_tab_id)
            if self._active_tab_id in self._tabs
            else 0
        )
        self._active_tab_id = tab_ids[(current_index + delta) % len(tab_ids)]
        self._render_active_tab()

    def activate_tab(self, tab_ref: int | str) -> None:
        """Jump directly to the tab identified by stable ID or index."""
        if not self._tabs:
            return
        resolved_id = self._resolve_tab_id(tab_ref)
        if resolved_id is None:
            return
        self._active_tab_id = resolved_id
        self._render_active_tab()

    def add_tab(
        self,
        tab: TabDefinition,
        *,
        activate: bool = False,
        tab_id: str | None = None,
    ) -> str:
        """Append a tab and optionally activate it. Returns the stable tab ID."""
        resolved_id = (tab_id or "").strip() or f"tab_{uuid4().hex[:8]}"
        if resolved_id in self._tabs:
            raise ValueError(f"Tab '{resolved_id}' is already registered.")
        self._tabs[resolved_id] = tab
        if self._active_tab_id is None or activate:
            self._active_tab_id = resolved_id
        if self.is_mounted:
            self._render_active_tab()
        return resolved_id

    def remove_tab(self, tab_ref: int | str) -> Optional[TabDefinition]:
        """Remove a tab by stable ID or index."""
        resolved_id = self._resolve_tab_id(tab_ref)
        if resolved_id is None:
            return None

        remaining_ids = [tab_id for tab_id in self._tabs.keys() if tab_id != resolved_id]
        removed = self._tabs.pop(resolved_id)
        if not self._tabs:
            self._active_tab_id = None
        elif self._active_tab_id == resolved_id:
            self._active_tab_id = remaining_ids[0]

        if self.is_mounted:
            self._render_active_tab()
        return removed

    def update_tab_title(self, tab_ref: int | str, title: str) -> bool:
        """Update the display title for a tab."""
        resolved_id = self._resolve_tab_id(tab_ref)
        if resolved_id is None:
            return False
        self._tabs[resolved_id].title = title
        if resolved_id == self._active_tab_id and self.is_mounted:
            self._render_active_tab()
        return True

    def get_tab_title(self, tab_ref: int | str) -> Optional[str]:
        """Return the display title for a tab."""
        resolved_id = self._resolve_tab_id(tab_ref)
        if resolved_id is None:
            return None
        return self._tabs[resolved_id].title

    @property
    def active_index(self) -> int:
        if self._active_tab_id is None:
            return 0
        return list(self._tabs.keys()).index(self._active_tab_id)

    @property
    def active_tab_id(self) -> Optional[str]:
        return self._active_tab_id

    @property
    def active_tab(self) -> Optional[TabDefinition]:
        if self._active_tab_id is None:
            return None
        return self._tabs.get(self._active_tab_id)

    @property
    def tab_count(self) -> int:
        return len(self._tabs)

    def _render_active_tab(self) -> None:
        """Swap the content and actions areas to reflect the active tab."""
        if not self.is_mounted:
            return

        title_widget = self.query_one("#tab-title", Static)
        content_area = self.query_one("#tab-content", Container)
        actions_area = self.query_one("#tab-actions", Container)

        for child in list(content_area.children):
            child.remove()
        for child in list(actions_area.children):
            child.remove()

        if not self._tabs or self._active_tab_id is None:
            title_widget.update("")
            return

        tab_ids = list(self._tabs.keys())
        active_index = tab_ids.index(self._active_tab_id)
        tab = self._tabs[self._active_tab_id]

        if len(self._tabs) > 1:
            title_widget.update(f"{tab.title} ({active_index + 1}/{len(self._tabs)})")
        else:
            title_widget.update(tab.title)

        content_area.mount(tab.content)
        if tab.actions is not None:
            actions_area.mount(tab.actions)

    def _resolve_tab_id(self, tab_ref: int | str) -> Optional[str]:
        if isinstance(tab_ref, int):
            tab_ids = list(self._tabs.keys())
            if tab_ref < 0 or tab_ref >= len(tab_ids):
                return None
            return tab_ids[tab_ref]

        resolved = str(tab_ref).strip()
        if not resolved or resolved not in self._tabs:
            return None
        return resolved
