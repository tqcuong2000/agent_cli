"""Tests for the Orchestrator."""

import asyncio
from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel

from agent_cli.agent.base import AgentConfig, BaseAgent, EffortLevel
from agent_cli.agent.memory import WorkingMemoryManager
from agent_cli.agent.react_loop import PromptBuilder
from agent_cli.agent.registry import AgentRegistry
from agent_cli.agent.schema import SchemaValidator
from agent_cli.agent.session_registry import SessionAgentRegistry
from agent_cli.core.events.event_bus import AsyncEventBus
from agent_cli.core.events.events import (
    AgentMessageEvent,
    StateChangeEvent,
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


@pytest.fixture
def multi_agent_deps():
    event_bus = AsyncEventBus()
    state_manager = TaskStateManager(event_bus)

    registry = ToolRegistry()
    output_formatter = ToolOutputFormatter()
    tool_executor = ToolExecutor(
        registry=registry,
        event_bus=event_bus,
        output_formatter=output_formatter,
        auto_approve=True,
    )
    schema_validator = SchemaValidator(registry.get_all_names())
    prompt_builder = PromptBuilder(registry)

    coder = MockAgent(
        config=AgentConfig(name="coder", effort_level=EffortLevel.LOW),
        provider=MockProvider(answer="coder-result"),
        tool_executor=tool_executor,
        schema_validator=schema_validator,
        memory_manager=WorkingMemoryManager(),
        event_bus=event_bus,
        state_manager=state_manager,
        prompt_builder=prompt_builder,
    )
    researcher = MockAgent(
        config=AgentConfig(name="researcher", effort_level=EffortLevel.LOW),
        provider=MockProvider(answer="researcher-result"),
        tool_executor=tool_executor,
        schema_validator=schema_validator,
        memory_manager=WorkingMemoryManager(),
        event_bus=event_bus,
        state_manager=state_manager,
        prompt_builder=prompt_builder,
    )

    agent_registry = AgentRegistry()
    agent_registry.register(coder)
    agent_registry.register(researcher)

    session_agents = SessionAgentRegistry()
    session_agents.add(coder, activate=True)
    session_agents.add(researcher, activate=False)

    orchestrator = Orchestrator(
        event_bus=event_bus,
        state_manager=state_manager,
        default_agent=coder,
        agent_registry=agent_registry,
        session_agents=session_agents,
    )
    return {
        "orchestrator": orchestrator,
        "event_bus": event_bus,
        "session_agents": session_agents,
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


def test_orchestrator_parse_mention_tag():
    assert Orchestrator._parse_mention("!coder fix the bug") == ("coder", "fix the bug")
    assert Orchestrator._parse_mention("fix the bug") == (None, "fix the bug")
    assert Orchestrator._parse_mention("!coder !researcher help") == (
        "coder",
        "!researcher help",
    )


@pytest.mark.asyncio
async def test_orchestrator_switches_agent_from_mention(multi_agent_deps):
    delegated = []

    async def on_delegated(event):
        delegated.append(event)

    multi_agent_deps["event_bus"].subscribe("TaskDelegatedEvent", on_delegated)

    result = await multi_agent_deps["orchestrator"].handle_request(
        "!researcher investigate the issue"
    )

    await asyncio.sleep(0.05)
    assert result == "researcher-result"
    assert multi_agent_deps["session_agents"].active_name == "researcher"
    assert delegated[-1].agent_name == "researcher"


@pytest.mark.asyncio
async def test_orchestrator_rejects_unknown_session_mention(multi_agent_deps):
    agent_messages = []

    async def on_message(event):
        agent_messages.append(event)

    multi_agent_deps["event_bus"].subscribe("AgentMessageEvent", on_message)
    result = await multi_agent_deps["orchestrator"].handle_request("!unknown do this")

    await asyncio.sleep(0.05)
    assert result is None
    assert any(
        isinstance(event, AgentMessageEvent) and "not in this session" in event.content
        for event in agent_messages
    )


@pytest.mark.asyncio
async def test_orchestrator_rejects_concurrent_request_while_busy():
    event_bus = AsyncEventBus()
    state_manager = TaskStateManager(event_bus)
    registry = ToolRegistry()
    output_formatter = ToolOutputFormatter()
    tool_executor = ToolExecutor(
        registry=registry,
        event_bus=event_bus,
        output_formatter=output_formatter,
        auto_approve=True,
    )
    schema_validator = SchemaValidator(registry.get_all_names())
    prompt_builder = PromptBuilder(registry)
    provider = MockProvider(answer="slow-42")

    async def delayed_safe_generate(context, tools=None, **kwargs):
        await asyncio.sleep(0.15)
        return await MockProvider.safe_generate(
            provider, context, tools=tools, **kwargs
        )

    provider.safe_generate = delayed_safe_generate  # type: ignore[assignment]

    agent = MockAgent(
        config=AgentConfig(name="test_agent", tools=[], effort_level=EffortLevel.LOW),
        provider=provider,
        tool_executor=tool_executor,
        schema_validator=schema_validator,
        memory_manager=WorkingMemoryManager(),
        event_bus=event_bus,
        state_manager=state_manager,
        prompt_builder=prompt_builder,
    )
    orchestrator = Orchestrator(
        event_bus=event_bus,
        state_manager=state_manager,
        default_agent=agent,
    )

    agent_messages = []

    async def on_message(event):
        agent_messages.append(event)

    event_bus.subscribe("AgentMessageEvent", on_message)

    first_task = asyncio.create_task(orchestrator.handle_request("first"))
    await asyncio.sleep(0.02)
    second_result = await orchestrator.handle_request("second")
    first_result = await first_task

    assert second_result is None
    assert first_result == "slow-42"
    assert any("already processing" in e.content for e in agent_messages)


@pytest.mark.asyncio
async def test_orchestrator_interrupts_active_task():
    event_bus = AsyncEventBus()
    state_manager = TaskStateManager(event_bus)
    registry = ToolRegistry()
    output_formatter = ToolOutputFormatter()
    tool_executor = ToolExecutor(
        registry=registry,
        event_bus=event_bus,
        output_formatter=output_formatter,
        auto_approve=True,
    )
    schema_validator = SchemaValidator(registry.get_all_names())
    prompt_builder = PromptBuilder(registry)
    provider = MockProvider(answer="slow-42")

    async def delayed_safe_generate(context, tools=None, **kwargs):
        await asyncio.sleep(0.4)
        return await MockProvider.safe_generate(
            provider, context, tools=tools, **kwargs
        )

    provider.safe_generate = delayed_safe_generate  # type: ignore[assignment]

    agent = MockAgent(
        config=AgentConfig(name="test_agent", tools=[], effort_level=EffortLevel.LOW),
        provider=provider,
        tool_executor=tool_executor,
        schema_validator=schema_validator,
        memory_manager=WorkingMemoryManager(),
        event_bus=event_bus,
        state_manager=state_manager,
        prompt_builder=prompt_builder,
    )
    orchestrator = Orchestrator(
        event_bus=event_bus,
        state_manager=state_manager,
        default_agent=agent,
    )

    result_events: List[TaskResultEvent] = []
    state_events: List[StateChangeEvent] = []

    async def on_result(event):
        result_events.append(event)

    async def on_state(event):
        state_events.append(event)

    event_bus.subscribe("TaskResultEvent", on_result)
    event_bus.subscribe("StateChangeEvent", on_state)

    run_task = asyncio.create_task(orchestrator.handle_request("long request"))
    await asyncio.sleep(0.05)

    interrupted = await orchestrator.interrupt_active_task()
    assert interrupted is True

    result = await run_task
    assert result == "Task cancelled by user."

    await asyncio.sleep(0.05)
    assert any(e.to_state == "CANCELLED" for e in state_events)
    assert any(
        (not e.is_success) and ("cancelled" in e.result.lower()) for e in result_events
    )
