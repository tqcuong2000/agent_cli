"""Regression guards for DataRegistry DI adoption."""

from __future__ import annotations

from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[3] / "agent_cli" / "core"


def _iter_py_files() -> list[Path]:
    return [p for p in CORE_ROOT.rglob("*.py") if p.is_file()]


def test_no_default_data_registry_factory_helpers() -> None:
    offenders: list[str] = []
    for path in _iter_py_files():
        text = path.read_text(encoding="utf-8")
        if "_default_data_registry" in text:
            offenders.append(str(path))
    assert not offenders, f"Found _default_data_registry helpers: {offenders}"


def test_no_or_data_registry_runtime_fallback_pattern() -> None:
    offenders: list[str] = []
    for path in _iter_py_files():
        text = path.read_text(encoding="utf-8")
        if "or DataRegistry()" in text:
            offenders.append(str(path))
    assert not offenders, f"Found runtime fallback pattern 'or DataRegistry()': {offenders}"

