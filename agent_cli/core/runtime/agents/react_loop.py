"""
ReAct Loop Helpers — ``StuckDetector`` and ``PromptBuilder``.

These are utility classes used by ``BaseAgent.handle_task()`` to
detect repetitive tool-call loops and assemble dynamic system prompts.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.runtime.tools.registry import ToolRegistry

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
        self._recent_batches: List[int] = []

    def is_stuck(self, tool_name: str, result: str) -> bool:
        """Check if the agent is repeating the same action.

        Args:
            tool_name: The tool that was just executed.
            result:    The formatted result string from the tool.

        Returns:
            ``True`` if the last N calls were identical (same tool
            + same result hash).
        """
        normalized_result = self._normalize_result_for_stuck_check(result)
        key = (tool_name, hash(normalized_result))
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
        self._recent_batches.clear()

    def is_stuck_batch(self, action_results: List[tuple[str, str]]) -> bool:
        """Check if the agent is repeating the same action batch."""
        if not action_results:
            return False

        fingerprint_parts: List[tuple[str, int]] = []
        for tool_name, result in sorted(action_results, key=lambda item: item[0]):
            normalized_result = self._normalize_result_for_stuck_check(result)
            fingerprint_parts.append((tool_name, hash(normalized_result)))
        batch_fingerprint = hash(tuple(fingerprint_parts))
        self._recent_batches.append(batch_fingerprint)

        if len(self._recent_batches) < self.threshold:
            return False

        last_n = self._recent_batches[-self.threshold :]
        if all(fp == last_n[0] for fp in last_n):
            self._recent_batches.clear()
            logger.warning(
                "Batch stuck detected: same %d-action batch repeated %d times",
                len(action_results),
                self.threshold,
            )
            return True

        if len(self._recent_batches) > self.history_cap:
            self._recent_batches = self._recent_batches[-self.history_cap :]

        return False

    @staticmethod
    def _normalize_result_for_stuck_check(result: str) -> str:
        """Remove volatile envelope fields so repeated outcomes can match."""
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            return result

        if not isinstance(parsed, dict):
            return result
        if parsed.get("type") != "tool_result":
            return result

        payload = parsed.get("payload", {})
        if not isinstance(payload, dict):
            return result

        stable = {
            "status": payload.get("status"),
            "truncated": payload.get("truncated"),
            "truncated_chars": payload.get("truncated_chars"),
            "output": payload.get("output"),
        }
        return json.dumps(stable, sort_keys=True, separators=(",", ":"))


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
        data_registry: DataRegistry,
    ) -> None:
        self.tool_registry = tool_registry
        self._data_registry = data_registry
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
        multi_action: bool = False,
        provider_managed_capabilities: List[str] | None = None,
    ) -> str:
        """Assemble a complete system prompt.

        Sections:
        1. Agent persona / role
        2. Output format instructions (JSON contract)
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
        sections.append(
            self._output_format_section(
                native_tool_mode=native_tool_mode,
                multi_action=multi_action,
            )
        )

        # 3. Tool descriptions
        if tool_names:
            tool_defs = self.tool_registry.get_definitions_for_llm(tool_names)
            sections.append(self._tools_section(tool_defs))

        # 3.5 Provider-managed capabilities (not local ToolRegistry tools)
        if provider_managed_capabilities:
            sections.append(
                self._provider_capabilities_section(provider_managed_capabilities)
            )

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

    def _output_format_section(
        self,
        native_tool_mode: bool = False,
        multi_action: bool = False,
    ) -> str:
        """Standard output format instructions for all agents."""
        if multi_action:
            template_name = (
                "output_format_multi_native"
                if native_tool_mode
                else "output_format_multi"
            )
        else:
            template_name = (
                "output_format_native" if native_tool_mode else "output_format"
            )
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

    @staticmethod
    def _provider_capabilities_section(capabilities: List[str]) -> str:
        """Render provider-managed capabilities that are not local tools."""
        normalized = [str(item).strip() for item in capabilities if str(item).strip()]
        if not normalized:
            return ""

        lines = [
            "# Provider-Managed Capabilities",
            (
                "The following capabilities are available through the model provider "
                "and are not listed as local tools:"
            ),
        ]
        for capability in sorted(set(normalized)):
            if capability == "web_search":
                lines.append(
                    "- `web_search`: Provider-native web search is configured, but availability depends on provider/deployment support at runtime."
                )
            else:
                lines.append(f"- `{capability}`")
        return "\n".join(lines)
