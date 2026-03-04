import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel

from agent_cli.agent.base import AgentConfig, BaseAgent
from agent_cli.agent.default import DefaultAgent
from agent_cli.agent.memory import WorkingMemoryManager
from agent_cli.agent.react_loop import PromptBuilder
from agent_cli.agent.schema import SchemaValidator
from agent_cli.core.error_handler.errors import MaxIterationsExceededError
from agent_cli.core.events.event_bus import AbstractEventBus, AsyncEventBus
from agent_cli.core.state.state_manager import TaskState, TaskStateManager
from agent_cli.core.registry import DataRegistry
from agent_cli.providers.base import BaseLLMProvider, BaseToolFormatter
from agent_cli.providers.models import (
    LLMResponse,
    ProviderRequestOptions,
    ToolCall,
    ToolCallMode,
)
from agent_cli.tools.ask_user_tool import AskUserTool
from agent_cli.tools.base import BaseTool, ToolCategory
from agent_cli.tools.executor import ToolExecutor
from agent_cli.tools.output_formatter import ToolOutputFormatter
from agent_cli.tools.registry import ToolRegistry

# ── Mocks ────────────────────────────────────────────────────────────


class MockMathToolArgs(BaseModel):
    x: int
    y: int


class MockMathTool(BaseTool):
    name = "add"
    description = "Add two numbers."
    category = ToolCategory.UTILITY

    @property
    def args_schema(self) -> type[BaseModel]:
        return MockMathToolArgs

    async def execute(self, x: int, y: int, **kwargs: Any) -> str:
        return str(x + y)


class MockLLMProvider(BaseLLMProvider):
    def __init__(
        self,
        responses: List[str],
        *,
        model_name: str = "mock_model",
        provider_name: str = "mock",
        supports_effort: bool = False,
    ):
        super().__init__(model_name)
        self.responses = responses
        self.call_count = 0
        self.requests = []
        self.request_options: List[ProviderRequestOptions | None] = []
        self.efforts: List[str | None] = []
        self._provider_name = provider_name
        self._supports_effort = supports_effort

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def supports_native_tools(self) -> bool:
        return False

    @property
    def supports_effort(self) -> bool:
        return self._supports_effort

    def _create_tool_formatter(self) -> BaseToolFormatter:
        return _MockToolFormatter()

    async def generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.requests.append(context)
        if self.call_count >= len(self.responses):
            text = _json_response(
                "notify_user",
                message="Out of mock responses.",
                title="Stop",
                thought="No more fixtures",
            )
        else:
            text = self.responses[self.call_count]
            self.call_count += 1

        return LLMResponse(text_content=text, tool_mode=ToolCallMode.PROMPT_JSON)

    def get_buffered_response(self) -> LLMResponse:
        return LLMResponse(
            text_content='{"title":"buffered","thought":"buffered","decision":{"type":"reflect"}}',
            tool_mode=ToolCallMode.PROMPT_JSON,
        )

    async def safe_generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
        max_retries: int = 3,
        task_id: str = "",
        event_bus: Optional[AbstractEventBus] = None,
        **kwargs,
    ) -> LLMResponse:
        # Override safe_generate directly since we mock everything
        self.request_options.append(kwargs.get("request_options"))
        effort = kwargs.get("effort")
        self.efforts.append(str(effort) if effort is not None else None)
        return await self.generate(context, tools, max_tokens)

    async def stream(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
    ):
        yield None

    async def check_health(self) -> bool:
        return True


class DummyAgent(BaseAgent):
    async def build_system_prompt(self, task_context: str) -> str:
        return self.prompt_builder.build(
            persona="You are a dummy.",
            tool_names=self.get_prompt_tool_names(),
        )

    async def on_tool_result(self, tool_name: str, result: str) -> None:
        pass

    async def on_final_answer(self, answer: str) -> str:
        return f"FINAL: {answer}"


