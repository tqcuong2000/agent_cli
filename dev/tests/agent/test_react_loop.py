import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel

from agent_cli.core.runtime.agents.base import AgentConfig, BaseAgent
from agent_cli.core.runtime.agents.default import DefaultAgent
from agent_cli.core.runtime.agents.memory import WorkingMemoryManager
from agent_cli.core.runtime.agents.react_loop import PromptBuilder, StuckDetector
from agent_cli.core.runtime.agents.schema import SchemaValidator
from agent_cli.core.infra.events.errors import (
    MaxIterationsExceededError,
    SchemaValidationError,
)
from agent_cli.core.infra.events.event_bus import AbstractEventBus, AsyncEventBus
from agent_cli.core.runtime.orchestrator.state_manager import TaskState, TaskStateManager
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.base.base import BaseLLMProvider, BaseToolFormatter
from agent_cli.core.providers.base.models import (
    LLMResponse,
    ProviderRequestOptions,
    ToolCall,
    ToolCallMode,
)
from agent_cli.core.runtime.tools.ask_user_tool import AskUserTool
from agent_cli.core.runtime.tools.base import BaseTool, ToolCategory
from agent_cli.core.runtime.tools.executor import ToolExecutor
from agent_cli.core.runtime.tools.output_formatter import ToolOutputFormatter
from agent_cli.core.runtime.tools.registry import ToolRegistry
from agent_cli.core.runtime._subprocess import ShellProfile
from agent_cli.core.runtime.services import SystemInfoProvider, SystemInfoSnapshot
from agent_cli.core.ux.interaction.interaction import (
    BaseInteractionHandler,
    UserInteractionRequest,
    UserInteractionResponse,
)

TEST_DATA_REGISTRY = DataRegistry()

# â”€â”€ Mocks â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


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


class MockReadToolArgs(BaseModel):
    path: str
    delay: float = 0.0


class MockParallelReadTool(BaseTool):
    name = "read_file"
    description = "Read mock file content."
    category = ToolCategory.FILE
    is_safe = True
    parallel_safe = True

    @property
    def args_schema(self) -> type[BaseModel]:
        return MockReadToolArgs

    async def execute(self, path: str, delay: float = 0.0, **kwargs: Any) -> str:
        if delay > 0:
            await asyncio.sleep(delay)
        return f"read:{path}"


class MockSequentialReadTool(MockParallelReadTool):
    parallel_safe = False


class AnsweringInteractionHandler(BaseInteractionHandler):
    def __init__(self, *, answer: str = "Balanced") -> None:
        self.answer = answer
        self.requests: List[UserInteractionRequest] = []

    async def request_human_input(
        self,
        request: UserInteractionRequest,
    ) -> UserInteractionResponse:
        self.requests.append(request)
        return UserInteractionResponse(action="answered", feedback=self.answer)

    async def notify(self, message: str) -> None:
        _ = message


class MockLLMProvider(BaseLLMProvider):
    def __init__(
        self,
        responses: List[str],
        *,
        model_name: str = "mock_model",
        provider_name: str = "mock",
        supports_effort: bool = False,
    ):
        super().__init__(model_name, data_registry=TEST_DATA_REGISTRY)
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


