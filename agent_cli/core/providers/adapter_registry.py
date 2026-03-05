"""Lifecycle-managed registry for provider adapters and token counters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from agent_cli.core.infra.config.config import AgentSettings
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.infra.registry.registry_base import RegistryLifecycleMixin
from agent_cli.core.providers.base.base import BaseLLMProvider
from agent_cli.core.providers.cost.token_counter import BaseTokenCounter

TokenCounterFactory = Callable[
    [AgentSettings, DataRegistry, BaseTokenCounter], BaseTokenCounter
]


@dataclass(frozen=True)
class AdapterBinding:
    """Binding between adapter type and runtime factories."""

    adapter_cls: type[BaseLLMProvider]
    token_counter_factory: TokenCounterFactory


class AdapterRegistry(RegistryLifecycleMixin):
    """Registry of provider adapter classes and token counter factories."""

    def __init__(self) -> None:
        super().__init__(registry_name="adapter_types")
        self._bindings: dict[str, AdapterBinding] = {}

    def register(
        self,
        adapter_type: str,
        *,
        adapter_cls: type[BaseLLMProvider],
        token_counter_factory: TokenCounterFactory,
    ) -> None:
        self._assert_mutable()
        key = str(adapter_type).strip().lower()
        if not key:
            raise ValueError("Adapter type must be a non-empty string.")
        if key in self._bindings:
            raise ValueError(f"Adapter type '{key}' is already registered.")
        if not isinstance(adapter_cls, type):
            raise ValueError("Adapter class must be a class object.")
        if not issubclass(adapter_cls, BaseLLMProvider):
            raise ValueError(
                f"Adapter '{key}' ({adapter_cls.__name__}) must inherit BaseLLMProvider."
            )
        if not hasattr(adapter_cls, "safe_generate"):
            raise ValueError(
                f"Adapter '{key}' ({adapter_cls.__name__}) missing 'safe_generate' method."
            )
        if not callable(token_counter_factory):
            raise ValueError(
                f"Adapter '{key}' must define a callable token counter factory."
            )
        self._bindings[key] = AdapterBinding(
            adapter_cls=adapter_cls,
            token_counter_factory=token_counter_factory,
        )

    def get_adapter_class(self, adapter_type: str) -> Optional[type[BaseLLMProvider]]:
        key = str(adapter_type).strip().lower()
        binding = self._bindings.get(key)
        return binding.adapter_cls if binding is not None else None

    def build_token_counter(
        self,
        adapter_type: str,
        *,
        settings: AgentSettings,
        data_registry: DataRegistry,
        fallback: BaseTokenCounter,
    ) -> Optional[BaseTokenCounter]:
        key = str(adapter_type).strip().lower()
        binding = self._bindings.get(key)
        if binding is None:
            return None
        return binding.token_counter_factory(settings, data_registry, fallback)

    def all_types(self) -> list[str]:
        return sorted(self._bindings.keys())

    def validate(self) -> None:
        if not self._bindings:
            raise RuntimeError("Adapter registry must contain at least one adapter.")

    def _freeze_summary(self) -> str:
        adapter_types = ", ".join(self.all_types())
        return f"{len(self._bindings)} adapter types: {adapter_types}"
