from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

if TYPE_CHECKING:
    from agent_cli.core.bootstrap import AppContext

from agent_cli.core.events.event_bus import AsyncEventBus
from agent_cli.core.events.events import (
    ChangedFileDetailEvent,
    ChangedFileReviewActionEvent,
    ChangedFileSelectedEvent,
    FileChangedEvent,
)
from agent_cli.core.file_tracker import ChangeType, FileChangeTracker
from agent_cli.ux.tui.views.body.messages.changed_file_detail_block import (
    ChangedFileDetailBlock,
)
from agent_cli.ux.tui.views.body.panel.changed_file import (
    ChangedFileRow,
    ChangedFilesPanel,
)
from agent_cli.ux.tui.views.body.text_window import TextWindowContainer
from agent_cli.ux.tui.views.common.error_popup import ErrorPopup
from agent_cli.ux.tui.views.footer.footer import FooterContainer


class _PanelHostApp(App):
    def __init__(self, bus: AsyncEventBus, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ctx = cast(
            "AppContext",
            SimpleNamespace(event_bus=bus),
        )

    def compose(self) -> ComposeResult:
        yield ChangedFilesPanel(app_context=self.ctx)


class _PanelAndTextHostApp(App):
    def __init__(self, bus: AsyncEventBus, tracker: FileChangeTracker, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ctx = cast(
            "AppContext",
            SimpleNamespace(event_bus=bus, file_tracker=tracker),
        )

    def compose(self) -> ComposeResult:
        yield ChangedFilesPanel(app_context=self.ctx)
        yield TextWindowContainer(app_context=self.ctx)
        yield ErrorPopup(id="error_popup")


class _PanelTextFooterHostApp(App):
    def __init__(self, bus: AsyncEventBus, tracker: FileChangeTracker, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ctx = cast(
            "AppContext",
            SimpleNamespace(event_bus=bus, file_tracker=tracker, command_parser=None),
        )
        self.app_context = self.ctx

    def compose(self) -> ComposeResult:
        yield ChangedFilesPanel(app_context=self.ctx)
        yield TextWindowContainer(app_context=self.ctx)
        yield ErrorPopup(id="error_popup")
        yield FooterContainer()


@pytest.mark.asyncio
async def test_changed_files_panel_hidden_when_empty_and_visible_when_changes_exist():
    bus = AsyncEventBus()
    app = _PanelHostApp(bus)

    async with app.run_test() as pilot:
        panel = app.query_one(ChangedFilesPanel)
        await pilot.pause()

        # Hidden by default
        assert panel.has_class("-hidden")
        header = str(panel.query_one("#changed_files_header", Static).content)
        assert "Changed Files (0)" in header

        # Emit one file change => panel becomes visible
        await bus.publish(
            FileChangedEvent(
                source="test",
                file_path="src/example.py",
                change_type="modified",
                agent_name="Assistant",
            )
        )
        await pilot.pause()

        assert not panel.has_class("-hidden")
        header = str(panel.query_one("#changed_files_header", Static).content)
        assert "Changed Files (1)" in header

        # Empty-state placeholder should be hidden once item exists
        empty = panel.query_one("#changed_files_empty", Static)
        assert empty.has_class("-hidden")


@pytest.mark.asyncio
async def test_changed_files_panel_renders_one_row_per_unique_file():
    bus = AsyncEventBus()
    app = _PanelHostApp(bus)

    async with app.run_test() as pilot:
        panel = app.query_one(ChangedFilesPanel)
        await pilot.pause()

        # Two different paths => two rows
        await bus.publish(
            FileChangedEvent(
                source="test",
                file_path="a.py",
                change_type="created",
                agent_name="Assistant",
            )
        )
        await bus.publish(
            FileChangedEvent(
                source="test",
                file_path="b.py",
                change_type="deleted",
                agent_name="Assistant",
            )
        )
        await pilot.pause()

        rows = list(panel.query(ChangedFileRow))
        assert len(rows) == 2

        # Update same file path should not create extra row
        await bus.publish(
            FileChangedEvent(
                source="test",
                file_path="a.py",
                change_type="modified",
                agent_name="Assistant",
            )
        )
        await pilot.pause()

        rows = list(panel.query(ChangedFileRow))
        assert len(rows) == 2
        header = str(panel.query_one("#changed_files_header", Static).content)
        assert "Changed Files (2)" in header


@pytest.mark.asyncio
async def test_selecting_changed_file_emits_selected_and_detail_events():
    bus = AsyncEventBus()
    selected_events: list[ChangedFileSelectedEvent] = []
    detail_events: list[ChangedFileDetailEvent] = []

    async def _capture_selected(event):
        if isinstance(event, ChangedFileSelectedEvent):
            selected_events.append(event)

    async def _capture_detail(event):
        if isinstance(event, ChangedFileDetailEvent):
            detail_events.append(event)

    bus.subscribe("ChangedFileSelectedEvent", _capture_selected, priority=50)
    bus.subscribe("ChangedFileDetailEvent", _capture_detail, priority=50)

    app = _PanelHostApp(bus)
    async with app.run_test() as pilot:
        panel = app.query_one(ChangedFilesPanel)
        await pilot.pause()

        await bus.publish(
            FileChangedEvent(
                source="test",
                file_path="pkg/mod.py",
                change_type="modified",
                agent_name="Assistant",
            )
        )
        await pilot.pause()

        row = panel.query_one(ChangedFileRow)

        # Simulate row selection message (same path as user click flow)
        panel.on_changed_file_row_selected(ChangedFileRow.Selected(row, row.data))
        await pilot.pause()

        assert len(selected_events) == 1
        assert selected_events[0].file_path == "pkg/mod.py"
        assert selected_events[0].change_type == "modified"

        assert len(detail_events) == 1
        assert detail_events[0].file_path == "pkg/mod.py"
        assert detail_events[0].change_type == "modified"
        assert "### mod.py (modified)" in detail_events[0].detail_markdown


@pytest.mark.asyncio
async def test_selecting_changed_file_marks_row_selected():
    bus = AsyncEventBus()
    app = _PanelHostApp(bus)

    async with app.run_test() as pilot:
        panel = app.query_one(ChangedFilesPanel)
        await pilot.pause()

        await bus.publish(
            FileChangedEvent(
                source="test",
                file_path="first.py",
                change_type="modified",
                agent_name="Assistant",
            )
        )
        await bus.publish(
            FileChangedEvent(
                source="test",
                file_path="second.py",
                change_type="created",
                agent_name="Assistant",
            )
        )
        await pilot.pause()

        rows = list(panel.query(ChangedFileRow))
        assert len(rows) == 2

        panel.on_changed_file_row_selected(
            ChangedFileRow.Selected(rows[0], rows[0].data)
        )
        await pilot.pause()
        assert rows[0].has_class("-selected")
        assert not rows[1].has_class("-selected")

        panel.on_changed_file_row_selected(
            ChangedFileRow.Selected(rows[1], rows[1].data)
        )
        await pilot.pause()
        assert not rows[0].has_class("-selected")
        assert rows[1].has_class("-selected")


@pytest.mark.asyncio
async def test_selection_to_detail_event_reaches_text_window_subscription(tmp_path):
    bus = AsyncEventBus()
    tracker = FileChangeTracker(event_bus=bus)
    tracker.start_tracking(tmp_path)

    target = tmp_path / "docs" / "readme.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("line 1\nline 2\n", encoding="utf-8")
    await tracker.record_change(
        path=target,
        change_type=ChangeType.MODIFIED,
        agent_name="Assistant",
    )
    target.write_text("line 1\nline 2 changed\n", encoding="utf-8")

    received_detail: list[ChangedFileDetailEvent] = []

    app = _PanelAndTextHostApp(bus, tracker)
    async with app.run_test() as pilot:
        text_window = app.query_one(TextWindowContainer)
        await pilot.pause()

        # Attach a lightweight observer through TextWindow to validate bus wiring
        # in the same host where TextWindow is mounted.
        async def _capture_detail(event):
            if isinstance(event, ChangedFileDetailEvent):
                received_detail.append(event)

        sub_id = bus.subscribe("ChangedFileDetailEvent", _capture_detail, priority=60)

        panel = app.query_one(ChangedFilesPanel)
        await pilot.pause()

        row = panel.query_one(ChangedFileRow)
        panel.on_changed_file_row_selected(ChangedFileRow.Selected(row, row.data))
        await pilot.pause()

        assert text_window is not None
        assert len(received_detail) == 1
        assert received_detail[0].file_path == "docs/readme.md"
        assert "### readme.md (modified)" in received_detail[0].detail_markdown

        # Verify the TextWindow subscriber rendered the changed-file detail
        # with container rows (not markdown body text).
        detail_blocks = list(text_window.query(ChangedFileDetailBlock))
        assert len(detail_blocks) > 0
        detail_block = detail_blocks[-1]

        title = str(
            detail_block.query_one(".changed_file_detail_title", Static).content
        )
        assert "readme.md (modified)" in title

        diff_nodes = list(detail_block.query(".changed_file_diff_line"))
        assert len(diff_nodes) >= 2
        assert any(node.has_class("-removed") for node in diff_nodes)
        assert any(node.has_class("-added") for node in diff_nodes)

        diff_text = "\n".join(str(node.content) for node in diff_nodes)
        assert "- line 2" in diff_text
        assert "+ line 2 changed" in diff_text

        bus.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_footer_review_accept_emits_action_and_panel_removes_item(tmp_path):
    bus = AsyncEventBus()
    tracker = FileChangeTracker(event_bus=bus)
    tracker.start_tracking(tmp_path)

    target = tmp_path / "accept_me.txt"
    target.write_text("before", encoding="utf-8")
    await tracker.record_change(
        path=target,
        change_type=ChangeType.MODIFIED,
        agent_name="Assistant",
    )
    target.write_text("after", encoding="utf-8")

    actions: list[ChangedFileReviewActionEvent] = []

    async def _capture_action(event):
        if isinstance(event, ChangedFileReviewActionEvent):
            actions.append(event)

    bus.subscribe("ChangedFileReviewActionEvent", _capture_action, priority=60)

    app = _PanelTextFooterHostApp(bus, tracker)
    async with app.run_test() as pilot:
        panel = app.query_one(ChangedFilesPanel)
        footer = app.query_one(FooterContainer)
        await pilot.pause()

        row = panel.query_one(ChangedFileRow)
        panel.on_changed_file_row_selected(ChangedFileRow.Selected(row, row.data))
        await pilot.pause()

        await footer.on_user_interaction_action_selected(
            footer.user_interaction.ActionSelected(
                footer.user_interaction,
                task_id="",
                action="review_accept",
            )
        )
        await pilot.pause()

        assert len(actions) == 1
        assert actions[0].action == "accept"
        assert actions[0].file_path == "accept_me.txt"

        assert tracker.has_change("accept_me.txt") is False
        assert len(list(panel.query(ChangedFileRow))) == 0
        assert panel.has_class("-hidden")

        # Regression: preview block should be removed from conversation
        # after the file is accepted.
        detail_blocks = list(
            app.query_one(TextWindowContainer).query(ChangedFileDetailBlock)
        )
        assert len(detail_blocks) == 0


@pytest.mark.asyncio
async def test_footer_review_reject_reverts_file_and_panel_removes_item(tmp_path):
    bus = AsyncEventBus()
    tracker = FileChangeTracker(event_bus=bus)
    tracker.start_tracking(tmp_path)

    target = tmp_path / "reject_me.txt"
    target.write_text("original", encoding="utf-8")
    await tracker.record_change(
        path=target,
        change_type=ChangeType.MODIFIED,
        agent_name="Assistant",
    )
    target.write_text("modified", encoding="utf-8")

    actions: list[ChangedFileReviewActionEvent] = []

    async def _capture_action(event):
        if isinstance(event, ChangedFileReviewActionEvent):
            actions.append(event)

    bus.subscribe("ChangedFileReviewActionEvent", _capture_action, priority=60)

    app = _PanelTextFooterHostApp(bus, tracker)
    async with app.run_test() as pilot:
        panel = app.query_one(ChangedFilesPanel)
        footer = app.query_one(FooterContainer)
        await pilot.pause()

        row = panel.query_one(ChangedFileRow)
        panel.on_changed_file_row_selected(ChangedFileRow.Selected(row, row.data))
        await pilot.pause()

        await footer.on_user_interaction_action_selected(
            footer.user_interaction.ActionSelected(
                footer.user_interaction,
                task_id="",
                action="review_reject",
            )
        )
        await pilot.pause()

        assert len(actions) == 1
        assert actions[0].action == "reject"
        assert actions[0].file_path == "reject_me.txt"

        assert target.read_text(encoding="utf-8") == "original"
        assert tracker.has_change("reject_me.txt") is False
        assert len(list(panel.query(ChangedFileRow))) == 0
        assert panel.has_class("-hidden")

        # Regression: preview block should be removed from conversation
        # after the file is rejected.
        detail_blocks = list(
            app.query_one(TextWindowContainer).query(ChangedFileDetailBlock)
        )
        assert len(detail_blocks) == 0
