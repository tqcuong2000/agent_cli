from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Input, Static

if TYPE_CHECKING:
    from textual.widget import Widget

    from agent_cli.core.infra.config.key_manager import KeySource
    from agent_cli.core.infra.config.key_manager import KeyManager
    from agent_cli.core.infra.config.config_models import ProviderSpec
    from agent_cli.core.infra.registry.bootstrap import AppContext


@dataclass
class ProviderRowState:
    name: str
    spec: "ProviderSpec"
    is_key_set: bool
    key_source: "KeySource"

    @property
    def is_locked(self) -> bool:
        return self.key_source == "env"

    @property
    def is_external(self) -> bool:
        return self.is_key_set and self.key_source == "none"

    @property
    def needs_key(self) -> bool:
        return bool(self.spec.require_verification)

    @property
    def is_interactive(self) -> bool:
        return self.needs_key and not self.is_locked and bool(self.spec.api_key_env)

    @property
    def row_classes(self) -> str:
        classes = ["provider-row"]
        if self.is_locked:
            classes.append("-locked")
        if not self.needs_key:
            classes.append("-no-key")
        if self.is_external:
            classes.append("-external")
        return " ".join(classes)

    @property
    def status_text(self) -> str:
        if not self.needs_key:
            return "➖"
        if self.is_locked:
            return "✅ 🔒"
        if self.is_external:
            return "✅"
        if self.key_source == "dotenv":
            return "✅"
        return "❌"

    @property
    def meta_text(self) -> str:
        if not self.needs_key:
            return "No API key required"
        if self.is_locked:
            return f"{self.spec.api_key_env} via environment"
        if self.is_external:
            return "Configured via external source"
        if self.key_source == "dotenv":
            return f"{self.spec.api_key_env} saved in .env"
        return f"Missing {self.spec.api_key_env or 'API key'}"


