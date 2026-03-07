"""Tests for the Orchestrator."""

import asyncio
import json
from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel

from agent_cli.core.infra.config.config import AgentSettings
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.ux.commands.base import (
    CommandContext,
    CommandDef,
    CommandRegistry,
    CommandResult,
)
from agent_cli.core.ux.commands.parser import CommandParser
from agent_cli.core.runtime.agents.base import AgentConfig, BaseAgent
from agent_cli.core.runtime.agents.memory import WorkingMemoryManager
from agent_cli.core.runtime.agents.react_loop import PromptBuilder
from agent_cli.core.runtime.agents.registry import AgentRegistry
from agent_cli.core.runtime.agents.schema import SchemaValidator
from agent_cli.core.runtime.agents.session_registry import SessionAgentRegistry
from agent_cli.core.infra.events.event_bus import AsyncEventBus
from agent_cli.core.infra.events.events import (
    AgentMessageEvent,
    StateChangeEvent,
    TaskDelegatedEvent,
    TaskResultEvent,
    UserRequestEvent,
)
from agent_cli.core.runtime.orchestrator.orchestrator import Orchestrator
from agent_cli.core.runtime.orchestrator.state_manager import TaskStateManager
from agent_cli.core.runtime.orchestrator.state_models import TaskState
from agent_cli.core.providers.base.base import BaseLLMProvider
from agent_cli.core.providers.base.models import LLMRequest, LLMResponse, ToolCallMode
from agent_cli.core.runtime.tools.base import BaseTool, ToolCategory
from agent_cli.core.runtime.tools.executor import ToolExecutor
from agent_cli.core.runtime.tools.output_formatter import ToolOutputFormatter
from agent_cli.core.runtime.tools.registry import ToolRegistry

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
        super().__init__("mock_model", data_registry=DataRegistry())
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
        text = json.dumps(
            {
                "title": "Process incoming user request safely now",
                "thought": "Processing.",
                "decision": {"type": "notify_user", "message": self._answer},
            }
        )
        return LLMResponse(text_content=text, tool_mode=ToolCallMode.PROMPT_JSON)

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


