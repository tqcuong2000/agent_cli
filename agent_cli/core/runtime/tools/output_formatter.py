"""
Tool Output Formatter — standardizes tool results for Working Memory.

All tool results pass through this formatter before reaching the
Agent's Working Memory.  This prevents context bloat and ensures
consistent formatting across all tools.
"""

from __future__ import annotations

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
        data_registry: DataRegistry,
    ) -> None:
        self.max_output_length = max_output_length
        defaults = data_registry.get_tool_defaults().get("output_formatter", {})
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
        action_id: str = "",
        error_id: str = "",
        error_code: str = "",
        retryable: bool | None = None,
        total_chars: int = 0,
        total_lines: int = 0,
        content_ref: str = "",
        batch_id: str = "",
    ) -> str:
        """Format a tool's raw output for the Agent's Working Memory.

        Rules:
        1. Wrap in protocol envelope fields: id/type/version/timestamp.
        2. Truncate if output exceeds max length (keep head + tail).
        3. Mark errors via ``payload.status = "error"``.
        """
        if not success:
            full_chars = total_chars if total_chars > 0 else len(raw_output)
            full_lines = total_lines if total_lines > 0 else self._count_lines(raw_output)
            return self._to_envelope(
                tool_name=tool_name,
                status="error",
                output=raw_output[: self.error_truncation_chars],
                truncated=len(raw_output) > self.error_truncation_chars,
                truncated_chars=max(0, len(raw_output) - self.error_truncation_chars),
                task_id=task_id,
                native_call_id=native_call_id,
                action_id=action_id,
                error_id=error_id,
                error_code=error_code,
                retryable=retryable,
                total_chars=full_chars,
                total_lines=full_lines,
                content_ref=content_ref,
                batch_id=batch_id,
            )

        if len(raw_output) <= self.max_output_length:
            return self._to_envelope(
                tool_name=tool_name,
                status="success",
                output=raw_output,
                truncated=False,
                truncated_chars=0,
                task_id=task_id,
                native_call_id=native_call_id,
                action_id=action_id,
                error_id=error_id,
                error_code=error_code,
                retryable=retryable,
                total_chars=0,
                total_lines=0,
                content_ref=content_ref,
                batch_id=batch_id,
            )

        # Truncate: keep head and tail for context
        half = self.max_output_length // 2
        head = raw_output[:half]
        tail = raw_output[-half:]
        truncated_chars = len(raw_output) - self.max_output_length
        full_chars = total_chars if total_chars > 0 else len(raw_output)
        full_lines = total_lines if total_lines > 0 else self._count_lines(raw_output)

        return self._to_envelope(
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
            action_id=action_id,
            error_id=error_id,
            error_code=error_code,
            retryable=retryable,
            total_chars=full_chars,
            total_lines=full_lines,
            content_ref=content_ref,
            batch_id=batch_id,
        )

    def _to_envelope(
        self,
        *,
        tool_name: str,
        status: str,
        output: str,
        truncated: bool,
        truncated_chars: int,
        task_id: str,
        native_call_id: str,
        action_id: str,
        error_id: str,
        error_code: str,
        retryable: bool | None,
        total_chars: int,
        total_lines: int,
        content_ref: str,
        batch_id: str,
    ) -> str:
        return self._to_lean_envelope(
            tool_name=tool_name,
            status=status,
            output=output,
            truncated=truncated,
            truncated_chars=truncated_chars,
            task_id=task_id,
            native_call_id=native_call_id,
            action_id=action_id,
            error_id=error_id,
            error_code=error_code,
            retryable=retryable,
            total_chars=total_chars,
            total_lines=total_lines,
            content_ref=content_ref,
            batch_id=batch_id,
        )

    @staticmethod
    def _to_lean_envelope(
        *,
        tool_name: str,
        status: str,
        output: str,
        truncated: bool,
        truncated_chars: int,
        task_id: str,
        native_call_id: str,
        action_id: str,
        error_id: str,
        error_code: str,
        retryable: bool | None,
        total_chars: int,
        total_lines: int,
        content_ref: str,
        batch_id: str,
    ) -> str:
        """Render a lean tool envelope that avoids JSON double-escaping."""
        parts = [
            f"tool={tool_name}",
            f"status={status}",
            f"truncated={'true' if truncated else 'false'}",
            f"truncated_chars={truncated_chars}",
        ]
        if error_code:
            parts.append(f"error_code={error_code}")
        if error_id:
            parts.append(f"error_id={error_id}")
        if retryable is not None:
            parts.append(f"retryable={'true' if retryable else 'false'}")
        if truncated and total_chars > 0:
            parts.append(f"total_chars={total_chars}")
        if truncated and total_lines > 0:
            parts.append(f"total_lines={total_lines}")
        if content_ref:
            parts.append(f"content_ref={content_ref}")
        if task_id:
            parts.append(f"task_id={task_id}")
        if native_call_id:
            parts.append(f"native_call_id={native_call_id}")
        if action_id:
            parts.append(f"action_id={action_id}")
        if batch_id:
            parts.append(f"batch_id={batch_id}")
        header = f"[tool_result {' '.join(parts)}]"
        return f"{header}\n{output}\n[/tool_result]"

    @staticmethod
    def _count_lines(value: str) -> int:
        if not value:
            return 0
        return value.count("\n") + 1
