"""Structural integrity checks for data-driven config and prompt files."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any


def _load_json(filename: str) -> dict[str, Any]:
    root = resources.files("agent_cli.data")
    with root.joinpath(filename).open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    assert isinstance(loaded, dict)
    return loaded


def test_models_json_structure() -> None:
    data = _load_json("models.json")

    assert set(data["internal_models"].keys()) == {
        "routing_model",
        "summarization_model",
    }
    models_root = resources.files("agent_cli.data").joinpath("models")
    gpt_4o = _load_json("models/gpt-4o.json")
    assert models_root.joinpath("gpt-4o.json").exists()
    offering = gpt_4o["offerings"]["gpt-4o"] if "offerings" in gpt_4o else gpt_4o
    assert offering["provider"] == "openai"
    assert offering["api_model"] == "gpt-4o"
    assert isinstance(offering["aliases"], list)
    assert offering["context_window"] == 128000
    assert offering["tokenizer"] == "o200k_base"
    assert offering["pricing_input"] == 2.5
    assert offering["pricing_output"] == 10.0

    capabilities = offering["capabilities"]
    assert capabilities["native_tools"]["supported"] is True
    assert isinstance(capabilities["effort"]["levels"], list)
    assert "supported" in capabilities["web_search"]
    assert "mode" in capabilities["web_search"]


def test_providers_json_structure() -> None:
    data = _load_json("providers.json")
    providers = data["providers"]
    for name in (
        "openai",
        "azure",
        "anthropic",
        "google",
        "huggingface",
        "openrouter",
    ):
        assert name in providers
        assert "adapter_type" in providers[name]
        assert "default_model" in providers[name]


def test_tools_json_structure() -> None:
    data = _load_json("tools.json")

    shell = data["shell"]
    assert isinstance(shell["default_timeout"], int)
    assert isinstance(shell["max_timeout"], int)
    assert isinstance(shell["safe_command_patterns"], list)
    assert shell["safe_command_patterns"]

    output_formatter = data["output_formatter"]
    assert output_formatter["error_truncation_chars"] == 2000

    file_tools = data["file_tools"]
    assert set(file_tools.keys()) == {
        "list_directory_default_depth",
        "search_files_default_max_results",
        "diff_context_lines",
        "diff_max_lines",
        "read_file_max_bytes",
        "search_files_max_file_bytes",
    }

    executor = data["executor"]
    assert isinstance(executor["approval_timeout_seconds"], float)

    workspace = data["workspace"]
    assert workspace["terminal_max_lines"] == 2000
    assert workspace["index_max_files"] == 5000


def test_memory_json_structure() -> None:
    data = _load_json("memory.json")

    context_budget = data["context_budget"]
    assert set(context_budget.keys()) == {
        "system_prompt_pct",
        "summary_pct",
        "response_reserve_pct",
        "compaction_threshold",
    }

    retry = data["retry"]
    assert set(retry.keys()) == {
        "llm_max_retries",
        "llm_retry_base_delay",
        "llm_retry_max_delay",
    }

    session = data["session"]
    assert session["auto_save_interval_seconds"] == 300.0

    summarizer = data["summarizer"]
    assert set(summarizer.keys()) == {
        "keep_recent_turns",
        "summary_budget_tokens",
        "summary_response_tokens",
        "summary_max_words",
        "min_summary_length",
        "summary_truncation_factor",
        "heuristic_limits",
    }

    heuristic_limits = summarizer["heuristic_limits"]
    assert set(heuristic_limits.keys()) == {
        "max_goals",
        "max_decisions",
        "max_actions",
        "max_tools",
        "max_files",
        "max_open_items",
        "condensed_line_max_chars",
        "single_line_max_chars",
    }

    token_counter = data["token_counter"]
    assert token_counter["heuristic_chars_per_token"] == 4.0

    stuck_detector = data["stuck_detector"]
    assert stuck_detector["threshold"] == 3
    assert stuck_detector["history_cap"] == 10


def test_schema_json_structure() -> None:
    data = _load_json("schema.json")
    assert set(data["title"].keys()) == {"min_words", "max_words"}
    assert data["title"]["min_words"] == 2
    assert data["title"]["max_words"] == 15
    assert data["validation"]["max_consecutive_schema_errors"] == 3


def test_prompt_templates_exist_and_are_non_empty() -> None:
    prompts_root = resources.files("agent_cli.data").joinpath("prompts")
    prompt_files = (
        "output_format.txt",
        "output_format_native.txt",
        "clarification_policy.txt",
        "default_persona.txt",
        "coder_persona.txt",
        "researcher_persona.txt",
    )

    for filename in prompt_files:
        content = prompts_root.joinpath(filename).read_text(encoding="utf-8")
        assert content.strip()
