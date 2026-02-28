"""
Tool Registry — centralized catalog for all available tools.

The registry is populated at startup and consumed by:
- ``ToolExecutor``: looks up tools by name to execute them.
- ``BaseToolFormatter``: generates LLM-compatible tool definitions.
- ``BaseAgent``: retrieves filtered tool subsets based on agent role.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agent_cli.tools.base import BaseTool, ToolCategory

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Tool Registry
# ══════════════════════════════════════════════════════════════════════


class ToolRegistry:
    """Central catalog of all available tools.

    Agents are initialized with a filtered subset based on their role.
    The ``BaseToolFormatter`` reads from this to generate LLM tool
    definitions.
    """

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    # ── Registration ─────────────────────────────────────────────

    def register(self, tool: BaseTool) -> None:
        """Register a tool.  Raises ``ValueError`` if name is taken."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered.")
        self._tools[tool.name] = tool
        logger.debug("Registered tool: %s (category=%s)", tool.name, tool.category.name)

    # ── Lookup ───────────────────────────────────────────────────

    def get(self, name: str) -> Optional[BaseTool]:
        """Retrieve a tool by name.  Returns ``None`` if not found."""
        return self._tools.get(name)

    def get_by_category(self, category: ToolCategory) -> List[BaseTool]:
        """Get all tools in a category."""
        return [t for t in self._tools.values() if t.category == category]

    def get_for_agent(self, tool_names: List[str]) -> List[BaseTool]:
        """Return a filtered list of tools for a specific agent.

        Used during agent initialization to assign its tool set.

        Raises:
            ValueError: If a requested tool name is not found.
        """
        tools: List[BaseTool] = []
        for name in tool_names:
            tool = self._tools.get(name)
            if tool is None:
                raise ValueError(f"Tool '{name}' not found in registry.")
            tools.append(tool)
        return tools

    # ── Introspection ────────────────────────────────────────────

    def get_all_names(self) -> List[str]:
        """Return all registered tool names (for the Schema Validator)."""
        return list(self._tools.keys())

    def get_definitions_for_llm(
        self, tool_names: List[str]
    ) -> List[Dict[str, Any]]:
        """Generate standardized tool definitions consumable by
        ``BaseToolFormatter``.

        Each definition includes: name, description, parameters
        (JSON Schema), is_safe, and category.

        Raises:
            KeyError: If a tool name is not registered.
        """
        definitions: List[Dict[str, Any]] = []
        for name in tool_names:
            if name not in self._tools:
                raise KeyError(f"Tool '{name}' not found in registry.")
            tool = self._tools[name]
            definitions.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.get_json_schema(),
                    "is_safe": tool.is_safe,
                    "category": tool.category.name,
                }
            )
        return definitions

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
