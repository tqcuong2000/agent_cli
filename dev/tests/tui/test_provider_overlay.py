from __future__ import annotations

from types import SimpleNamespace

import pytest
from textual import events
from textual.app import App, ComposeResult
from textual.widgets import Input, Static

from agent_cli.core.infra.config.config_models import ProviderSpec
from agent_cli.core.ux.tui.views.main.provider.provider_overlay import (
    ProviderOverlay,
)


class _FakeDataRegistry:
    def __init__(self, specs: dict[str, ProviderSpec]) -> None:
        self._specs = specs

    def get_provider_specs(self) -> dict[str, ProviderSpec]:
        return dict(self._specs)


class _FakeKeyManager:
    def __init__(self, states: dict[str, tuple[bool, str]]) -> None:
        self._states = dict(states)
        self._env_to_provider: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, str]] = []
        self.delete_calls: list[tuple[str, str]] = []

    def bind_specs(self, specs: dict[str, ProviderSpec]) -> None:
        self._env_to_provider = {
            spec.api_key_env: name
            for name, spec in specs.items()
            if spec.api_key_env is not None
        }

    def set_key(self, provider_name: str, env_var: str, value: str) -> bool:
        self.set_calls.append((provider_name, env_var, value))
        self._states[provider_name] = (True, "dotenv")
        return True

    def delete_key(self, provider_name: str, env_var: str) -> bool:
        self.delete_calls.append((provider_name, env_var))
        self._states[provider_name] = (False, "none")
        return True

    def is_key_set(self, provider_name: str) -> bool:
        return self._states.get(provider_name, (False, "none"))[0]

    def get_key_source(self, env_var: str) -> str:
        provider_name = self._env_to_provider.get(env_var, "")
        return self._states.get(provider_name, (False, "none"))[1]


class _ProviderOverlayHostApp(App):
    def __init__(self, app_context, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.app_context = app_context
        self.overlay = ProviderOverlay()
        self.notices: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield self.overlay

    def notify(self, message, *, severity="information", **kwargs) -> None:
        self.notices.append((str(message), str(severity)))


def _build_specs() -> dict[str, ProviderSpec]:
    return {
        "openai": ProviderSpec(
            name="openai",
            adapter_type="openai",
            api_key_env="OPENAI_API_KEY",
            require_verification=True,
        ),
        "google": ProviderSpec(
            name="google",
            adapter_type="google",
            api_key_env="GOOGLE_API_KEY",
            require_verification=True,
        ),
        "ollama": ProviderSpec(
            name="ollama",
            adapter_type="openai_compatible",
            base_url="http://localhost:11434/v1",
            require_verification=False,
        ),
    }


def _row_for(overlay: ProviderOverlay, provider_name: str):
    for row in overlay.query(".provider-row"):
        if getattr(row, "_provider_name", None) == provider_name:
            return row
    raise AssertionError(f"Missing provider row: {provider_name}")


@pytest.mark.asyncio
async def test_provider_overlay_renders_statuses_and_non_key_rows():
    specs = _build_specs()
    key_manager = _FakeKeyManager(
        {
            "openai": (False, "none"),
            "google": (True, "env"),
            "ollama": (False, "none"),
        }
    )
    key_manager.bind_specs(specs)
    app = _ProviderOverlayHostApp(
        SimpleNamespace(
            data_registry=_FakeDataRegistry(specs),
            key_manager=key_manager,
        )
    )

    async with app.run_test() as pilot:
        app.overlay.show_overlay()
        await pilot.pause()

        rows = [getattr(row, "_provider_name", None) for row in app.overlay.query(".provider-row")]
        assert rows == ["google", "openai", "ollama"]

        google_status = str(_row_for(app.overlay, "google").query_one(".provider-status", Static).content)
        openai_status = str(_row_for(app.overlay, "openai").query_one(".provider-status", Static).content)
        ollama_status = str(_row_for(app.overlay, "ollama").query_one(".provider-status", Static).content)

        assert google_status == "✅ 🔒"
        assert openai_status == "❌"
        assert ollama_status == "➖"
        assert _row_for(app.overlay, "google").has_class("-locked")
        assert _row_for(app.overlay, "ollama").has_class("-no-key")


@pytest.mark.asyncio
async def test_provider_overlay_enter_and_submit_saves_key():
    specs = {
        "openai": _build_specs()["openai"],
        "ollama": _build_specs()["ollama"],
    }
    key_manager = _FakeKeyManager(
        {
            "openai": (False, "none"),
            "ollama": (False, "none"),
        }
    )
    key_manager.bind_specs(specs)
    app = _ProviderOverlayHostApp(
        SimpleNamespace(
            data_registry=_FakeDataRegistry(specs),
            key_manager=key_manager,
        )
    )

    async with app.run_test() as pilot:
        app.overlay.show_overlay()
        await pilot.pause()

        await app.overlay.on_key(events.Key("enter", None))
        await pilot.pause()

        key_input = app.overlay.query_one("#provider-key-input", Input)
        assert key_input.has_class("visible")

        await app.overlay.on_input_submitted(
            Input.Submitted(key_input, "sk-openai-test")
        )
        await pilot.pause()

        assert key_manager.set_calls == [
            ("openai", "OPENAI_API_KEY", "sk-openai-test")
        ]
        assert not key_input.has_class("visible")
        openai_status = str(_row_for(app.overlay, "openai").query_one(".provider-status", Static).content)
        assert openai_status == "✅"
        assert any("Saved API key for openai." in notice for notice, _ in app.notices)


@pytest.mark.asyncio
async def test_provider_overlay_delete_key_and_locked_rows_do_not_mutate():
    specs = {
        "anthropic": ProviderSpec(
            name="anthropic",
            adapter_type="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
            require_verification=True,
        ),
        "google": _build_specs()["google"],
    }
    key_manager = _FakeKeyManager(
        {
            "anthropic": (True, "dotenv"),
            "google": (True, "env"),
        }
    )
    key_manager.bind_specs(specs)
    app = _ProviderOverlayHostApp(
        SimpleNamespace(
            data_registry=_FakeDataRegistry(specs),
            key_manager=key_manager,
        )
    )

    async with app.run_test() as pilot:
        app.overlay.show_overlay()
        await pilot.pause()

        await app.overlay.on_key(events.Key("ctrl+d", None))
        await pilot.pause()

        assert key_manager.delete_calls == [("anthropic", "ANTHROPIC_API_KEY")]
        anthropic_status = str(
            _row_for(app.overlay, "anthropic").query_one(".provider-status", Static).content
        )
        assert anthropic_status == "❌"

        await app.overlay.on_key(events.Key("down", None))
        await pilot.pause()
        await app.overlay.on_key(events.Key("ctrl+d", None))
        await pilot.pause()

        assert key_manager.delete_calls == [("anthropic", "ANTHROPIC_API_KEY")]
        assert any(
            "google is configured via environment variable." in notice
            for notice, _ in app.notices
        )
