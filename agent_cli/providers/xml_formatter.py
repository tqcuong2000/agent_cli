"""
XML Tool Formatter — shared by providers without native function calling.

Converts internal tool definitions into a text block that gets injected
into the system prompt.  The LLM is instructed to use ``<action>`` XML
tags to call tools and ``<final_answer>`` for final responses.
"""

from __future__ import annotations

from typing import Any, Dict, List

from agent_cli.providers.base import BaseToolFormatter


class XMLToolFormatter(BaseToolFormatter):
    """Formats tools as text for system prompt injection.

    Used by ``OpenAICompatibleProvider`` and any other provider
    that doesn't support native function calling.
    """

    def format_for_native_fc(self, tools: List[Dict[str, Any]]) -> Any:
        raise NotImplementedError(
            "XMLToolFormatter does not support native function calling. "
            "Use format_for_prompt_injection() instead."
        )

    def format_for_prompt_injection(self, tools: List[Dict[str, Any]]) -> str:
        lines = ["## Available Tools\n"]
        lines.append("You MUST use these tools by outputting XML tags.\n")

        for tool in tools:
            lines.append(f"### {tool['name']}")
            lines.append(f"{tool.get('description', '')}\n")

            params = tool.get("parameters", {}).get("properties", {})
            required = tool.get("parameters", {}).get("required", [])

            if params:
                lines.append("Arguments:")
                for param_name, param_info in params.items():
                    req = "(required)" if param_name in required else "(optional)"
                    desc = param_info.get("description", "")
                    lines.append(f"  - {param_name} {req}: {desc}")

            lines.append("")

        lines.append("## Tool Call Format")
        lines.append("To call a tool, output:")
        lines.append("```")
        lines.append("<action>")
        lines.append('    <tool>tool_name</tool>')
        lines.append('    <args>{"param": "value"}</args>')
        lines.append("</action>")
        lines.append("```")
        lines.append("")
        lines.append("To give a final answer, output:")
        lines.append("```")
        lines.append("<final_answer>Your response here</final_answer>")
        lines.append("```")

        return "\n".join(lines)
