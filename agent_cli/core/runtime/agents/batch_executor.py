"""Batch tool execution with safe parallelism and deterministic ordering."""

from __future__ import annotations

import asyncio
import logging
from typing import List

from agent_cli.core.runtime.agents.parsers import ParsedAction
from agent_cli.core.runtime.tools.base import ToolResult
from agent_cli.core.runtime.tools.executor import ToolExecutor
from agent_cli.core.runtime.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class BatchExecutor:
    """Execute a batch of parsed actions with mixed parallel/serial strategy."""

    def __init__(
        self,
        *,
        tool_executor: ToolExecutor,
        tool_registry: ToolRegistry,
        max_concurrent: int = 5,
    ) -> None:
        self._tool_executor = tool_executor
        self._tool_registry = tool_registry
        self._max_concurrent = max(int(max_concurrent), 1)

    async def execute_batch(
        self,
        actions: List[ParsedAction],
        *,
        task_id: str = "",
    ) -> List[ToolResult]:
        """Execute actions and return results in the original action order."""
        if not actions:
            return []

        results: List[ToolResult | None] = [None] * len(actions)
        parallel_indexes: List[int] = []
        sequential_indexes: List[int] = []
        for idx, action in enumerate(actions):
            if self._is_parallel_safe(action.tool_name):
                parallel_indexes.append(idx)
            else:
                sequential_indexes.append(idx)

        semaphore = asyncio.Semaphore(self._max_concurrent)

        async def _run_parallel(index: int) -> None:
            action = actions[index]
            results[index] = await self._execute_action(
                action=action,
                task_id=task_id,
                fallback_action_id=f"act_{index}",
                semaphore=semaphore,
            )

        if parallel_indexes:
            await asyncio.gather(*(_run_parallel(i) for i in parallel_indexes))

        for index in sequential_indexes:
            action = actions[index]
            results[index] = await self._execute_action(
                action=action,
                task_id=task_id,
                fallback_action_id=f"act_{index}",
            )

        finalized: List[ToolResult] = []
        for idx, result in enumerate(results):
            if result is not None:
                finalized.append(result)
                continue
            action = actions[idx]
            action_id = action.action_id or f"act_{idx}"
            formatted = self._tool_executor.output_formatter.format(
                action.tool_name,
                "Batch execution did not produce a result.",
                success=False,
                task_id=task_id,
                native_call_id=action.native_call_id,
                action_id=action_id,
            )
            finalized.append(
                ToolResult(
                    success=False,
                    output=formatted,
                    error="Batch execution did not produce a result.",
                    action_id=action_id,
                    tool_name=action.tool_name,
                )
            )
        return finalized

    def _is_parallel_safe(self, tool_name: str) -> bool:
        tool = self._tool_registry.get(tool_name)
        if tool is None:
            return False
        return bool(getattr(tool, "parallel_safe", True))

    async def _execute_action(
        self,
        *,
        action: ParsedAction,
        task_id: str,
        fallback_action_id: str,
        semaphore: asyncio.Semaphore | None = None,
    ) -> ToolResult:
        action_id = action.action_id or fallback_action_id

        async def _call_executor() -> ToolResult:
            return await self._tool_executor.execute(
                tool_name=action.tool_name,
                arguments=action.arguments,
                task_id=task_id,
                native_call_id=action.native_call_id,
                action_id=action_id,
            )

        try:
            if semaphore is not None:
                async with semaphore:
                    return await _call_executor()
            return await _call_executor()
        except Exception as exc:  # pragma: no cover - defensive shield
            logger.exception(
                "Batch action execution failed unexpectedly (tool=%s action_id=%s)",
                action.tool_name,
                action_id,
            )
            raw_error = f"{type(exc).__name__}: {exc}"
            formatted = self._tool_executor.output_formatter.format(
                action.tool_name,
                raw_error,
                success=False,
                task_id=task_id,
                native_call_id=action.native_call_id,
                action_id=action_id,
            )
            return ToolResult(
                success=False,
                output=formatted,
                error=raw_error,
                action_id=action_id,
                tool_name=action.tool_name,
            )
