"""
JSON Tool Formatter - shared by providers without native function calling.

Converts internal tool definitions into a text block that gets injected
into the system prompt. The LLM is instructed to return one JSON object
per turn and use ``decision.type = "execute_action"`` for tool calls.
"""

from __future__ import annotations

from typing import Any, Dict, List

from agent_cli.providers.base import BaseToolFormatter


class JSONToolFormatter(BaseToolFormatter):
    """Formats tools as JSON-contract text for prompt injection providers."""

    def format_for_native_fc(self, tools: List[Dict[str, Any]]) -> Any:
        raise NotImplementedError(
            "JSONToolFormatter does not support native function calling. "
            "Use format_for_prompt_injection() instead."
        )

    def format_for_prompt_injection(self, tools: List[Dict[str, Any]]) -> str:
        lines = ["## Available Tools", ""]
        lines.append(
            'If you need a tool, return JSON with `decision.type` = "execute_action".'
        )
        lines.append("")

        for tool in tools:
            lines.append(f"### {tool['name']}")
            lines.append(f"{tool.get('description', '')}")

            params = tool.get("parameters", {}).get("properties", {})
            required = tool.get("parameters", {}).get("required", [])

            if params:
                lines.append("Arguments:")
                for param_name, param_info in params.items():
                    req = "required" if param_name in required else "optional"
                    param_type = param_info.get("type", "any")
                    desc = param_info.get("description", "")
                    lines.append(
                        f"  - {param_name} ({req}, type: {param_type}): {desc}"
                    )
            else:
                lines.append("Arguments: none")

            lines.append("")

        lines.append("## Decision Contract")
        lines.append("Return exactly one JSON object and no other text.")
        lines.append("{")
        lines.append('  "title": "short title",')
        lines.append('  "thought": "reasoning for this turn",')
        lines.append('  "decision": {')
        lines.append('    "type": "reflect | execute_action | notify_user | yield",')
        lines.append('    "tool": "required for execute_action",')
        lines.append('    "args": {},')
        lines.append('    "message": "required for notify_user and yield"')
        lines.append("  }")
        lines.append("}")
        lines.append("")
        lines.append(
            "Tool call rule: use exactly one listed tool name and object args."
        )
        lines.append("Completion rule: use notify_user with the user-facing answer.")
        lines.append("Abort rule: use yield with a short reason.")

        return "\n".join(lines)