class _MockToolFormatter(BaseToolFormatter):
    def format_for_native_fc(self, tools: List[Dict[str, Any]]) -> Any:
        return tools

    def format_for_prompt_injection(self, tools: List[Dict[str, Any]]) -> str:
        return ""


class MockWebSearchProvider(MockLLMProvider):
    def __init__(self, responses: List[str]):
        super().__init__(
            responses,
            model_name="gemini-2.5-flash-lite",
            provider_name="google",
            supports_effort=True,
        )

    @property
    def supports_web_search(self) -> bool:
        return True


def _json_response(
    decision_type: str,
    *,
    tool: str = "",
    args: dict | None = None,
    message: str = "",
    title: str = "Plan next step",
    thought: str = "I will continue.",
) -> str:
    payload: Dict[str, Any] = {
        "title": title,
        "thought": thought,
        "decision": {"type": decision_type},
    }
    if tool:
        payload["decision"]["tool"] = tool
    if args is not None:
        payload["decision"]["args"] = args
    if message:
        payload["decision"]["message"] = message
    return json.dumps(payload)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def base_deps():
    registry = ToolRegistry()
    registry.register(MockMathTool())

    event_bus = AsyncEventBus()
    state_manager = TaskStateManager(event_bus)
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

    from agent_cli.core.config import AgentSettings

    settings = AgentSettings()
    settings.max_iterations = 100

    return {
        "tool_executor": tool_executor,
        "schema_validator": schema_validator,
        "memory_manager": memory_manager,
        "event_bus": event_bus,
        "state_manager": state_manager,
        "prompt_builder": prompt_builder,
        "settings": settings,
    }


# ── Integration Tests ────────────────────────────────────────────────


def test_max_iterations_prefers_agent_override(base_deps):
    provider = MockLLMProvider([])

    agent = DummyAgent(
        config=AgentConfig(name="dummy", tools=["add"], max_iterations_override=7),
        provider=provider,
        **base_deps,
    )

    assert agent.config.max_iterations_override == 7


def test_max_iterations_falls_back_to_global_setting(base_deps):
    provider = MockLLMProvider([])
    base_deps["settings"].max_iterations = 220

    agent = DummyAgent(
        config=AgentConfig(name="dummy", tools=["add"]),
        provider=provider,
        **base_deps,
    )

    assert agent.config.max_iterations_override is None
    assert agent.settings.max_iterations == 220


def test_format_assistant_history_keeps_prompt_mode_text_unchanged():
    response = LLMResponse(
        text_content='{"title":"Plan","thought":"Do it","decision":{"type":"reflect"}}',
        tool_mode=ToolCallMode.PROMPT_JSON,
    )

    assert BaseAgent._format_assistant_history(response) == response.text_content


def test_format_assistant_history_appends_native_tool_call_json_trace():
    response = LLMResponse(
        text_content='{"title":"Read file","thought":"Need file","decision":{"type":"execute_action"}}',
        tool_mode=ToolCallMode.NATIVE,
        tool_calls=[
            ToolCall(
                tool_name="read_file",
                arguments={"path": "README.md"},
                native_call_id="call_1",
            )
        ],
    )

    formatted = BaseAgent._format_assistant_history(response)
    trace = json.loads(formatted.splitlines()[-1])
    assert trace["type"] == "tool_call"
    assert trace["version"] == "1.0"
    assert trace["payload"]["tool"] == "read_file"
    assert trace["payload"]["args"] == {"path": "README.md"}
    assert trace["metadata"]["native_call_id"] == "call_1"


