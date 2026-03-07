"""Shared terminal output sanitization helpers."""

from __future__ import annotations

import re

_ANSI_CSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC_RE = re.compile(r"\x1B\][^\x1B\x07]*(?:\x07|\x1B\\)")
_ANSI_SS3_RE = re.compile(r"\x1BO[@-~]")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0B-\x1A\x1C-\x1F\x7F]")


def sanitize_terminal_output(text: str) -> str:
    """Strip terminal control sequences from command output."""
    sanitized = _ANSI_OSC_RE.sub("", text)
    sanitized = _ANSI_CSI_RE.sub("", sanitized)
    sanitized = _ANSI_SS3_RE.sub("", sanitized)
    sanitized = _CTRL_CHARS_RE.sub("", sanitized)
    return sanitized


__all__ = ["sanitize_terminal_output"]
