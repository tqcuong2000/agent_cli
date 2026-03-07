"""Shared subprocess execution helpers."""

from __future__ import annotations

import os
from collections.abc import Mapping

_PYTHON_ENV_DEFAULTS = {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
}


def build_subprocess_env(
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a subprocess environment with UTF-8-safe Python defaults."""
    env = dict(os.environ if base_env is None else base_env)
    for key, value in _PYTHON_ENV_DEFAULTS.items():
        env.setdefault(key, value)
    return env