def _build_test_command_parser(
    *,
    event_bus: AsyncEventBus,
    state_manager: TaskStateManager,
    memory_manager: WorkingMemoryManager,
) -> CommandParser:
    registry = CommandRegistry()

    async def _help_handler(args: List[str], context: CommandContext) -> CommandResult:
        return CommandResult(success=True, message="Available commands: /help, /exit")

    registry.register(
        CommandDef(
            name="help",
            description="Show available commands",
            usage="/help",
            handler=_help_handler,
            category="System",
        )
    )
    context = CommandContext(
        settings=AgentSettings(default_model="gpt-4o-mini"),
        event_bus=event_bus,
        state_manager=state_manager,
        memory_manager=memory_manager,
    )
    return CommandParser(registry=registry, context=context)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def deps():
    """Build a full set of deps for the Orchestrator."""
    event_bus = AsyncEventBus()
    state_manager = TaskStateManager(event_bus)

    registry = ToolRegistry()
    registry.register(MockTool())
    data_registry = DataRegistry()

    output_formatter = ToolOutputFormatter(data_registry=data_registry)
    tool_executor = ToolExecutor(
        registry=registry,
        event_bus=event_bus,
        output_formatter=output_formatter,
        auto_approve=True,
        data_registry=data_registry,
    )

    schema_validator = SchemaValidator(
        registry.get_all_names(),
        data_registry=data_registry,
    )
    memory_manager = WorkingMemoryManager(data_registry=data_registry)
    command_parser = _build_test_command_parser(
        event_bus=event_bus,
        state_manager=state_manager,
        memory_manager=memory_manager,
    )
    prompt_builder = PromptBuilder(registry, data_registry=data_registry)

    provider = MockProvider(answer="42")

    config = AgentConfig(
        name="test_agent",
        tools=["mock_tool"],
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
        data_registry=data_registry,
    )

    orchestrator = Orchestrator(
        event_bus=event_bus,
        state_manager=state_manager,
        default_agent=agent,
        command_parser=command_parser,
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
    data_registry = DataRegistry()
    output_formatter = ToolOutputFormatter(data_registry=data_registry)
    tool_executor = ToolExecutor(
        registry=registry,
        event_bus=event_bus,
        output_formatter=output_formatter,
        auto_approve=True,
        data_registry=data_registry,
    )
    schema_validator = SchemaValidator(
        registry.get_all_names(),
        data_registry=data_registry,
    )
    default_memory = WorkingMemoryManager(data_registry=data_registry)
    command_parser = _build_test_command_parser(
        event_bus=event_bus,
        state_manager=state_manager,
        memory_manager=default_memory,
    )
    prompt_builder = PromptBuilder(registry, data_registry=data_registry)

    coder = MockAgent(
        config=AgentConfig(name="coder"),
        provider=MockProvider(answer="coder-result"),
        tool_executor=tool_executor,
        schema_validator=schema_validator,
        memory_manager=default_memory,
        event_bus=event_bus,
        state_manager=state_manager,
        prompt_builder=prompt_builder,
        data_registry=data_registry,
    )
    researcher = MockAgent(
        config=AgentConfig(name="researcher"),
        provider=MockProvider(answer="researcher-result"),
        tool_executor=tool_executor,
        schema_validator=schema_validator,
        memory_manager=WorkingMemoryManager(data_registry=data_registry),
        event_bus=event_bus,
        state_manager=state_manager,
        prompt_builder=prompt_builder,
        data_registry=data_registry,
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
        command_parser=command_parser,
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
    data_registry = DataRegistry()
    output_formatter = ToolOutputFormatter(data_registry=data_registry)
    tool_executor = ToolExecutor(
        registry=registry,
        event_bus=event_bus,
        output_formatter=output_formatter,
        auto_approve=True,
        data_registry=data_registry,
    )
    schema_validator = SchemaValidator(
        registry.get_all_names(),
        data_registry=data_registry,
    )
    prompt_builder = PromptBuilder(registry, data_registry=data_registry)
    provider = MockProvider(answer="slow-42")

    async def delayed_safe_generate(context, tools=None, **kwargs):
        await asyncio.sleep(0.15)
        return await MockProvider.safe_generate(
            provider, context, tools=tools, **kwargs
        )

    provider.safe_generate = delayed_safe_generate  # type: ignore[assignment]

    agent = MockAgent(
        config=AgentConfig(name="test_agent", tools=[]),
        provider=provider,
        tool_executor=tool_executor,
        schema_validator=schema_validator,
        memory_manager=WorkingMemoryManager(data_registry=data_registry),
        event_bus=event_bus,
        state_manager=state_manager,
        prompt_builder=prompt_builder,
        data_registry=data_registry,
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
    data_registry = DataRegistry()
    output_formatter = ToolOutputFormatter(data_registry=data_registry)
    tool_executor = ToolExecutor(
        registry=registry,
        event_bus=event_bus,
        output_formatter=output_formatter,
        auto_approve=True,
        data_registry=data_registry,
    )
    schema_validator = SchemaValidator(
        registry.get_all_names(),
        data_registry=data_registry,
    )
    prompt_builder = PromptBuilder(registry, data_registry=data_registry)
    provider = MockProvider(answer="slow-42")

    async def delayed_safe_generate(context, tools=None, **kwargs):
        await asyncio.sleep(0.4)
        return await MockProvider.safe_generate(
            provider, context, tools=tools, **kwargs
        )

    provider.safe_generate = delayed_safe_generate  # type: ignore[assignment]

    agent = MockAgent(
        config=AgentConfig(name="test_agent", tools=[]),
        provider=provider,
        tool_executor=tool_executor,
        schema_validator=schema_validator,
        memory_manager=WorkingMemoryManager(data_registry=data_registry),
        event_bus=event_bus,
        state_manager=state_manager,
        prompt_builder=prompt_builder,
        data_registry=data_registry,
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
