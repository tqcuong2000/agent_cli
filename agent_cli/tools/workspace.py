"""Deprecated workspace wrapper kept for backward compatibility."""

from __future__ import annotations

from pathlib import Path

from agent_cli.workspace.strict import StrictWorkspaceManager


class WorkspaceContext(StrictWorkspaceManager):
    """Thin wrapper around ``StrictWorkspaceManager``.

    Existing code still imports ``WorkspaceContext`` from this module.
    New code should import ``StrictWorkspaceManager`` from
    ``agent_cli.workspace.strict``.
    """

    def __init__(self, root_path: Path) -> None:
        super().__init__(root_path=root_path)
