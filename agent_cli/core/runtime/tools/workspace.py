"""Deprecated workspace wrapper kept for backward compatibility."""

from __future__ import annotations

from pathlib import Path

from agent_cli.core.infra.config.config import AgentSettings
from agent_cli.core.ux.interaction.strict import StrictWorkspaceManager


class WorkspaceContext(StrictWorkspaceManager):
    """Thin wrapper around ``StrictWorkspaceManager``.

    Existing code still imports ``WorkspaceContext`` from this module.
    New code should import ``StrictWorkspaceManager`` from
    ``agent_cli.core.ux.interaction.strict``.
    """

    def __init__(self, root_path: Path) -> None:
        settings = AgentSettings()
        super().__init__(
            root_path=root_path,
            deny_patterns=settings.workspace_deny_patterns,
            allow_overrides=settings.workspace_allow_overrides,
        )
