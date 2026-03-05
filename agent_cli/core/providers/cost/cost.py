"""Cost tracking based on data-driven pricing defaults."""

from __future__ import annotations

from agent_cli.core.infra.registry.registry import DataRegistry


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    data_registry: DataRegistry,
) -> float:
    """Estimate API cost in USD for a single call."""
    pricing = data_registry.get_pricing(model)
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return round(input_cost + output_cost, 6)