class BatchTrackingAgent(DummyAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tool_result_calls: List[tuple[str, str]] = []
        self.batch_calls: List[tuple[List[Any], List[Any]]] = []

    async def on_tool_result(self, tool_name: str, result: str) -> None:
        self.tool_result_calls.append((tool_name, result))

    async def on_batch_complete(self, actions: List[Any], results: List[Any]) -> None:
        self.batch_calls.append((actions, results))


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


class MockUsageLLMProvider(MockLLMProvider):
    async def generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        base = await super().generate(context, tools, max_tokens)
        base.input_tokens = 120
        base.output_tokens = 30
        base.cost_usd = 0.0125
        return base


def _json_response(
    decision_type: str,
    *,
    tool: str = "",
    args: dict | None = None,
    actions: List[Dict[str, Any]] | None = None,
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
    if actions is not None:
        payload["decision"]["actions"] = actions
    if message:
        payload["decision"]["message"] = message
    return json.dumps(payload)


def _parse_tool_envelope(content: str) -> Dict[str, Any]:
    if content.startswith("[tool_result "):
        header, body = content.split("\n", 1)
        body = body.rsplit("\n[/tool_result]", 1)[0]
        attrs: Dict[str, str] = {}
        for part in header[len("[tool_result ") : -1].split():
            key, value = part.split("=", 1)
            attrs[key] = value
        metadata = {
            "task_id": attrs.get("task_id", ""),
            "native_call_id": attrs.get("native_call_id", ""),
            "action_id": attrs.get("action_id", ""),
        }
        metadata = {k: v for k, v in metadata.items() if v}
        return {
            "type": "tool_result",
            "payload": {
                "tool": attrs.get("tool", ""),
                "status": attrs.get("status", ""),
                "truncated": attrs.get("truncated", "false") == "true",
                "truncated_chars": int(attrs.get("truncated_chars", "0")),
                "output": body,
            },
            "metadata": metadata,
        }

    return json.loads(content)


# â”€â”€ Fixtures â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _build_deps(
    registry: ToolRegistry,
    *,
    multi_action_enabled: bool = False,
) -> Dict[str, Any]:
    event_bus = AsyncEventBus()
    state_manager = TaskStateManager(event_bus)
    output_formatter = ToolOutputFormatter(data_registry=TEST_DATA_REGISTRY)

    tool_executor = ToolExecutor(
        registry=registry,
        event_bus=event_bus,
        output_formatter=output_formatter,
        auto_approve=True,
        data_registry=TEST_DATA_REGISTRY,
    )

    schema_validator = SchemaValidator(
        registry.get_all_names(),
        data_registry=TEST_DATA_REGISTRY,
        multi_action_enabled=multi_action_enabled,
    )
    memory_manager = WorkingMemoryManager(data_registry=TEST_DATA_REGISTRY)
    prompt_builder = PromptBuilder(registry, data_registry=TEST_DATA_REGISTRY)

    from agent_cli.core.infra.config.config import AgentSettings

    settings = AgentSettings()
    settings.max_iterations = 100

    return {
        "tool_executor": tool_executor,
        "schema_validator": schema_validator,
        "memory_manager": memory_manager,
        "event_bus": event_bus,
        "state_manager": state_manager,
        "prompt_builder": prompt_builder,
        "data_registry": TEST_DATA_REGISTRY,
        "settings": settings,
    }


@pytest.fixture
def base_deps():
    registry = ToolRegistry()
    registry.register(MockMathTool())
    return _build_deps(registry)


# â”€â”€ Integration Tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


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
    assert trace["payload"]["action_id"] == "call_1"
    assert trace["metadata"]["native_call_id"] == "call_1"


def test_format_assistant_history_assigns_fallback_action_ids_for_multi_native_calls():
    response = LLMResponse(
        text_content="",
        tool_mode=ToolCallMode.NATIVE,
        tool_calls=[
            ToolCall(tool_name="read_file", arguments={"path": "a.txt"}, native_call_id=""),
            ToolCall(tool_name="grep_search", arguments={"query": "TODO"}, native_call_id=""),
        ],
    )

    formatted = BaseAgent._format_assistant_history(response)
    traces = [json.loads(line) for line in formatted.splitlines()]
    assert traces[0]["payload"]["action_id"] == "act_0"
    assert traces[1]["payload"]["action_id"] == "act_1"


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
        _parse_tool_envelope(m["content"]) for m in memory if m.get("role") == "tool"
    ]
    assert any(p.get("type") == "tool_result" for p in tool_payloads)
    assert any(p.get("payload", {}).get("tool") == "add" for p in tool_payloads)


