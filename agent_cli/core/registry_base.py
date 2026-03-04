"""Shared lifecycle primitives for mutable registries."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class RegistryLifecycleMixin:
    """Add a validate->freeze lifecycle to registry-like classes.

    Subclasses should call ``self._assert_mutable()`` at the top of each
    mutating method and may override ``validate()`` for consistency checks.
    """

    _frozen: bool = False
    _registry_name: str = "unnamed"

    def freeze(self) -> None:
        """Validate and lock against further mutation."""
        if self._frozen:
            return

        self.validate()
        self._frozen = True
        logger.info(
            "Registry '%s' frozen (%s).",
            self._registry_name,
            self._freeze_summary(),
        )

    def validate(self) -> None:
        """Validate internal state before freeze.

        Override in subclasses. Raise ``RuntimeError`` for invalid state.
        """

    @property
    def is_frozen(self) -> bool:
        """Return ``True`` when mutation is no longer allowed."""
        return self._frozen

    def _assert_mutable(self) -> None:
        """Guard used by mutating methods."""
        if self._frozen:
            raise RuntimeError(
                f"Registry '{self._registry_name}' is frozen; mutations rejected."
            )

    def _freeze_summary(self) -> str:
        """Return a human-readable summary for freeze logs."""
        return "ok"
