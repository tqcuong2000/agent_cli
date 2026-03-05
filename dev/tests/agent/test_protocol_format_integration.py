from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel

from agent_cli.core.infra.config.config import AgentSettings
from agent_cli.core.infra.events.event_bus import AbstractEventBus, AsyncEventBus
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.base.base import BaseLLMProvider, BaseToolFormatter
from agent_cli.core.providers.base.models import LLMResponse, ProviderRequestOptions, ToolCallMode
from agent_cli.core.runtime.agents.base import AgentConfig, BaseAgent
from agent_cli.core.runtime.agents.memory import WorkingMemoryManager
from agent_cli.core.runtime.agents.react_loop import PromptBuilder
from agent_cli.core.runtime.agents.schema import SchemaValidator
from agent_cli.core.runtime.orchestrator.state_manager import TaskState, TaskStateManager
from agent_cli.core.runtime.tools.base import BaseTool, ToolCategory
from agent_cli.core.runtime.tools.executor import ToolExecutor
from agent_cli.core.runtime.tools.output_formatter import ToolOutputFormatter
from agent_cli.core.runtime.tools.registry import ToolRegistry

TEST_DATA_REGISTRY = DataRegistry()


def _parse_tool_envelope(content: str) -> Dict[str, Any]:
    if content.startswith("[tool_result "):
        header, body = content.split("\n", 1)
        body = body.rsplit("\n[/tool_result]", 1)[0]
        attrs: Dict[str, str] = {}
        for part in header[len("[tool_result ") : -1].split():
            key, value = part.split("=", 1)
            attrs[key] = value
        return {
            "payload": {
                "tool": attrs.get("tool", ""),
                "status": attrs.get("status", ""),
                "truncated": attrs.get("truncated", "false") == "true",
                "truncated_chars": int(attrs.get("truncated_chars", "0")),
                "total_chars": int(attrs.get("total_chars", "0"))
                if "total_chars" in attrs
                else 0,
                "total_lines": int(attrs.get("total_lines", "0"))
                if "total_lines" in attrs
                else 0,
                "output": body,
            },
            "metadata": {
                "content_ref": attrs.get("content_ref", ""),
                "batch_id": attrs.get("batch_id", ""),
                "action_id": attrs.get("action_id", ""),
            },
        }

    parsed = json.loads(content)
    payload = parsed.get("payload", {})
    metadata = parsed.get("metadata", {})
    return {
        "payload": {
            "tool": str(payload.get("tool", "")),
            "status": str(payload.get("status", "")),
            "truncated": bool(payload.get("truncated", False)),
            "truncated_chars": int(payload.get("truncated_chars", 0)),
            "total_chars": int(payload.get("total_chars", 0)),
            "total_lines": int(payload.get("total_lines", 0)),
            "output": str(payload.get("output", "")),
        },
        "metadata": {
            "content_ref": str(metadata.get("content_ref", "")),
            "batch_id": str(metadata.get("batch_id", "")),
            "action_id": str(metadata.get("action_id", "")),
        },
    }


class _NoopToolFormatter(BaseToolFormatter):
    def format_for_native_fc(self, tools: List[Dict[str, Any]]) -> Any:
        return tools

    def format_for_prompt_injection(self, tools: List[Dict[str, Any]]) -> str:
        return ""


class _PathArgs(BaseModel):
    path: str


class _WriteArgs(BaseModel):
    path: str
    content: str


class _ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read fixture content."
    category = ToolCategory.FILE
    is_safe = True

    def __init__(self, content: str) -> None:
        self._content = content

    @property
    def args_schema(self) -> type[BaseModel]:
        return _PathArgs

    async def execute(self, path: str, **kwargs: Any) -> str:
        _ = path, kwargs
        return self._content


class _WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write fixture content."
    category = ToolCategory.FILE
    is_safe = True
    parallel_safe = False

    def __init__(self) -> None:
        self.writes: Dict[str, str] = {}

    @property
    def args_schema(self) -> type[BaseModel]:
        return _WriteArgs

    async def execute(self, path: str, content: str, **kwargs: Any) -> str:
        _ = kwargs
        self.writes[path] = content
        return f"WROTE {path} ({len(content)} chars)"


