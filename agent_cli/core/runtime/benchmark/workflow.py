"""Headless benchmark workflow runner for reproducible session analysis."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from agent_cli.core.infra.config.config import AgentSettings
from agent_cli.core.infra.registry.bootstrap import AppContext, create_app
from agent_cli.core.runtime.session.file_store import FileSessionManager


@dataclass(slots=True)
class BenchmarkTurn:
    """One deterministic turn in a benchmark scenario."""

    turn_id: str
    prompt: str
    expected_tools: list[str] = field(default_factory=list)
    required_headings: list[str] = field(default_factory=list)
    required_fragments: list[str] = field(default_factory=list)
    agent_name: str | None = None


@dataclass(slots=True)
class BenchmarkScenario:
    """A named sequence of turns with stable evaluation anchors."""

    scenario_id: str
    description: str
    turns: list[BenchmarkTurn]


@dataclass(slots=True)
class BenchmarkRunPaths:
    """Filesystem layout for one benchmark run."""

    output_dir: Path
    workspace_dir: Path
    session_dir: Path
    log_dir: Path
    export_json: Path
    report_md: Path


def default_empty_folder_scenario() -> BenchmarkScenario:
    """Return the default empty-folder communication benchmark."""
    return BenchmarkScenario(
        scenario_id="empty_folder_communication_v1",
        description=(
            "Three-turn explicit workflow for measuring agent communication, "
            "tool discipline, and token/cost stability in an empty workspace."
        ),
        turns=[
            BenchmarkTurn(
                turn_id="inventory",
                prompt=(
                    "This is a benchmark run in an empty workspace. "
                    "Use exactly one tool call: list_directory with path='.' and max_depth=1. "
                    "Do not create, modify, or delete files. "
                    "After the tool result, reply in Markdown with exactly these headings and no others:\n"
                    "# Workspace Status\n"
                    "# Evidence\n"
                    "# Constraints\n"
                    "In '# Workspace Status', write exactly one sentence stating whether the workspace is empty."
                ),
                expected_tools=["list_directory"],
                required_headings=[
                    "# Workspace Status",
                    "# Evidence",
                    "# Constraints",
                ],
                required_fragments=["workspace", "empty", "constraints"],
            ),
            BenchmarkTurn(
                turn_id="bootstrap_search",
                prompt=(
                    "Continue the benchmark. "
                    "Use exactly one tool call: find_by_name with pattern='*', path='.', extensions=['md','toml','json'], and max_depth=2. "
                    "Do not use any other tools. "
                    "Reply in Markdown with exactly these headings and no others:\n"
                    "# Search Result\n"
                    "# Interpretation\n"
                    "# Recommended Next Step\n"
                    "In '# Search Result', state either 'No bootstrap files found.' or list the matches."
                ),
                expected_tools=["find_by_name"],
                required_headings=[
                    "# Search Result",
                    "# Interpretation",
                    "# Recommended Next Step",
                ],
                required_fragments=["bootstrap", "next step"],
            ),
            BenchmarkTurn(
                turn_id="starter_plan",
                prompt=(
                    "Continue the benchmark. "
                    "Do not call any tools. Do not create files. "
                    "Reply in Markdown with exactly these headings and no others:\n"
                    "# Minimal Starter Set\n"
                    "# Why This Is Enough\n"
                    "# Stop Condition\n"
                    "Under '# Minimal Starter Set', provide exactly three numbered items for files you would create if asked."
                ),
                expected_tools=[],
                required_headings=[
                    "# Minimal Starter Set",
                    "# Why This Is Enough",
                    "# Stop Condition",
                ],
                required_fragments=["1.", "2.", "3.", "stop"],
            ),
        ],
    )


def default_file_operations_scenario() -> BenchmarkScenario:
    """Return the file-operation tool benchmark for an empty workspace."""
    return BenchmarkScenario(
        scenario_id="file_operations_tool_flow_v1",
        description=(
            "Five-turn file workflow for measuring deterministic tool use across "
            "write, read, run, edit, and delete operations in an empty workspace."
        ),
        turns=[
            BenchmarkTurn(
                turn_id="write_file",
                prompt=(
                    "This is a benchmark run in an empty workspace. "
                    "Use exactly one tool call: write_file with path='hello.py'. "
                    "Write a four-line Python script that defines main(), prints exactly 'phase1', "
                    "and calls main() under the __name__ == '__main__' guard. "
                    "Do not use any other tools. "
                    "Reply in Markdown with exactly these headings and no others:\n"
                    "# Write Result\n"
                    "# File Path\n"
                    "# Expected Runtime Output\n"
                    "In '# File Path', write exactly `hello.py`."
                ),
                expected_tools=["write_file"],
                required_headings=[
                    "# Write Result",
                    "# File Path",
                    "# Expected Runtime Output",
                ],
                required_fragments=["hello.py", "phase1"],
            ),
            BenchmarkTurn(
                turn_id="read_file",
                prompt=(
                    "Continue the benchmark. "
                    "Use exactly one tool call: read_file with path='hello.py'. "
                    "Do not use any other tools. "
                    "Reply in Markdown with exactly these headings and no others:\n"
                    "# Read Result\n"
                    "# Confirmed Content\n"
                    "# Next Action\n"
                    "In '# Confirmed Content', state that the file prints phase1."
                ),
                expected_tools=["read_file"],
                required_headings=[
                    "# Read Result",
                    "# Confirmed Content",
                    "# Next Action",
                ],
                required_fragments=["phase1", "hello.py"],
            ),
            BenchmarkTurn(
                turn_id="run_file",
                prompt=(
                    "Continue the benchmark. "
                    "Use exactly one tool call: run_command with command='python hello.py'. "
                    "Do not use any other tools. "
                    "Reply in Markdown with exactly these headings and no others:\n"
                    "# Run Result\n"
                    "# Observed Output\n"
                    "# Exit Status\n"
                    "In '# Observed Output', include the exact stdout text."
                ),
                expected_tools=["run_command"],
                required_headings=[
                    "# Run Result",
                    "# Observed Output",
                    "# Exit Status",
                ],
                required_fragments=["phase1", "0"],
            ),
            BenchmarkTurn(
                turn_id="edit_file",
                prompt=(
                    "Continue the benchmark. "
                    "Use exactly one tool call: str_replace with path='hello.py', "
                    "old_str='print(\"phase1\")', and new_str='print(\"phase2\")'. "
                    "Do not use any other tools. "
                    "Reply in Markdown with exactly these headings and no others:\n"
                    "# Edit Result\n"
                    "# Change Applied\n"
                    "# Expected Next Output\n"
                    "In '# Change Applied', state that phase1 was replaced with phase2."
                ),
                expected_tools=["str_replace"],
                required_headings=[
                    "# Edit Result",
                    "# Change Applied",
                    "# Expected Next Output",
                ],
                required_fragments=["phase1", "phase2"],
            ),
            BenchmarkTurn(
                turn_id="delete_file",
                prompt=(
                    "Continue the benchmark. "
                    "Use exactly one tool call: run_command with command="
                    "\"python -c \\\"from pathlib import Path; Path('hello.py').unlink(); print('deleted hello.py')\\\"\". "
                    "Do not use any other tools. "
                    "Reply in Markdown with exactly these headings and no others:\n"
                    "# Delete Result\n"
                    "# Deletion Evidence\n"
                    "# Workspace State\n"
                    "In '# Workspace State', state that hello.py should no longer exist."
                ),
                expected_tools=["run_command"],
                required_headings=[
                    "# Delete Result",
                    "# Deletion Evidence",
                    "# Workspace State",
                ],
                required_fragments=["deleted hello.py", "no longer exist"],
            ),
        ],
    )


def default_large_file_summary_scenario() -> BenchmarkScenario:
    """Benchmark for reading a large file that triggers truncation."""
    # Line noise filler
    filler = " This is a repetitive log entry for padding."
    lines = [f"Line {i:03}:{filler}" for i in range(1, 101)]
    lines.append("Line 101: CRITICAL_EVENT: SYSTEM_CORE_DATA_CORRUPTION_IN_SECTOR_7")
    lines.extend([f"Line {i:03}:{filler}" for i in range(102, 201)])
    content = "\n".join(lines)

    return BenchmarkScenario(
        scenario_id="large_file_summary_v1",
        description="Verify handling of large truncated files by requiring the agent to find content in the middle.",
        turns=[
            BenchmarkTurn(
                turn_id="write_audit_log",
                prompt=(
                    "Start a new session in an empty workspace. "
                    "Use one tool call: write_file with path='audit.log' and a very large content. "
                    "I will provide the content now. Save it exactly. "
                    "Reply in Markdown with heading '# Status' stating 'File Created'.\n"
                    f"\n```\n{content}\n```"
                ),
                expected_tools=["write_file"],
                required_headings=["# Status"],
                required_fragments=["File Created"],
            ),
            BenchmarkTurn(
                turn_id="diagnose_critical_error",
                prompt=(
                    "Read 'audit.log'. It will be truncated in the response. "
                    "Find the 'CRITICAL_EVENT' starting with 'Line 101'. "
                    "Do not use grep or search, use read_file with line ranges as needed. "
                    "Tell me what the error is and which sector is affected. "
                    "Format your response with exactly these headings:\n"
                    "# Found Error\n"
                    "# Sector Info\n"
                    "# Evidence"
                ),
                expected_tools=["read_file"],
                required_headings=["# Found Error", "# Sector Info", "# Evidence"],
                required_fragments=[
                    "SYSTEM_CORE_DATA_CORRUPTION",
                    "Sector 7",
                    "Line 101",
                ],
            ),
        ],
    )


def get_benchmark_scenario(name: str) -> BenchmarkScenario:
    """Resolve a benchmark scenario by stable identifier."""
    normalized = str(name).strip().lower()
    factories = {
        "empty_folder_communication_v1": default_empty_folder_scenario,
        "empty_folder": default_empty_folder_scenario,
        "file_operations_tool_flow_v1": default_file_operations_scenario,
        "file_operations": default_file_operations_scenario,
        "large_file_summary_v1": default_large_file_summary_scenario,
        "large_file": default_large_file_summary_scenario,
    }
    factory = factories.get(normalized)
    if factory is None:
        available = ", ".join(sorted(list_benchmark_scenarios()))
        raise ValueError(f"Unknown benchmark scenario '{name}'. Available: {available}")
    return factory()


def list_benchmark_scenarios() -> list[str]:
    """Return the stable public benchmark scenario names."""
    return [
        "empty_folder_communication_v1",
        "file_operations_tool_flow_v1",
        "large_file_summary_v1",
    ]


async def run_benchmark_scenario(
    *,
    output_dir: str | Path,
    workspace_dir: str | Path | None = None,
    model: str | None = None,
    agent_name: str | None = None,
    scenario: BenchmarkScenario | str | None = None,
    settings: AgentSettings | None = None,
) -> dict[str, Any]:
    """Run the benchmark scenario in an isolated workspace and export results."""
    if scenario is None:
        resolved_scenario = default_empty_folder_scenario()
    elif isinstance(scenario, BenchmarkScenario):
        resolved_scenario = scenario
    else:
        resolved_scenario = get_benchmark_scenario(scenario)
    paths = _prepare_run_paths(output_dir=Path(output_dir), workspace_dir=workspace_dir)
    configured_settings = _build_benchmark_settings(
        settings=settings,
        model=model,
        log_dir=paths.log_dir,
        agent_name=agent_name,
    )
    app_context = create_app(
        settings=configured_settings,
        root_folder=paths.workspace_dir,
    )
    _isolate_app_context(
        app_context,
        session_dir=paths.session_dir,
        default_model=configured_settings.default_model,
    )

    started_at = _utc_now()
    runtime_info: dict[str, Any] = {}
    turn_results: list[dict[str, Any]] = []

    try:
        await app_context.startup()
        runtime_info = _runtime_snapshot(app_context)

        for turn in resolved_scenario.turns:
            turn_results.append(await _run_turn(app_context, turn))
    finally:
        await app_context.shutdown()

    log_entries = (
        _load_jsonl(app_context.observability.log_file)
        if app_context.observability is not None
        else []
    )
    summary_payload = _load_json_file(
        app_context.observability.summary_file
        if app_context.observability is not None
        else None
    )
    active_session_payload = _load_active_session_payload(paths.session_dir)
    _attach_task_log_entries(turn_results, log_entries)

    completed_at = _utc_now()
    export_payload = {
        "workflow": {
            "scenario_id": resolved_scenario.scenario_id,
            "description": resolved_scenario.description,
            "turn_count": len(resolved_scenario.turns),
        },
        "run": {
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_seconds": _duration_seconds(started_at, completed_at),
            "workspace_dir": str(paths.workspace_dir),
            "output_dir": str(paths.output_dir),
            "log_file": str(app_context.observability.log_file)
            if app_context.observability is not None
            else "",
            "summary_file": str(app_context.observability.summary_file)
            if app_context.observability is not None
            else "",
        },
        "runtime": runtime_info,
        "settings": {
            "default_model": configured_settings.default_model,
            "default_agent": configured_settings.default_agent,
            "auto_approve_tools": configured_settings.auto_approve_tools,
            "session_auto_save": configured_settings.session_auto_save,
        },
        "session": active_session_payload,
        "tasks": turn_results,
        "observability_summary": summary_payload,
    }
    export_payload["aggregate"] = _build_aggregate_summary(export_payload)

    paths.export_json.write_text(
        json.dumps(export_payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    paths.report_md.write_text(_render_run_report(export_payload), encoding="utf-8")
    return export_payload


def compare_benchmark_runs(
    baseline: dict[str, Any] | str | Path,
    candidate: dict[str, Any] | str | Path,
) -> dict[str, Any]:
    """Compare two exported benchmark runs."""
    baseline_payload = _coerce_run_payload(baseline)
    candidate_payload = _coerce_run_payload(candidate)

    baseline_tasks = {
        str(task.get("turn_id", "")): task
        for task in baseline_payload.get("tasks", [])
        if str(task.get("turn_id", "")).strip()
    }
    candidate_tasks = {
        str(task.get("turn_id", "")): task
        for task in candidate_payload.get("tasks", [])
        if str(task.get("turn_id", "")).strip()
    }

    per_turn: list[dict[str, Any]] = []
    for turn_id in sorted(set(baseline_tasks) | set(candidate_tasks)):
        left = baseline_tasks.get(turn_id, {})
        right = candidate_tasks.get(turn_id, {})
        baseline_answer = str(left.get("final_answer", ""))
        candidate_answer = str(right.get("final_answer", ""))
        per_turn.append(
            {
                "turn_id": turn_id,
                "answer_similarity": round(
                    SequenceMatcher(
                        None,
                        _normalize_text(baseline_answer),
                        _normalize_text(candidate_answer),
                    ).ratio(),
                    4,
                ),
                "baseline_total_tokens": int(
                    left.get("metrics", {}).get("total_tokens", 0)
                ),
                "candidate_total_tokens": int(
                    right.get("metrics", {}).get("total_tokens", 0)
                ),
                "token_delta": int(right.get("metrics", {}).get("total_tokens", 0))
                - int(left.get("metrics", {}).get("total_tokens", 0)),
                "baseline_tool_calls": int(len(left.get("observed_tools", []))),
                "candidate_tool_calls": int(len(right.get("observed_tools", []))),
                "baseline_compliance": float(left.get("compliance_score", 0.0)),
                "candidate_compliance": float(right.get("compliance_score", 0.0)),
            }
        )

    baseline_agg = baseline_payload.get("aggregate", {})
    candidate_agg = candidate_payload.get("aggregate", {})
    return {
        "baseline": {
            "scenario_id": baseline_payload.get("workflow", {}).get("scenario_id", ""),
            "total_tokens": int(baseline_agg.get("total_tokens", 0)),
            "total_cost_usd": float(baseline_agg.get("total_cost_usd", 0.0)),
        },
        "candidate": {
            "scenario_id": candidate_payload.get("workflow", {}).get("scenario_id", ""),
            "total_tokens": int(candidate_agg.get("total_tokens", 0)),
            "total_cost_usd": float(candidate_agg.get("total_cost_usd", 0.0)),
        },
        "delta": {
            "total_tokens": int(candidate_agg.get("total_tokens", 0))
            - int(baseline_agg.get("total_tokens", 0)),
            "total_cost_usd": round(
                float(candidate_agg.get("total_cost_usd", 0.0))
                - float(baseline_agg.get("total_cost_usd", 0.0)),
                6,
            ),
            "average_answer_similarity": round(
                sum(item["answer_similarity"] for item in per_turn) / len(per_turn),
                4,
            )
            if per_turn
            else 0.0,
        },
        "per_turn": per_turn,
    }


async def _run_turn(app_context: AppContext, turn: BenchmarkTurn) -> dict[str, Any]:
    session_before = (
        app_context.session_manager.get_active()
        if app_context.session_manager is not None
        else None
    )
    prior_task_ids = list(session_before.task_ids) if session_before is not None else []
    request_text = _compose_turn_request(turn)
    final_answer = await app_context.orchestrator.handle_request(request_text)

    session_after = (
        app_context.session_manager.get_active()
        if app_context.session_manager is not None
        else None
    )
    task_id = _resolve_new_task_id(
        prior_task_ids,
        session_after.task_ids if session_after is not None else [],
    )
    task_messages = app_context.orchestrator.active_agent.get_last_task_messages()
    observed_tools = _extract_tool_names(task_messages)
    metrics = (
        app_context.observability.get_task_metrics(task_id)
        if app_context.observability is not None and task_id
        else {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "llm_calls": 0,
            "tool_calls": 0,
            "tool_errors": 0,
            "cost_usd": 0.0,
        }
    )
    compliance = _score_turn(
        turn=turn,
        final_answer=str(final_answer or ""),
        observed_tools=observed_tools,
    )
    return {
        "turn_id": turn.turn_id,
        "prompt": turn.prompt,
        "task_id": task_id,
        "agent_name": app_context.orchestrator.active_agent_name,
        "final_answer": str(final_answer or ""),
        "messages": task_messages,
        "observed_tools": observed_tools,
        "metrics": metrics,
        "compliance_score": compliance["score"],
        "compliance_checks": compliance["checks"],
        "log_entries": [],
    }


def _prepare_run_paths(
    *,
    output_dir: Path,
    workspace_dir: str | Path | None,
) -> BenchmarkRunPaths:
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_workspace = (
        Path(workspace_dir)
        if workspace_dir is not None
        else output_dir / "workspace"
    )
    resolved_workspace.mkdir(parents=True, exist_ok=True)
    if any(resolved_workspace.iterdir()):
        raise ValueError(
            f"Benchmark workspace must be empty: {resolved_workspace}"
        )

    session_dir = output_dir / "sessions"
    log_dir = output_dir / "logs"
    session_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    return BenchmarkRunPaths(
        output_dir=output_dir,
        workspace_dir=resolved_workspace,
        session_dir=session_dir,
        log_dir=log_dir,
        export_json=output_dir / "benchmark_run.json",
        report_md=output_dir / "benchmark_report.md",
    )


def _build_benchmark_settings(
    *,
    settings: AgentSettings | None,
    model: str | None,
    log_dir: Path,
    agent_name: str | None,
) -> AgentSettings:
    if settings is not None:
        configured = settings.model_copy(deep=True)
    else:
        configured = AgentSettings()

    if model:
        configured.default_model = str(model).strip()
    if agent_name:
        configured.default_agent = str(agent_name).strip()
    configured.log_directory = str(log_dir)
    configured.auto_approve_tools = True
    configured.session_auto_save = True
    configured.semantic_memory_enabled = False
    configured.semantic_memory_auto_learn = False
    return configured


def _isolate_app_context(
    app_context: AppContext,
    *,
    session_dir: Path,
    default_model: str,
) -> None:
    isolated_session_manager = FileSessionManager(
        session_dir=session_dir,
        default_model=default_model,
    )
    app_context.session_manager = isolated_session_manager
    if app_context.orchestrator is not None:
        app_context.orchestrator._session_manager = isolated_session_manager
        app_context.orchestrator._title_service = None
        app_context.orchestrator._capability_probe = None
    app_context.title_service = None
    app_context.capability_probe = None

    command_parser = getattr(app_context, "command_parser", None)
    command_context = getattr(command_parser, "context", None)
    if command_context is not None:
        setattr(command_context, "session_manager", isolated_session_manager)


def _runtime_snapshot(app_context: AppContext) -> dict[str, Any]:
    system_info = app_context.system_info_provider.snapshot()
    runtime_identity = app_context.providers.get_runtime_identity(
        app_context.settings.default_model
    )
    return {
        "observability_session_id": app_context.observability.session_id
        if app_context.observability is not None
        else "",
        "system_info": asdict(system_info),
        "runtime_identity": runtime_identity,
        "tool_count": len(app_context.tool_registry.get_all_names()),
    }


def _compose_turn_request(turn: BenchmarkTurn) -> str:
    agent_name = str(turn.agent_name or "").strip()
    if agent_name:
        return f"!{agent_name} {turn.prompt}"
    return turn.prompt


def _resolve_new_task_id(previous: list[str], current: list[str]) -> str:
    previous_set = set(previous)
    for task_id in current:
        if task_id not in previous_set:
            return task_id
    return current[-1] if current else ""


def _score_turn(
    *,
    turn: BenchmarkTurn,
    final_answer: str,
    observed_tools: list[str],
) -> dict[str, Any]:
    normalized_answer = _normalize_text(final_answer)
    checks: list[dict[str, Any]] = []

    for heading in turn.required_headings:
        checks.append(
            {
                "type": "heading",
                "value": heading,
                "passed": heading.lower() in final_answer.lower(),
            }
        )
    for fragment in turn.required_fragments:
        checks.append(
            {
                "type": "fragment",
                "value": fragment,
                "passed": _normalize_text(fragment) in normalized_answer,
            }
        )
    checks.append(
        {
            "type": "tools_exact",
            "value": list(turn.expected_tools),
            "passed": observed_tools == list(turn.expected_tools),
        }
    )

    passed_count = sum(1 for check in checks if check["passed"])
    return {
        "score": round(passed_count / len(checks), 4) if checks else 1.0,
        "checks": checks,
    }


def _extract_tool_names(messages: list[dict[str, Any]]) -> list[str]:
    tool_names: list[str] = []
    for message in messages:
        if str(message.get("role", "")).lower() != "assistant":
            continue
        content = str(message.get("content", ""))
        tool_names.extend(_extract_tool_names_from_text(content))
    return _dedupe_preserve_order(tool_names)


def _extract_tool_names_from_text(content: str) -> list[str]:
    names: list[str] = []
    for candidate in _iter_json_objects(content):
        if not isinstance(candidate, dict):
            continue

        decision = candidate.get("decision")
        if isinstance(decision, dict):
            decision_type = str(decision.get("type", "")).strip().lower()
            if decision_type == "execute_action":
                tool_name = str(decision.get("tool", "")).strip()
                if tool_name:
                    names.append(tool_name)
            elif decision_type == "execute_actions":
                actions = decision.get("actions", [])
                if isinstance(actions, list):
                    for action in actions:
                        if isinstance(action, dict):
                            tool_name = str(action.get("tool", "")).strip()
                            if tool_name:
                                names.append(tool_name)

        payload = candidate.get("payload")
        if (
            str(candidate.get("type", "")).strip().lower() == "tool_call"
            and isinstance(payload, dict)
        ):
            tool_name = str(payload.get("tool", "")).strip()
            if tool_name:
                names.append(tool_name)
    return names


def _iter_json_objects(content: str) -> list[Any]:
    candidates: list[Any] = []
    stripped = content.strip()
    if stripped:
        try:
            candidates.append(json.loads(stripped))
        except json.JSONDecodeError:
            pass

    for line in content.splitlines():
        candidate = line.strip()
        if not candidate or not candidate.startswith("{"):
            continue
        try:
            candidates.append(json.loads(candidate))
        except json.JSONDecodeError:
            continue
    return candidates


def _attach_task_log_entries(
    task_results: list[dict[str, Any]],
    log_entries: list[dict[str, Any]],
) -> None:
    for task in task_results:
        task_id = str(task.get("task_id", "")).strip()
        if not task_id:
            continue
        task["log_entries"] = [
            entry for entry in log_entries if str(entry.get("task_id", "")) == task_id
        ]


def _load_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def _load_json_file(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_active_session_payload(session_dir: Path) -> dict[str, Any]:
    active_index = session_dir / "active_session.json"
    if not active_index.exists():
        return {}
    try:
        active_payload = json.loads(active_index.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    active_session_id = str(active_payload.get("active_session_id", "")).strip()
    if not active_session_id:
        return {}
    session_path = session_dir / f"{active_session_id}.json"
    session_payload = _load_json_file(session_path)
    session_payload["session_path"] = str(session_path)
    return session_payload


def _build_aggregate_summary(export_payload: dict[str, Any]) -> dict[str, Any]:
    tasks = export_payload.get("tasks", [])
    total_tokens = sum(
        int(task.get("metrics", {}).get("total_tokens", 0)) for task in tasks
    )
    total_cost = sum(
        float(task.get("metrics", {}).get("cost_usd", 0.0)) for task in tasks
    )
    compliance_scores = [float(task.get("compliance_score", 0.0)) for task in tasks]
    return {
        "total_tokens": int(total_tokens),
        "total_cost_usd": round(total_cost, 6),
        "average_compliance_score": round(
            sum(compliance_scores) / len(compliance_scores),
            4,
        )
        if compliance_scores
        else 0.0,
        "task_count": len(tasks),
    }


def _render_run_report(export_payload: dict[str, Any]) -> str:
    workflow = export_payload.get("workflow", {})
    run = export_payload.get("run", {})
    aggregate = export_payload.get("aggregate", {})
    lines = [
        f"# Benchmark Report: {workflow.get('scenario_id', 'unknown')}",
        "",
        workflow.get("description", ""),
        "",
        "## Run",
        f"- Workspace: `{run.get('workspace_dir', '')}`",
        f"- Output: `{run.get('output_dir', '')}`",
        f"- Duration (s): {run.get('duration_seconds', 0.0)}",
        f"- Total tokens: {aggregate.get('total_tokens', 0)}",
        f"- Total cost (USD): {aggregate.get('total_cost_usd', 0.0)}",
        f"- Average compliance: {aggregate.get('average_compliance_score', 0.0)}",
        "",
        "## Tasks",
    ]
    for task in export_payload.get("tasks", []):
        metrics = task.get("metrics", {})
        lines.extend(
            [
                f"### {task.get('turn_id', '')}",
                f"- Task ID: `{task.get('task_id', '')}`",
                f"- Agent: `{task.get('agent_name', '')}`",
                f"- Tokens: {metrics.get('total_tokens', 0)}",
                f"- Cost (USD): {metrics.get('cost_usd', 0.0)}",
                f"- Observed tools: {', '.join(task.get('observed_tools', [])) or '(none)'}",
                f"- Compliance: {task.get('compliance_score', 0.0)}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _coerce_run_payload(payload: dict[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    return _load_json_file(Path(payload))


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip().lower())


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _duration_seconds(started_at: str, completed_at: str) -> float:
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return round(max((completed - started).total_seconds(), 0.0), 3)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
