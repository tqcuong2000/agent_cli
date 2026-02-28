"""Tests for the Orchestrator."""

import asyncio
from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel

from agent_cli.agent.base import AgentConfig, BaseAgent, EffortLevel
from agent_cli.agent.memory import WorkingMemoryManager
from agent_cli.agent.react_loop import PromptBuilder
from agent_cli.agent.schema import SchemaValidator
from agent_cli.core.events.event_bus import AsyncEventBus
from agent_cli.core.events.events import (
    TaskDelegatedEvent,
    TaskResultEvent,
    UserRequestEvent,
)
from agent_cli.core.orchestrator import Orchestrator
from agent_cli.core.state.state_manager import TaskStateManager
from agent_cli.core.state.state_models import TaskState
from agent_cli.providers.base import BaseLLMProvider
from agent_cli.providers.models import LLMRequest, LLMResponse, ToolCallMode
from agent_cli.tools.base import BaseTool, ToolCategory
from agent_cli.tools.executor import ToolExecutor
from agent_cli.tools.output_formatter import ToolOutputFormatter
from agent_cli.tools.registry import ToolRegistry

# ── Mocks ────────────────────────────────────────────────────────────


class MockToolArgs(BaseModel):
    query: str


class MockTool(BaseTool):
    name = "mock_tool"
    description = "A mock tool."
    category = ToolCategory.UTILITY

    @property
    def args_schema(self) -> type[BaseModel]:
        return MockToolArgs

    async def execute(self, query: str, **kwargs: Any) -> str:
        return f"Mock result for: {query}"


class MockProvider(BaseLLMProvider):
    """Mock provider that always returns a final answer."""

    def __init__(self, answer: str = "Done."):
        super().__init__("mock_model")
        self._answer = answer

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def supports_native_tools(self) -> bool:
        return False

    def _create_tool_formatter(self):
        return None

    async def generate(self, request: LLMRequest) -> LLMResponse:
        text = (
            f"<title>Process incoming user request safely now</title>\n"
            f"<thinking>Processing.</thinking>\n"
            f"<final_answer>{self._answer}</final_answer>"
        )
        return LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    def get_buffered_response(self) -> LLMResponse:
        return LLMResponse()

    async def safe_generate(self, context, tools=None, **kwargs) -> LLMResponse:
        return await self.generate(LLMRequest(messages=[]))

    async def stream(self, request: LLMRequest):
        yield None

    async def check_health(self) -> bool:
        return True


class MockAgent(BaseAgent):
    """Simple agent that delegates to the LLM and returns."""

    async def build_system_prompt(self, task_context: str) -> str:
        return "You are a mock agent."

    async def on_tool_result(self, tool_name: str, result: str) -> None:
        pass

    async def on_final_answer(self, answer: str) -> str:
        return answer


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def deps():
    """Build a full set of deps for the Orchestrator."""
    event_bus = AsyncEventBus()
    state_manager = TaskStateManager(event_bus)

    registry = ToolRegistry()
    registry.register(MockTool())

    output_formatter = ToolOutputFormatter()
    tool_executor = ToolExecutor(
        registry=registry,
        event_bus=event_bus,
        output_formatter=output_formatter,
        auto_approve=True,
    )

    schema_validator = SchemaValidator(registry.get_all_names())
    memory_manager = WorkingMemoryManager()
    prompt_builder = PromptBuilder(registry)

    provider = MockProvider(answer="42")

    config = AgentConfig(
        name="test_agent",
        tools=["mock_tool"],
        effort_level=EffortLevel.LOW,
    )
    agent = MockAgent(
        config=config,
        provider=provider,
        tool_executor=tool_executor,
        schema_validator=schema_validator,
        memory_manager=memory_manager,
        event_bus=event_bus,
        state_manager=state_manager,
        prompt_builder=prompt_builder,
    )

    orchestrator = Orchestrator(
        event_bus=event_bus,
        state_manager=state_manager,
        default_agent=agent,
    )

    return {
        "orchestrator": orchestrator,
        "event_bus": event_bus,
        "state_manager": state_manager,
        "agent": agent,
    }


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_routes_to_agent(deps):
    """Normal request flows through the agent and returns."""
    result = await deps["orchestrator"].handle_request("What is 6 * 7?")

    assert result == "42"


@pytest.mark.asyncio
async def test_orchestrator_task_lifecycle(deps):
    """Verify the full PENDING → ROUTING → WORKING → SUCCESS flow."""
    events = []

    async def on_result(event):
        events.append(event)

    deps["event_bus"].subscribe("TaskResultEvent", on_result)

    result = await deps["orchestrator"].handle_request("Test lifecycle")

    assert result == "42"

    # Wait for fire-and-forget events
    await asyncio.sleep(0.05)

    assert len(events) == 1
    assert events[0].is_success
    assert events[0].result == "42"


@pytest.mark.asyncio
async def test_orchestrator_emits_delegation_event(deps):
    """Verify TaskDelegatedEvent is emitted on routing."""
    delegated = []

    async def on_delegated(event):
        delegated.append(event)

    deps["event_bus"].subscribe("TaskDelegatedEvent", on_delegated)

    await deps["orchestrator"].handle_request("Delegated request")

    await asyncio.sleep(0.05)

    assert len(delegated) == 1
    assert delegated[0].agent_name == "test_agent"


@pytest.mark.asyncio
async def test_orchestrator_command_interception(deps):
    """Slash-commands are intercepted and not routed to agents."""

    # Register a test command
    async def mock_help(text: str) -> str:
        return "Available commands: /help, /exit"

    deps["orchestrator"].register_command("help", mock_help)

    result = await deps["orchestrator"].handle_request("/help")

    assert result == "Available commands: /help, /exit"


@pytest.mark.asyncio
async def test_orchestrator_unknown_command(deps):
    """Unknown commands return an error message."""
    result = await deps["orchestrator"].handle_request("/foobar")

    assert "Unknown command" in result
    assert "/foobar" in result


@pytest.mark.asyncio
async def test_orchestrator_event_bus_integration(deps):
    """Test that publishing a UserRequestEvent triggers processing."""
    results = []

    async def on_result(event):
        results.append(event)

    deps["event_bus"].subscribe("TaskResultEvent", on_result)

    await deps["event_bus"].publish(
        UserRequestEvent(source="tui", text="Event bus test")
    )

    # Wait for processing
    await asyncio.sleep(0.1)

    assert len(results) == 1
    assert results[0].is_success
