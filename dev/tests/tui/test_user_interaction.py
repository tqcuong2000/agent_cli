from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from agent_cli.core.infra.events.event_bus import AsyncEventBus
from agent_cli.core.infra.events.events import (
    AgentQuestionRequestEvent,
    AgentQuestionResponseEvent,
    UserApprovalRequestEvent,
    UserApprovalResponseEvent,
)
from agent_cli.core.ux.tui.views.layout.footer import FooterContainer
from agent_cli.core.ux.tui.views.main.input.user_input import UserInputComponent
from agent_cli.core.ux.tui.views.main.input.user_interaction import UserInteraction


class _FooterHostApp(App):
    def __init__(self, bus: AsyncEventBus, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app_context = SimpleNamespace(event_bus=bus, command_parser=None)

    def compose(self) -> ComposeResult:
        yield FooterContainer()


@pytest.mark.asyncio
async def test_user_interaction_hidden_by_default():
    bus = AsyncEventBus()
    app = _FooterHostApp(bus)

    async with app.run_test() as pilot:
        panel = app.query_one(UserInteraction)
        await pilot.pause()
        assert panel.has_class("-hidden")


@pytest.mark.asyncio
async def test_user_interaction_shows_on_approval_request():
    bus = AsyncEventBus()
    app = _FooterHostApp(bus)

    async with app.run_test() as pilot:
        panel = app.query_one(UserInteraction)
        await pilot.pause()

        await bus.publish(
            UserApprovalRequestEvent(
                source="tool_executor",
                task_id="task-approval-1",
                tool_name="run_command",
                arguments={"command": "node -v"},
                risk_description="This command requires approval.",
            )
        )
        await pilot.pause()

        assert not panel.has_class("-hidden")
        content = str(panel.query_one("#ui_approval_message", Static).content)
        title = str(panel.query_one("#ui_approval_title", Static).content)
        assert "Approval Required" in title
        assert "run_command(" in content
        assert "node -v" in content


@pytest.mark.asyncio
async def test_footer_emits_approval_response_on_action():
    bus = AsyncEventBus()
    responses: list[UserApprovalResponseEvent] = []

    async def _capture_response(event):
        responses.append(event)

    bus.subscribe("UserApprovalResponseEvent", _capture_response)
    app = _FooterHostApp(bus)

    async with app.run_test() as pilot:
        footer = app.query_one(FooterContainer)
        panel = app.query_one(UserInteraction)
        await pilot.pause()

        await bus.publish(
            UserApprovalRequestEvent(
                source="tool_executor",
                task_id="task-approval-2",
                tool_name="run_command",
                arguments={"command": "node -v"},
                risk_description="Needs user confirmation.",
            )
        )
        await pilot.pause()

        await footer.on_user_interaction_action_selected(
            UserInteraction.ActionSelected(
                panel,
                task_id="task-approval-2",
                action="approve",
            )
        )
        await asyncio.sleep(0.05)
        await pilot.pause()

        assert panel.has_class("-hidden")
        assert len(responses) == 1
        assert responses[0].task_id == "task-approval-2"
        assert responses[0].approved is True


@pytest.mark.asyncio
async def test_user_interaction_shows_question_and_emits_option_answer():
    bus = AsyncEventBus()
    responses: list[AgentQuestionResponseEvent] = []

    async def _capture_response(event):
        responses.append(event)

    bus.subscribe("AgentQuestionResponseEvent", _capture_response)
    app = _FooterHostApp(bus)

    async with app.run_test() as pilot:
        footer = app.query_one(FooterContainer)
        panel = app.query_one(UserInteraction)
        await pilot.pause()

        await bus.publish(
            AgentQuestionRequestEvent(
                source="interaction_handler",
                task_id="task-q-1",
                question="Which environment should I target?",
                options=["Development", "Production"],
            )
        )
        await pilot.pause()

        assert panel.question_active is True
        qtext = str(panel.query_one("#ui_question_text", Static).content)
        assert "Which environment" in qtext

        await footer.on_user_interaction_question_answered(
            UserInteraction.QuestionAnswered(
                panel,
                task_id="task-q-1",
                answer="Development",
            )
        )
        await asyncio.sleep(0.05)
        await pilot.pause()

        assert len(responses) == 1
        assert responses[0].task_id == "task-q-1"
        assert responses[0].answer == "Development"


@pytest.mark.asyncio
async def test_user_interaction_renders_up_to_five_question_options():
    bus = AsyncEventBus()
    app = _FooterHostApp(bus)

    async with app.run_test() as pilot:
        panel = app.query_one(UserInteraction)
        await pilot.pause()

        await bus.publish(
            AgentQuestionRequestEvent(
                source="interaction_handler",
                task_id="task-q-5-options",
                question="Pick one option",
                options=["One", "Two", "Three", "Four", "Five"],
            )
        )
        await pilot.pause()

        option_nodes = list(panel.query(".ui_question_option"))
        assert len(option_nodes) == 5


@pytest.mark.asyncio
async def test_footer_typed_answer_is_routed_to_agent_question_response():
    bus = AsyncEventBus()
    responses: list[AgentQuestionResponseEvent] = []

    async def _capture_response(event):
        responses.append(event)

    bus.subscribe("AgentQuestionResponseEvent", _capture_response)
    app = _FooterHostApp(bus)

    async with app.run_test() as pilot:
        footer = app.query_one(FooterContainer)
        await pilot.pause()

        await bus.publish(
            AgentQuestionRequestEvent(
                source="interaction_handler",
                task_id="task-q-2",
                question="Which OS are you using?",
                options=["Windows", "Linux", "macOS"],
            )
        )
        await pilot.pause()

        await footer.on_user_input_component_submitted(
            UserInputComponent.Submitted(
                footer.input_comp,
                "Windows 11",
            )
        )
        await asyncio.sleep(0.05)
        await pilot.pause()

        assert len(responses) == 1
        assert responses[0].task_id == "task-q-2"
        assert responses[0].answer == "Windows 11"
