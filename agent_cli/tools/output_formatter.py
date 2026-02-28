"""
Tool Output Formatter — standardizes tool results for Working Memory.

All tool results pass through this formatter before reaching the
Agent's Working Memory.  This prevents context bloat and ensures
consistent formatting across all tools.
"""

from __future__ import annotations


# ══════════════════════════════════════════════════════════════════════
# Output Formatter
# ══════════════════════════════════════════════════════════════════════


class ToolOutputFormatter:
    """Standardizes tool output before it enters Working Memory.

    Enforces max length, adds tool name prefix, and handles truncation
    using a head + tail strategy to preserve useful context from both
    the beginning and end of long output.
    """

    def __init__(self, max_output_length: int = 5000) -> None:
        self.max_output_length = max_output_length

    def format(
        self,
        tool_name: str,
        raw_output: str,
        success: bool = True,
    ) -> str:
        """Format a tool's raw output for the Agent's Working Memory.

        Rules:
        1. Prefix with tool name for LLM context.
        2. Truncate if exceeds max length (keep head + tail).
        3. Mark errors clearly.
        """
        if not success:
            return f"[Tool: {tool_name}] Error:\n{raw_output[:2000]}"

        if len(raw_output) <= self.max_output_length:
            return f"[Tool: {tool_name}] Result:\n{raw_output}"

        # Truncate: keep head and tail for context
        half = self.max_output_length // 2
        head = raw_output[:half]
        tail = raw_output[-half:]
        truncated_chars = len(raw_output) - self.max_output_length

        return (
            f"[Tool: {tool_name}] Result (truncated):\n"
            f"{head}\n"
            f"\n[...TRUNCATED {truncated_chars:,} characters. "
            f"Use read_file with line range for full content.]\n\n"
            f"{tail}"
        )
