from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, DefaultDict, List, Optional, Tuple

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.css.query import NoMatches

from agent_cli.core.events.events import (
    AgentMessageEvent,
    BaseEvent,
    ChangedFileDetailEvent,
    ChangedFileDiffLine,
    ChangedFileReviewActionEvent,
    SystemErrorEvent,
    TaskErrorEvent,
    TaskResultEvent,
    ToolExecutionResultEvent,
    ToolExecutionStartEvent,
    UserRequestEvent,
)
from agent_cli.ux.tui.views.body.messages.agent_response import AgentResponseContainer
from agent_cli.ux.tui.views.body.messages.changed_file_detail_block import DiffLine
from agent_cli.ux.tui.views.body.messages.tool_step import ToolStepWidget
from agent_cli.ux.tui.views.body.messages.user_message import UserMessageContainer
from agent_cli.ux.tui.views.common.error_popup import ErrorPopup

if TYPE_CHECKING:
    from agent_cli.core.bootstrap import AppContext


class TextWindowContainer(Container):
    """Event-driven container for chat message rendering."""

    DEFAULT_CSS = """
    TextWindowContainer {
        width: 3fr;
        height: 100%;
        background: transparent;
    }

    TextWindowContainer #messages {
        width: 100%;
        height: 100%;
        scrollbar-size: 0 0;
    }
    """

    def __init__(self, app_context: Optional["AppContext"] = None, **kwargs):
        if "id" not in kwargs:
            kwargs["id"] = "text_window"
        super().__init__(**kwargs)
        self._app_context = app_context
        self._subscriptions: List[str] = []
        self._current_arc: Optional[AgentResponseContainer] = None
        self._active_task_id: str = ""
        self._pending_tools: DefaultDict[
            str, List[Tuple[str, ToolStepWidget, float]]
        ] = defaultdict(list)

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="messages")

    def on_mount(self) -> None:
        if self._app_context is None:
            self._show_error(
                title="Event Bus Unavailable",
                message="Text window is running without AppContext wiring.",
                error_type="warning",
            )
            return

        bus = self._app_context.event_bus
        self._subscriptions.append(
            bus.subscribe("UserRequestEvent", self._on_user_request, priority=50)
        )
        self._subscriptions.append(
            bus.subscribe("AgentMessageEvent", self._on_agent_message, priority=50)
        )
        self._subscriptions.append(
            bus.subscribe(
                "ToolExecutionStartEvent",
                self._on_tool_start,
                priority=50,
            )
        )
        self._subscriptions.append(
            bus.subscribe(
                "ToolExecutionResultEvent",
                self._on_tool_result,
                priority=50,
            )
        )
        self._subscriptions.append(
            bus.subscribe("TaskResultEvent", self._on_task_result, priority=50)
        )
        self._subscriptions.append(
            bus.subscribe("TaskErrorEvent", self._on_task_error, priority=50)
        )
        self._subscriptions.append(
            bus.subscribe("SystemErrorEvent", self._on_system_error, priority=50)
        )
        self._subscriptions.append(
            bus.subscribe(
                "ChangedFileDetailEvent",
                self._on_changed_file_detail,
                priority=50,
            )
        )
        self._subscriptions.append(
            bus.subscribe(
                "ChangedFileReviewActionEvent",
                self._on_changed_file_review_action,
                priority=50,
            )
        )

    def on_unmount(self) -> None:
        if self._app_context is None:
            return
        bus = self._app_context.event_bus
        for subscription_id in self._subscriptions:
            bus.unsubscribe(subscription_id)
        self._subscriptions.clear()

    async def _on_user_request(self, event: BaseEvent) -> None:
        if not isinstance(event, UserRequestEvent):
            return
        # User message is usually mounted synchronously by the footer via
        # add_user_message() BEFORE this event fires, but we still trigger
        # a scroll refresh here to ensure visual consistency across all events.
        self.call_after_refresh(self._scroll_to_end)

    async def _on_agent_message(self, event: BaseEvent) -> None:
        if not isinstance(event, AgentMessageEvent):
            return
        response = self._ensure_current_response()
        if event.is_monologue:
            thinking = response.get_active_thinking()
            if thinking is None:
                thinking = response.append_thinking()
            thinking.append_chunk(event.content)
        else:
            active_thinking = response.get_active_thinking()
            if active_thinking is not None:
                active_thinking.finish_streaming()
            response.set_answer(event.content)
        self.call_after_refresh(self._scroll_to_end)

    async def _on_tool_start(self, event: BaseEvent) -> None:
        if not isinstance(event, ToolExecutionStartEvent):
            return
        response = self._ensure_current_response()
        active_thinking = response.get_active_thinking()
        if active_thinking is not None:
            active_thinking.finish_streaming()

        step = response.append_tool_step(event.tool_name, event.arguments)
        key = self._task_key(event.task_id)
        self._pending_tools[key].append((event.tool_name, step, event.timestamp))
        if event.task_id:
            self._active_task_id = event.task_id
        self.call_after_refresh(self._scroll_to_end)

    async def _on_tool_result(self, event: BaseEvent) -> None:
        if not isinstance(event, ToolExecutionResultEvent):
            return

        match = self._resolve_tool_step(event)
        if match is None:
            response = self._ensure_current_response()
            step = response.append_tool_step(event.tool_name, {})
            started_at = event.timestamp
        else:
            step, started_at = match

        duration_ms = max(0, int((event.timestamp - started_at) * 1000))
        if event.is_error:
            step.mark_failed(event.output)
        else:
            step.mark_success(duration_ms)
        self.call_after_refresh(self._scroll_to_end)

    async def _on_task_result(self, event: BaseEvent) -> None:
        if not isinstance(event, TaskResultEvent):
            return

        if not event.is_success:
            self._show_error(
                title="Task Failed",
                message=event.result,
                error_type="error",
            )

        task_key = self._task_key(event.task_id)
        self._pending_tools.pop(task_key, None)

        active_thinking = (
            self._current_arc.get_active_thinking() if self._current_arc else None
        )
        if active_thinking is not None:
            active_thinking.finish_streaming()

        self._current_arc = None
        if self._active_task_id == event.task_id:
            self._active_task_id = ""

    async def _on_task_error(self, event: BaseEvent) -> None:
        if not isinstance(event, TaskErrorEvent):
            return
        self._show_error(
            title=f"Task Error ({event.tier or 'UNKNOWN'})",
            message=event.error_message or event.technical_detail,
            error_type="error",
        )

    async def _on_system_error(self, event: BaseEvent) -> None:
        if not isinstance(event, SystemErrorEvent):
            return
        message = (
            f"{event.error_message}\n"
            f"[{event.original_event_type}] subscriber={event.subscriber_id}"
        )
        self._show_error(
            title="System Error",
            message=message,
            error_type="error",
        )

    async def _on_changed_file_detail(self, event: BaseEvent) -> None:
        if not isinstance(event, ChangedFileDetailEvent):
            return

        response = self._ensure_current_response()
        active_thinking = response.get_active_thinking()
        if active_thinking is not None:
            active_thinking.finish_streaming()

        diff_lines = [
            DiffLine(kind=line.kind, text=line.text)
            for line in event.diff_lines
            if isinstance(line, ChangedFileDiffLine)
        ]

        title = event.title
        if not title:
            file_name = event.file_path.rsplit("/", 1)[-1] if event.file_path else ""
            label = (event.change_type or "changed").lower()
            title = f"{file_name} ({label})" if file_name else f"({label})"

        summary = event.summary
        if not summary and not diff_lines:
            summary = "Preview unavailable."

        response.set_changed_file_detail(
            title=title,
            summary=summary,
            diff_lines=diff_lines,
            file_path=event.file_path,
        )
        self.call_after_refresh(self._scroll_to_end)

    async def _on_changed_file_review_action(self, event: BaseEvent) -> None:
        if not isinstance(event, ChangedFileReviewActionEvent):
            return

        target_path = (event.file_path or "").strip()
        if not target_path:
            return

        from agent_cli.ux.tui.views.body.messages.changed_file_detail_block import (
            ChangedFileDetailBlock,
        )

        for detail in list(self.query(ChangedFileDetailBlock)):
            detail_path = getattr(detail, "file_path", "")
            if detail_path == target_path:
                detail.remove()

        self.call_after_refresh(self._scroll_to_end)

    def _ensure_current_response(self) -> AgentResponseContainer:
        if self._current_arc is None:
            self._current_arc = AgentResponseContainer()
            self._messages.mount(self._current_arc)
            self.call_after_refresh(self._scroll_to_end)
        return self._current_arc

    def _resolve_tool_step(
        self, event: ToolExecutionResultEvent
    ) -> Optional[Tuple[ToolStepWidget, float]]:
        candidate_keys = [self._task_key(event.task_id)]
        if self._active_task_id:
            candidate_keys.append(self._task_key(self._active_task_id))
        candidate_keys.append("__global__")

        seen = set()
        for key in candidate_keys:
            if key in seen:
                continue
            seen.add(key)
            queue = self._pending_tools.get(key, [])
            if not queue:
                continue

            for idx, (tool_name, step, started_at) in enumerate(queue):
                if tool_name == event.tool_name:
                    queue.pop(idx)
                    return step, started_at

            _, step, started_at = queue.pop(0)
            return step, started_at

        return None

    def _task_key(self, task_id: str) -> str:
        if task_id:
            return task_id
        if self._active_task_id:
            return self._active_task_id
        return "__global__"

    def _show_error(self, title: str, message: str, error_type: str) -> None:
        try:
            popup = self.app.query_one("#error_popup", ErrorPopup)
            popup.show_error(title=title, message=message, error_type=error_type)
        except NoMatches:
            pass

    def _scroll_to_end(self) -> None:
        self._messages.scroll_end(animate=False)

    def add_user_message(self, text: str) -> None:
        """Mount a user message bubble directly (called by footer before emitting event).

        This ensures the user bubble appears in the DOM before the
        Orchestrator starts the agent and agent responses begin mounting.

        Also resets ``_current_arc`` so the next agent response creates
        a fresh container below this user message — not inside an old
        container left over from a slash-command response.
        """
        # Finish any lingering thinking block from a prior turn
        if self._current_arc is not None:
            active_thinking = self._current_arc.get_active_thinking()
            if active_thinking is not None:
                active_thinking.finish_streaming()

        self._current_arc = None
        self._active_task_id = ""

        self._messages.mount(UserMessageContainer(text))
        self.call_after_refresh(self._scroll_to_end)

    @property
    def _messages(self) -> VerticalScroll:
        return self.query_one("#messages", VerticalScroll)