@pytest.mark.asyncio
async def test_react_loop_successful_task(base_deps):
    """Test a full ReAct loop where the agent thinks, uses a tool,
    and returns a final answer."""

    mock_responses = [
        # Iteration 1: Call 'add' tool
        _json_response(
            "execute_action",
            tool="add",
            args={"x": 2, "y": 3},
            title="Compute sum using add tool call",
            thought="I should add.",
        ),
        # Iteration 2: Return final answer
        _json_response(
            "notify_user",
            message="The answer is 5.",
            title="Return concise final answer to user",
            thought="Got the result.",
        ),
    ]

    provider = MockLLMProvider(mock_responses)
    config = AgentConfig(name="dummy", tools=["add"])

    agent = DummyAgent(config=config, provider=provider, **base_deps)

    task = await base_deps["state_manager"].create_task("Add 2 and 3")
    await base_deps["state_manager"].transition(task.task_id, TaskState.ROUTING)
    await base_deps["state_manager"].transition(task.task_id, TaskState.WORKING)

    result = await agent.handle_task(
        task_id=task.task_id,
        task_description="Add 2 and 3",
    )

    assert result == "FINAL: The answer is 5."
    assert provider.call_count == 2

    # Verify working memory
    memory = base_deps["memory_manager"].get_working_context()
    assert len(memory) > 3  # sys prompt, task desc, iteration 1 turn, etc.
    tool_payloads = [
        json.loads(m["content"]) for m in memory if m.get("role") == "tool"
    ]
    assert any(p.get("type") == "tool_result" for p in tool_payloads)
    assert any(p.get("payload", {}).get("tool") == "add" for p in tool_payloads)


@pytest.mark.asyncio
async def test_react_loop_schema_correction(base_deps):
    """Test that the agent recovers from a malformed schema response
    by receiving the error and trying again."""

    mock_responses = [
        # Iteration 1: malformed JSON payload (missing decision object)
        json.dumps(
            {"title": "Detect malformed action and recover quickly", "thought": "Oops"}
        ),
        # Iteration 2: Correct format
        _json_response(
            "notify_user",
            message="I fixed it.",
            title="Provide corrected response after feedback",
            thought="Let me fix that.",
        ),
    ]

    provider = MockLLMProvider(mock_responses)
    config = AgentConfig(name="dummy", tools=["add"])

    agent = DummyAgent(config=config, provider=provider, **base_deps)

    task = await base_deps["state_manager"].create_task("Do something")
    await base_deps["state_manager"].transition(task.task_id, TaskState.ROUTING)
    await base_deps["state_manager"].transition(task.task_id, TaskState.WORKING)

    result = await agent.handle_task(
        task_id=task.task_id,
        task_description="Do something",
    )

    assert result == "FINAL: I fixed it."

    # Check that memory contains the schema error feedback
    mem = base_deps["memory_manager"].get_working_context()
    assert any("Schema Error" in m["content"] for m in mem)
    assert any("Valid prompt JSON examples" in m["content"] for m in mem)


@pytest.mark.asyncio
async def test_react_loop_max_iterations(base_deps):
    """Test that the agent raises MaxIterationsExceededError if it
    loops too many times without a final answer."""

    # Always returning an action (infinite loop scenario)
    infinite_action = _json_response(
        "execute_action",
        tool="add",
        args={"x": 1, "y": 1},
        title="Repeat same action to test max iterations",
        thought="Looping",
    )

    # Feed 35 copies of the same action
    base_deps["settings"].max_iterations = 30
    provider = MockLLMProvider([infinite_action] * 35)
    config = AgentConfig(name="dummy", tools=["add"])

    agent = DummyAgent(config=config, provider=provider, **base_deps)

    task = await base_deps["state_manager"].create_task("Loop forever")
    await base_deps["state_manager"].transition(task.task_id, TaskState.ROUTING)
    await base_deps["state_manager"].transition(task.task_id, TaskState.WORKING)

    with pytest.raises(MaxIterationsExceededError) as exc:
        await agent.handle_task(
            task_id=task.task_id,
            task_description="Loop forever",
        )

    assert "reached 30 iterations" in str(exc.value)


