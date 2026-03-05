"""
Base Tool Abstractions — ``BaseTool`` ABC, ``ToolCategory``, and ``ToolResult``.

Every tool in the system inherits from ``BaseTool`` and declares:

* **name** — unique identifier used by the LLM to invoke the tool.
* **description** — human-readable docstring (injected into prompts).
* **args_schema** — Pydantic model for arguments (auto JSON Schema for native FC).
* **is_safe** — whether this tool requires user approval before execution.
* **category** — grouping for agent-level tool filtering.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Type

from pydantic import BaseModel

# ══════════════════════════════════════════════════════════════════════
# Tool Category
# ══════════════════════════════════════════════════════════════════════


class ToolCategory(Enum):
    """Categories for organizing and filtering tools."""

    FILE = auto()  # read_file, write_file, edit_file
    SEARCH = auto()  # grep_search, find_files
    EXECUTION = auto()  # run_command, spawn_terminal
    TERMINAL = auto()  # read_terminal, send_terminal_input, kill_terminal
    UTILITY = auto()  # sleep, wait_for_terminal, ask_user


# ══════════════════════════════════════════════════════════════════════
# Tool Result
# ══════════════════════════════════════════════════════════════════════


@dataclass
class ToolResult:
    """Standardized result from a tool execution.

    Used internally between ``ToolExecutor`` and the Agent loop.
    The ``output`` field contains the formatted string that the Agent
    sees in its Working Memory.
    """

    success: bool = True
    output: str = ""
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    action_id: str = ""
    tool_name: str = ""


# ══════════════════════════════════════════════════════════════════════
# Base Tool ABC
# ══════════════════════════════════════════════════════════════════════


class BaseTool(ABC):
    """Abstract base class for all tools in the system.

    Every tool declares:
    - name:        Unique identifier used by the LLM to invoke the tool.
    - description: Human-readable docstring (injected into LLM prompt
                   for prompt mode).
    - args_schema: Pydantic model defining expected arguments
                   (auto-converted to JSON Schema for native FC).
    - is_safe:     Whether this tool can execute without user approval.
    - category:    Grouping for tool filtering (e.g., file-only agents
                   get FILE + SEARCH tools).
    - parallel_safe: Whether this tool can run in parallel with other tools.
    """

    name: str
    description: str
    is_safe: bool = False
    category: ToolCategory = ToolCategory.UTILITY
    parallel_safe: bool = True

    @property
    @abstractmethod
    def args_schema(self) -> Type[BaseModel]:
        """Return the Pydantic model class for this tool's arguments.

        Used for:
        - Argument validation before execution.
        - Auto-generating JSON Schema for native FC providers.
        - Auto-generating text descriptions for prompt injection.
        """

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        """Execute the tool with validated arguments.

        Returns:
            A formatted string result (passed to ``ToolOutputFormatter``
            before reaching the Agent's Working Memory).

        Raises:
            ToolExecutionError: On any recoverable failure (file not
            found, permission denied, command failed).  The error
            handler in the Agent loop returns this as an observation,
            not a crash.
        """

    def validate_args(self, **kwargs: Any) -> BaseModel:
        """Validate arguments against the Pydantic schema.

        Raises ``ValidationError`` with a helpful message if invalid.
        """
        return self.args_schema(**kwargs)

    def get_json_schema(self) -> Dict[str, Any]:
        """Generate JSON Schema for native FC providers.

        Used by ``BaseToolFormatter.format_for_native_fc()``.
        """
        return self.args_schema.model_json_schema()