@pytest.mark.asyncio
async def test_react_loop_execute_actions_dispatches_batch(base_deps):
    mock_responses = [
        _json_response(
            "execute_actions",
            actions=[
                {"tool": "add", "args": {"x": 2, "y": 3}},
                {"tool": "add", "args": {"x": 10, "y": 5}},
            ],
            title="Run independent tool calls",
            thought="I can execute both actions in one batch.",
        ),
        _json_response(
            "notify_user",
            message="Batch completed.",
            title="Finalize",
            thought="Done",
        ),
    ]
    provider = MockLLMProvider(mock_responses)
    deps = dict(base_deps)
    deps["schema_validator"] = SchemaValidator(
        deps["tool_executor"].registry.get_all_names(),
        data_registry=TEST_DATA_REGISTRY,
        multi_action_enabled=True,
    )
    config = AgentConfig(name="dummy", tools=["add"], multi_action_enabled=True)
    agent = BatchTrackingAgent(config=config, provider=provider, **deps)

    task = await deps["state_manager"].create_task("Batch add")
    await deps["state_manager"].transition(task.task_id, TaskState.ROUTING)
    await deps["state_manager"].transition(task.task_id, TaskState.WORKING)

    result = await agent.handle_task(
        task_id=task.task_id,
        task_description="Add numbers in parallel",
    )

    assert result == "FINAL: Batch completed."
    assert len(agent.tool_result_calls) == 2
    assert len(agent.batch_calls) == 1

    tool_messages = [
        m for m in deps["memory_manager"].get_working_context() if m.get("role") == "tool"
    ]
    assert len(tool_messages) == 2
    envelopes = [_parse_tool_envelope(m["content"]) for m in tool_messages]
    assert [e["metadata"]["action_id"] for e in envelopes] == ["act_0", "act_1"]


@pytest.mark.asyncio
async def test_react_loop_multi_action_parallel_read_fanout_e2e():
    responses = [
        _json_response(
            "execute_actions",
            actions=[
                {"tool": "read_file", "args": {"path": "a.txt", "delay": 0.20}},
                {"tool": "read_file", "args": {"path": "b.txt", "delay": 0.20}},
                {"tool": "read_file", "args": {"path": "c.txt", "delay": 0.20}},
            ],
            title="Read all files",
            thought="Fan out independent reads.",
        ),
        _json_response(
            "notify_user",
            message="All reads done.",
            title="Complete",
            thought="Collected all file outputs.",
        ),
    ]

    parallel_registry = ToolRegistry()
    parallel_registry.register(MockParallelReadTool())
    parallel_deps = _build_deps(parallel_registry, multi_action_enabled=True)
    parallel_provider = MockLLMProvider(list(responses))
    parallel_agent = BatchTrackingAgent(
        config=AgentConfig(
            name="dummy",
            tools=["read_file"],
            multi_action_enabled=True,
            max_concurrent_actions=3,
        ),
        provider=parallel_provider,
        **parallel_deps,
    )

    parallel_task = await parallel_deps["state_manager"].create_task("Fanout reads")
    await parallel_deps["state_manager"].transition(parallel_task.task_id, TaskState.ROUTING)
    await parallel_deps["state_manager"].transition(parallel_task.task_id, TaskState.WORKING)
    started = time.perf_counter()
    result = await parallel_agent.handle_task(
        task_id=parallel_task.task_id,
        task_description="Read a,b,c files",
    )
    parallel_elapsed = time.perf_counter() - started

    sequential_registry = ToolRegistry()
    sequential_registry.register(MockSequentialReadTool())
    sequential_deps = _build_deps(sequential_registry, multi_action_enabled=True)
    sequential_provider = MockLLMProvider(list(responses))
    sequential_agent = BatchTrackingAgent(
        config=AgentConfig(
            name="dummy",
            tools=["read_file"],
            multi_action_enabled=True,
            max_concurrent_actions=3,
        ),
        provider=sequential_provider,
        **sequential_deps,
    )
    sequential_task = await sequential_deps["state_manager"].create_task("Sequential reads")
    await sequential_deps["state_manager"].transition(
        sequential_task.task_id,
        TaskState.ROUTING,
    )
    await sequential_deps["state_manager"].transition(
        sequential_task.task_id,
        TaskState.WORKING,
    )
    started = time.perf_counter()
    seq_result = await sequential_agent.handle_task(
        task_id=sequential_task.task_id,
        task_description="Read a,b,c files",
    )
    sequential_elapsed = time.perf_counter() - started

    assert result == "FINAL: All reads done."
    assert seq_result == "FINAL: All reads done."
    assert len(parallel_agent.tool_result_calls) == 3
    assert len(parallel_agent.batch_calls) == 1
    assert parallel_elapsed < sequential_elapsed * 0.75
    assert sequential_elapsed >= 0.55

    tool_messages = [
        m
        for m in parallel_deps["memory_manager"].get_working_context()
        if m.get("role") == "tool"
    ]
    assert len(tool_messages) == 3


