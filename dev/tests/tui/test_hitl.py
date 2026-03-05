from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, Field

from agent_cli.core.infra.events.event_bus import AsyncEventBus
from agent_cli.core.infra.events.events import (
    AgentMessageEvent,
    AgentQuestionRequestEvent,
    AgentQuestionResponseEvent,
    StateChangeEvent,
    UserApprovalRequestEvent,
    UserApprovalResponseEvent,
)
from agent_cli.core.ux.interaction.interaction import (
    BaseInteractionHandler,
    InteractionType,
    UserInteractionRequest,
    UserInteractionResponse,
)
from agent_cli.core.ux.interaction.tui_interaction_handler import TUIInteractionHandler
from agent_cli.core.runtime.tools.base import BaseTool, ToolCategory
from agent_cli.core.runtime.tools.executor import ToolExecutor
from agent_cli.core.runtime.tools.output_formatter import ToolOutputFormatter
from agent_cli.core.runtime.tools.registry import ToolRegistry


class _MockStateManager:
    def __init__(self) -> None:
        self.transitions: list[tuple[str, str]] = []

    async def transition(self, task_id, to_state, result=None, error=None):
        self.transitions.append((task_id, to_state.name))


class _MockSettings:
    approval_timeout_seconds = 0


class _MockApp:
    def __init__(self, bus: AsyncEventBus, state_manager: _MockStateManager) -> None:
        self.app_context = SimpleNamespace(
            event_bus=bus,
            state_manager=state_manager,
            settings=_MockSettings(),
        )


@pytest.mark.asyncio
async def test_tui_interaction_handler_approval_roundtrip():
    bus = AsyncEventBus()
    state_manager = _MockStateManager()
    app = _MockApp(bus, state_manager)
    handler = TUIInteractionHandler(app)  # type: ignore[arg-type]

    approval_requests: list[UserApprovalRequestEvent] = []

    async def capture_request(event):
        approval_requests.append(event)

    bus.subscribe("UserApprovalRequestEvent", capture_request)

    wait_task = asyncio.create_task(
        handler.request_human_input(
            UserInteractionRequest(
                interaction_type=InteractionType.APPROVAL,
                message="Tool needs approval.",
                task_id="task-hitl-1",
                source="tool_executor",
                tool_name="run_command",
                tool_args={"command": "node -v"},
            )
        )
    )

    await asyncio.sleep(0.05)
    assert len(approval_requests) == 1
    assert approval_requests[0].task_id == "task-hitl-1"
    assert approval_requests[0].tool_name == "run_command"

    await bus.publish(
        UserApprovalResponseEvent(
            source="tui",
            task_id="task-hitl-1",
            approved=True,
        )
    )

    response = await wait_task
    assert response.action == "approve"
    assert ("task-hitl-1", "AWAITING_INPUT") in state_manager.transitions
    assert ("task-hitl-1", "WORKING") in state_manager.transitions


class _DummyArgs(BaseModel):
    arg1: str = Field(description="first arg")


class _UnsafeTool(BaseTool):
    name = "unsafe_tool"
    description = "Unsafe tool for HITL test."
    is_safe = False
    category = ToolCategory.UTILITY

    @property
    def args_schema(self) -> type[BaseModel]:
        return _DummyArgs

    async def execute(self, arg1: str, **kwargs: Any) -> str:
        return f"unsafe:{arg1}"


class _DenyInteractionHandler(BaseInteractionHandler):
    def __init__(self) -> None:
        self.called = False
        self.last_request: UserInteractionRequest | None = None

    async def request_human_input(
        self, request: UserInteractionRequest
    ) -> UserInteractionResponse:
        self.called = True
        self.last_request = request
        return UserInteractionResponse(action="deny", feedback="No.")

    async def notify(self, message: str) -> None:
        return None


@pytest.mark.asyncio
async def test_tool_executor_uses_interaction_handler_for_approval():
    registry = ToolRegistry()
    registry.register(_UnsafeTool())

    bus = AsyncEventBus()
    formatter = ToolOutputFormatter(max_output_length=5000)
    interaction_handler = _DenyInteractionHandler()
    executor = ToolExecutor(
        registry,
        bus,
        formatter,
        auto_approve=False,
        interaction_handler=interaction_handler,
    )

    result = await executor.execute(
        "unsafe_tool",
        {"arg1": "x"},
        task_id="task-hitl-2",
    )

    assert interaction_handler.called is True
    assert interaction_handler.last_request is not None
    assert interaction_handler.last_request.interaction_type == InteractionType.APPROVAL
    assert interaction_handler.last_request.tool_name == "unsafe_tool"
    assert "User denied execution." in result


