"""Session persistence interfaces and file-backed implementation."""

from .base import AbstractSessionManager, Session, SessionSummary
from .file_store import FileSessionManager

__all__ = [
    "AbstractSessionManager",
    "Session",
    "SessionSummary",
    "FileSessionManager",
]
