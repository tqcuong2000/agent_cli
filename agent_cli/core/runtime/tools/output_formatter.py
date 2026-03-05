"""
Tool Output Formatter — standardizes tool results for Working Memory.

All tool results pass through this formatter before reaching the
Agent's Working Memory.  This prevents context bloat and ensures
consistent formatting across all tools.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from agent_cli.core.infra.registry.registry import DataRegistry

# ══════════════════════════════════════════════════════════════════════
# Output Formatter
# ══════════════════════════════════════════════════════════════════════


class ToolOutputFormatter:
    """Standardizes tool output before it enters Working Memory.

    Enforces max length and wraps tool output in a compact JSON envelope
    with metadata and truncation fields.
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
        *,
        task_id: str = "",
        native_call_id: str = "",
    ) -> str:
        """Format a tool's raw output for the Agent's Working Memory.

        Rules:
        1. Wrap in protocol envelope fields: id/type/version/timestamp.
        2. Truncate if output exceeds max length (keep head + tail).
        3. Mark errors via ``payload.status = "error"``.
        """
        if not success:
            return self._to_json_envelope(
                tool_name=tool_name,
                status="error",
                output=raw_output[: self.error_truncation_chars],
                truncated=len(raw_output) > self.error_truncation_chars,
                truncated_chars=max(0, len(raw_output) - self.error_truncation_chars),
                task_id=task_id,
                native_call_id=native_call_id,
            )

        if len(raw_output) <= self.max_output_length:
            return self._to_json_envelope(
                tool_name=tool_name,
                status="success",
                output=raw_output,
                truncated=False,
                truncated_chars=0,
                task_id=task_id,
                native_call_id=native_call_id,
            )

        # Truncate: keep head and tail for context
        half = self.max_output_length // 2
        head = raw_output[:half]
        tail = raw_output[-half:]
        truncated_chars = len(raw_output) - self.max_output_length

        return self._to_json_envelope(
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
            task_id=task_id,
            native_call_id=native_call_id,
        )

    @staticmethod
    def _to_json_envelope(
        *,
        tool_name: str,
        status: str,
        output: str,
        truncated: bool,
        truncated_chars: int,
        task_id: str,
        native_call_id: str,
    ) -> str:
        """Render a tool result envelope as compact JSON for working memory."""
        metadata: dict[str, str] = {}
        if task_id:
            metadata["task_id"] = task_id
        if native_call_id:
            metadata["native_call_id"] = native_call_id

        envelope = {
            "id": f"msg_{uuid4().hex}",
            "type": "tool_result",
            "version": "1.0",
            "timestamp": datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "payload": {
                "tool": str(tool_name),
                "status": status,
                "truncated": truncated,
                "truncated_chars": truncated_chars,
                "output": output,
            },
            "metadata": metadata,
        }
        return json.dumps(
            envelope,
            ensure_ascii=True,
            separators=(",", ":"),
        )