@pytest.mark.asyncio
async def test_react_loop_stuck_detection(base_deps):
    """Test that the stuck detector injects a warning if the agent
    repeats the exact same tool call and gets the same result."""

    same_action = _json_response(
        "execute_action",
        tool="add",
        args={"x": 0, "y": 0},
        title="Repeat identical action to trigger stuck hint",
        thought="Stuck",
    )

    # 3 repetitions triggers the stuck detector warning for the 4th iteration
    mock_responses = [
        same_action,  # iter 1 -> result 0
        same_action,  # iter 2 -> result 0
        same_action,  # iter 3 -> result 0, triggers stuck detection!
        _json_response(
            "notify_user",
            message="I was stuck.",
            title="Acknowledge loop and finish with answer",
            thought="Oh",
        ),
    ]

    provider = MockLLMProvider(mock_responses)
    config = AgentConfig(name="dummy", tools=["add"])

    agent = DummyAgent(config=config, provider=provider, **base_deps)

    task = await base_deps["state_manager"].create_task("Test stuck")
    await base_deps["state_manager"].transition(task.task_id, TaskState.ROUTING)
    await base_deps["state_manager"].transition(task.task_id, TaskState.WORKING)

    result = await agent.handle_task(
        task_id=task.task_id,
        task_description="Test stuck",
    )

    assert result == "FINAL: I was stuck."

    mem = base_deps["memory_manager"].get_working_context()
    assert any("repeating the same action" in m["content"] for m in mem)


def test_prompt_builder_adds_ask_user_policy_when_tool_available():
    registry = ToolRegistry()
    registry.register(MockMathTool())
    registry.register(AskUserTool())
    prompt_builder = PromptBuilder(registry)

    prompt = prompt_builder.build(
        persona="You are a tester.",
        tool_names=["add", "ask_user"],
    )
    assert "# Clarification Policy" in prompt
    assert "MUST use the `ask_user` tool" in prompt
    assert "Use 2-5 likely answer options in `ask_user`" in prompt


def test_prompt_builder_skips_ask_user_policy_when_tool_missing():
    registry = ToolRegistry()
    registry.register(MockMathTool())
    prompt_builder = PromptBuilder(registry)

    prompt = prompt_builder.build(
        persona="You are a tester.",
        tool_names=["add"],
    )
    assert "# Clarification Policy" not in prompt


def test_prompt_builder_renders_title_word_limit_from_template():
    registry = ToolRegistry()
    registry.register(MockMathTool())
    prompt_builder = PromptBuilder(registry)

    prompt = prompt_builder.build(
        persona="You are a tester.",
        tool_names=["add"],
    )

    assert "{title_max_words}" not in prompt
    assert "1 to 15 words" in prompt


def test_prompt_builder_switches_native_vs_prompt_json_output_template():
    registry = ToolRegistry()
    registry.register(MockMathTool())
    prompt_builder = PromptBuilder(registry)

    prompt_json = prompt_builder.build(
        persona="You are a tester.",
        tool_names=["add"],
        native_tool_mode=False,
    )
    native_prompt = prompt_builder.build(
        persona="You are a tester.",
        tool_names=["add"],
        native_tool_mode=True,
    )

    assert "Return exactly ONE JSON object" in prompt_json
    assert "native function-calling" in native_prompt


def test_prompt_builder_renders_provider_managed_capabilities_section():
    registry = ToolRegistry()
    registry.register(MockMathTool())
    prompt_builder = PromptBuilder(registry)

    prompt = prompt_builder.build(
        persona="You are a tester.",
        tool_names=["add"],
        provider_managed_capabilities=["web_search"],
    )

    assert "# Provider-Managed Capabilities" in prompt
    assert "web_search" in prompt


def test_agent_prompt_tools_exclude_provider_managed_tokens(base_deps):
    provider = MockLLMProvider([])
    agent = DummyAgent(
        config=AgentConfig(name="dummy", tools=["add", "web_search"]),
        provider=provider,
        **base_deps,
    )
    assert agent.get_prompt_tool_names() == ["add"]


