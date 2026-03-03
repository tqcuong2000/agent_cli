"""Unit tests for the DataRegistry."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_cli.core.models.config_models import ProviderConfig
from agent_cli.data import DataRegistry
from agent_cli.data import registry as registry_module


@pytest.fixture
def registry() -> DataRegistry:
    return DataRegistry()


def test_context_window_lookup_exact_prefix_and_default(registry: DataRegistry) -> None:
    assert registry.get_context_window("gpt-4o") == 128_000
    assert registry.get_context_window("gpt-5-mini") == 128_000
    assert registry.get_context_window("gemini-1.5-pro-exp-0827") == 2_000_000
    assert registry.get_context_window("unknown-model") == 128_000


def test_pricing_lookup_and_copy_semantics(registry: DataRegistry) -> None:
    known = registry.get_pricing("gpt-4o")
    assert known == {"input": 2.5, "output": 10.0}

    unknown = registry.get_pricing("not-in-table")
    assert unknown == {"input": 0.0, "output": 0.0}

    known["input"] = 999.0
    assert registry.get_pricing("gpt-4o")["input"] == 2.5


def test_builtin_providers(registry: DataRegistry) -> None:
    providers = registry.get_builtin_providers()
    assert set(providers.keys()) == {
        "openai",
        "azure",
        "anthropic",
        "google",
        "huggingface",
        "openrouter",
    }
    assert isinstance(providers["openai"], ProviderConfig)
    assert providers["openai"].adapter_type == "openai"
    assert providers["openai"].default_model == "gpt-4o"

    providers["openai"].models.append("mutated")
    fresh = registry.get_builtin_providers()
    assert "mutated" not in fresh["openai"].models


def test_tokenizer_encoding_lookup(registry: DataRegistry) -> None:
    assert registry.get_tokenizer_encoding("gpt-5") == "o200k_base"
    assert registry.get_tokenizer_encoding("o3-mini") == "o200k_base"
    assert registry.get_tokenizer_encoding("claude-sonnet-4.6") == "cl100k_base"


def test_internal_models(registry: DataRegistry) -> None:
    internal = registry.get_internal_models()
    assert internal["routing_model"] == "gemini-2.5-flash-lite"
    assert internal["summarization_model"] == "gemini-2.5-flash-lite"


def test_safe_command_patterns_and_tool_defaults(registry: DataRegistry) -> None:
    patterns = registry.get_safe_command_patterns()
    assert len(patterns) == 6
    assert patterns[0].startswith("^(ls|dir|cat|type|echo")

    defaults = registry.get_tool_defaults()
    assert defaults["shell"]["default_timeout"] == 30
    assert defaults["shell"]["max_timeout"] == 120
    assert defaults["output_formatter"]["error_truncation_chars"] == 2000
    assert defaults["file_tools"]["diff_context_lines"] == 2
    assert defaults["executor"]["approval_timeout_seconds"] == 300.0
    assert defaults["workspace"]["index_max_files"] == 5000

    defaults["shell"]["default_timeout"] = 999
    assert registry.get_tool_defaults()["shell"]["default_timeout"] == 30


def test_memory_defaults(registry: DataRegistry) -> None:
    context_budget = registry.get_context_budget()
    retry = registry.get_retry_defaults()
    session = registry.get_session_defaults()
    summarizer = registry.get_summarizer_defaults()
    token_counter = registry.get_token_counter_defaults()
    stuck = registry.get_stuck_detector_defaults()

    assert context_budget["compaction_threshold"] == 0.80
    assert retry["llm_max_retries"] == 5
    assert session["auto_save_interval_seconds"] == 300.0
    assert summarizer["keep_recent_turns"] == 5
    assert summarizer["heuristic_limits"]["max_files"] == 8
    assert token_counter["heuristic_chars_per_token"] == 4.0
    assert stuck["threshold"] == 3
    assert stuck["history_cap"] == 10


def test_schema_defaults(registry: DataRegistry) -> None:
    schema = registry.get_schema_defaults()
    assert schema["title"]["min_words"] == 2
    assert schema["title"]["max_words"] == 15
    assert schema["validation"]["max_consecutive_schema_errors"] == 3


def test_prompt_template_loading_and_missing_file(registry: DataRegistry) -> None:
    prompt = registry.get_prompt_template("output_format")
    assert "{title_max_words}" in prompt

    native_prompt = registry.get_prompt_template("output_format_native")
    assert "Do not write XML action tags" in native_prompt

    persona = registry.get_prompt_template("default_persona")
    assert "expert AI coding assistant" in persona

    coder_persona = registry.get_prompt_template("coder_persona")
    assert "software engineer" in coder_persona

    with pytest.raises(FileNotFoundError):
        registry.get_prompt_template("does_not_exist")


def test_constructor_raises_runtime_error_for_missing_toml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(registry_module.resources, "files", lambda _pkg: tmp_path)

    with pytest.raises(RuntimeError, match="models.toml"):
        DataRegistry()


def test_constructor_raises_runtime_error_for_malformed_toml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "models.toml").write_text("not = [valid", encoding="utf-8")
    monkeypatch.setattr(registry_module.resources, "files", lambda _pkg: tmp_path)

    with pytest.raises(RuntimeError, match="models.toml"):
        DataRegistry()
