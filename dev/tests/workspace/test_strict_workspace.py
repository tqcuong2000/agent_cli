"""Tests for strict workspace path-jailing policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_cli.core.infra.config.config import AgentSettings
from agent_cli.core.infra.events.errors import ToolExecutionError
from agent_cli.core.ux.interaction.strict import StrictWorkspaceManager


def test_strict_workspace_blocks_path_traversal(tmp_path: Path):
    manager = StrictWorkspaceManager(root_path=tmp_path)

    with pytest.raises(ToolExecutionError, match="outside the workspace"):
        manager.resolve_path("../../etc/passwd")


def test_strict_workspace_blocks_absolute_escape(tmp_path: Path):
    manager = StrictWorkspaceManager(root_path=tmp_path)

    outside = (
        Path("C:/Windows/system.ini") if Path("C:/").exists() else Path("/etc/hosts")
    )
    with pytest.raises(ToolExecutionError, match="outside the workspace"):
        manager.resolve_path(str(outside))


def test_strict_workspace_denies_sensitive_patterns(tmp_path: Path):
    manager = StrictWorkspaceManager(
        root_path=tmp_path,
        deny_patterns=AgentSettings().workspace_deny_patterns,
    )

    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]", encoding="utf-8")
    (tmp_path / "private.key").write_text("key", encoding="utf-8")

    with pytest.raises(ToolExecutionError, match="Access denied by workspace policy"):
        manager.resolve_path(".env", must_exist=True)
    with pytest.raises(ToolExecutionError, match="Access denied by workspace policy"):
        manager.resolve_path(".git/config", must_exist=True)
    with pytest.raises(ToolExecutionError, match="Access denied by workspace policy"):
        manager.resolve_path("private.key", must_exist=True)


def test_strict_workspace_allow_override_can_permit_denied_path(tmp_path: Path):
    manager = StrictWorkspaceManager(
        root_path=tmp_path,
        deny_patterns=(".env",),
        allow_overrides=(".env",),
    )
    env_file = tmp_path / ".env"
    env_file.write_text("TOKEN=not-secret", encoding="utf-8")

    resolved = manager.resolve_path(".env", must_exist=True)
    assert resolved == env_file.resolve()


def test_strict_workspace_symlink_escape_is_blocked(tmp_path: Path):
    manager = StrictWorkspaceManager(root_path=tmp_path)

    outside_dir = tmp_path.parent / "outside-target"
    outside_dir.mkdir(exist_ok=True)
    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("secret", encoding="utf-8")

    link = tmp_path / "escape-link.txt"
    try:
        link.symlink_to(outside_file)
    except (NotImplementedError, OSError):
        pytest.skip("Symlink creation not supported in this environment")

    with pytest.raises(ToolExecutionError, match="outside the workspace"):
        manager.resolve_path("escape-link.txt", must_exist=True)
