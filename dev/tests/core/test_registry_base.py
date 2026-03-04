from __future__ import annotations

import pytest

from agent_cli.core.registry_base import RegistryLifecycleMixin


class _TestRegistry(RegistryLifecycleMixin):
    def __init__(self) -> None:
        self._registry_name = "test"
        self.items: list[str] = []

    def add(self, value: str) -> None:
        self._assert_mutable()
        self.items.append(value)


class _ValidatedRegistry(RegistryLifecycleMixin):
    def __init__(self, *, should_fail: bool = False) -> None:
        self._registry_name = "validated"
        self.should_fail = should_fail
        self.validate_calls = 0

    def validate(self) -> None:
        self.validate_calls += 1
        if self.should_fail:
            raise RuntimeError("invalid state")


def test_freeze_sets_frozen_and_blocks_mutation() -> None:
    registry = _TestRegistry()
    registry.add("a")
    registry.freeze()

    assert registry.is_frozen is True
    with pytest.raises(RuntimeError, match="frozen"):
        registry.add("b")


def test_freeze_is_idempotent() -> None:
    registry = _ValidatedRegistry()
    registry.freeze()
    registry.freeze()

    assert registry.is_frozen is True
    assert registry.validate_calls == 1


def test_freeze_runs_validate_before_freezing() -> None:
    registry = _ValidatedRegistry()

    registry.freeze()

    assert registry.validate_calls == 1
    assert registry.is_frozen is True


def test_validate_failure_prevents_freeze() -> None:
    registry = _ValidatedRegistry(should_fail=True)

    with pytest.raises(RuntimeError, match="invalid state"):
        registry.freeze()

    assert registry.validate_calls == 1
    assert registry.is_frozen is False
