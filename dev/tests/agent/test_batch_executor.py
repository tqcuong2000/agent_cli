from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
from pydantic import BaseModel

from agent_cli.core.infra.events.errors import ToolExecutionError
from agent_cli.core.infra.events.event_bus import AsyncEventBus
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.runtime.agents.batch_executor import BatchExecutor
from agent_cli.core.runtime.agents.parsers import ParsedAction
from agent_cli.core.runtime.tools.base import BaseTool, ToolCategory
from agent_cli.core.runtime.tools.executor import ToolExecutor
from agent_cli.core.runtime.tools.output_formatter import ToolOutputFormatter
from agent_cli.core.runtime.tools.registry import ToolRegistry

TEST_DATA_REGISTRY = DataRegistry()


class _DelayArgs(BaseModel):
    value: str = ""
    delay: float = 0.0


class _ParallelTool(BaseTool):
    name = "parallel_tool"
    description = "parallel-safe delay tool"
    category = ToolCategory.UTILITY
    is_safe = True
    parallel_safe = True

    def __init__(self, tracker: dict[str, int] | None = None) -> None:
        self._tracker = tracker

    @property
    def args_schema(self) -> type[BaseModel]:
        return _DelayArgs

    async def execute(self, value: str = "", delay: float = 0.0, **kwargs: Any) -> str:
        if self._tracker is not None:
            self._tracker["active"] = self._tracker.get("active", 0) + 1
            self._tracker["max_active"] = max(
                self._tracker.get("max_active", 0),
                self._tracker["active"],
            )
        try:
            await asyncio.sleep(delay)
            return f"parallel:{value}"
        finally:
            if self._tracker is not None:
                self._tracker["active"] = max(self._tracker.get("active", 1) - 1, 0)


class _SequentialTool(BaseTool):
    name = "sequential_tool"
    description = "sequential delay tool"
    category = ToolCategory.UTILITY
    is_safe = True
    parallel_safe = False

    @property
    def args_schema(self) -> type[BaseModel]:
        return _DelayArgs

    async def execute(self, value: str = "", delay: float = 0.0, **kwargs: Any) -> str:
        await asyncio.sleep(delay)
        return f"sequential:{value}"


class _FailTool(BaseTool):
    name = "fail_tool"
    description = "always fails"
    category = ToolCategory.UTILITY
    is_safe = True
    parallel_safe = True

    @property
    def args_schema(self) -> type[BaseModel]:
        return _DelayArgs

    async def execute(self, **kwargs: Any) -> str:
        raise ToolExecutionError("boom", tool_name=self.name)


def _build_batch_executor(
    *tools: BaseTool,
    max_concurrent: int = 5,
) -> BatchExecutor:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)

    executor = ToolExecutor(
        registry=registry,
        event_bus=AsyncEventBus(),
        output_formatter=ToolOutputFormatter(data_registry=TEST_DATA_REGISTRY),
        auto_approve=True,
        data_registry=TEST_DATA_REGISTRY,
    )
    return BatchExecutor(
        tool_executor=executor,
        tool_registry=registry,
        max_concurrent=max_concurrent,
    )


def _action(tool_name: str, idx: int, *, delay: float = 0.0) -> ParsedAction:
    return ParsedAction(
        tool_name=tool_name,
        arguments={"value": str(idx), "delay": delay},
        action_id=f"act_{idx}",
    )


def _payload_output(result_output: str) -> str:
    if result_output.startswith("[tool_result "):
        _header, body = result_output.split("\n", 1)
        payload = {"output": body.rsplit("\n[/tool_result]", 1)[0]}
    else:
        payload = json.loads(result_output)["payload"]
    return str(payload.get("output", ""))


def _metadata_from_output(result_output: str) -> dict[str, str]:
    if result_output.startswith("[tool_result "):
        header = result_output.split("\n", 1)[0]
        attrs: dict[str, str] = {}
        for part in header[len("[tool_result ") : -1].split():
            key, value = part.split("=", 1)
            attrs[key] = value
        return {
            "task_id": attrs.get("task_id", ""),
            "action_id": attrs.get("action_id", ""),
            "batch_id": attrs.get("batch_id", ""),
        }

    parsed = json.loads(result_output)
    metadata = parsed.get("metadata", {})
    return {
        "task_id": str(metadata.get("task_id", "")),
        "action_id": str(metadata.get("action_id", "")),
        "batch_id": str(metadata.get("batch_id", "")),
    }


