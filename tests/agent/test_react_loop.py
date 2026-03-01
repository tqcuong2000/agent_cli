import asyncio
from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel

from agent_cli.agent.base import AgentConfig, BaseAgent, EffortLevel
from agent_cli.agent.memory import WorkingMemoryManager
from agent_cli.agent.react_loop import PromptBuilder
from agent_cli.agent.schema import SchemaValidator
from agent_cli.core.error_handler.errors import MaxIterationsExceededError
from agent_cli.core.events.event_bus import AsyncEventBus
from agent_cli.core.state.state_manager import TaskState, TaskStateManager
from agent_cli.providers.base import BaseLLMProvider
from agent_cli.providers.models import LLMRequest, LLMResponse, ToolCallMode
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
    def __init__(self, responses: List[str]):
        super().__init__("mock_model")
        self.responses = responses
        self.call_count = 0
        self.requests = []

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def supports_native_tools(self) -> bool:
        return False

    def _create_tool_formatter(self):
        return None

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request.to_message_dicts())
        if self.call_count >= len(self.responses):
            text = "<final_answer>Out of mock responses.</final_answer>"
        else:
            text = self.responses[self.call_count]
            self.call_count += 1

        return LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    def get_buffered_response(self) -> LLMResponse:
        return LLMResponse(text_content="buffered", tool_mode=ToolCallMode.XML)

    async def safe_generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> LLMResponse:
        # Override safe_generate directly since we mock everything
        request = LLMRequest(messages=[])
        return await self.generate(request)

    async def stream(self, request: LLMRequest):
        yield None

    async def check_health(self) -> bool:
        return True


class DummyAgent(BaseAgent):
    async def build_system_prompt(self, task_context: str) -> str:
        return self.prompt_builder.build(
            persona="You are a dummy.",
            tool_names=self.config.tools,
            effort_constraints={"reasoning_instruction": "Just do it."},
        )

    async def on_tool_result(self, tool_name: str, result: str) -> None:
        pass

    async def on_final_answer(self, answer: str) -> str:
        return f"FINAL: {answer}"


def _reasoning(title: str, thoughts: str) -> str:
    return f"<title>{title}</title>\n<thinking>{thoughts}</thinking>"


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
    # Force standard constraints for tests so local config.toml doesn't break them
    settings.core["effort"] = {
        "LOW": {"max_iterations": 30},
        "MEDIUM": {"max_iterations": 50},
        "HIGH": {"max_iterations": 100},
        "XHIGH": {"max_iterations": 250},
    }

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


@pytest.mark.asyncio
async def test_react_loop_successful_task(base_deps):
    """Test a full ReAct loop where the agent thinks, uses a tool,
    and returns a final answer."""

    mock_responses = [
        # Iteration 1: Call 'add' tool
        _reasoning("Compute sum using add tool call", "I should add.") + "\n"
        '<action>\n  <tool>add</tool>\n  <args>{"x": 2, "y": 3}</args>\n</action>',
        # Iteration 2: Return final answer
        _reasoning("Return concise final answer to user", "Got the result.") + "\n"
        "<final_answer>The answer is 5.</final_answer>",
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
    assert any("[Tool: add] Result:" in m["content"] for m in memory)


@pytest.mark.asyncio
async def test_react_loop_schema_correction(base_deps):
    """Test that the agent recovers from a malformed schema response
    by receiving the error and trying again."""

    mock_responses = [
        # Iteration 1: Malformed action (missing <tool>)
        _reasoning("Detect malformed action and recover quickly", "Oops") + "\n"
        "<action>\n  <args>{}</args>\n</action>",
        # Iteration 2: Correct format
        _reasoning("Provide corrected response after feedback", "Let me fix that.")
        + "\n"
        "<final_answer>I fixed it.</final_answer>",
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


@pytest.mark.asyncio
async def test_react_loop_max_iterations(base_deps):
    """Test that the agent raises MaxIterationsExceededError if it
    loops too many times without a final answer."""

    # Always returning an action (infinite loop scenario)
    infinite_action = (
        _reasoning("Repeat same action to test max iterations", "Looping") + "\n"
        '<action>\n  <tool>add</tool>\n  <args>{"x": 1, "y": 1}</args>\n</action>'
    )

    # Feed 35 copies of the same action
    # We set LOW effort so max_iterations is 30.
    provider = MockLLMProvider([infinite_action] * 35)
    config = AgentConfig(name="dummy", tools=["add"], effort_level=EffortLevel.LOW)

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

    same_action = (
        _reasoning("Repeat identical action to trigger stuck hint", "Stuck") + "\n"
        '<action>\n  <tool>add</tool>\n  <args>{"x": 0, "y": 0}</args>\n</action>'
    )

    # 3 repetitions triggers the stuck detector warning for the 4th iteration
    mock_responses = [
        same_action,  # iter 1 -> result 0
        same_action,  # iter 2 -> result 0
        same_action,  # iter 3 -> result 0, triggers stuck detection!
        _reasoning("Acknowledge loop and finish with answer", "Oh")
        + "<final_answer>I was stuck.</final_answer>",
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
        effort_constraints={"reasoning_instruction": "Think carefully."},
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
        effort_constraints={"reasoning_instruction": "Think carefully."},
    )
    assert "# Clarification Policy" not in prompt
