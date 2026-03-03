"""Multi-turn continuity tests through Orchestrator + SessionManager."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from agent_cli.agent.base import AgentConfig, BaseAgent
from agent_cli.agent.memory import WorkingMemoryManager
from agent_cli.agent.react_loop import PromptBuilder
from agent_cli.agent.registry import AgentRegistry
from agent_cli.agent.schema import SchemaValidator
from agent_cli.agent.session_registry import SessionAgentRegistry
from agent_cli.core.events.event_bus import AsyncEventBus
from agent_cli.core.orchestrator import Orchestrator
from agent_cli.core.state.state_manager import TaskStateManager
from agent_cli.providers.base import BaseLLMProvider
from agent_cli.providers.models import LLMResponse, ToolCallMode
from agent_cli.session.file_store import FileSessionManager
from agent_cli.tools.executor import ToolExecutor
from agent_cli.tools.output_formatter import ToolOutputFormatter
from agent_cli.tools.registry import ToolRegistry


class ContextCaptureProvider(BaseLLMProvider):
    """Mock provider that records request context for assertions."""

    def __init__(self) -> None:
        super().__init__("mock-model")
        self.context_calls: List[List[Dict[str, Any]]] = []

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def supports_native_tools(self) -> bool:
        return False

    def _create_tool_formatter(self):
        return None

    async def generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.context_calls.append(deepcopy(context))
        turn = len(self.context_calls)
        text = (
            "<title>Respond to user task now</title>\n"
            "<thinking>Processing request.</thinking>\n"
            f"<final_answer>turn-{turn}</final_answer>"
        )
        return LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    async def stream(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
    ):
        yield None

    def get_buffered_response(self) -> LLMResponse:
        return LLMResponse(text_content="", tool_mode=ToolCallMode.XML)

    async def safe_generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> LLMResponse:
        return await self.generate(context=context, tools=tools)


class SessionAwareAgent(BaseAgent):
    async def build_system_prompt(self, task_context: str) -> str:
        return "You are a test agent."

    async def on_tool_result(self, tool_name: str, result: str) -> None:
        return None

    async def on_final_answer(self, answer: str) -> str:
        return answer


@pytest.mark.asyncio
async def test_orchestrator_persists_and_rehydrates_multi_turn_context(tmp_path: Path):
    event_bus = AsyncEventBus()
    state_manager = TaskStateManager(event_bus)

    registry = ToolRegistry()
    tool_executor = ToolExecutor(
        registry=registry,
        event_bus=event_bus,
        output_formatter=ToolOutputFormatter(),
        auto_approve=True,
    )
    validator = SchemaValidator(registry.get_all_names())
    memory = WorkingMemoryManager()
    prompt_builder = PromptBuilder(registry)

    provider = ContextCaptureProvider()
    agent = SessionAwareAgent(
        config=AgentConfig(name="session-agent"),
        provider=provider,
        tool_executor=tool_executor,
        schema_validator=validator,
        memory_manager=memory,
        event_bus=event_bus,
        state_manager=state_manager,
        prompt_builder=prompt_builder,
    )

    session_manager = FileSessionManager(
        session_dir=tmp_path / "sessions", default_model="mock-model"
    )
    orchestrator = Orchestrator(
        event_bus=event_bus,
        state_manager=state_manager,
        default_agent=agent,
        session_manager=session_manager,
    )

    first = await orchestrator.handle_request("first question")
    second = await orchestrator.handle_request("second question")

    assert first == "turn-1"
    assert second == "turn-2"
    assert len(provider.context_calls) == 2

    # Second call should include first-turn user context from the persisted session.
    second_context = provider.context_calls[1]
    assert any(msg.get("content") == "first question" for msg in second_context)

    active = session_manager.get_active()
    assert active is not None
    assert len(active.task_ids) == 2
    assert active.active_model == "mock-model"

    user_contents = [
        m.get("content", "") for m in active.messages if m.get("role") == "user"
    ]
    assert "first question" in user_contents
    assert "second question" in user_contents


@pytest.mark.asyncio
async def test_end_to_end_switch_save_and_restore_session(tmp_path: Path):
    def _build_runtime():
        event_bus = AsyncEventBus()
        state_manager = TaskStateManager(event_bus)
        registry = ToolRegistry()
        tool_executor = ToolExecutor(
            registry=registry,
            event_bus=event_bus,
            output_formatter=ToolOutputFormatter(),
            auto_approve=True,
        )
        validator = SchemaValidator(registry.get_all_names())
        prompt_builder = PromptBuilder(registry)
        return (
            event_bus,
            state_manager,
            tool_executor,
            validator,
            prompt_builder,
        )

    # First app run
    (
        event_bus_1,
        state_manager_1,
        tool_executor_1,
        validator_1,
        prompt_builder_1,
    ) = _build_runtime()
    session_dir = tmp_path / "sessions"
    session_manager = FileSessionManager(
        session_dir=session_dir, default_model="mock-model"
    )

    coder_provider_1 = ContextCaptureProvider()
    researcher_provider_1 = ContextCaptureProvider()
    coder_1 = SessionAwareAgent(
        config=AgentConfig(name="coder"),
        provider=coder_provider_1,
        tool_executor=tool_executor_1,
        schema_validator=validator_1,
        memory_manager=WorkingMemoryManager(),
        event_bus=event_bus_1,
        state_manager=state_manager_1,
        prompt_builder=prompt_builder_1,
    )
    researcher_1 = SessionAwareAgent(
        config=AgentConfig(name="researcher"),
        provider=researcher_provider_1,
        tool_executor=tool_executor_1,
        schema_validator=validator_1,
        memory_manager=WorkingMemoryManager(),
        event_bus=event_bus_1,
        state_manager=state_manager_1,
        prompt_builder=prompt_builder_1,
    )
    agent_registry_1 = AgentRegistry()
    agent_registry_1.register(coder_1)
    agent_registry_1.register(researcher_1)
    session_agents_1 = SessionAgentRegistry()
    session_agents_1.add(coder_1, activate=True)
    session_agents_1.add(researcher_1, activate=False)

    orchestrator_1 = Orchestrator(
        event_bus=event_bus_1,
        state_manager=state_manager_1,
        default_agent=coder_1,
        session_manager=session_manager,
        agent_registry=agent_registry_1,
        session_agents=session_agents_1,
    )

    await orchestrator_1.handle_request("write tests")
    await orchestrator_1.handle_request("!researcher evaluate risks")

    active = session_manager.get_active()
    assert active is not None
    user_messages = [
        m.get("content", "") for m in active.messages if m.get("role") == "user"
    ]
    assert "write tests" in user_messages
    assert "evaluate risks" in user_messages

    # Simulate a new app run restoring from same session files
    (
        event_bus_2,
        state_manager_2,
        tool_executor_2,
        validator_2,
        prompt_builder_2,
    ) = _build_runtime()
    coder_provider_2 = ContextCaptureProvider()
    researcher_provider_2 = ContextCaptureProvider()
    coder_2 = SessionAwareAgent(
        config=AgentConfig(name="coder"),
        provider=coder_provider_2,
        tool_executor=tool_executor_2,
        schema_validator=validator_2,
        memory_manager=WorkingMemoryManager(),
        event_bus=event_bus_2,
        state_manager=state_manager_2,
        prompt_builder=prompt_builder_2,
    )
    researcher_2 = SessionAwareAgent(
        config=AgentConfig(name="researcher"),
        provider=researcher_provider_2,
        tool_executor=tool_executor_2,
        schema_validator=validator_2,
        memory_manager=WorkingMemoryManager(),
        event_bus=event_bus_2,
        state_manager=state_manager_2,
        prompt_builder=prompt_builder_2,
    )
    agent_registry_2 = AgentRegistry()
    agent_registry_2.register(coder_2)
    agent_registry_2.register(researcher_2)
    session_agents_2 = SessionAgentRegistry()
    session_agents_2.add(coder_2, activate=True)
    session_agents_2.add(researcher_2, activate=False)
    orchestrator_2 = Orchestrator(
        event_bus=event_bus_2,
        state_manager=state_manager_2,
        default_agent=coder_2,
        session_manager=session_manager,
        agent_registry=agent_registry_2,
        session_agents=session_agents_2,
    )

    await orchestrator_2.handle_request("continue plan")
    assert coder_provider_2.context_calls
    restored_context = coder_provider_2.context_calls[0]
    assert any(m.get("content") == "write tests" for m in restored_context)
    assert any(m.get("content") == "evaluate risks" for m in restored_context)