class _SearchTool(BaseTool):
    name = "search_files"
    description = "Parallel-safe mock search."
    category = ToolCategory.SEARCH
    is_safe = True
    parallel_safe = True

    @property
    def args_schema(self) -> type[BaseModel]:
        return _PathArgs

    async def execute(self, path: str, **kwargs: Any) -> str:
        _ = kwargs
        return f"SEARCH:{path}"


class _ListTool(BaseTool):
    name = "list_directory"
    description = "Parallel-safe mock list."
    category = ToolCategory.FILE
    is_safe = True
    parallel_safe = True

    @property
    def args_schema(self) -> type[BaseModel]:
        return _PathArgs

    async def execute(self, path: str, **kwargs: Any) -> str:
        _ = kwargs
        return f"LIST:{path}"


class _ScenarioProvider(BaseLLMProvider):
    def __init__(self) -> None:
        super().__init__("mock-model", data_registry=TEST_DATA_REGISTRY)
        self.context_calls: List[List[Dict[str, Any]]] = []
        self._call_count = 0

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def supports_native_tools(self) -> bool:
        return False

    def _create_tool_formatter(self) -> BaseToolFormatter:
        return _NoopToolFormatter()

    async def generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
        effort: str | None = None,
        request_options: ProviderRequestOptions | None = None,
    ) -> LLMResponse:
        _ = tools, max_tokens, effort, request_options
        self.context_calls.append([dict(msg) for msg in context])
        self._call_count += 1

        if self._call_count == 1:
            payload = {
                "title": "Read source content",
                "thought": "I need the source content first.",
                "decision": {"type": "execute_action", "tool": "read_file", "args": {"path": "src.txt"}},
            }
        elif self._call_count == 2:
            content_ref = self._extract_latest_content_ref(context)
            payload = {
                "title": "Write via reference",
                "thought": "Reuse previous output by content_ref.",
                "decision": {
                    "type": "execute_action",
                    "tool": "write_file",
                    "args": {"path": "out.txt", "content_ref": content_ref},
                },
            }
        elif self._call_count == 3:
            payload = {
                "title": "Run independent checks",
                "thought": "Run two independent read-only checks in parallel.",
                "decision": {
                    "type": "execute_actions",
                    "actions": [
                        {"tool": "search_files", "args": {"path": "src"}},
                        {"tool": "list_directory", "args": {"path": "."}},
                    ],
                },
            }
        elif self._call_count == 4:
            payload = {
                "title": "Reflect once",
                "thought": "Confirm the protocol-level artifacts before finishing.",
                "decision": {"type": "reflect"},
            }
        else:
            payload = {
                "thought": "Completed protocol verification with all checks passed.",
                "decision": {
                    "type": "notify_user",
                    "message": "Protocol flow complete.",
                    "intent": "report",
                },
            }

        return LLMResponse(
            text_content=json.dumps(payload),
            tool_mode=ToolCallMode.PROMPT_JSON,
            input_tokens=120,
            output_tokens=30,
            cost_usd=0.0125,
        )

    async def safe_generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
        max_retries: int = 3,
        task_id: str = "",
        event_bus: Optional[AbstractEventBus] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        _ = max_retries, task_id, event_bus, kwargs
        return await self.generate(context=context, tools=tools, max_tokens=max_tokens)

    async def stream(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
        effort: str | None = None,
        request_options: ProviderRequestOptions | None = None,
    ):
        _ = context, tools, max_tokens, effort, request_options
        yield None

    def get_buffered_response(self) -> LLMResponse:
        return LLMResponse(text_content="", tool_mode=ToolCallMode.PROMPT_JSON)

    @staticmethod
    def _extract_latest_content_ref(context: List[Dict[str, Any]]) -> str:
        for msg in reversed(context):
            if msg.get("role") != "tool":
                continue
            raw_content = str(msg.get("content", ""))
            parsed = _parse_tool_envelope(raw_content)
            ref = parsed.get("metadata", {}).get("content_ref", "")
            if ref:
                return str(ref)
        return ""


