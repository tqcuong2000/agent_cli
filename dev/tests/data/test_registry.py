"""Unit tests for the DataRegistry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_cli.core.models.config_models import (
    CapabilityObservation,
    CapabilitySnapshot,
    CapabilitySpec,
    ModelSpec,
    ProviderConfig,
)
from agent_cli.data import DataRegistry
from agent_cli.data import registry as registry_module


@pytest.fixture
def registry() -> DataRegistry:
    return DataRegistry()


def test_context_window_lookup_uses_model_specs_and_default(
    registry: DataRegistry,
) -> None:
    assert registry.get_context_window("gpt-4o") == 128_000
    assert registry.get_context_window("openai:gpt-4o-mini") == 128_000
    assert registry.get_context_window("gemini-1.5-pro-exp-0827") == 128_000
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


def test_typed_provider_specs(registry: DataRegistry) -> None:
    specs = registry.get_provider_specs()
    assert "openai" in specs
    assert specs["openai"].adapter_type == "openai"
    assert specs["openai"].default_model == "gpt-4o"
    assert specs["openai"].web_search["enabled"] is True


def test_typed_model_specs_resolution_and_capabilities(registry: DataRegistry) -> None:
    specs = registry.get_model_specs()
    assert "gpt-4o" in specs
    assert isinstance(specs["gpt-4o"], ModelSpec)

    resolved = registry.resolve_model_spec("openai/gpt-4o")
    assert resolved is not None
    assert resolved.model_id == "gpt-4o"
    assert resolved.provider == "openai"
    assert resolved.api_model == "gpt-4o"

    capabilities = registry.get_model_capabilities("gemini-2.5-flash-lite")
    assert isinstance(capabilities, CapabilitySpec)
    assert capabilities is not None
    assert capabilities.native_tools.supported is True
    assert capabilities.effort.supported is True
    assert "high" in capabilities.effort.levels
    assert capabilities.web_search.supported is True


def test_tokenizer_encoding_lookup(registry: DataRegistry) -> None:
    assert registry.get_tokenizer_encoding("gpt-4o") == "o200k_base"
    assert registry.get_tokenizer_encoding("openai:gpt-4o-mini") == "o200k_base"
    assert registry.get_tokenizer_encoding("claude-sonnet-4.6") == "cl100k_base"


def test_accessors_map_through_typed_model_entries(
    registry: DataRegistry,
) -> None:
    assert registry.get_context_window("openai/gpt-4o") == 128_000
    assert registry.get_pricing("openai:gpt-4o-mini") == {
        "input": 0.15,
        "output": 0.6,
    }
    assert registry.get_tokenizer_encoding("google/gemini-2.5-flash-lite") == (
        "cl100k_base"
    )


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


def test_web_search_defaults_are_data_driven(registry: DataRegistry) -> None:
    defaults = registry.get_web_search_defaults()
    assert defaults["allowed_domains"] == []

    anthropic = registry.get_web_search_provider_defaults("anthropic")
    assert anthropic["max_uses"] == 10
    assert anthropic["allowed_domains"] == []

    google = registry.get_web_search_provider_defaults("google")
    assert google["enabled"] is True
    assert google["allowed_domains"] == []

    azure = registry.get_web_search_provider_defaults("azure")
    assert azure["enabled"] is True
    assert azure["tool_type"] == "web_search_preview"


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
    assert "Return exactly ONE JSON object" in prompt

    native_prompt = registry.get_prompt_template("output_format_native")
    assert "native function-calling" in native_prompt

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


def _write_required_registry_files(root: Path) -> None:
    (root / "providers.toml").write_text(
        '[providers.openai]\nadapter_type = "openai"\nmodels = ["m1", "m2"]\ndefault_model = "m1"\n',
        encoding="utf-8",
    )
    (root / "memory.toml").write_text(
        "[context_budget]\ncompaction_threshold = 0.8\n",
        encoding="utf-8",
    )
    (root / "tools.toml").write_text(
        "[shell]\nsafe_command_patterns = []\n",
        encoding="utf-8",
    )
    (root / "schema.toml").write_text(
        "[title]\nmin_words = 2\nmax_words = 15\n\n"
        "[validation]\nmax_consecutive_schema_errors = 3\n",
        encoding="utf-8",
    )


def test_typed_model_validation_rejects_duplicate_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_required_registry_files(tmp_path)
    (tmp_path / "models.toml").write_text(
        '[models."m1"]\nprovider = "openai"\napi_model = "m1"\naliases = ["a"]\n'
        'context_window = 1000\ntokenizer = "cl100k_base"\npricing_input = 0.0\npricing_output = 0.0\n'
        '[models."m1".capabilities]\n'
        "native_tools = { supported = true }\n"
        'effort = { supported = false, levels = ["auto"] }\n'
        'web_search = { supported = false, mode = "none" }\n\n'
        '[models."m2"]\nprovider = "openai"\napi_model = "m2"\naliases = ["a"]\n'
        'context_window = 1000\ntokenizer = "cl100k_base"\npricing_input = 0.0\npricing_output = 0.0\n'
        '[models."m2".capabilities]\n'
        "native_tools = { supported = true }\n"
        'effort = { supported = false, levels = ["auto"] }\n'
        'web_search = { supported = false, mode = "none" }\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_module.resources, "files", lambda _pkg: tmp_path)

    registry = DataRegistry()
    with pytest.raises(RuntimeError, match="Duplicate model alias"):
        registry.get_model_specs()


def test_typed_model_validation_rejects_missing_capability_block(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_required_registry_files(tmp_path)
    (tmp_path / "models.toml").write_text(
        '[models."m1"]\nprovider = "openai"\napi_model = "m1"\n'
        'context_window = 1000\ntokenizer = "cl100k_base"\npricing_input = 0.0\npricing_output = 0.0\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_module.resources, "files", lambda _pkg: tmp_path)

    registry = DataRegistry()
    with pytest.raises(RuntimeError, match="Missing required capabilities block"):
        registry.get_model_specs()


def test_typed_model_validation_rejects_invalid_web_search_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_required_registry_files(tmp_path)
    (tmp_path / "models.toml").write_text(
        '[models."m1"]\nprovider = "openai"\napi_model = "m1"\n'
        'context_window = 1000\ntokenizer = "cl100k_base"\npricing_input = 0.0\npricing_output = 0.0\n'
        '[models."m1".capabilities]\n'
        "native_tools = { supported = true }\n"
        'effort = { supported = false, levels = ["auto"] }\n'
        'web_search = { supported = true, mode = "invalid_mode" }\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_module.resources, "files", lambda _pkg: tmp_path)

    registry = DataRegistry()
    with pytest.raises(RuntimeError, match="unsupported mode"):
        registry.get_model_specs()


def test_capability_snapshot_defaults_to_declared_when_no_observed(
    registry: DataRegistry,
) -> None:
    snapshot = registry.get_capability_snapshot(
        provider="openai",
        model="gpt-4o",
        deployment_id="openai:gpt-4o",
    )
    assert isinstance(snapshot, CapabilitySnapshot)
    assert snapshot.effective["native_tools"].status == "supported"
    assert snapshot.effective["native_tools"].source == "declared"
    assert snapshot.effective["effort"].status == "unsupported"
    assert snapshot.effective["web_search"].status == "supported"


def test_capability_snapshot_prefers_fresh_observed_values(
    registry: DataRegistry,
) -> None:
    now = datetime.now(timezone.utc)
    registry.save_capability_observation(
        provider="google",
        model="gemini-2.5-flash-lite",
        deployment_id="google:flash-lite",
        observation={
            "web_search": CapabilityObservation(
                status="unsupported",
                reason="provider_rejected_tool",
                checked_at=now,
                source="probe",
            )
        },
    )

    snapshot = registry.get_capability_snapshot(
        provider="google",
        model="gemini-2.5-flash-lite",
        deployment_id="google:flash-lite",
        max_age_seconds=900,
    )
    assert snapshot.effective["web_search"].status == "unsupported"
    assert snapshot.effective["web_search"].reason == "provider_rejected_tool"
    assert snapshot.effective["web_search"].source == "probe"


def test_capability_snapshot_stale_observed_falls_back_to_declared(
    registry: DataRegistry,
) -> None:
    stale = datetime.now(timezone.utc) - timedelta(hours=2)
    registry.save_capability_observation(
        provider="google",
        model="gemini-2.5-flash-lite",
        deployment_id="google:flash-lite",
        observation={
            "web_search": CapabilityObservation(
                status="unsupported",
                reason="old_probe",
                checked_at=stale,
                source="probe",
            )
        },
    )

    snapshot = registry.get_capability_snapshot(
        provider="google",
        model="gemini-2.5-flash-lite",
        deployment_id="google:flash-lite",
        max_age_seconds=60,
    )
    assert snapshot.effective["web_search"].status == "supported"
    assert snapshot.effective["web_search"].source == "declared"


def test_capability_snapshot_unknown_model_is_unknown_effective(
    registry: DataRegistry,
) -> None:
    snapshot = registry.get_capability_snapshot(
        provider="openai",
        model="nonexistent-model",
        deployment_id="openai:nonexistent-model",
    )
    assert snapshot.effective["native_tools"].status == "unknown"
    assert snapshot.effective["native_tools"].reason == "model_not_registered"
    assert snapshot.effective["effort"].status == "unknown"
    assert snapshot.effective["web_search"].status == "unknown"


def test_capability_observation_invalidation_and_cache_version(
    registry: DataRegistry,
) -> None:
    registry.save_capability_observation(
        provider="openai",
        model="gpt-4o",
        deployment_id="openai:gpt-4o",
        observation={
            "web_search": {
                "status": "supported",
                "reason": "probe_ok",
                "checked_at": datetime.now(timezone.utc),
                "source": "probe",
            }
        },
    )
    removed = registry.invalidate_capability_observations(
        provider="openai",
        model="gpt-4o",
        deployment_id="openai:gpt-4o",
    )
    assert removed == 1

    registry.save_capability_observation(
        provider="google",
        model="gemini-2.5-flash-lite",
        deployment_id="google:flash-lite",
        observation={
            "web_search": {
                "status": "supported",
                "checked_at": datetime.now(timezone.utc),
                "source": "probe",
            }
        },
    )
    current_version = registry.capability_cache_version
    bumped = registry.bump_capability_cache_version()
    assert bumped == current_version + 1
    assert registry.invalidate_capability_observations() == 0
