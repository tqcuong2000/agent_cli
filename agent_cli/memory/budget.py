"""Token budget model and model-context lookup helpers."""

from __future__ import annotations

from dataclasses import dataclass


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
    """Infer max context window size for common model families."""
    lower = model_name.lower()

    # OpenAI families
    if lower.startswith(("gpt-4o", "gpt-4.1", "gpt-5")):
        return 128_000
    if lower.startswith(("o1", "o3", "o4")):
        return 200_000

    # Anthropic families
    if "claude-3-5" in lower or "claude-3.5" in lower or lower.startswith("claude"):
        return 200_000

    # Gemini families (examples include Gemini 1.5 Pro = 2M)
    if "gemini-1.5-pro" in lower:
        return 2_000_000
    if lower.startswith("gemini"):
        return 1_000_000

    # Conservative default for unknown providers/models.
    return 128_000


def budget_for_model(
    model_name: str,
    *,
    response_reserve: int = 4096,
    compaction_threshold: float = 0.80,
    max_context_override: int | None = None,
) -> TokenBudget:
    """Build a TokenBudget for a model with optional provider override."""
    max_context = max_context_override or infer_model_max_context(model_name)
    return TokenBudget(
        max_context=max_context,
        response_reserve=response_reserve,
        compaction_threshold=compaction_threshold,
    )