@pytest.mark.asyncio
async def test_react_loop_multi_action_enforces_ask_user_singleton_e2e(
    caplog: pytest.LogCaptureFixture,
):
    registry = ToolRegistry()
    registry.register(MockMathTool())
    registry.register(AskUserTool())
    deps = _build_deps(registry, multi_action_enabled=True)
    interaction_handler = AnsweringInteractionHandler(answer="Option B")
    deps["tool_executor"].set_interaction_handler(interaction_handler)

    mock_responses = [
        _json_response(
            "execute_actions",
            actions=[
                {
                    "tool": "ask_user",
                    "args": {
                        "question": "Which mode?",
                        "options": ["Option A", "Option B"],
                    },
                },
                {"tool": "add", "args": {"x": 1, "y": 2}},
            ],
            title="Need clarification first",
            thought="ask_user must run alone.",
        ),
        _json_response(
            "notify_user",
            message="Clarification captured.",
            title="Complete",
            thought="Done",
        ),
    ]
    provider = MockLLMProvider(mock_responses)
    agent = BatchTrackingAgent(
        config=AgentConfig(
            name="dummy",
            tools=["ask_user", "add"],
            multi_action_enabled=True,
        ),
        provider=provider,
        **deps,
    )

    task = await deps["state_manager"].create_task("Ask then continue")
    await deps["state_manager"].transition(task.task_id, TaskState.ROUTING)
    await deps["state_manager"].transition(task.task_id, TaskState.WORKING)

    result = await agent.handle_task(
        task_id=task.task_id,
        task_description="Ask a clarification",
    )

    assert result == "FINAL: Clarification captured."
    assert len(interaction_handler.requests) == 1
    assert len(agent.tool_result_calls) == 1
    assert agent.tool_result_calls[0][0] == "ask_user"
    tool_messages = [
        m for m in deps["memory_manager"].get_working_context() if m.get("role") == "tool"
    ]
    assert len(tool_messages) == 1
    payload = _parse_tool_envelope(tool_messages[0]["content"])["payload"]
    assert payload["tool"] == "ask_user"
    assert "stripping batch to ask_user only" in caplog.text


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
    assert any("SCHEMA_ERROR|code=missing_field|field=decision" in m["content"] for m in mem)
    assert any("Valid example:" in m["content"] for m in mem)


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
    assert exc.value.error_id == "agent.max_iterations_exceeded"


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


