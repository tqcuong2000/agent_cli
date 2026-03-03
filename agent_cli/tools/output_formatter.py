"""
Tool Output Formatter — standardizes tool results for Working Memory.

All tool results pass through this formatter before reaching the
Agent's Working Memory.  This prevents context bloat and ensures
consistent formatting across all tools.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from agent_cli.data import DataRegistry

# ══════════════════════════════════════════════════════════════════════
# Output Formatter
# ══════════════════════════════════════════════════════════════════════


class ToolOutputFormatter:
    """Standardizes tool output before it enters Working Memory.

    Enforces max length, adds tool name prefix, and handles truncation
    using a head + tail strategy to preserve useful context from both
    the beginning and end of long output.
    """

    def __init__(
        self,
        max_output_length: int = 5000,
        *,
        error_truncation_chars: int | None = None,
        data_registry: DataRegistry | None = None,
    ) -> None:
        self.max_output_length = max_output_length
        defaults = (
            (data_registry or DataRegistry())
            .get_tool_defaults()
            .get("output_formatter", {})
        )
        self.error_truncation_chars = int(
            error_truncation_chars
            if error_truncation_chars is not None
            else defaults.get("error_truncation_chars", 2000)
        )

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
            return self._to_xml(
                tool_name=tool_name,
                status="error",
                output=raw_output[: self.error_truncation_chars],
                truncated=len(raw_output) > self.error_truncation_chars,
                truncated_chars=max(0, len(raw_output) - self.error_truncation_chars),
            )

        if len(raw_output) <= self.max_output_length:
            return self._to_xml(
                tool_name=tool_name,
                status="success",
                output=raw_output,
                truncated=False,
                truncated_chars=0,
            )

        # Truncate: keep head and tail for context
        half = self.max_output_length // 2
        head = raw_output[:half]
        tail = raw_output[-half:]
        truncated_chars = len(raw_output) - self.max_output_length

        return self._to_xml(
            tool_name=tool_name,
            status="success",
            output=(
                f"{head}\n\n"
                f"[...TRUNCATED {truncated_chars:,} characters. "
                f"Use read_file with line range for full content.]\n\n"
                f"{tail}"
            ),
            truncated=True,
            truncated_chars=truncated_chars,
        )

    @staticmethod
    def _to_xml(
        *,
        tool_name: str,
        status: str,
        output: str,
        truncated: bool,
        truncated_chars: int,
    ) -> str:
        """Render a tool result envelope as XML for working memory."""
        safe_tool = escape(str(tool_name))
        safe_status = escape(status)
        safe_output = escape(output)
        trunc_flag = "true" if truncated else "false"
        return (
            "<tool_result>\n"
            f"  <tool>{safe_tool}</tool>\n"
            f"  <status>{safe_status}</status>\n"
            f"  <truncated>{trunc_flag}</truncated>\n"
            f"  <truncated_chars>{truncated_chars}</truncated_chars>\n"
            f"  <output>{safe_output}</output>\n"
            "</tool_result>"
        )
