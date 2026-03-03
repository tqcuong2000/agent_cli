import asyncio
from typing import Any

import pytest
from pydantic import BaseModel, Field

from agent_cli.core.error_handler.errors import ToolExecutionError
from agent_cli.core.events.event_bus import AsyncEventBus
from agent_cli.core.events.events import (
    ToolExecutionResultEvent,
    ToolExecutionStartEvent,
    UserApprovalRequestEvent,
    UserApprovalResponseEvent,
)
from agent_cli.core.interaction import (
    BaseInteractionHandler,
    InteractionType,
    UserInteractionRequest,
    UserInteractionResponse,
)
from agent_cli.tools.ask_user_tool import AskUserTool
from agent_cli.tools.base import BaseTool, ToolCategory
from agent_cli.tools.executor import ToolExecutor
from agent_cli.tools.output_formatter import ToolOutputFormatter
from agent_cli.tools.registry import ToolRegistry


class DummyArgs(BaseModel):
    arg1: str = Field(description="First argument")


class SafeDummyTool(BaseTool):
    name = "safe_tool"
    description = "A safe dummy tool."
    category = ToolCategory.UTILITY
    is_safe = True

    @property
    def args_schema(self) -> type[BaseModel]:
        return DummyArgs

    async def execute(self, arg1: str, **kwargs: Any) -> str:
        if arg1 == "fail":
            raise ToolExecutionError("Expected failure")
        elif arg1 == "crash":
            raise ValueError("Unexpected exception")
        return f"Safe {arg1}"


class UnsafeDummyTool(BaseTool):
    name = "unsafe_tool"
    description = "An unsafe dummy tool."
    category = ToolCategory.UTILITY
    is_safe = False

    @property
    def args_schema(self) -> type[BaseModel]:
        return DummyArgs

    async def execute(self, arg1: str, **kwargs: Any) -> str:
        return f"Unsafe {arg1}"


class _AnswerInteractionHandler(BaseInteractionHandler):
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.last_request: UserInteractionRequest | None = None

    async def request_human_input(
        self, request: UserInteractionRequest
    ) -> UserInteractionResponse:
        self.last_request = request
        return UserInteractionResponse(action="answered", feedback=self.answer)

    async def notify(self, message: str) -> None:
        return None


@pytest.fixture
def registry():
    reg = ToolRegistry()
    reg.register(SafeDummyTool())
    reg.register(UnsafeDummyTool())
    return reg


@pytest.fixture
def event_bus():
    return AsyncEventBus()


@pytest.fixture
def output_formatter():
    return ToolOutputFormatter(max_output_length=5000)


@pytest.mark.asyncio
async def test_executor_safe_tool_success(registry, event_bus, output_formatter):
    executor = ToolExecutor(registry, event_bus, output_formatter)

    events = []

    async def on_event(event):
        events.append(event)

    event_bus.subscribe("ToolExecutionStartEvent", on_event)
    event_bus.subscribe("ToolExecutionResultEvent", on_event)

    result = await executor.execute("safe_tool", {"arg1": "test"})

    assert "Safe test" in result
    assert "<tool_result>" in result
    assert "<status>success</status>" in result
    assert "<status>error</status>" not in result

    await asyncio.sleep(0.05)
    assert len(events) == 2
    start_event = events[0]
    assert isinstance(start_event, ToolExecutionStartEvent)
    assert start_event.tool_name == "safe_tool"

    result_event = events[1]
    assert isinstance(result_event, ToolExecutionResultEvent)
    assert not result_event.is_error
    assert result_event.output == result


@pytest.mark.asyncio
async def test_executor_validation_failure(registry, event_bus, output_formatter):
    executor = ToolExecutor(registry, event_bus, output_formatter)

    # Missing arg1
    result = await executor.execute("safe_tool", {})
    assert "<status>error</status>" in result
    assert "Invalid arguments" in result


@pytest.mark.asyncio
async def test_executor_unknown_tool(registry, event_bus, output_formatter):
    executor = ToolExecutor(registry, event_bus, output_formatter)

    result = await executor.execute("unknown", {"arg1": "test"})
    assert "<status>error</status>" in result
    assert "Unknown tool" in result


