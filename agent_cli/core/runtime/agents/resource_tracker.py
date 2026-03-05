"""Session-scoped token/cost tracker for budget-aware agent hints."""

from __future__ import annotations


class ResourceTracker:
    """Track aggregate LLM resource usage for the current task/session."""

    def __init__(
        self,
        context_limit: int = 128_000,
        cost_budget: float | None = None,
    ) -> None:
        self.context_limit = max(int(context_limit), 1)
        self.cost_budget = (
            float(cost_budget) if cost_budget is not None and cost_budget > 0 else None
        )
        self.tokens_used = 0
        self.session_cost = 0.0
        self.turn_count = 0

    @property
    def has_data(self) -> bool:
        return self.turn_count > 0

    def update(self, input_tokens: int, output_tokens: int, cost: float) -> None:
        self.tokens_used += max(int(input_tokens), 0) + max(int(output_tokens), 0)
        self.session_cost += max(float(cost), 0.0)
        self.turn_count += 1

    def summary(self) -> str:
        if not self.has_data:
            return ""

        context_pct = min(
            (self.tokens_used / self.context_limit) * 100.0,
            999.0,
        )
        parts = [f"Turn {self.turn_count}", f"context ~{context_pct:.0f}% used"]
        if self.cost_budget is not None:
            parts.append(f"cost ${self.session_cost:.4f}/${self.cost_budget:.2f}")
        return f"[{', '.join(parts)}]"
