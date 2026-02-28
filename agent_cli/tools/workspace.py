"""
Workspace Context — minimal path jailing for Phase 3.

Provides a ``WorkspaceContext`` that enforces all file operations
stay within a designated root directory.  Prevents path traversal
attacks (e.g. ``../../etc/passwd``).

.. note::

    The full ``BaseWorkspaceManager`` with sandbox configuration,
    allow-lists, and deny-patterns is a Phase 5 concern.  This
    module provides the minimal enforcement needed for Phase 3 tools.
"""

from __future__ import annotations

import logging
from pathlib import Path

from agent_cli.core.error_handler.errors import ToolExecutionError

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Workspace Context
# ══════════════════════════════════════════════════════════════════════


class WorkspaceContext:
    """Minimal workspace jailing for tool file operations.

    All tool paths are resolved against ``root_path`` and checked to
    ensure they remain within the workspace boundary.

    Attributes:
        root_path:    The absolute path to the workspace root.
    """

    def __init__(self, root_path: Path) -> None:
        self.root_path = root_path.resolve()

    def resolve_path(
        self,
        relative_path: str,
        *,
        must_exist: bool = False,
        writable: bool = False,
    ) -> Path:
        """Resolve a user-supplied path against the workspace root.

        Args:
            relative_path: Path from the LLM / tool call.  May be
                           relative or absolute.
            must_exist:    If ``True``, raise if the resolved path does
                           not exist on disk.
            writable:      If ``True``, this is a write operation — extra
                           logging for audit trail.

        Returns:
            The fully resolved, workspace-jailed ``Path``.

        Raises:
            ToolExecutionError: If the resolved path escapes the
                                workspace root, or if ``must_exist``
                                is set and the file is missing.
        """
        user_path = Path(relative_path)

        # Handle both relative and absolute paths
        if user_path.is_absolute():
            resolved = user_path.resolve()
        else:
            resolved = (self.root_path / user_path).resolve()

        # ── Jail check — must be under root ──
        try:
            resolved.relative_to(self.root_path)
        except ValueError:
            raise ToolExecutionError(
                f"Path '{relative_path}' resolves outside the workspace. "
                f"All file operations must stay within: {self.root_path}",
                tool_name="workspace",
            )

        # ── Existence check ──
        if must_exist and not resolved.exists():
            raise ToolExecutionError(
                f"File not found: {relative_path}",
                tool_name="workspace",
            )

        if writable:
            logger.debug("Write access granted: %s", resolved)

        return resolved
