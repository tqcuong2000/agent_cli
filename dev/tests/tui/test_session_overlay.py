from __future__ import annotations

from types import SimpleNamespace

import pytest
from textual import events
from textual.app import App, ComposeResult

from agent_cli.core.runtime.session.file_store import FileSessionManager
from agent_cli.core.ux.tui.views.main.session.session_overlay import SessionOverlay


class _DummyMemoryManager:
    def __init__(self) -> None:
        self.reset_called = False

    def reset_working(self) -> None:
        self.reset_called = True


class _SessionOverlayHostApp(App):
    def __init__(self, session_manager: FileSessionManager, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.app_context = SimpleNamespace(
            session_manager=session_manager,
            memory_manager=_DummyMemoryManager(),
        )
        self.overlay = SessionOverlay()

    def compose(self) -> ComposeResult:
        yield self.overlay


@pytest.mark.asyncio
async def test_ctrl_d_delete_keeps_overlay_focus_and_updates_selected_border(tmp_path):
    session_manager = FileSessionManager(
        session_dir=tmp_path / "sessions",
        default_model="gpt-4o-mini",
    )
    first = session_manager.create_session(name="First")
    session_manager.save(first)
    second = session_manager.create_session(name="Second")
    session_manager.save(second)

    app = _SessionOverlayHostApp(session_manager)

    async with app.run_test() as pilot:
        app.overlay.show_overlay()
        await pilot.pause()

        rows_before = list(app.overlay.query(".session-row"))
        assert rows_before[0].has_class("selected")

        await app.overlay.on_key(events.Key("ctrl+d", None))
        await pilot.pause()
        
        rows_after = list(app.overlay.query(".session-row"))
        assert len(rows_after) == 1
        assert app.overlay.has_focus is True