@pytest.mark.asyncio
async def test_tui_interaction_handler_clarification_roundtrip():
    bus = AsyncEventBus()
    state_manager = _MockStateManager()
    app = _MockApp(bus, state_manager)
    handler = TUIInteractionHandler(app)  # type: ignore[arg-type]

    question_events: list[AgentQuestionRequestEvent] = []
    message_events: list[AgentMessageEvent] = []

    async def capture_question(event):
        question_events.append(event)

    async def capture_message(event):
        message_events.append(event)

    bus.subscribe("AgentQuestionRequestEvent", capture_question)
    bus.subscribe("AgentMessageEvent", capture_message)

    wait_task = asyncio.create_task(
        handler.request_human_input(
            UserInteractionRequest(
                interaction_type=InteractionType.CLARIFICATION,
                message="Which profile should I use?",
                task_id="task-q-hitl-1",
                source="ask_user_tool",
                options=["Fast", "Balanced", "Thorough"],
            )
        )
    )

    await asyncio.sleep(0.05)
    assert len(question_events) == 1
    assert question_events[0].task_id == "task-q-hitl-1"
    assert question_events[0].options == ["Fast", "Balanced", "Thorough"]

    await bus.publish(
        AgentQuestionResponseEvent(
            source="tui",
            task_id="task-q-hitl-1",
            answer="Balanced",
        )
    )

    response = await wait_task
    assert response.action == "answered"
    assert response.feedback == "Balanced"
    assert ("task-q-hitl-1", "AWAITING_INPUT") in state_manager.transitions
    assert ("task-q-hitl-1", "WORKING") in state_manager.transitions
    assert message_events == []

    await bus.publish(
        StateChangeEvent(
            source="state_manager",
            task_id="task-q-hitl-1",
            from_state="WORKING",
            to_state="SUCCESS",
        )
    )
    await asyncio.sleep(0.05)

    assert message_events == []


@pytest.mark.asyncio
async def test_tui_interaction_handler_clarification_no_summary_for_multiple_questions():
    bus = AsyncEventBus()
    state_manager = _MockStateManager()
    app = _MockApp(bus, state_manager)
    handler = TUIInteractionHandler(app)  # type: ignore[arg-type]

    message_events: list[AgentMessageEvent] = []

    async def capture_message(event):
        message_events.append(event)

    bus.subscribe("AgentMessageEvent", capture_message)

    wait_task_1 = asyncio.create_task(
        handler.request_human_input(
            UserInteractionRequest(
                interaction_type=InteractionType.CLARIFICATION,
                message="Which profile should I use?",
                task_id="task-q-hitl-2",
                source="ask_user_tool",
                options=["Fast", "Balanced", "Thorough"],
            )
        )
    )
    await asyncio.sleep(0.05)
    await bus.publish(
        AgentQuestionResponseEvent(
            source="tui",
            task_id="task-q-hitl-2",
            answer="Balanced",
        )
    )
    await wait_task_1

    wait_task_2 = asyncio.create_task(
        handler.request_human_input(
            UserInteractionRequest(
                interaction_type=InteractionType.CLARIFICATION,
                message="Which environment should I target?",
                task_id="task-q-hitl-2",
                source="ask_user_tool",
                options=["Dev", "Prod"],
            )
        )
    )
    await asyncio.sleep(0.05)
    await bus.publish(
        AgentQuestionResponseEvent(
            source="tui",
            task_id="task-q-hitl-2",
            answer="Prod",
        )
    )
    await wait_task_2

    assert message_events == []

    await bus.publish(
        StateChangeEvent(
            source="state_manager",
            task_id="task-q-hitl-2",
            from_state="WORKING",
            to_state="SUCCESS",
        )
    )
    await asyncio.sleep(0.05)

    assert message_events == []


@pytest.mark.asyncio
async def test_tui_interaction_handler_clarification_has_no_hard_limit():
    bus = AsyncEventBus()
    state_manager = _MockStateManager()
    app = _MockApp(bus, state_manager)
    handler = TUIInteractionHandler(app)  # type: ignore[arg-type]

    async def ask_and_answer(index: int) -> UserInteractionResponse:
        task_id = "task-q-unlimited"
        wait_task = asyncio.create_task(
            handler.request_human_input(
                UserInteractionRequest(
                    interaction_type=InteractionType.CLARIFICATION,
                    message=f"Question {index}?",
                    task_id=task_id,
                    source="ask_user_tool",
                    options=["A", "B"],
                )
            )
        )
        await asyncio.sleep(0.02)
        await bus.publish(
            AgentQuestionResponseEvent(
                source="tui",
                task_id=task_id,
                answer="A",
            )
        )
        return await wait_task

    responses = []
    for i in range(1, 7):  # >5 to verify the previous limit is gone
        responses.append(await ask_and_answer(i))

    assert all(response.action == "answered" for response in responses)


@pytest.mark.asyncio
async def test_tui_interaction_handler_clarification_rejects_more_than_five_options():
    bus = AsyncEventBus()
    state_manager = _MockStateManager()
    app = _MockApp(bus, state_manager)
    handler = TUIInteractionHandler(app)  # type: ignore[arg-type]

    response = await handler.request_human_input(
        UserInteractionRequest(
            interaction_type=InteractionType.CLARIFICATION,
            message="Pick one option.",
            task_id="task-q-too-many-options",
            source="ask_user_tool",
            options=["A", "B", "C", "D", "E", "F"],
        )
    )

    assert response.action == "deny"
    assert "2-5 suggested answers" in response.feedback