@pytest.mark.asyncio
async def test_default_agent_prompt_includes_web_search_capability_when_supported(
    base_deps,
):
    provider = MockWebSearchProvider([])
    agent = DefaultAgent(
        config=AgentConfig(name="default", tools=["add", "web_search"]),
        provider=provider,
        **base_deps,
    )

    prompt = await agent.build_system_prompt("")
    assert "# Provider-Managed Capabilities" in prompt
    assert "web_search" in prompt


@pytest.mark.asyncio
async def test_agent_passes_web_search_request_option_to_provider(base_deps):
    provider = MockWebSearchProvider(
        [
            _json_response(
                "notify_user",
                message="done",
                title="Finish quickly",
                thought="Done.",
            )
        ]
    )
    agent = DummyAgent(
        config=AgentConfig(name="dummy", tools=["add", "web_search"]),
        provider=provider,
        **base_deps,
    )
    task = await base_deps["state_manager"].create_task("web")
    await base_deps["state_manager"].transition(task.task_id, TaskState.ROUTING)
    await base_deps["state_manager"].transition(task.task_id, TaskState.WORKING)

    result = await agent.handle_task(task_id=task.task_id, task_description="web")
    assert result == "FINAL: done"
    assert provider.request_options
    option = provider.request_options[0]
    assert option is not None
    assert option.web_search_enabled is True


@pytest.mark.asyncio
async def test_agent_disables_web_search_when_effective_capability_is_unsupported(
    base_deps,
):
    registry = DataRegistry()
    provider = MockWebSearchProvider(
        [
            _json_response(
                "notify_user",
                message="done",
                title="Finish quickly",
                thought="Done.",
            )
        ]
    )
    registry.save_capability_observation(
        provider="google",
        model="gemini-2.5-flash-lite",
        deployment_id="google:gemini-2.5-flash-lite",
        observation={
            "web_search": {
                "status": "unsupported",
                "reason": "runtime_rejected",
                "source": "probe",
                "checked_at": datetime.now(timezone.utc),
            }
        },
    )

    agent = DummyAgent(
        config=AgentConfig(name="dummy", tools=["add", "web_search"]),
        provider=provider,
        data_registry=registry,
        **base_deps,
    )
    task = await base_deps["state_manager"].create_task("web")
    await base_deps["state_manager"].transition(task.task_id, TaskState.ROUTING)
    await base_deps["state_manager"].transition(task.task_id, TaskState.WORKING)

    result = await agent.handle_task(task_id=task.task_id, task_description="web")
    assert result == "FINAL: done"
    assert provider.request_options
    option = provider.request_options[0]
    assert option is not None
    assert option.web_search_enabled is False


@pytest.mark.asyncio
async def test_agent_forces_effort_auto_when_effective_capability_is_unsupported(
    base_deps,
):
    registry = DataRegistry()
    provider = MockLLMProvider(
        [
            _json_response(
                "notify_user",
                message="done",
                title="Finish quickly",
                thought="Done.",
            )
        ],
        model_name="gemini-2.5-flash-lite",
        provider_name="google",
        supports_effort=True,
    )
    registry.save_capability_observation(
        provider="google",
        model="gemini-2.5-flash-lite",
        deployment_id="google:gemini-2.5-flash-lite",
        observation={
            "effort": {
                "status": "unsupported",
                "reason": "runtime_rejected",
                "source": "probe",
                "checked_at": datetime.now(timezone.utc),
            }
        },
    )

    agent = DummyAgent(
        config=AgentConfig(name="dummy", tools=["add"]),
        provider=provider,
        data_registry=registry,
        **base_deps,
    )
    task = await base_deps["state_manager"].create_task("effort")
    await base_deps["state_manager"].transition(task.task_id, TaskState.ROUTING)
    await base_deps["state_manager"].transition(task.task_id, TaskState.WORKING)

    result = await agent.handle_task(
        task_id=task.task_id,
        task_description="effort",
        desired_effort="high",
    )
    assert result == "FINAL: done"
    assert provider.efforts
    assert provider.efforts[0] == "auto"
