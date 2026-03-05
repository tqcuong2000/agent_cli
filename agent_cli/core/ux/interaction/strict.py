"""Strict workspace manager with path jailing + deny/allow policies."""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
from typing import Iterable, Sequence

from agent_cli.core.infra.events.errors import ToolExecutionError
from agent_cli.core.ux.interaction.base import BaseWorkspaceManager

logger = logging.getLogger(__name__)


class StrictWorkspaceManager(BaseWorkspaceManager):
    """Enforce strict in-workspace path access with deny-list patterns."""

    def __init__(
        self,
        root_path: Path,
        *,
        deny_patterns: Sequence[str] | None = None,
        allow_overrides: Sequence[str] | None = None,
    ) -> None:
        self._root = root_path.resolve()
        self._deny_patterns = tuple(deny_patterns or ())
        self._allow_overrides = tuple(allow_overrides or ())

    def resolve_path(
        self,
        path: str,
        *,
        must_exist: bool = False,
        writable: bool = False,
    ) -> Path:
        user_path = Path(path)
        candidate = user_path if user_path.is_absolute() else (self._root / user_path)
        resolved = candidate.resolve(strict=False)

        if not self._is_within_root(resolved):
            raise ToolExecutionError(
                f"Path '{path}' resolves outside the workspace. "
                f"All file operations must stay within: {self._root}",
                tool_name="workspace",
            )

        if must_exist and not resolved.exists():
            raise ToolExecutionError(
                f"File not found: {path}",
                tool_name="workspace",
            )

        # Existing symlinks are fully resolved above; if they point out of root,
        # the jail check already fails. We still raise policy errors for denied paths.
        if not self.is_allowed(resolved):
            raise ToolExecutionError(
                f"Access denied by workspace policy: {path}",
                tool_name="workspace",
            )

        if writable:
            logger.debug(
                "Write access granted by strict workspace policy: %s", resolved
            )

        return resolved

    def is_allowed(self, path: str | Path) -> bool:
        resolved = self._resolve_within_root(path)
        if resolved is None:
            return False

        rel = self._to_rel_posix(resolved)
        if self._matches_any(rel, self._allow_overrides):
            return True

        return not self._matches_any(rel, self._deny_patterns)

    def get_root(self) -> Path:
        return self._root

    @property
    def root_path(self) -> Path:
        """Backward-compatible alias used by legacy tool code/tests."""
        return self._root

    def _resolve_within_root(self, path: str | Path) -> Path | None:
        p = Path(path)
        candidate = p if p.is_absolute() else (self._root / p)
        resolved = candidate.resolve(strict=False)
        if not self._is_within_root(resolved):
            return None
        return resolved

    def _is_within_root(self, path: Path) -> bool:
        try:
            path.relative_to(self._root)
            return True
        except ValueError:
            return False

    def _to_rel_posix(self, path: Path) -> str:
        rel = path.relative_to(self._root)
        text = rel.as_posix()
        return text if text else "."

    @staticmethod
    def _matches_any(rel_path: str, patterns: Iterable[str]) -> bool:
        rel_norm = rel_path.replace("\\", "/")
        basename = Path(rel_norm).name

        for pattern in patterns:
            pat = pattern.replace("\\", "/").strip()
            if not pat:
                continue

            if pat.endswith("/"):
                prefix = pat.rstrip("/")
                if rel_norm == prefix or rel_norm.startswith(prefix + "/"):
                    return True
                continue

            if fnmatch.fnmatch(rel_norm, pat):
                return True
            if fnmatch.fnmatch(basename, pat):
                return True

        return False
