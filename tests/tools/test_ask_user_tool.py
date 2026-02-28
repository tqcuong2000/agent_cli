from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_cli.core.error_handler.errors import ToolExecutionError
from agent_cli.core.interaction import (
    BaseInteractionHandler,
    InteractionType,
    UserInteractionRequest,
    UserInteractionResponse,
)
from agent_cli.tools.ask_user_tool import AskUserTool


class _MockInteractionHandler(BaseInteractionHandler):
    def __init__(self, response: UserInteractionResponse) -> None:
        self._response = response
        self.last_request: UserInteractionRequest | None = None

    async def request_human_input(
        self, request: UserInteractionRequest
    ) -> UserInteractionResponse:
        self.last_request = request
        return self._response

    async def notify(self, message: str) -> None:
        return None


def test_ask_user_args_validation():
    tool = AskUserTool()

    validated = tool.validate_args(
        question=" Which profile should I use? ",
        options=[" Fast ", " Balanced "],
    )
    assert validated.question == "Which profile should I use?"
    assert validated.options == ["Fast", "Balanced"]

    with pytest.raises(ValidationError):
        tool.validate_args(question="?", options=["only-one-option"])

    with pytest.raises(ValidationError):
        tool.validate_args(question="   ", options=["A", "B"])

    validated_five = tool.validate_args(
        question="Pick one",
        options=["A", "B", "C", "D", "E"],
    )
    assert validated_five.options == ["A", "B", "C", "D", "E"]

    with pytest.raises(ValidationError):
        tool.validate_args(
            question="Too many",
            options=["A", "B", "C", "D", "E", "F"],
        )


@pytest.mark.asyncio
async def test_ask_user_tool_roundtrip_success():
    tool = AskUserTool()
    handler = _MockInteractionHandler(
        UserInteractionResponse(action="answered", feedback="Balanced"),
    )

    result = await tool.execute(
        question="Which profile should I use?",
        options=["Fast", "Balanced", "Thorough"],
        _interaction_handler=handler,
        _task_id="task-ask-user-1",
    )

    assert result == "User replied: Balanced"
    assert handler.last_request is not None
    assert handler.last_request.interaction_type == InteractionType.CLARIFICATION
    assert handler.last_request.task_id == "task-ask-user-1"
    assert handler.last_request.options == ["Fast", "Balanced", "Thorough"]


@pytest.mark.asyncio
async def test_ask_user_tool_requires_interaction_handler():
    tool = AskUserTool()

    with pytest.raises(ToolExecutionError, match="requires an interaction handler"):
        await tool.execute(
            question="Which profile should I use?",
            options=["Fast", "Balanced"],
        )


@pytest.mark.asyncio
async def test_ask_user_tool_denied_or_empty_answer_raises():
    tool = AskUserTool()
    handler = _MockInteractionHandler(
        UserInteractionResponse(action="deny", feedback="User cancelled."),
    )

    with pytest.raises(ToolExecutionError, match="User cancelled."):
        await tool.execute(
            question="Which profile should I use?",
            options=["Fast", "Balanced"],
            _interaction_handler=handler,
            _task_id="task-ask-user-2",
        )