@pytest.mark.asyncio
async def test_executor_tool_execution_error(registry, event_bus, output_formatter):
    executor = ToolExecutor(registry, event_bus, output_formatter)

    events = []

    async def on_event(event):
        events.append(event)

    event_bus.subscribe("ToolExecutionResultEvent", on_event)

    result = await executor.execute("safe_tool", {"arg1": "fail"})

    assert "<status>error</status>" in result
    assert "Expected failure" in result

    await asyncio.sleep(0.05)
    assert len(events) == 1
    assert events[0].is_error
    assert events[0].output == result


@pytest.mark.asyncio
async def test_executor_unexpected_exception(registry, event_bus, output_formatter):
    executor = ToolExecutor(registry, event_bus, output_formatter)

    events = []

    async def on_event(event):
        events.append(event)

    event_bus.subscribe("ToolExecutionResultEvent", on_event)

    result = await executor.execute("safe_tool", {"arg1": "crash"})

    assert "Error" in result
    assert "Unexpected exception" in result

    await asyncio.sleep(0.05)
    assert len(events) == 1
    assert events[0].is_error
    assert events[0].output == result


@pytest.mark.asyncio
async def test_executor_unsafe_tool_auto_approve(registry, event_bus, output_formatter):
    executor = ToolExecutor(registry, event_bus, output_formatter, auto_approve=True)

    result = await executor.execute("unsafe_tool", {"arg1": "test"})
    assert "Unsafe test" in result


@pytest.mark.asyncio
async def test_executor_unsafe_tool_requires_approval(
    registry, event_bus, output_formatter
):
    executor = ToolExecutor(registry, event_bus, output_formatter, auto_approve=False)

    approval_events = []

    async def on_event(event):
        approval_events.append(event)

    event_bus.subscribe("UserApprovalRequestEvent", on_event)

    # We need a background task to simulate the TUI approving it
    async def approve_delayed():
        await asyncio.sleep(0.1)
        assert len(approval_events) == 1
        req = approval_events[0]
        await event_bus.publish(
            UserApprovalResponseEvent(
                source="tui",
                task_id=req.task_id,
                approved=True,
            )
        )

    task = asyncio.create_task(approve_delayed())

    result = await executor.execute("unsafe_tool", {"arg1": "test"}, task_id="task_1")

    await task

    assert "Unsafe test" in result


@pytest.mark.asyncio
async def test_executor_unsafe_tool_denied(registry, event_bus, output_formatter):
    executor = ToolExecutor(registry, event_bus, output_formatter, auto_approve=False)

    # We need a background task to simulate the TUI denying it
    async def deny_delayed():
        await asyncio.sleep(0.1)
        await event_bus.publish(
            UserApprovalResponseEvent(
                source="tui",
                task_id="task_2",
                approved=False,
            )
        )

    task = asyncio.create_task(deny_delayed())

    result = await executor.execute("unsafe_tool", {"arg1": "test"}, task_id="task_2")

    await task

    assert "User denied execution." in result
    assert "unsafe_tool" in result


@pytest.mark.asyncio
async def test_executor_routes_ask_user_to_interaction_handler(
    event_bus, output_formatter
):
    registry = ToolRegistry()
    registry.register(AskUserTool())
    interaction_handler = _AnswerInteractionHandler(answer="Balanced")

    executor = ToolExecutor(
        registry,
        event_bus,
        output_formatter,
        interaction_handler=interaction_handler,
    )

    result = await executor.execute(
        "ask_user",
        {
            "question": "Which profile should I use?",
            "options": ["Fast", "Balanced"],
        },
        task_id="task-ask-executor-1",
    )

    assert "<tool>ask_user</tool>" in result
    assert "<status>success</status>" in result
    assert "User replied: Balanced" in result
    assert interaction_handler.last_request is not None
    assert (
        interaction_handler.last_request.interaction_type
        == InteractionType.CLARIFICATION
    )
    assert interaction_handler.last_request.task_id == "task-ask-executor-1"