@pytest.mark.asyncio
async def test_react_loop_reflect_messages_include_counts_and_budget_summary(base_deps):
    mock_responses = [
        _json_response("reflect", title="Think", thought="Plan first"),
        _json_response("reflect", title="Think more", thought="Still planning"),
        _json_response(
            "notify_user",
            message="Done.",
            title="Complete",
            thought="Finished planning.",
        ),
    ]
    provider = MockUsageLLMProvider(mock_responses)
    agent = DummyAgent(
        config=AgentConfig(name="dummy", tools=["add"]),
        provider=provider,
        **base_deps,
    )

    task = await base_deps["state_manager"].create_task("Need reflection")
    await base_deps["state_manager"].transition(task.task_id, TaskState.ROUTING)
    await base_deps["state_manager"].transition(task.task_id, TaskState.WORKING)

    result = await agent.handle_task(
        task_id=task.task_id,
        task_description="Need reflection",
    )
    assert result == "FINAL: Done."

    system_messages = [
        m["content"]
        for m in base_deps["memory_manager"].get_working_context()
        if m.get("role") == "system"
    ]
    assert any("Reasoning noted (1/3 reflects used)." in msg for msg in system_messages)
    assert any("context ~" in msg for msg in system_messages)
    assert any(
        "Reasoning noted (2/3 reflects used)." in msg
        and "You must act or respond on your next turn." in msg
        for msg in system_messages
    )


def test_stuck_detector_detects_repeated_batch_order_independently():
    detector = StuckDetector(threshold=2, history_cap=5)

    batch_a = [("read_file", "alpha"), ("grep_search", "beta")]
    batch_b = [("grep_search", "beta"), ("read_file", "alpha")]

    assert detector.is_stuck_batch(batch_a) is False
    assert detector.is_stuck_batch(batch_b) is True


def test_stuck_detector_batch_reset_clears_batch_history():
    detector = StuckDetector(threshold=2, history_cap=5)
    batch = [("read_file", "alpha"), ("grep_search", "beta")]

    assert detector.is_stuck_batch(batch) is False
    detector.reset()
    assert detector.is_stuck_batch(batch) is False


def test_stuck_detector_normalizes_lean_tool_result() -> None:
    lean = "[tool_result tool=read_file status=success truncated=false truncated_chars=0]\nhello\n[/tool_result]"
    expected = (
        '{"output":"hello","status":"success","truncated":false,"truncated_chars":0}'
    )
    assert StuckDetector._normalize_result_for_stuck_check(lean) == expected


def test_prompt_builder_adds_ask_user_policy_when_tool_available():
    registry = ToolRegistry()
    registry.register(MockMathTool())
    registry.register(AskUserTool())
    prompt_builder = PromptBuilder(registry, data_registry=TEST_DATA_REGISTRY)

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
    prompt_builder = PromptBuilder(registry, data_registry=TEST_DATA_REGISTRY)

    prompt = prompt_builder.build(
        persona="You are a tester.",
        tool_names=["add"],
    )
    assert "# Clarification Policy" not in prompt


def test_prompt_builder_renders_title_word_limit_from_template():
    registry = ToolRegistry()
    registry.register(MockMathTool())
    prompt_builder = PromptBuilder(registry, data_registry=TEST_DATA_REGISTRY)

    prompt = prompt_builder.build(
        persona="You are a tester.",
        tool_names=["add"],
    )

    assert "{title_max_words}" not in prompt
    assert "1 to 15 words" in prompt


def test_prompt_builder_renders_system_information_section():
    registry = ToolRegistry()
    registry.register(MockMathTool())
    prompt_builder = PromptBuilder(registry, data_registry=TEST_DATA_REGISTRY)
    system_info = SystemInfoSnapshot(
        operating_system="Windows 11",
        architecture="AMD64",
        python_version="3.13.7",
        local_date="2026-03-07",
        timezone="ICT (UTC+07:00)",
        workspace_root="X:\\agent_cli",
        command_working_directory="X:\\agent_cli",
        shell_name="Windows PowerShell",
        shell_executable="powershell.exe",
    )

    prompt = prompt_builder.build(
        persona="You are a tester.",
        tool_names=["add"],
        system_info=system_info,
    )

    assert "# System Information" in prompt
    assert "Operating system: Windows 11" in prompt
    assert "Command shell: Windows PowerShell" in prompt
    assert "Shell executable: powershell.exe" in prompt


def test_prompt_builder_switches_native_vs_prompt_json_output_template():
    registry = ToolRegistry()
    registry.register(MockMathTool())
    prompt_builder = PromptBuilder(registry, data_registry=TEST_DATA_REGISTRY)

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


