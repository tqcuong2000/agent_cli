"""
AskUser Tool — request clarification from the user via HITL flow.

This tool lets the agent explicitly pause and ask one clarifying
question with 2-5 likely answer options.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Type

from pydantic import BaseModel, Field, field_validator

from agent_cli.core.error_handler.errors import ToolExecutionError
from agent_cli.core.interaction import (
    InteractionType,
    UserInteractionRequest,
)
from agent_cli.tools.base import BaseTool, ToolCategory

if TYPE_CHECKING:
    from agent_cli.core.interaction import BaseInteractionHandler


class AskUserArgs(BaseModel):
    """Arguments for the ``ask_user`` tool."""

    question: str = Field(
        description="The clarification question to ask the user.",
    )
    options: list[str] = Field(
        min_length=2,
        max_length=5,
        description="2-5 likely answers the user can choose from.",
    )

    @field_validator("question")
    @classmethod
    def _validate_question(cls, value: str) -> str:
        question = value.strip()
        if not question:
            raise ValueError("question must not be empty.")
        return question

    @field_validator("options")
    @classmethod
    def _validate_options(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if len(cleaned) < 2 or len(cleaned) > 5:
            raise ValueError("options must contain 2-5 non-empty answers.")
        return cleaned


class AskUserTool(BaseTool):
    """Ask the user a clarification question and return their answer."""

    name = "ask_user"
    description = (
        "Ask the user one clarification question with 2-5 likely answers. "
        "Use this when required details are missing before continuing."
        "Use this when needed to ask the user questions"
    )
    is_safe = True
    category = ToolCategory.UTILITY

    @property
    def args_schema(self) -> Type[BaseModel]:
        return AskUserArgs

    async def execute(
        self,
        question: str,
        options: list[str],
        _interaction_handler: "BaseInteractionHandler" | None = None,
        _task_id: str = "",
        **kwargs: Any,
    ) -> str:
        if _interaction_handler is None:
            raise ToolExecutionError(
                "ask_user requires an interaction handler but none is configured.",
                tool_name=self.name,
            )

        response = await _interaction_handler.request_human_input(
            UserInteractionRequest(
                interaction_type=InteractionType.CLARIFICATION,
                message=question,
                task_id=_task_id,
                source="ask_user_tool",
                options=options,
            )
        )

        answer = (response.feedback or "").strip()
        if response.action != "answered" or not answer:
            reason = response.feedback or "No answer received from user."
            raise ToolExecutionError(reason, tool_name=self.name)

        return f"User replied: {answer}"
