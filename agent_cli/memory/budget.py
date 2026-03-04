"""Token budget model and model-context lookup helpers."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from agent_cli.core.registry import DataRegistry


@dataclass(frozen=True)
class TokenBudget:
    """Token budgeting policy for a model context window."""

    max_context: int
    response_reserve: int = 4096
    compaction_threshold: float = 0.80

    def available_for_context(self) -> int:
        """Tokens available for prompt + history after reserving response space."""
        return max(self.max_context - self.response_reserve, 0)

    def should_compact(self, current_tokens: int) -> bool:
        """Whether context compaction should run for current token usage."""
        trigger_at = int(self.available_for_context() * self.compaction_threshold)
        return current_tokens >= trigger_at


def infer_model_max_context(model_name: str) -> int:
    """Infer max context window size from the data registry."""
    return _default_data_registry().get_context_window(model_name)


def budget_for_model(
    model_name: str,
    *,
    response_reserve: int = 4096,
    compaction_threshold: float = 0.80,
    max_context_override: int | None = None,
    data_registry: DataRegistry | None = None,
) -> TokenBudget:
    """Build a TokenBudget for a model with optional provider override."""
    if max_context_override is not None:
        max_context = max_context_override
    else:
        registry = data_registry or _default_data_registry()
        max_context = registry.get_context_window(model_name)
    return TokenBudget(
        max_context=max_context,
        response_reserve=response_reserve,
        compaction_threshold=compaction_threshold,
    )


@lru_cache(maxsize=1)
def _default_data_registry() -> DataRegistry:
    return DataRegistry()