def test_prompt_builder_switches_multi_action_output_template():
    registry = ToolRegistry()
    registry.register(MockMathTool())
    prompt_builder = PromptBuilder(registry, data_registry=TEST_DATA_REGISTRY)

    prompt_json = prompt_builder.build(
        persona="You are a tester.",
        tool_names=["add"],
        multi_action=True,
    )
    native_prompt = prompt_builder.build(
        persona="You are a tester.",
        tool_names=["add"],
        native_tool_mode=True,
        multi_action=True,
    )

    assert "execute_actions" in prompt_json
    assert "Wait for ALL Results" in prompt_json
    assert "execute_actions" in native_prompt
    assert "native function-calling" in native_prompt


def test_prompt_builder_renders_provider_managed_capabilities_section():
    registry = ToolRegistry()
    registry.register(MockMathTool())
    prompt_builder = PromptBuilder(registry, data_registry=TEST_DATA_REGISTRY)

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
async def test_default_agent_prompt_includes_system_information(base_deps):
    provider = MockLLMProvider([])
    agent = DefaultAgent(
        config=AgentConfig(name="default", tools=["add"]),
        provider=provider,
        system_info_provider=SystemInfoProvider(
            workspace_root=Path("X:/agent_cli"),
            shell_profile=ShellProfile(
                executable="powershell.exe",
                flavor="powershell",
                display_name="Windows PowerShell",
            ),
        ),
        **base_deps,
    )

    prompt = await agent.build_system_prompt("")
    assert "# System Information" in prompt
    assert "Command shell: Windows PowerShell" in prompt


@pytest.mark.asyncio
async def test_default_agent_prompt_uses_multi_action_template_when_enabled(base_deps):
    provider = MockLLMProvider([])
    agent = DefaultAgent(
        config=AgentConfig(name="default", tools=["add"], multi_action_enabled=True),
        provider=provider,
        **base_deps,
    )

    prompt = await agent.build_system_prompt("")
    assert "execute_actions" in prompt
    assert "Wait for ALL Results" in prompt


def test_schema_recovery_message_for_unknown_ask_user_is_machine_actionable(base_deps):
    provider = MockLLMProvider([])
    agent = DummyAgent(
        config=AgentConfig(name="dummy", tools=["add"], multi_action_enabled=True),
        provider=provider,
        **base_deps,
    )
    message = agent._build_schema_recovery_message(
        SchemaValidationError(
            "Unknown decision.type. Allowed values: reflect, execute_action, execute_actions, notify_user, yield.",
            raw_response='{"title":"x","thought":"y","decision":{"type":"ask_user","question":"q"}}',
        )
    )
    assert "SCHEMA_ERROR|code=enum_unknown|field=decision.type" in message
    assert "received=ask_user" in message
    assert 'decision.tool="ask_user"' in message
    assert '"tool":"ask_user"' in message
    assert '"type":"yield"' in message


def test_schema_recovery_message_for_generic_schema_error_has_safe_fallback(base_deps):
    provider = MockLLMProvider([])
    agent = DummyAgent(
        config=AgentConfig(name="dummy", tools=["add"], multi_action_enabled=False),
        provider=provider,
        **base_deps,
    )
    message = agent._build_schema_recovery_message(SchemaValidationError("bad schema"))
    assert "SCHEMA_ERROR|code=schema_invalid|field=response" in message
    assert "If you are still uncertain, return this fallback JSON exactly:" in message
    assert '"type":"yield"' in message


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
    deps = dict(base_deps)
    deps["data_registry"] = registry

    agent = DummyAgent(
        config=AgentConfig(name="dummy", tools=["add", "web_search"]),
        provider=provider,
        **deps,
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
    deps = dict(base_deps)
    deps["data_registry"] = registry

    agent = DummyAgent(
        config=AgentConfig(name="dummy", tools=["add"]),
        provider=provider,
        **deps,
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

