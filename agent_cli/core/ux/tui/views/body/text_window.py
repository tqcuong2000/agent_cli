from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import TYPE_CHECKING, DefaultDict, List, Optional, Tuple

from agent_cli.core.runtime.agents.schema import SchemaValidator

from textual import events
from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.css.query import NoMatches

from agent_cli.core.infra.events.events import (
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
    SessionLoadedEvent,
)
from agent_cli.core.ux.tui.views.body.messages.agent_response import AgentResponseContainer
from agent_cli.core.ux.tui.views.body.messages.changed_file_detail_block import DiffLine
from agent_cli.core.ux.tui.views.body.messages.system_message import SystemMessageContainer
from agent_cli.core.ux.tui.views.body.messages.tool_step import ToolStepWidget
from agent_cli.core.ux.tui.views.body.messages.user_message import UserMessageContainer
from agent_cli.core.ux.tui.views.common.error_popup import ErrorPopup

if TYPE_CHECKING:
    from agent_cli.core.infra.registry.bootstrap import AppContext


class TextWindowContainer(Container):
    """Event-driven container for chat message rendering."""

    DEFAULT_CSS = ""

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
        self._subscriptions.append(
            bus.subscribe(
                "SessionLoadedEvent",
                self._on_session_loaded,
                priority=50,
            )
        )
        self._show_empty_state()

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
        if self._is_system_message(event):
            self._add_system_message(event.content)
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

        is_user_cancel = (
            (event.result or "").strip().lower().startswith("task cancelled by user")
        )
        if not event.is_success and not is_user_cancel:
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

        from agent_cli.core.ux.tui.views.body.messages.changed_file_detail_block import (
            ChangedFileDetailBlock,
        )

        for detail in list(self.query(ChangedFileDetailBlock)):
            detail_path = getattr(detail, "file_path", "")
            if detail_path == target_path:
                detail.remove()

        self.call_after_refresh(self._scroll_to_end)

    async def _on_session_loaded(self, event: BaseEvent) -> None:
        if not isinstance(event, SessionLoadedEvent):
            return

        # Clear existing messages
        self._messages.remove_children()
        self._current_arc = None
        self._active_task_id = ""
        self._pending_tools.clear()

        if not event.messages:
            self._show_empty_state()
            return

        # Rebuild messages from history
        for msg in event.messages:
            role = str(msg.get("role", "")).lower()
            content = str(msg.get("content", ""))
            
            if role == "user":
                self._messages.mount(UserMessageContainer(content))
                self._current_arc = None
            elif role == "assistant":
                self._current_arc = AgentResponseContainer()
                self._messages.mount(self._current_arc)
                
                # Try JSON protocol parsing
                if self._app_context is not None:
                    validator = SchemaValidator(
                        registered_tools=[],
                        data_registry=self._app_context.data_registry,
                    )
                    parsed = validator._extract_json_object(content)
                else:
                    parsed = None

                if isinstance(parsed, dict) and "decision" in parsed:
                    title = str(parsed.get("title", "")).strip()
                    thought = str(parsed.get("thought", "")).strip()
                    if thought or title:
                        tb = self._current_arc.append_thinking()
                        tb.append_chunk(json.dumps({"title": title, "thought": thought}))
                        tb.finish_streaming()
                        
                    decision = parsed.get("decision", {})
                    dtype = str(decision.get("type", ""))
                    if dtype == "execute_action":
                        tool = str(decision.get("tool", ""))
                        args = decision.get("args", {})
                        if isinstance(args, dict):
                            self._current_arc.append_tool_step(tool, args).mark_success(0)
                        self._current_arc = None
                        continue
                    elif dtype in ("notify_user", "yield"):
                        ans = decision.get("message", parsed.get("final_answer", ""))
                        if ans:
                            self._current_arc.set_answer(str(ans).strip())
                        self._current_arc = None
                        continue
                        
                # Native Tool Fallback
                lines = content.splitlines()
                text_lines = []
                found_tools = False
                for line in lines:
                    try:
                        p = json.loads(line)
                        if isinstance(p, dict) and p.get("type") == "tool_call" and "payload" in p:
                            if text_lines:
                                tb = self._current_arc.append_thinking()
                                tb.append_chunk("\n".join(text_lines).strip())
                                tb.finish_streaming()
                                text_lines = []
                            payload = p.get("payload", {})
                            tool = str(payload.get("tool", ""))
                            args = payload.get("args", {})
                            if isinstance(args, dict):
                                self._current_arc.append_tool_step(tool, args).mark_success(0)
                            found_tools = True
                            continue
                    except Exception:
                        pass
                    text_lines.append(line)
                    
                if not found_tools:
                    self._current_arc.set_answer(content)
                elif text_lines:
                    ans = "\n".join(text_lines).strip()
                    if ans:
                        self._current_arc.set_answer(ans)
                    
                self._current_arc = None
            elif role == "system":
                if "Schema Error:" in content or "Tool Error:" in content:
                    continue
                self._messages.mount(SystemMessageContainer(content))
                self._current_arc = None
                
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

    def on_resize(self, event: events.Resize) -> None:
        _ = event  # resize details currently not needed
        self.call_after_refresh(self._scroll_to_end)

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

    def _is_system_message(self, event: AgentMessageEvent) -> bool:
        return (not event.is_monologue) and event.source == "command_system"

    def _add_system_message(self, content: str) -> None:
        if not content:
            return

        # Ensure command/system output appears as a standalone message,
        # not inside an in-progress agent arc.
        if self._current_arc is not None:
            active_thinking = self._current_arc.get_active_thinking()
            if active_thinking is not None:
                active_thinking.finish_streaming()
            self._current_arc = None
            self._active_task_id = ""

        self._messages.mount(SystemMessageContainer(content))
        self.call_after_refresh(self._scroll_to_end)

    def _show_empty_state(self) -> None:
        if list(self._messages.children):
            return
        welcome = (
            "Welcome to Agent CLI.\n"
            "Try: ask a task, use !coder / !researcher, or /agent list.\n"
            "Useful commands: /help, /model <name>, /config, /sessions."
        )
        self._messages.mount(SystemMessageContainer(welcome))
        self.call_after_refresh(self._scroll_to_end)

    @property
    def _messages(self) -> VerticalScroll:
        return self.query_one("#messages", VerticalScroll)
