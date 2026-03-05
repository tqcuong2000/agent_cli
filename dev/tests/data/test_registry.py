"""Unit tests for the DataRegistry."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_cli.core.infra.registry import registry as registry_module
from agent_cli.core.infra.config.config_models import (
    CapabilityObservation,
    CapabilitySnapshot,
    CapabilitySpec,
    EffortCapabilitySpec,
    NativeToolsCapabilitySpec,
    ProviderConfig,
    WebSearchCapabilitySpec,
)
from agent_cli.core.infra.registry.registry import DataRegistry


@pytest.fixture
def registry() -> DataRegistry:
    return DataRegistry()


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
    assert providers["openai"].default_model


def test_typed_provider_specs(registry: DataRegistry) -> None:
    specs = registry.get_provider_specs()
    assert "openai" in specs
    assert specs["openai"].adapter_type == "openai"
    assert specs["openai"].default_model
    assert specs["openai"].web_search["enabled"] is True


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


def test_constructor_raises_runtime_error_for_missing_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(registry_module.resources, "files", lambda _pkg: tmp_path)

    with pytest.raises(RuntimeError, match="models.json"):
        DataRegistry()


def test_constructor_raises_runtime_error_for_malformed_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "models.json").write_text("{ invalid", encoding="utf-8")
    monkeypatch.setattr(registry_module.resources, "files", lambda _pkg: tmp_path)

    with pytest.raises(RuntimeError, match="models.json"):
        DataRegistry()


def test_constructor_logs_loaded_counts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _write_required_registry_files(tmp_path)
    (tmp_path / "models" / "m1.json").write_text(
        json.dumps(
            {
                "provider": "openai",
                "api_model": "m1",
                "context_window": 1000,
                "tokenizer": "cl100k_base",
                "pricing_input": 0.0,
                "pricing_output": 0.0,
                "capabilities": {
                    "native_tools": {"supported": True},
                    "effort": {"supported": False, "levels": ["auto"]},
                    "web_search": {"supported": False, "mode": "none"},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_module.resources, "files", lambda _pkg: tmp_path)

    with caplog.at_level(logging.INFO, logger="agent_cli.core.infra.registry.registry"):
        DataRegistry()

    assert any("DataRegistry loaded" in r.message for r in caplog.records)


def _write_required_registry_files(root: Path) -> None:
    (root / "models.json").write_text(
        json.dumps(
            {
                "internal_models": {
                    "routing_model": "m1",
                    "summarization_model": "m1",
                }
            }
        ),
        encoding="utf-8",
    )
    (root / "models").mkdir(parents=True, exist_ok=True)
    (root / "providers.json").write_text(
        json.dumps(
            {
                "providers": {
                    "openai": {
                        "adapter_type": "openai",
                        "default_model": "m1",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (root / "memory.json").write_text(
        json.dumps({"context_budget": {"compaction_threshold": 0.8}}),
        encoding="utf-8",
    )
    (root / "tools.json").write_text(
        json.dumps({"shell": {"safe_command_patterns": []}}),
        encoding="utf-8",
    )
    (root / "schema.json").write_text(
        json.dumps(
            {
                "title": {"min_words": 2, "max_words": 15},
                "validation": {"max_consecutive_schema_errors": 3},
            }
        ),
        encoding="utf-8",
    )


def test_typed_model_validation_rejects_duplicate_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_required_registry_files(tmp_path)
    (tmp_path / "models" / "m1.json").write_text(
        json.dumps(
            {
                "provider": "openai",
                "api_model": "m1",
                "aliases": ["a"],
                "context_window": 1000,
                "tokenizer": "cl100k_base",
                "pricing_input": 0.0,
                "pricing_output": 0.0,
                "capabilities": {
                    "native_tools": {"supported": True},
                    "effort": {"supported": False, "levels": ["auto"]},
                    "web_search": {"supported": False, "mode": "none"},
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "models" / "m2.json").write_text(
        json.dumps(
            {
                "provider": "openai",
                "api_model": "m2",
                "aliases": ["a"],
                "context_window": 1000,
                "tokenizer": "cl100k_base",
                "pricing_input": 0.0,
                "pricing_output": 0.0,
                "capabilities": {
                    "native_tools": {"supported": True},
                    "effort": {"supported": False, "levels": ["auto"]},
                    "web_search": {"supported": False, "mode": "none"},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_module.resources, "files", lambda _pkg: tmp_path)

    registry = DataRegistry()
    with pytest.raises(RuntimeError, match="Duplicate offering alias"):
        registry.get_model_specs()


def test_typed_model_validation_rejects_missing_capability_block(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_required_registry_files(tmp_path)
    (tmp_path / "models" / "m1.json").write_text(
        json.dumps(
            {
                "provider": "openai",
                "api_model": "m1",
                "context_window": 1000,
                "tokenizer": "cl100k_base",
                "pricing_input": 0.0,
                "pricing_output": 0.0,
            }
        ),
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
    (tmp_path / "models" / "m1.json").write_text(
        json.dumps(
            {
                "provider": "openai",
                "api_model": "m1",
                "context_window": 1000,
                "tokenizer": "cl100k_base",
                "pricing_input": 0.0,
                "pricing_output": 0.0,
                "capabilities": {
                    "native_tools": {"supported": True},
                    "effort": {"supported": False, "levels": ["auto"]},
                    "web_search": {"supported": True, "mode": "invalid_mode"},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_module.resources, "files", lambda _pkg: tmp_path)

    registry = DataRegistry()
    with pytest.raises(RuntimeError, match="unsupported mode"):
        registry.get_model_specs()


def test_typed_model_validation_supports_grouped_offerings_in_one_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_required_registry_files(tmp_path)
    (tmp_path / "models" / "grouped.json").write_text(
        json.dumps(
            {
                "offerings": {
                    "m1": {
                        "provider": "openai",
                        "api_model": "m1",
                        "aliases": ["a1"],
                        "context_window": 1000,
                        "tokenizer": "cl100k_base",
                        "pricing_input": 0.0,
                        "pricing_output": 0.0,
                        "capabilities": {
                            "native_tools": {"supported": True},
                            "effort": {"supported": False, "levels": ["auto"]},
                            "web_search": {"supported": False, "mode": "none"},
                        },
                    },
                    "m2": {
                        "provider": "openai",
                        "api_model": "m2",
                        "aliases": ["a2"],
                        "context_window": 2000,
                        "tokenizer": "cl100k_base",
                        "pricing_input": 0.0,
                        "pricing_output": 0.0,
                        "capabilities": {
                            "native_tools": {"supported": True},
                            "effort": {"supported": False, "levels": ["auto"]},
                            "web_search": {"supported": False, "mode": "none"},
                        },
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_module.resources, "files", lambda _pkg: tmp_path)

    registry = DataRegistry()
    specs = registry.get_model_specs()
    assert "m1" in specs
    assert "m2" in specs
    assert registry.resolve_model_spec("a1") is not None
    assert registry.resolve_model_spec("a2") is not None


def test_resolve_model_spec_logs_hit_and_miss(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _write_required_registry_files(tmp_path)
    (tmp_path / "models" / "m1.json").write_text(
        json.dumps(
            {
                "provider": "openai",
                "api_model": "m1",
                "aliases": ["alias-m1"],
                "context_window": 1000,
                "tokenizer": "cl100k_base",
                "pricing_input": 0.0,
                "pricing_output": 0.0,
                "capabilities": {
                    "native_tools": {"supported": True},
                    "effort": {"supported": False, "levels": ["auto"]},
                    "web_search": {"supported": False, "mode": "none"},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_module.resources, "files", lambda _pkg: tmp_path)
    reg = DataRegistry()

    with caplog.at_level(logging.DEBUG, logger="agent_cli.core.infra.registry.registry"):
        assert reg.resolve_model_spec("alias-m1") is not None
        assert reg.resolve_model_spec("unknown-model") is None

    messages = [record.message for record in caplog.records]
    assert any("resolve_model_spec hit" in message for message in messages)
    assert any("resolve_model_spec miss" in message for message in messages)


def test_declared_support_uses_accessor_map(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = CapabilitySpec(
        native_tools=NativeToolsCapabilitySpec(supported=True),
        effort=EffortCapabilitySpec(supported=False, levels=["auto"]),
        web_search=WebSearchCapabilitySpec(supported=True, mode="provider_native"),
    )
    monkeypatch.setattr(
        DataRegistry,
        "_CAPABILITY_ACCESSORS",
        {"native_tools": lambda _spec: False},
    )

    assert DataRegistry._declared_support(spec, "native_tools") is False
    assert DataRegistry._declared_support(spec, "effort") is False


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
        model="gemini-2.5-flash-lite",
        deployment_id="openai:flash-lite",
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
        model="gemini-2.5-flash-lite",
        deployment_id="openai:flash-lite",
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


def test_capability_observation_operations_are_thread_safe(
    registry: DataRegistry,
) -> None:
    errors: list[BaseException] = []

    def _worker(worker_id: int) -> None:
        try:
            for idx in range(150):
                deployment = f"google:flash-lite:{worker_id % 3}"
                if idx % 4 == 0:
                    registry.save_capability_observation(
                        provider="google",
                        model="gemini-2.5-flash-lite",
                        deployment_id=deployment,
                        observation={
                            "web_search": {
                                "status": "supported",
                                "reason": f"probe_{worker_id}_{idx}",
                                "checked_at": datetime.now(timezone.utc),
                                "source": "probe",
                            }
                        },
                    )
                elif idx % 4 == 1:
                    snapshot = registry.get_capability_snapshot(
                        provider="google",
                        model="gemini-2.5-flash-lite",
                        deployment_id=deployment,
                    )
                    assert isinstance(snapshot, CapabilitySnapshot)
                    assert "web_search" in snapshot.effective
                elif idx % 4 == 2:
                    registry.invalidate_capability_observations(
                        provider="google",
                        model="gemini-2.5-flash-lite",
                        deployment_id=deployment,
                    )
                else:
                    registry.bump_capability_cache_version()
        except BaseException as exc:  # pragma: no cover - diagnostic path
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_worker, worker_id) for worker_id in range(6)]
        for future in futures:
            future.result()

    assert errors == []
    assert registry.capability_cache_version >= 1
