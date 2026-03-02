"""
ReAct Loop Helpers — ``StuckDetector`` and ``PromptBuilder``.

These are utility classes used by ``BaseAgent.handle_task()`` to
detect repetitive tool-call loops and assemble dynamic system prompts.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from agent_cli.data import DataRegistry
from agent_cli.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


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

    def __init__(self, threshold: int = 3, history_cap: int = 10) -> None:
        self.threshold = threshold
        self.history_cap = max(int(history_cap), 1)
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

        # Keep only the recent bounded history to cap memory usage.
        if len(self._recent) > self.history_cap:
            self._recent = self._recent[-self.history_cap :]

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

    def __init__(
        self,
        tool_registry: ToolRegistry,
        data_registry: DataRegistry | None = None,
    ) -> None:
        self.tool_registry = tool_registry
        self._data_registry = data_registry or DataRegistry()
        title_defaults = self._data_registry.get_schema_defaults().get("title", {})
        self._title_max_words = int(title_defaults.get("max_words", 15))

    def build(
        self,
        persona: str,
        tool_names: List[str],
        *,
        workspace_context: str = "",
        extra_instructions: str = "",
        native_tool_mode: bool = False,
    ) -> str:
        """Assemble a complete system prompt.

        Sections:
        1. Agent persona / role
        2. Output format instructions (XML tags)
        3. Tool descriptions (auto-generated from registry)
        4. Workspace context (project type, language)
        5. Agent-specific extra instructions

        Args:
            persona:             Agent's role description.
            tool_names:          Which tools this agent can use.
            workspace_context:   Project info (language, framework).
            extra_instructions:  Agent-specific additions.
            native_tool_mode:    Whether the provider handles tools natively.
        """
        sections: List[str] = []

        # 1. Persona
        sections.append(f"# Role\n{persona}")

        # 2. Output format
        sections.append(self._output_format_section(native_tool_mode))

        # 3. Tool descriptions
        if tool_names:
            tool_defs = self.tool_registry.get_definitions_for_llm(tool_names)
            sections.append(self._tools_section(tool_defs))

        # 4. Clarification policy (only if ask_user is available)
        if "ask_user" in tool_names:
            sections.append(self._ask_user_policy_section())

        # 5. Workspace context
        if workspace_context:
            sections.append(f"# Workspace Context\n{workspace_context}")

        # 6. Extra instructions
        if extra_instructions:
            sections.append(f"# Additional Instructions\n{extra_instructions}")

        return "\n\n".join(sections)

    # ── Private Section Builders ─────────────────────────────────

    def _output_format_section(self, native_tool_mode: bool = False) -> str:
        """Standard output format instructions for all agents."""
        template_name = "output_format_native" if native_tool_mode else "output_format"
        template = self._data_registry.get_prompt_template(template_name)
        # Avoid str.format because templates intentionally include JSON braces.
        return template.replace("{title_max_words}", str(self._title_max_words))

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

    def _ask_user_policy_section(self) -> str:
        """Hard policy for how the agent must ask user questions."""
        return self._data_registry.get_prompt_template("clarification_policy")
