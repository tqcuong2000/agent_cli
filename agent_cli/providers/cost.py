"""
Cost Tracking — per-model pricing table and estimation.

Prices are in USD per 1 million tokens.  The table is updated
periodically and can be extended via TOML configuration.
"""

from __future__ import annotations

from typing import Dict


# ══════════════════════════════════════════════════════════════════════
# Pricing Table (per 1M tokens, USD) — February 2026
# ══════════════════════════════════════════════════════════════════════

PRICING_TABLE: Dict[str, Dict[str, float]] = {
    # ── OpenAI ───────────────────────────────────────────────────
    "gpt-4.5":              {"input": 75.00,  "output": 150.00},
    "gpt-4.5-mini":         {"input": 0.40,   "output": 1.60},
    "gpt-4o":               {"input": 2.50,   "output": 10.00},
    "gpt-4o-mini":          {"input": 0.15,   "output": 0.60},
    "gpt-5":                {"input": 2.00,   "output": 8.00},
    "gpt-5-mini":           {"input": 0.30,   "output": 1.20},
    "o3":                   {"input": 2.00,   "output": 8.00},
    "o3-mini":              {"input": 1.10,   "output": 4.40},
    "o3-pro":               {"input": 20.00,  "output": 80.00},
    "o4-mini":              {"input": 1.10,   "output": 4.40},
    "codex-mini":           {"input": 1.50,   "output": 6.00},

    # ── Anthropic ────────────────────────────────────────────────
    "claude-sonnet-4.6":    {"input": 3.00,   "output": 15.00},
    "claude-opus-4.6":      {"input": 15.00,  "output": 75.00},
    "claude-haiku-4.5":     {"input": 0.80,   "output": 4.00},
    "claude-sonnet-4":      {"input": 3.00,   "output": 15.00},
    "claude-opus-4":        {"input": 15.00,  "output": 75.00},
    # Legacy aliases
    "claude-3-5-sonnet":    {"input": 3.00,   "output": 15.00},
    "claude-3-opus":        {"input": 15.00,  "output": 75.00},

    # ── Google ───────────────────────────────────────────────────
    "gemini-2.5-pro":       {"input": 1.25,   "output": 10.00},
    "gemini-2.5-flash":     {"input": 0.15,   "output": 0.60},
    "gemini-2.0-flash":     {"input": 0.10,   "output": 0.40},

    # ── Local / Free ─────────────────────────────────────────────
    "llama-3-8b":           {"input": 0.0,    "output": 0.0},
    "codestral":            {"input": 0.0,    "output": 0.0},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate API cost in USD for a single call.

    Returns 0.0 for unknown models (safe default).
    """
    pricing = PRICING_TABLE.get(model, {"input": 0.0, "output": 0.0})
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return round(input_cost + output_cost, 6)
