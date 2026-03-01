"""Abstract workspace manager contract for path-jailing policies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class BaseWorkspaceManager(ABC):
    """Contract for workspace path resolution and access policy checks."""

    @abstractmethod
    def resolve_path(
        self,
        path: str,
        *,
        must_exist: bool = False,
        writable: bool = False,
    ) -> Path:
        """Resolve and validate a path under workspace policy."""

    @abstractmethod
    def is_allowed(self, path: str | Path) -> bool:
        """Whether a path is allowed by workspace policy."""

    @abstractmethod
    def get_root(self) -> Path:
        """Return workspace root path."""
