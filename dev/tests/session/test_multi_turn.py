"""Multi-turn continuity tests through Orchestrator + SessionManager."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from agent_cli.core.runtime.agents.base import AgentConfig, BaseAgent
from agent_cli.core.runtime.agents.memory import WorkingMemoryManager
from agent_cli.core.runtime.agents.react_loop import PromptBuilder
from agent_cli.core.runtime.agents.registry import AgentRegistry
from agent_cli.core.runtime.agents.schema import SchemaValidator
from agent_cli.core.runtime.agents.session_registry import SessionAgentRegistry
from agent_cli.core.infra.events.event_bus import AsyncEventBus
from agent_cli.core.runtime.orchestrator.orchestrator import Orchestrator
from agent_cli.core.runtime.orchestrator.state_manager import TaskStateManager
from agent_cli.core.providers.base.base import BaseLLMProvider
from agent_cli.core.providers.base.models import LLMResponse, ToolCallMode
from agent_cli.core.runtime.session.file_store import FileSessionManager
from agent_cli.core.runtime.tools.executor import ToolExecutor
from agent_cli.core.runtime.tools.output_formatter import ToolOutputFormatter
from agent_cli.core.runtime.tools.registry import ToolRegistry


class ContextCaptureProvider(BaseLLMProvider):
    """Mock provider that records request context for assertions."""

    def __init__(self) -> None:
        super().__init__("mock-model")
        self.context_calls: List[List[Dict[str, Any]]] = []
        self.effort_calls: List[str] = []

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
        text = json.dumps(
            {
                "title": "Respond to user task now",
                "thought": "Processing request.",
                "decision": {"type": "notify_user", "message": f"turn-{turn}"},
            }
        )
        return LLMResponse(text_content=text, tool_mode=ToolCallMode.PROMPT_JSON)

    async def stream(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
    ):
        yield None

    def get_buffered_response(self) -> LLMResponse:
        return LLMResponse(text_content="", tool_mode=ToolCallMode.PROMPT_JSON)

    async def safe_generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> LLMResponse:
        effort = kwargs.get("effort")
        if effort is not None:
            self.effort_calls.append(str(effort))
        return await self.generate(context=context, tools=tools)


class EffortCapableContextProvider(ContextCaptureProvider):
    @property
    def provider_name(self) -> str:
        return "google"

    @property
    def supports_effort(self) -> bool:
        return True


class SessionAwareAgent(BaseAgent):
    async def build_system_prompt(self, task_context: str) -> str:
        return "You are a test agent."

    async def on_tool_result(self, tool_name: str, result: str) -> None:
        return None

    async def on_final_answer(self, answer: str) -> str:
        return answer


class EmptyTitleProvider(ContextCaptureProvider):
    async def generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.context_calls.append(deepcopy(context))
        text = json.dumps(
            {
                "title": "",
                "thought": "Processing request.",
                "decision": {"type": "notify_user", "message": "ok"},
            }
        )
        return LLMResponse(text_content=text, tool_mode=ToolCallMode.PROMPT_JSON)


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
async def test_orchestrator_defers_session_name_and_calls_service(tmp_path: Path):
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
    provider = ContextCaptureProvider()
    agent = SessionAwareAgent(
        config=AgentConfig(name="session-agent"),
        provider=provider,
        tool_executor=tool_executor,
        schema_validator=validator,
        memory_manager=WorkingMemoryManager(),
        event_bus=event_bus,
        state_manager=state_manager,
        prompt_builder=PromptBuilder(registry),
    )
    session_manager = FileSessionManager(
        session_dir=tmp_path / "sessions", default_model="mock-model"
    )

    class MockTitleService:
        called = False
        def should_generate(self, session, force=False):
            # Simulate min_turns = 2
            user_turns = sum(1 for m in session.messages if m.get("role") == "user")
            return user_turns >= 2
        async def generate_title(self, p, messages, max_tokens=32):
            self.called = True
            return "Generated By Service"

    title_service = MockTitleService()

    orchestrator = Orchestrator(
        event_bus=event_bus,
        state_manager=state_manager,
        default_agent=agent,
        session_manager=session_manager,
        title_service=title_service,
    )

    # First turn: should not generate title
    await orchestrator.handle_request("summarize this project")
    active = session_manager.get_active()
    assert active is not None
    assert active.name == "Untitled session"
    assert not title_service.called

    # Second turn: should trigger title generation
    await orchestrator.handle_request("and also this other thing")
    active = session_manager.get_active()
    assert active is not None
    assert active.name == "Generated By Service"
    assert title_service.called


@pytest.mark.asyncio
async def test_orchestrator_falls_back_to_untitled_session(tmp_path: Path):
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
    provider = EmptyTitleProvider()
    agent = SessionAwareAgent(
        config=AgentConfig(name="session-agent"),
        provider=provider,
        tool_executor=tool_executor,
        schema_validator=validator,
        memory_manager=WorkingMemoryManager(),
        event_bus=event_bus,
        state_manager=state_manager,
        prompt_builder=PromptBuilder(registry),
    )
    session_manager = FileSessionManager(
        session_dir=tmp_path / "sessions", default_model="mock-model"
    )
    class MockEmptyTitleService:
        def should_generate(self, session, force=False):
            return True
        async def generate_title(self, p, messages, max_tokens=32):
            return ""

    orchestrator = Orchestrator(
        event_bus=event_bus,
        state_manager=state_manager,
        default_agent=agent,
        session_manager=session_manager,
        title_service=MockEmptyTitleService(),
    )

    await orchestrator.handle_request("hello")
    active = session_manager.get_active()
    assert active is not None
    assert active.name == "Untitled session"


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


@pytest.mark.asyncio
async def test_orchestrator_passes_session_desired_effort_to_agent(tmp_path: Path):
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
    provider = EffortCapableContextProvider()
    provider.model_name = "gemini-2.5-flash"
    agent = SessionAwareAgent(
        config=AgentConfig(name="session-agent"),
        provider=provider,
        tool_executor=tool_executor,
        schema_validator=validator,
        memory_manager=WorkingMemoryManager(),
        event_bus=event_bus,
        state_manager=state_manager,
        prompt_builder=PromptBuilder(registry),
    )
    session_manager = FileSessionManager(
        session_dir=tmp_path / "sessions", default_model="mock-model"
    )
    active = session_manager.create_session()
    active.desired_effort = "high"
    session_manager.save(active)

    orchestrator = Orchestrator(
        event_bus=event_bus,
        state_manager=state_manager,
        default_agent=agent,
        session_manager=session_manager,
    )

    await orchestrator.handle_request("use effort")
    assert provider.effort_calls
    assert provider.effort_calls[-1] == "high"


@pytest.mark.asyncio
async def test_orchestrator_probes_capabilities_once_on_first_session_creation(
    tmp_path: Path,
):
    class _ProbeRecorder:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def probe_provider(self, provider: Any, *, trigger: str) -> None:
            self.calls.append(trigger)

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
    provider = ContextCaptureProvider()
    agent = SessionAwareAgent(
        config=AgentConfig(name="session-agent"),
        provider=provider,
        tool_executor=tool_executor,
        schema_validator=validator,
        memory_manager=WorkingMemoryManager(),
        event_bus=event_bus,
        state_manager=state_manager,
        prompt_builder=PromptBuilder(registry),
    )
    session_manager = FileSessionManager(
        session_dir=tmp_path / "sessions", default_model="mock-model"
    )
    probe = _ProbeRecorder()
    orchestrator = Orchestrator(
        event_bus=event_bus,
        state_manager=state_manager,
        default_agent=agent,
        session_manager=session_manager,
        capability_probe=probe,  # type: ignore[arg-type]
    )

    await orchestrator.handle_request("first")
    await orchestrator.handle_request("second")
    assert probe.calls == ["session_start"]
