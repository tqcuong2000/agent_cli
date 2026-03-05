from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agent_cli.core.runtime.agents.base import AgentConfig, BaseAgent
from agent_cli.core.runtime.agents.memory import WorkingMemoryManager
from agent_cli.core.runtime.agents.react_loop import PromptBuilder
from agent_cli.core.runtime.agents.registry import AgentRegistry
from agent_cli.core.runtime.agents.schema import SchemaValidator
from agent_cli.core.runtime.agents.session_registry import AgentStatus, SessionAgentRegistry
from agent_cli.core.infra.events.event_bus import AsyncEventBus
from agent_cli.core.runtime.orchestrator.state_manager import TaskStateManager
from agent_cli.core.providers.base.base import BaseLLMProvider
from agent_cli.core.providers.base.models import LLMRequest, LLMResponse, ToolCallMode
from agent_cli.core.runtime.tools.executor import ToolExecutor
from agent_cli.core.runtime.tools.output_formatter import ToolOutputFormatter
from agent_cli.core.runtime.tools.registry import ToolRegistry


class _Provider(BaseLLMProvider):
    def __init__(self, model_name: str = "mock-model") -> None:
        super().__init__(model_name)

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def supports_native_tools(self) -> bool:
        return False

    def _create_tool_formatter(self):
        return None

    async def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text_content='{"title":"Return answer now","thought":"ok","decision":{"type":"notify_user","message":"ok"}}',
            tool_mode=ToolCallMode.PROMPT_JSON,
        )

    async def safe_generate(self, context, tools=None, **kwargs) -> LLMResponse:
        return await self.generate(LLMRequest(messages=[]))

    async def stream(self, request: LLMRequest):
        yield None

    def get_buffered_response(self) -> LLMResponse:
        return LLMResponse()


class _Agent(BaseAgent):
    async def build_system_prompt(self, task_context: str) -> str:
        return "You are a test agent."

    async def on_tool_result(self, tool_name: str, result: str) -> None:
        return None

    async def on_final_answer(self, answer: str) -> str:
        return answer


def _make_agent(name: str) -> BaseAgent:
    event_bus = AsyncEventBus()
    state_manager = TaskStateManager(event_bus)
    tool_registry = ToolRegistry()
    tool_executor = ToolExecutor(
        registry=tool_registry,
        event_bus=event_bus,
        output_formatter=ToolOutputFormatter(),
        auto_approve=True,
    )
    return _Agent(
        config=AgentConfig(name=name),
        provider=_Provider(),
        tool_executor=tool_executor,
        schema_validator=SchemaValidator(tool_registry.get_all_names()),
        memory_manager=WorkingMemoryManager(),
        event_bus=event_bus,
        state_manager=state_manager,
        prompt_builder=PromptBuilder(tool_registry),
    )


def test_agent_registry_register_and_lookup() -> None:
    registry = AgentRegistry()
    agent = _make_agent("coder")
    registry.register(agent)

    assert registry.has("coder")
    assert registry.get("coder") is agent
    assert registry.names() == ["coder"]
    assert len(registry) == 1
    assert "coder" in registry
    assert "researcher" not in registry

    with pytest.raises(ValueError, match="already registered"):
        registry.register(agent)


def test_agent_registry_freeze_blocks_register() -> None:
    registry = AgentRegistry()
    registry.register(_make_agent("default"))
    registry.freeze()

    with pytest.raises(RuntimeError, match="frozen"):
        registry.register(_make_agent("coder"))


def test_agent_registry_freeze_rejects_empty_registry() -> None:
    registry = AgentRegistry()
    with pytest.raises(RuntimeError, match="at least one agent"):
        registry.freeze()


def test_agent_registry_rejects_missing_name() -> None:
    registry = AgentRegistry()
    with pytest.raises(ValueError, match="non-empty 'name'"):
        registry.register(object())  # type: ignore[arg-type]


def test_agent_registry_rejects_missing_handle_task() -> None:
    registry = AgentRegistry()
    with pytest.raises(ValueError, match="'handle_task' method"):
        registry.register(SimpleNamespace(name="fake"))  # type: ignore[arg-type]


def test_session_agent_registry_add_switch_disable_enable_remove() -> None:
    session_registry = SessionAgentRegistry()
    coder = _make_agent("coder")
    researcher = _make_agent("researcher")

    session_registry.add(coder, activate=True)
    session_registry.add(researcher, activate=False)

    assert session_registry.active_name == "coder"
    assert session_registry.active_agent is coder

    session_registry.switch_to("researcher")
    assert session_registry.active_name == "researcher"
    by_name = {item.name: item.status for item in session_registry.list_agents()}
    assert by_name["coder"] == AgentStatus.IDLE
    assert by_name["researcher"] == AgentStatus.ACTIVE

    with pytest.raises(ValueError, match="Cannot disable the active agent"):
        session_registry.disable("researcher")

    session_registry.disable("coder")
    assert {item.name: item.status for item in session_registry.list_agents()}[
        "coder"
    ] == AgentStatus.INACTIVE

    session_registry.enable("coder")
    assert {item.name: item.status for item in session_registry.list_agents()}[
        "coder"
    ] == AgentStatus.IDLE

    session_registry.remove("coder")
    assert not session_registry.has("coder")

    with pytest.raises(KeyError):
        session_registry.switch_to("unknown")


def test_session_agent_registry_rejects_missing_name() -> None:
    session_registry = SessionAgentRegistry()
    with pytest.raises(ValueError, match="non-empty 'name'"):
        session_registry.add(object())  # type: ignore[arg-type]
