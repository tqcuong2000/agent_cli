"""Cost tracking based on data-driven pricing defaults."""

from __future__ import annotations

from functools import lru_cache

from agent_cli.core.registry import DataRegistry


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    data_registry: DataRegistry | None = None,
) -> float:
    """Estimate API cost in USD for a single call."""
    registry = data_registry or _default_data_registry()
    pricing = registry.get_pricing(model)
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return round(input_cost + output_cost, 6)


@lru_cache(maxsize=1)
def _default_data_registry() -> DataRegistry:
    return DataRegistry()
