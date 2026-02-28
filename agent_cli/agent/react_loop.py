"""
ReAct Loop Helpers — ``StuckDetector`` and ``PromptBuilder``.

These are utility classes used by ``BaseAgent.handle_task()`` to
detect repetitive tool-call loops and assemble dynamic system prompts.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agent_cli.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Effort → Constraints Mapping
# ══════════════════════════════════════════════════════════════════════

# Imported from base.py to avoid circular deps — but also re-exportable
# from here for convenience.  The canonical definition lives in base.py.


# ══════════════════════════════════════════════════════════════════════
# Stuck Detector
# ══════════════════════════════════════════════════════════════════════


class StuckDetector:
    """Tracks recent actions to detect repetitive loops.

    If the agent calls the same tool with the same result hash
    ``threshold`` times consecutively, it is considered stuck —
    the agent loop injects a hint message into Working Memory.

    Attributes:
        threshold:  Number of consecutive identical actions to trigger.
    """

    def __init__(self, threshold: int = 3) -> None:
        self.threshold = threshold
        self._recent: List[tuple[str, int]] = []

    def is_stuck(self, tool_name: str, result: str) -> bool:
        """Check if the agent is repeating the same action.

        Args:
            tool_name: The tool that was just executed.
            result:    The formatted result string from the tool.

        Returns:
            ``True`` if the last N calls were identical (same tool
            + same result hash).
        """
        key = (tool_name, hash(result))
        self._recent.append(key)

        if len(self._recent) < self.threshold:
            return False

        # Check if the last N actions are identical
        last_n = self._recent[-self.threshold :]
        if all(k == last_n[0] for k in last_n):
            self._recent.clear()  # Reset after detection
            logger.warning(
                "Stuck detected: tool '%s' repeated %d times",
                tool_name,
                self.threshold,
            )
            return True

        # Keep only the last 10 entries to bound memory
        if len(self._recent) > 10:
            self._recent = self._recent[-10:]

        return False

    def reset(self) -> None:
        """Clear the history."""
        self._recent.clear()


# ══════════════════════════════════════════════════════════════════════
# Prompt Builder
# ══════════════════════════════════════════════════════════════════════


class PromptBuilder:
    """Assembles the system prompt from composable sections.

    Each agent customizes its prompt via ``build_system_prompt()``
    which delegates to this builder with agent-specific persona and
    configuration.

    Args:
        tool_registry: Registry for generating tool descriptions.
    """

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self.tool_registry = tool_registry

    def build(
        self,
        persona: str,
        tool_names: List[str],
        effort_constraints: Dict[str, Any],
        *,
        workspace_context: str = "",
        extra_instructions: str = "",
    ) -> str:
        """Assemble a complete system prompt.

        Sections:
        1. Agent persona / role
        2. Output format instructions (XML tags)
        3. Tool descriptions (auto-generated from registry)
        4. Effort-level behavioral modifiers
        5. Workspace context (project type, language)
        6. Agent-specific extra instructions

        Args:
            persona:             Agent's role description.
            tool_names:          Which tools this agent can use.
            effort_constraints:  Dict from ``EFFORT_CONSTRAINTS[effort]``.
            workspace_context:   Project info (language, framework).
            extra_instructions:  Agent-specific additions.
        """
        sections: List[str] = []

        # 1. Persona
        sections.append(f"# Role\n{persona}")

        # 2. Output format
        sections.append(self._output_format_section())

        # 3. Tool descriptions
        if tool_names:
            tool_defs = self.tool_registry.get_definitions_for_llm(tool_names)
            sections.append(self._tools_section(tool_defs))

        # 4. Effort-level behavior
        reasoning_instruction = effort_constraints.get("reasoning_instruction", "")
        if reasoning_instruction:
            sections.append(f"# Reasoning Policy\n{reasoning_instruction}")

        # 5. Workspace context
        if workspace_context:
            sections.append(f"# Workspace Context\n{workspace_context}")

        # 6. Extra instructions
        if extra_instructions:
            sections.append(f"# Additional Instructions\n{extra_instructions}")

        return "\n\n".join(sections)

    # ── Private Section Builders ─────────────────────────────────

    @staticmethod
    def _output_format_section() -> str:
        """Standard XML output format instructions for all agents."""
        return (
            "# Output Format\n"
            "You MUST structure every response as follows:\n\n"
            "1. **Title**: Provide a short title in <title> tags (4 to 12 words).\n"
            "2. **Thinking**: Wrap your reasoning chain in <thinking> tags.\n"
            "3. **Action**: If you need to use a tool, wrap it in <action> tags:\n"
            "   <action><tool>tool_name</tool>"
            '<args>{"key": "value"}</args></action>\n'
            "4. **Final Answer**: When the task is complete, provide your "
            "answer in <final_answer> tags:\n"
            "   <final_answer>Your response to the user.</final_answer>\n\n"
            "You must ALWAYS include both <title> and <thinking> before any "
            "action or final answer.\n"
            "Required skeleton:\n"
            "<title>Short 4-12 word title</title>\n"
            "<thinking>Your reasoning chain here.</thinking>"
        )

    @staticmethod
    def _tools_section(tool_defs: List[Dict[str, Any]]) -> str:
        """Generate human-readable tool descriptions for the prompt."""
        lines = ["# Available Tools\n"]

        for t in tool_defs:
            params = t["parameters"].get("properties", {})
            required = t["parameters"].get("required", [])

            lines.append(f"## {t['name']}")
            lines.append(f"{t['description']}")

            if params:
                lines.append("**Parameters:**")
                for pname, pinfo in params.items():
                    req = " (required)" if pname in required else " (optional)"
                    ptype = pinfo.get("type", "any")
                    pdesc = pinfo.get("description", "")
                    lines.append(f"  - `{pname}` ({ptype}){req}: {pdesc}")

            lines.append("")

        return "\n".join(lines)
