"""
Textual-backed human interaction handler.

Bridges backend HITL requests to the existing TUI approval panel by
emitting/consuming approval events and suspending with ``asyncio.Event``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Dict, Optional

from agent_cli.core.events.events import (
    AgentMessageEvent,
    AgentQuestionRequestEvent,
    AgentQuestionResponseEvent,
    BaseEvent,
    UserApprovalRequestEvent,
    UserApprovalResponseEvent,
)
from agent_cli.core.interaction import (
    BaseInteractionHandler,
    InteractionType,
    UserInteractionRequest,
    UserInteractionResponse,
)
from agent_cli.core.state.state_models import TaskState

if TYPE_CHECKING:
    from agent_cli.ux.tui.app import AgentCLIApp

logger = logging.getLogger(__name__)


class TUIInteractionHandler(BaseInteractionHandler):
    """HITL adapter for the Textual app.

    Current implementation supports ``InteractionType.APPROVAL`` using
    the inline footer interaction panel.
    """

    def __init__(self, app: "AgentCLIApp") -> None:
        self.app = app
        self._pending_approval_events: Dict[str, asyncio.Event] = {}
        self._pending_approval_responses: Dict[str, UserInteractionResponse] = {}
        self._pending_question_events: Dict[str, asyncio.Event] = {}
        self._pending_question_responses: Dict[str, UserInteractionResponse] = {}
        self._pending_questions: Dict[str, str] = {}

        app_context = getattr(self.app, "app_context", None)
        event_bus = getattr(app_context, "event_bus", None)
        self._event_bus = event_bus
        self._state_manager = getattr(app_context, "state_manager", None)
        self._settings = getattr(app_context, "settings", None)

        self._subscription_id: Optional[str] = None
        self._question_subscription_id: Optional[str] = None
        if self._event_bus is not None:
            self._subscription_id = self._event_bus.subscribe(
                "UserApprovalResponseEvent",
                self._on_user_approval_response,
                priority=40,
            )
            self._question_subscription_id = self._event_bus.subscribe(
                "AgentQuestionResponseEvent",
                self._on_agent_question_response,
                priority=40,
            )

    async def request_human_input(
        self, request: UserInteractionRequest
    ) -> UserInteractionResponse:
        if request.interaction_type == InteractionType.APPROVAL:
            return await self._request_approval(request)

        if request.interaction_type == InteractionType.CLARIFICATION:
            return await self._request_clarification(request)

        await self.notify(
            f"Interaction type '{request.interaction_type.name}' "
            "is not implemented yet."
        )
        return UserInteractionResponse(
            action="deny",
            feedback="Interaction type not implemented.",
        )

    async def _request_approval(
        self, request: UserInteractionRequest
    ) -> UserInteractionResponse:
        if self._event_bus is None:
            return UserInteractionResponse(
                action="deny",
                feedback="Event bus unavailable for approval flow.",
            )

        interaction_task_id = request.task_id or f"approval-{uuid.uuid4()}"
        if interaction_task_id in self._pending_approval_events:
            return UserInteractionResponse(
                action="deny",
                feedback="Another approval is already pending for this task.",
            )

        wait_event = asyncio.Event()
        self._pending_approval_events[interaction_task_id] = wait_event

        # Expose paused state in TUI status bar.
        await self._transition_task(request.task_id, TaskState.AWAITING_INPUT)

        await self._event_bus.emit(
            UserApprovalRequestEvent(
                source=request.source or "interaction_handler",
                task_id=interaction_task_id,
                tool_name=request.tool_name or "",
                arguments=request.tool_args or {},
                risk_description=request.message,
            )
        )

        timeout = getattr(self._settings, "approval_timeout_seconds", 0) or 0
        try:
            if timeout > 0:
                await asyncio.wait_for(wait_event.wait(), timeout=timeout)
            else:
                await wait_event.wait()
        except asyncio.TimeoutError:
            logger.warning(
                "Approval timed out (task_id=%s, timeout=%ss)",
                interaction_task_id,
                timeout,
            )
            response = UserInteractionResponse(
                action="deny",
                feedback=f"Approval timed out after {timeout}s.",
            )
        finally:
            self._pending_approval_events.pop(interaction_task_id, None)
            await self._transition_task(request.task_id, TaskState.WORKING)

        if "response" in locals():
            return response

        return self._pending_approval_responses.pop(
            interaction_task_id,
            UserInteractionResponse(
                action="deny",
                feedback="Approval response was not received.",
            ),
        )

    async def _request_clarification(
        self, request: UserInteractionRequest
    ) -> UserInteractionResponse:
        if self._event_bus is None:
            return UserInteractionResponse(
                action="deny",
                feedback="Event bus unavailable for clarification flow.",
            )

        interaction_task_id = request.task_id or f"question-{uuid.uuid4()}"

        if self._pending_question_events:
            return UserInteractionResponse(
                action="deny",
                feedback="Only one AgentQuestion can be shown at a time.",
            )

        options = [o.strip() for o in request.options if o and o.strip()]
        if len(options) < 2 or len(options) > 5:
            return UserInteractionResponse(
                action="deny",
                feedback="AgentQuestion requires 2-5 suggested answers.",
            )

        wait_event = asyncio.Event()
        self._pending_question_events[interaction_task_id] = wait_event
        self._pending_questions[interaction_task_id] = request.message

        # Expose paused state in TUI status bar.
        await self._transition_task(request.task_id, TaskState.AWAITING_INPUT)

        await self._event_bus.emit(
            AgentQuestionRequestEvent(
                source=request.source or "interaction_handler",
                task_id=interaction_task_id,
                question=request.message,
                options=options,
            )
        )

        timeout = getattr(self._settings, "approval_timeout_seconds", 0) or 0
        try:
            if timeout > 0:
                await asyncio.wait_for(wait_event.wait(), timeout=timeout)
            else:
                await wait_event.wait()
        except asyncio.TimeoutError:
            logger.warning(
                "AgentQuestion timed out (task_id=%s, timeout=%ss)",
                interaction_task_id,
                timeout,
            )
            response = UserInteractionResponse(
                action="deny",
                feedback=f"AgentQuestion timed out after {timeout}s.",
            )
        finally:
            self._pending_question_events.pop(interaction_task_id, None)
            await self._transition_task(request.task_id, TaskState.WORKING)

        if "response" in locals():
            self._pending_questions.pop(interaction_task_id, None)
            return response

        response = self._pending_question_responses.pop(
            interaction_task_id,
            UserInteractionResponse(
                action="deny",
                feedback="AgentQuestion response was not received.",
            ),
        )
        self._pending_questions.pop(interaction_task_id, None)
        return response

    async def notify(self, message: str) -> None:
        if self._event_bus is None:
            return
        await self._event_bus.emit(
            AgentMessageEvent(
                source="interaction_handler",
                agent_name="system",
                content=message,
                is_monologue=False,
            )
        )

    async def shutdown(self) -> None:
        if self._event_bus is None or self._subscription_id is None:
            return
        self._event_bus.unsubscribe(self._subscription_id)
        self._subscription_id = None
        if self._question_subscription_id is not None:
            self._event_bus.unsubscribe(self._question_subscription_id)
            self._question_subscription_id = None

    async def _on_user_approval_response(self, event: BaseEvent) -> None:
        if not isinstance(event, UserApprovalResponseEvent):
            return
        task_id = event.task_id
        wait_event = self._pending_approval_events.get(task_id)
        if wait_event is None:
            return

        self._pending_approval_responses[task_id] = UserInteractionResponse(
            action="approve" if event.approved else "deny",
            feedback="Approved" if event.approved else "Denied by user.",
            edited_args=None,
        )
        wait_event.set()

    async def _on_agent_question_response(self, event: BaseEvent) -> None:
        if not isinstance(event, AgentQuestionResponseEvent):
            return
        task_id = event.task_id
        wait_event = self._pending_question_events.get(task_id)
        if wait_event is None:
            return

        self._pending_question_responses[task_id] = UserInteractionResponse(
            action="answered",
            feedback=event.answer,
            edited_args=None,
        )
        wait_event.set()

    async def _transition_task(
        self,
        task_id: str,
        to_state: TaskState,
    ) -> None:
        if not task_id or self._state_manager is None:
            return
        try:
            await self._state_manager.transition(task_id, to_state)
        except Exception:
            # Best-effort; task may already be in a different valid state.
            logger.debug(
                "Ignoring transition failure for task_id=%s to %s",
                task_id,
                to_state.name,
            )
