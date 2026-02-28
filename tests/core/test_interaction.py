"""Tests for HITL interaction models and interface (Phase 4.3.1)."""

from __future__ import annotations

import pytest

from agent_cli.core.interaction import (
    BaseInteractionHandler,
    InteractionType,
    UserInteractionRequest,
    UserInteractionResponse,
)


def test_interaction_type_enum_members():
    assert InteractionType.APPROVAL.name == "APPROVAL"
    assert InteractionType.CLARIFICATION.name == "CLARIFICATION"
    assert InteractionType.PLAN_APPROVAL.name == "PLAN_APPROVAL"
    assert InteractionType.FATAL_ERROR.name == "FATAL_ERROR"


def test_user_interaction_request_defaults_and_fields():
    req = UserInteractionRequest(
        interaction_type=InteractionType.APPROVAL,
        message="Approve command?",
    )

    assert req.task_id == ""
    assert req.source == ""
    assert req.tool_name is None
    assert req.tool_args is None
    assert req.plan_assignments is None
    assert req.error_details is None
    assert req.options == []


def test_user_interaction_request_options_is_not_shared():
    req1 = UserInteractionRequest(
        interaction_type=InteractionType.APPROVAL,
        message="First request",
    )
    req2 = UserInteractionRequest(
        interaction_type=InteractionType.APPROVAL,
        message="Second request",
    )

    req1.options.append("approve")
    assert req1.options == ["approve"]
    assert req2.options == []


def test_user_interaction_response_defaults_and_fields():
    res = UserInteractionResponse()
    assert res.action == ""
    assert res.feedback == ""
    assert res.edited_args is None

    res2 = UserInteractionResponse(
        action="approve",
        feedback="Looks safe",
        edited_args={"command": "node --version"},
    )
    assert res2.action == "approve"
    assert res2.feedback == "Looks safe"
    assert res2.edited_args == {"command": "node --version"}


@pytest.mark.asyncio
async def test_base_interaction_handler_contract():
    class _MockInteractionHandler(BaseInteractionHandler):
        async def request_human_input(
            self, request: UserInteractionRequest
        ) -> UserInteractionResponse:
            return UserInteractionResponse(
                action="approve",
                feedback=f"Handled: {request.message}",
            )

        async def notify(self, message: str) -> None:
            self.last_message = message

    handler = _MockInteractionHandler()
    response = await handler.request_human_input(
        UserInteractionRequest(
            interaction_type=InteractionType.APPROVAL,
            message="Run this command?",
        )
    )
    await handler.notify("done")

    assert response.action == "approve"
    assert "Run this command?" in response.feedback
    assert handler.last_message == "done"