class ProviderOverlay(Container):
    can_focus = True

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._providers: list[ProviderRowState] = []
        self._selected_index = 0
        self._editing_provider_name: Optional[str] = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="provider-popup"):
            with Container(classes="title-row"):
                yield Static("Provider Connections", classes="title-left")
                yield Static("esc", id="provider-overlay-esc", classes="title-right")
            yield Container(id="provider-list", classes="provider-area")
            yield Input(
                placeholder="Enter API key",
                id="provider-key-input",
            )
            yield Static(
                "[b]enter [dimgrey]set/change[/dimgrey] | ctrl+d [dimgrey]delete[/dimgrey][/b]",
                classes="footer-hint",
            )

    def show_overlay(self) -> None:
        self.refresh_providers()
        self._hide_key_input()
        self.add_class("visible")
        self.focus()

    def hide_overlay(self) -> None:
        self._hide_key_input()
        self.remove_class("visible")

    async def on_key(self, event: events.Key) -> None:
        key = event.key.lower()
        if key == "escape":
            event.stop()
            if self._editing_provider_name is not None:
                self._hide_key_input()
                self.focus()
                return
            self.hide_overlay()
            return

        if self._editing_provider_name is not None:
            return

        if key == "up":
            event.stop()
            self._move_selection(-1)
            return

        if key == "down":
            event.stop()
            self._move_selection(1)
            return

        if key == "enter":
            event.stop()
            self._begin_key_input()
            return

        if key == "ctrl+d":
            event.stop()
            self._delete_selected_key()
            return

    async def on_click(self, event: events.Click) -> None:
        widget = event.widget
        if widget is not None and widget.id == "provider-overlay-esc":
            event.stop()
            self.hide_overlay()
            return

        provider_name = self._extract_row_id(widget)
        if provider_name is None:
            return

        event.stop()
        for idx, row in enumerate(self._providers):
            if row.name == provider_name:
                self._selected_index = idx
                self._update_selected_row_styles()
                return

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "provider-key-input":
            return
        if self._editing_provider_name is None:
            return

        value = event.value.strip()
        if not value:
            self._notify("API key cannot be empty.", severity="warning")
            return

        row = self._find_provider_row(self._editing_provider_name)
        key_manager = self._get_key_manager()
        if row is None or key_manager is None or not row.spec.api_key_env:
            self._notify("Provider key manager unavailable.", severity="error")
            return

        if key_manager.set_key(row.name, row.spec.api_key_env, value):
            self._notify(f"Saved API key for {row.name}.")
            self._hide_key_input()
            self.refresh_providers(selected_name=row.name)
            self.focus()
            return

        self._notify(f"Failed to save API key for {row.name}.", severity="error")

    def refresh_providers(self, selected_name: str | None = None) -> None:
        app_context = self._get_app_context()
        key_manager = self._get_key_manager()
        if app_context is None or key_manager is None:
            self._providers = []
            self._selected_index = 0
            self._render_provider_rows(error="Provider services are unavailable.")
            return

        specs = app_context.data_registry.get_provider_specs()
        ordered_specs = sorted(
            specs.values(),
            key=lambda spec: (not spec.require_verification, spec.name.lower()),
        )

        self._providers = []
        for spec in ordered_specs:
            source: KeySource = "none"
            if spec.api_key_env:
                source = key_manager.get_key_source(spec.api_key_env)
            self._providers.append(
                ProviderRowState(
                    name=spec.name,
                    spec=spec,
                    is_key_set=key_manager.is_key_set(spec.name),
                    key_source=source,
                )
            )

        if not self._providers:
            self._selected_index = 0
        elif selected_name is not None:
            for idx, row in enumerate(self._providers):
                if row.name == selected_name:
                    self._selected_index = idx
                    break
            else:
                self._selected_index = min(self._selected_index, len(self._providers) - 1)
        else:
            self._selected_index = max(
                0,
                min(self._selected_index, len(self._providers) - 1),
            )

        self._render_provider_rows()

    def _render_provider_rows(self, error: Optional[str] = None) -> None:
        area = self.query_one("#provider-list", Container)
        area.remove_children()

        if error:
            area.mount(Static(error, classes="empty-state"))
            return

        if not self._providers:
            area.mount(Static("No built-in providers found.", classes="empty-state"))
            return

        for idx, row_state in enumerate(self._providers):
            left = Static(
                f"{row_state.name}\n[dim]{row_state.meta_text}[/dim]",
                classes="provider-name",
            )
            right = Static(row_state.status_text, classes="provider-status")
            row_classes = row_state.row_classes
            if idx == self._selected_index:
                row_classes = f"{row_classes} -selected"
            row = Container(left, right, classes=row_classes)
            setattr(row, "_provider_name", row_state.name)
            area.mount(row)

        self.call_after_refresh(self._update_selected_row_styles)

    def _update_selected_row_styles(self) -> None:
        area = self.query_one("#provider-list", Container)
        rows = [child for child in area.children if isinstance(child, Container)]
        for i, row in enumerate(rows):
            if i == self._selected_index:
                row.add_class("-selected")
                area.scroll_to_widget(row, animate=False)
            else:
                row.remove_class("-selected")

    def _move_selection(self, delta: int) -> None:
        if not self._providers:
            return
        new_index = max(0, min(self._selected_index + delta, len(self._providers) - 1))
        if new_index == self._selected_index:
            return
        self._selected_index = new_index
        self._update_selected_row_styles()

    def _begin_key_input(self) -> None:
        row = self._selected_provider()
        if row is None:
            return
        if not row.needs_key:
            self._notify(f"{row.name} does not require an API key.")
            return
        if row.is_locked:
            self._notify(
                f"{row.name} is configured via environment variable.",
                severity="warning",
            )
            return
        if not row.spec.api_key_env:
            self._notify(
                f"{row.name} has no API key environment variable configured.",
                severity="warning",
            )
            return

        key_input = self.query_one("#provider-key-input", Input)
        key_input.value = ""
        key_input.placeholder = (
            f"Enter {row.name} API key ({row.spec.api_key_env}) and press Enter"
        )
        key_input.add_class("visible")
        key_input.focus()
        self._editing_provider_name = row.name

    def _hide_key_input(self) -> None:
        key_input = self.query_one("#provider-key-input", Input)
        key_input.remove_class("visible")
        key_input.value = ""
        key_input.placeholder = "Enter API key"
        self._editing_provider_name = None

    def _delete_selected_key(self) -> None:
        row = self._selected_provider()
        key_manager = self._get_key_manager()
        if row is None or key_manager is None:
            return
        if not row.needs_key:
            self._notify(f"{row.name} does not require an API key.")
            return
        if row.is_locked:
            self._notify(
                f"{row.name} is configured via environment variable.",
                severity="warning",
            )
            return
        if not row.spec.api_key_env:
            self._notify(
                f"{row.name} has no API key environment variable configured.",
                severity="warning",
            )
            return
        if row.key_source != "dotenv":
            self._notify(f"No saved .env key found for {row.name}.", severity="warning")
            return

        if key_manager.delete_key(row.name, row.spec.api_key_env):
            self._notify(f"Deleted API key for {row.name}.")
            self.refresh_providers(selected_name=row.name)
            return

        self._notify(f"Failed to delete API key for {row.name}.", severity="error")

    def _selected_provider(self) -> Optional[ProviderRowState]:
        if not self._providers:
            return None
        return self._providers[self._selected_index]

    def _find_provider_row(self, provider_name: str) -> Optional[ProviderRowState]:
        for row in self._providers:
            if row.name == provider_name:
                return row
        return None

    def _get_app_context(self) -> Optional["AppContext"]:
        return getattr(self.app, "app_context", None)

    def _get_key_manager(self) -> Optional["KeyManager"]:
        app_context = self._get_app_context()
        if app_context is None:
            return None
        return getattr(app_context, "key_manager", None)

    def _extract_row_id(self, widget: Optional["Widget"]) -> Optional[str]:
        current = widget
        while current is not None and current is not self:
            provider_name = getattr(current, "_provider_name", None)
            if isinstance(provider_name, str) and provider_name:
                return provider_name
            current = current.parent
        return None

    def _notify(self, message: str, severity: str = "information") -> None:
        try:
            self.app.notify(message, severity=severity)
        except Exception:
            pass