class _ReplayProvider(BaseLLMProvider):
    def __init__(self) -> None:
        super().__init__("mock-model", data_registry=TEST_DATA_REGISTRY)
        self.context_calls: List[List[Dict[str, Any]]] = []

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def supports_native_tools(self) -> bool:
        return False

    def _create_tool_formatter(self) -> BaseToolFormatter:
        return _NoopToolFormatter()

    async def generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
        effort: str | None = None,
        request_options: ProviderRequestOptions | None = None,
    ) -> LLMResponse:
        _ = tools, max_tokens, effort, request_options
        self.context_calls.append([dict(msg) for msg in context])
        payload = {
            "title": "Continue session",
            "thought": "Legacy history replay loaded successfully.",
            "decision": {"type": "notify_user", "message": "Replay ok."},
        }
        return LLMResponse(
            text_content=json.dumps(payload),
            tool_mode=ToolCallMode.PROMPT_JSON,
            input_tokens=50,
            output_tokens=15,
            cost_usd=0.005,
        )

    async def safe_generate(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
        max_retries: int = 3,
        task_id: str = "",
        event_bus: Optional[AbstractEventBus] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        _ = max_retries, task_id, event_bus, kwargs
        return await self.generate(context=context, tools=tools, max_tokens=max_tokens)

    async def stream(
        self,
        context: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 4096,
        effort: str | None = None,
        request_options: ProviderRequestOptions | None = None,
    ):
        _ = context, tools, max_tokens, effort, request_options
        yield None

    def get_buffered_response(self) -> LLMResponse:
        return LLMResponse(text_content="", tool_mode=ToolCallMode.PROMPT_JSON)


class _IntegrationAgent(BaseAgent):
    async def build_system_prompt(self, task_context: str) -> str:
        _ = task_context
        return self.prompt_builder.build(
            persona="You are an integration test agent.",
            tool_names=self.get_prompt_tool_names(),
            multi_action=self.config.multi_action_enabled,
        )

    async def on_tool_result(self, tool_name: str, result: str) -> None:
        _ = tool_name, result

    async def on_final_answer(self, answer: str) -> str:
        return answer


def _build_agent(
    *,
    provider: BaseLLMProvider,
    registry: ToolRegistry,
    multi_action_enabled: bool,
) -> tuple[_IntegrationAgent, TaskStateManager]:
    event_bus = AsyncEventBus()
    state_manager = TaskStateManager(event_bus)
    output_formatter = ToolOutputFormatter(max_output_length=120, data_registry=TEST_DATA_REGISTRY)
    output_formatter.lean_envelope = True

    tool_executor = ToolExecutor(
        registry=registry,
        event_bus=event_bus,
        output_formatter=output_formatter,
        auto_approve=True,
        data_registry=TEST_DATA_REGISTRY,
    )
    validator = SchemaValidator(
        registry.get_all_names(),
        data_registry=TEST_DATA_REGISTRY,
        multi_action_enabled=multi_action_enabled,
    )
    settings = AgentSettings()
    settings.max_iterations = 25
    memory = WorkingMemoryManager(data_registry=TEST_DATA_REGISTRY)
    prompt_builder = PromptBuilder(registry, data_registry=TEST_DATA_REGISTRY)

    tools = registry.get_all_names()
    agent = _IntegrationAgent(
        config=AgentConfig(
            name="integration",
            tools=tools,
            multi_action_enabled=multi_action_enabled,
            max_concurrent_actions=4,
        ),
        provider=provider,
        tool_executor=tool_executor,
        schema_validator=validator,
        memory_manager=memory,
        event_bus=event_bus,
        state_manager=state_manager,
        prompt_builder=prompt_builder,
        data_registry=TEST_DATA_REGISTRY,
        settings=settings,
    )
    return agent, state_manager


@pytest.mark.asyncio
async def test_protocol_full_session_integration_with_all_improvements() -> None:
    read_content = "\n".join(f"line-{idx:03d}" for idx in range(1, 80))
    write_tool = _WriteFileTool()
    registry = ToolRegistry()
    registry.register(_ReadFileTool(read_content))
    registry.register(write_tool)
    registry.register(_SearchTool())
    registry.register(_ListTool())

    provider = _ScenarioProvider()
    agent, state_manager = _build_agent(
        provider=provider,
        registry=registry,
        multi_action_enabled=True,
    )

    task = await state_manager.create_task("run full protocol integration")
    await state_manager.transition(task.task_id, TaskState.ROUTING)
    await state_manager.transition(task.task_id, TaskState.WORKING)
    final = await agent.handle_task(
        task_id=task.task_id,
        task_description="run full protocol integration",
    )

    assert final == "Protocol flow complete."
    assert write_tool.writes.get("out.txt") == read_content

    memory = agent.memory.get_working_context()
    tool_messages = [m for m in memory if m.get("role") == "tool"]
    parsed_tools = [_parse_tool_envelope(str(m.get("content", ""))) for m in tool_messages]

    read_results = [p for p in parsed_tools if p["payload"]["tool"] == "read_file"]
    assert len(read_results) == 1
    assert read_results[0]["payload"]["truncated"] is True
    assert read_results[0]["payload"]["total_chars"] > 0
    assert read_results[0]["payload"]["total_lines"] > 0
    assert read_results[0]["metadata"]["content_ref"].startswith("sha256:")

    batch_results = [
        p
        for p in parsed_tools
        if p["payload"]["tool"] in {"search_files", "list_directory"}
    ]
    assert len(batch_results) == 2
    batch_ids = {item["metadata"]["batch_id"] for item in batch_results}
    assert len(batch_ids) == 1
    only_batch_id = next(iter(batch_ids))
    assert only_batch_id.startswith("batch_")

    system_messages = [
        str(msg.get("content", ""))
        for msg in memory
        if msg.get("role") == "system"
    ]
    assert any("Reasoning noted (1/3 reflects used)." in msg for msg in system_messages)
    assert any("context ~" in msg for msg in system_messages)

    assert agent.get_last_task_title().startswith("Completed protocol verification with all")
    assert agent.get_last_task_title().endswith("...")


@pytest.mark.asyncio
async def test_legacy_json_tool_result_replay_remains_compatible() -> None:
    registry = ToolRegistry()
    provider = _ReplayProvider()
    agent, state_manager = _build_agent(
        provider=provider,
        registry=registry,
        multi_action_enabled=False,
    )

    legacy_tool_result = json.dumps(
        {
            "id": "msg_old_1",
            "type": "tool_result",
            "version": "1.0",
            "timestamp": "2026-03-01T00:00:00Z",
            "payload": {
                "tool": "read_file",
                "status": "success",
                "truncated": False,
                "truncated_chars": 0,
                "output": "legacy output text",
            },
            "metadata": {"task_id": "old_task"},
        },
        separators=(",", ":"),
    )
    prior_session = [
        {"role": "system", "content": "old system prompt"},
        {"role": "user", "content": "initial question"},
        {"role": "assistant", "content": "initial answer"},
        {"role": "tool", "content": legacy_tool_result},
    ]

    task = await state_manager.create_task("resume legacy replay")
    await state_manager.transition(task.task_id, TaskState.ROUTING)
    await state_manager.transition(task.task_id, TaskState.WORKING)
    final = await agent.handle_task(
        task_id=task.task_id,
        task_description="resume legacy replay",
        session_messages=prior_session,
    )

    assert final == "Replay ok."
    assert provider.context_calls, "provider did not receive context"
    replay_context = provider.context_calls[0]
    assert replay_context[0]["role"] == "system"
    assert replay_context[0]["content"] != "old system prompt"
    assert any(
        msg.get("role") == "tool" and msg.get("content") == legacy_tool_result
        for msg in replay_context
    )
