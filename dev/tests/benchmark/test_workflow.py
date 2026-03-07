from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_cli.core.infra.config.config import AgentSettings
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.base.base import BaseLLMProvider
from agent_cli.core.providers.base.models import LLMResponse, ToolCallMode
from agent_cli.core.runtime.benchmark.workflow import (
    compare_benchmark_runs,
    default_empty_folder_scenario,
    run_benchmark_scenario,
)


TEST_DATA_REGISTRY = DataRegistry()


class _BenchmarkMockProvider(BaseLLMProvider):
    def __init__(self) -> None:
        super().__init__("mock-model", data_registry=TEST_DATA_REGISTRY)

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
        context: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        effort: str | None = None,
        request_options=None,
    ) -> LLMResponse:
        last_user = ""
        for message in reversed(context):
            if str(message.get("role", "")).lower() == "user":
                last_user = str(message.get("content", ""))
                break
        tool_messages = [
            str(message.get("content", ""))
            for message in context
            if str(message.get("role", "")).lower() == "tool"
        ]

        if "# Workspace Status" in last_user:
            if not tool_messages:
                return self._response(
                    {
                        "title": "Inspect workspace",
                        "thought": "Need one directory listing.",
                        "decision": {
                            "type": "execute_action",
                            "tool": "list_directory",
                            "args": {"path": ".", "max_depth": 1},
                        },
                    }
                )
            return self._response(
                {
                    "title": "Report workspace",
                    "thought": "Enough evidence collected.",
                    "decision": {
                        "type": "notify_user",
                        "message": (
                            "# Workspace Status\n"
                            "The workspace is empty.\n\n"
                            "# Evidence\n"
                            "- No files were listed in the workspace root.\n\n"
                            "# Constraints\n"
                            "- This benchmark does not allow file changes."
                        ),
                    },
                },
                input_tokens=120,
                output_tokens=36,
                cost_usd=0.0012,
            )

        if "# Search Result" in last_user:
            if not tool_messages or "No matches found" not in tool_messages[-1]:
                return self._response(
                    {
                        "title": "Search bootstrap files",
                        "thought": "Need one search.",
                        "decision": {
                            "type": "execute_action",
                            "tool": "find_by_name",
                            "args": {
                                "pattern": "*",
                                "path": ".",
                                "extensions": ["md", "toml", "json"],
                                "max_depth": 2,
                            },
                        },
                    }
                )
            return self._response(
                {
                    "title": "Report search",
                    "thought": "Summarize absence of files.",
                    "decision": {
                        "type": "notify_user",
                        "message": (
                            "# Search Result\n"
                            "No bootstrap files found.\n\n"
                            "# Interpretation\n"
                            "The workspace has no starter project structure yet.\n\n"
                            "# Recommended Next Step\n"
                            "Choose a starter layout before creating files."
                        ),
                    },
                },
                input_tokens=100,
                output_tokens=32,
                cost_usd=0.001,
            )

        return self._response(
            {
                "title": "Plan starter set",
                "thought": "No tools needed.",
                "decision": {
                    "type": "notify_user",
                    "message": (
                        "# Minimal Starter Set\n"
                        "1. `pyproject.toml`\n"
                        "2. `README.md`\n"
                        "3. `agent_cli/__main__.py`\n\n"
                        "# Why This Is Enough\n"
                        "These files define packaging, usage guidance, and the entry point.\n\n"
                        "# Stop Condition\n"
                        "Stop after the plan because the benchmark forbids file creation."
                    ),
                },
            },
            input_tokens=80,
            output_tokens=28,
            cost_usd=0.0008,
        )

    def _response(
        self,
        payload: dict[str, Any],
        *,
        input_tokens: int = 60,
        output_tokens: int = 18,
        cost_usd: float = 0.0006,
    ) -> LLMResponse:
        return LLMResponse(
            text_content=json.dumps(payload),
            tool_mode=ToolCallMode.PROMPT_JSON,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            model="mock-model",
            provider="mock",
        )

    def stream(
        self,
        context: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        effort: str | None = None,
        request_options=None,
    ):
        raise NotImplementedError

    def get_buffered_response(self) -> LLMResponse:
        return LLMResponse(text_content="", tool_mode=ToolCallMode.PROMPT_JSON)


@pytest.mark.asyncio
async def test_run_benchmark_scenario_exports_explicit_metrics(tmp_path: Path) -> None:
    output_dir = tmp_path / "benchmark"
    workspace_dir = output_dir / "workspace"
    settings = AgentSettings(
        default_model="gpt-4.1",
        auto_approve_tools=True,
    )

    from agent_cli.core.runtime.benchmark import workflow as workflow_module

    original_create_app = workflow_module.create_app

    def _patched_create_app(*args, **kwargs):
        app = original_create_app(*args, **kwargs)
        mock_provider = _BenchmarkMockProvider()
        mock_provider.set_observability(app.observability)
        for agent in app.agent_registry.get_all():
            agent.provider = mock_provider
        return app

    workflow_module.create_app = _patched_create_app
    try:
        payload = await run_benchmark_scenario(
            output_dir=output_dir,
            workspace_dir=workspace_dir,
            settings=settings,
            scenario=default_empty_folder_scenario(),
        )
    finally:
        workflow_module.create_app = original_create_app

    assert payload["workflow"]["scenario_id"] == "empty_folder_communication_v1"
    assert payload["aggregate"]["task_count"] == 3
    assert payload["aggregate"]["total_tokens"] > 0
    assert payload["aggregate"]["average_compliance_score"] == pytest.approx(1.0)
    assert payload["tasks"][0]["observed_tools"] == ["list_directory"]
    assert payload["tasks"][1]["observed_tools"] == ["find_by_name"]
    assert payload["tasks"][2]["observed_tools"] == []
    assert Path(output_dir / "benchmark_run.json").exists()
    assert Path(output_dir / "benchmark_report.md").exists()
    assert payload["session"]["messages"]


def test_compare_benchmark_runs_reports_similarity_and_token_delta() -> None:
    baseline = {
        "workflow": {"scenario_id": "empty_folder_communication_v1"},
        "aggregate": {"total_tokens": 300, "total_cost_usd": 0.01},
        "tasks": [
            {
                "turn_id": "inventory",
                "final_answer": "# Workspace Status\nThe workspace is empty.",
                "metrics": {"total_tokens": 100},
                "observed_tools": ["list_directory"],
                "compliance_score": 1.0,
            }
        ],
    }
    candidate = {
        "workflow": {"scenario_id": "empty_folder_communication_v1"},
        "aggregate": {"total_tokens": 330, "total_cost_usd": 0.012},
        "tasks": [
            {
                "turn_id": "inventory",
                "final_answer": "# Workspace Status\nThe workspace is empty.",
                "metrics": {"total_tokens": 120},
                "observed_tools": ["list_directory"],
                "compliance_score": 0.9,
            }
        ],
    }

    comparison = compare_benchmark_runs(baseline, candidate)

    assert comparison["delta"]["total_tokens"] == 30
    assert comparison["delta"]["total_cost_usd"] == pytest.approx(0.002)
    assert comparison["delta"]["average_answer_similarity"] == pytest.approx(1.0)
    assert comparison["per_turn"][0]["token_delta"] == 20