@pytest.mark.asyncio
async def test_execute_batch_parallel_safe_actions_run_concurrently() -> None:
    batch = _build_batch_executor(_ParallelTool())
    actions = [_action("parallel_tool", 0, delay=0.20), _action("parallel_tool", 1, delay=0.20), _action("parallel_tool", 2, delay=0.20)]

    start = time.perf_counter()
    results = await batch.execute_batch(actions, task_id="task_parallel")
    elapsed = time.perf_counter() - start

    assert elapsed < 0.45
    assert [r.action_id for r in results] == ["act_0", "act_1", "act_2"]
    assert [r.success for r in results] == [True, True, True]
    assert [_payload_output(r.output) for r in results] == [
        "parallel:0",
        "parallel:1",
        "parallel:2",
    ]


@pytest.mark.asyncio
async def test_execute_batch_sequential_actions_run_serially() -> None:
    batch = _build_batch_executor(_SequentialTool())
    actions = [_action("sequential_tool", 0, delay=0.20), _action("sequential_tool", 1, delay=0.20), _action("sequential_tool", 2, delay=0.20)]

    start = time.perf_counter()
    results = await batch.execute_batch(actions, task_id="task_sequential")
    elapsed = time.perf_counter() - start

    assert elapsed >= 0.55
    assert [r.action_id for r in results] == ["act_0", "act_1", "act_2"]
    assert [_payload_output(r.output) for r in results] == [
        "sequential:0",
        "sequential:1",
        "sequential:2",
    ]


@pytest.mark.asyncio
async def test_execute_batch_mixed_tools_preserve_original_result_order() -> None:
    batch = _build_batch_executor(_ParallelTool(), _SequentialTool())
    actions = [
        _action("sequential_tool", 0, delay=0.05),
        _action("parallel_tool", 1, delay=0.05),
        _action("parallel_tool", 2, delay=0.05),
    ]

    results = await batch.execute_batch(actions, task_id="task_mixed")
    assert [r.action_id for r in results] == ["act_0", "act_1", "act_2"]
    assert [_payload_output(r.output) for r in results] == [
        "sequential:0",
        "parallel:1",
        "parallel:2",
    ]


@pytest.mark.asyncio
async def test_execute_batch_honors_max_concurrency_cap() -> None:
    tracker: dict[str, int] = {"active": 0, "max_active": 0}
    batch = _build_batch_executor(_ParallelTool(tracker=tracker), max_concurrent=2)
    actions = [_action("parallel_tool", 0, delay=0.15), _action("parallel_tool", 1, delay=0.15), _action("parallel_tool", 2, delay=0.15), _action("parallel_tool", 3, delay=0.15)]

    results = await batch.execute_batch(actions, task_id="task_cap")
    assert len(results) == 4
    assert tracker["max_active"] <= 2


@pytest.mark.asyncio
async def test_execute_batch_single_action_edge_case() -> None:
    batch = _build_batch_executor(_ParallelTool())
    results = await batch.execute_batch([_action("parallel_tool", 7, delay=0.01)])
    assert len(results) == 1
    assert results[0].action_id == "act_7"
    assert _payload_output(results[0].output) == "parallel:7"


@pytest.mark.asyncio
async def test_execute_batch_failure_does_not_abort_other_actions() -> None:
    batch = _build_batch_executor(_ParallelTool(), _FailTool())
    actions = [_action("parallel_tool", 0, delay=0.05), _action("fail_tool", 1)]

    results = await batch.execute_batch(actions, task_id="task_fail")
    assert len(results) == 2
    assert results[0].success is True
    assert results[1].success is False
    assert "boom" in _payload_output(results[1].output)


@pytest.mark.asyncio
async def test_execute_batch_assigns_shared_batch_id() -> None:
    batch = _build_batch_executor(_ParallelTool())
    actions = [_action("parallel_tool", 0), _action("parallel_tool", 1)]

    results = await batch.execute_batch(actions, task_id="task_batch_id")
    batch_ids = [_metadata_from_output(result.output)["batch_id"] for result in results]

    assert len(results) == 2
    assert all(batch_ids)
    assert len(set(batch_ids)) == 1
    assert batch_ids[0].startswith("batch_")
