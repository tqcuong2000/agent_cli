from __future__ import annotations

from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Markdown, Static

from agent_cli.core.infra.events.event_bus import AsyncEventBus
from agent_cli.core.infra.events.events import (
    AgentMessageEvent,
    TaskResultEvent,
    ToolExecutionResultEvent,
    ToolExecutionStartEvent,
    UserRequestEvent,
)
from agent_cli.core.ux.tui.views.main.chat.messages.agent_response import AgentResponseContainer
from agent_cli.core.ux.tui.views.main.chat.messages.answer_block import AnswerBlock
from agent_cli.core.ux.tui.views.main.chat.messages.system_message import SystemMessageContainer
from agent_cli.core.ux.tui.views.main.chat.messages.thinking_block import ThinkingBlock
from agent_cli.core.ux.tui.views.main.chat.messages.tool_step import ToolStepWidget
from agent_cli.core.ux.tui.views.main.chat.text_window import TextWindowContainer
from agent_cli.core.ux.tui.views.common.error_popup import ErrorPopup


class _HostApp(App):
    def __init__(self, widget, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


class _TextWindowHostApp(App):
    def __init__(self, bus: AsyncEventBus, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ctx = SimpleNamespace(event_bus=bus)

    def compose(self) -> ComposeResult:
        yield TextWindowContainer(app_context=self.ctx)
        yield ErrorPopup(id="error_popup")


@pytest.mark.asyncio
async def test_agent_response_container_mounts_thinking_block():
    arc = AgentResponseContainer(id="arc")
    app = _HostApp(arc)

    async with app.run_test() as pilot:
        mounted = arc.append_thinking()
        await pilot.pause()

        blocks = list(arc.query(ThinkingBlock))
        assert len(blocks) == 1
        assert blocks[0] is mounted


@pytest.mark.asyncio
async def test_thinking_block_append_chunk_and_finish_streaming():
    block = ThinkingBlock(id="thinking")
    app = _HostApp(block)

    async with app.run_test() as pilot:
        block.append_chunk(
            "Title: Review context and choose implementation path\nFirst thought line."
        )
        await pilot.pause()

        assert "First thought line." in block._thoughts
        assert isinstance(block.query_one(".thinking_content", Markdown), Markdown)

        block.finish_streaming()
        await pilot.pause()

        header = str(block.query_one(".thinking_header", Static).content)
        assert block.is_streaming is False
        assert "Review context and choose implementation path" in header


@pytest.mark.asyncio
async def test_thinking_block_click_toggle_changes_is_expanded():
    class _DummyClick:
        def stop(self) -> None:
            return None

    block = ThinkingBlock(id="thinking")
    app = _HostApp(block)

    async with app.run_test() as pilot:
        initial = block.is_expanded
        block.on_click(_DummyClick())
        await pilot.pause()
        assert block.is_expanded is (not initial)

        block.on_click(_DummyClick())
        await pilot.pause()
        assert block.is_expanded is initial


@pytest.mark.asyncio
async def test_tool_step_mark_success_updates_label_and_stops_timer():
    step = ToolStepWidget("read_file", {"path": "a.py"})
    app = _HostApp(step)

    async with app.run_test() as pilot:
        await pilot.pause()
        step.mark_success(42)
        await pilot.pause()

        label = str(step.query_one(".tool_step_label", Static).content)
        assert "✓" in label
        assert "(42 ms)" in label
        assert step._timer is None


@pytest.mark.asyncio
async def test_tool_step_mark_failed_updates_label_and_stops_timer():
    step = ToolStepWidget("read_file", {"path": "a.py"})
    app = _HostApp(step)

    async with app.run_test() as pilot:
        await pilot.pause()
        step.mark_failed("permission denied")
        await pilot.pause()

        label = str(step.query_one(".tool_step_label", Static).content)
        assert "✗" in label
        assert "permission denied" in label
        assert step._timer is None


@pytest.mark.asyncio
async def test_tool_step_mark_failed_escapes_rich_markup_in_error():
    step = ToolStepWidget("read_file", {"path": "a.py"})
    app = _HostApp(step)

    raw_error = "error truncated=false truncated_chars=0 er...[/dim]"
    async with app.run_test() as pilot:
        await pilot.pause()
        step.mark_failed(raw_error)
        await pilot.pause()

        label = str(step.query_one(".tool_step_label", Static).content)
        assert "truncated=false" in label
        assert "truncated_chars=0" in label
        assert step._timer is None


@pytest.mark.asyncio
async def test_tool_step_mark_failed_renders_raw_tool_envelope_without_markup_crash():
    step = ToolStepWidget("list_directory", {"path": "X:/datapack", "max_depth": 1})
    app = _HostApp(step)

    raw_error = (
        "[tool_result tool=list_directory status=error truncated=false "
        "truncated_chars=0 content_ref=sha256:abc123]"
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        step.mark_failed(raw_error)
        await pilot.pause()

        label = str(step.query_one(".tool_step_label", Static).content)
        assert "[tool_result tool=list_directory" in label
        assert "truncated=false" in label
        assert step._timer is None


@pytest.mark.asyncio
async def test_answer_block_append_chunk_accumulates_content():
    answer = AnswerBlock("Hello")
    app = _HostApp(answer)

    async with app.run_test() as pilot:
        answer.append_chunk(" world")
        await pilot.pause()

        assert answer._buffer == "Hello world"
        assert isinstance(answer.query_one(Markdown), Markdown)


@pytest.mark.asyncio
async def test_error_popup_show_and_dismiss():
    popup = ErrorPopup(id="error_popup")
    app = _HostApp(popup)

    async with app.run_test() as pilot:
        popup.show_error("Test Error", "Something happened", "error")
        await pilot.pause()
        assert popup.has_class("visible")

        popup.dismiss()
        await pilot.pause()
        assert not popup.has_class("visible")


@pytest.mark.asyncio
async def test_text_window_auto_scroll_triggers_across_event_flow():
    bus = AsyncEventBus()
    app = _TextWindowHostApp(bus)

    async with app.run_test() as pilot:
        text_window = app.query_one(TextWindowContainer)
        scroll_calls: list[int] = []
        text_window._scroll_to_end = lambda: scroll_calls.append(1)  # type: ignore[method-assign]
        await pilot.pause()

        await bus.publish(UserRequestEvent(source="tui", text="hello"))
        await pilot.pause()
        c1 = len(scroll_calls)
        assert c1 > 0

        await bus.publish(
            AgentMessageEvent(
                source="agent",
                agent_name="coder",
                content=(
                    "Title: Review context and choose safe approach\n"
                    "Thinking chunk one."
                ),
                is_monologue=True,
            )
        )
        await pilot.pause()
        c2 = len(scroll_calls)
        assert c2 > c1

        await bus.publish(
            AgentMessageEvent(
                source="agent",
                agent_name="coder",
                content=(
                    "Title: Review context and choose safe approach\n"
                    "Thinking chunk two."
                ),
                is_monologue=True,
            )
        )
        await pilot.pause()
        c3 = len(scroll_calls)
        assert c3 > c2

        await bus.publish(
            ToolExecutionStartEvent(
                source="tool_executor",
                task_id="task-1",
                tool_name="read_file",
                arguments={"path": "README.md"},
            )
        )
        await pilot.pause()
        c4 = len(scroll_calls)
        assert c4 > c3

        await bus.publish(
            ToolExecutionResultEvent(
                source="tool_executor",
                task_id="task-1",
                tool_name="read_file",
                output="ok",
                is_error=False,
            )
        )
        await pilot.pause()
        c5 = len(scroll_calls)
        assert c5 > c4

        await bus.publish(
            AgentMessageEvent(
                source="agent",
                agent_name="coder",
                content="Final answer body.",
                is_monologue=False,
            )
        )
        await pilot.pause()
        c6 = len(scroll_calls)
        assert c6 > c5


@pytest.mark.asyncio
async def test_command_system_message_uses_system_message_container():
    bus = AsyncEventBus()
    app = _TextWindowHostApp(bus)

    async with app.run_test() as pilot:
        await pilot.pause()

        await bus.publish(
            AgentMessageEvent(
                source="command_system",
                content="Log level set to: DEBUG",
                is_monologue=False,
            )
        )
        await pilot.pause()

        text_window = app.query_one(TextWindowContainer)
        system_messages = list(text_window.query(SystemMessageContainer))
        assert len(system_messages) >= 1
        assert system_messages[-1].message_text == "Log level set to: DEBUG"

        # Command/system messages should not be rendered as answer blocks.
        assert len(list(text_window.query(AnswerBlock))) == 0


@pytest.mark.asyncio
async def test_cancelled_task_result_does_not_show_error_popup():
    bus = AsyncEventBus()
    app = _TextWindowHostApp(bus)

    async with app.run_test() as pilot:
        await pilot.pause()

        popup = app.query_one("#error_popup", ErrorPopup)
        assert not popup.has_class("visible")

        await bus.publish(
            TaskResultEvent(
                source="orchestrator",
                task_id="task-cancel",
                result="Task cancelled by user.",
                is_success=False,
            )
        )
        await pilot.pause()

        assert not popup.has_class("visible")
