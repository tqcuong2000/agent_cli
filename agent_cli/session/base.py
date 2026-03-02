"""Session persistence contracts and shared data models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


@dataclass
class Session:
    """A persisted multi-turn conversation session."""

    session_id: str
    name: Optional[str] = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    messages: List[Dict[str, Any]] = field(default_factory=list)
    active_model: str = ""
    total_cost: float = 0.0
    task_ids: List[str] = field(default_factory=list)
    # UI-friendly metadata for session manager overlays/lists.
    # `last_activity_at` is a stable source for "time ago" and date rendering.
    # `last_message_preview` can be shown as a one-line summary.
    last_activity_at: datetime = field(default_factory=utc_now)
    last_message_preview: str = ""


@dataclass
class SessionSummary:
    """Compact metadata used for session listings."""

    session_id: str
    name: Optional[str]
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime
    message_count: int
    active_model: str
    total_cost: float
    display_name: str
    is_active: bool
    last_message_preview: str


class AbstractSessionManager(ABC):
    """Persistence API for session lifecycle management."""

    @abstractmethod
    def create_session(self, name: Optional[str] = None) -> Session:
        """Create a fresh session and set it as active."""

    @abstractmethod
    def save(self, session: Session) -> None:
        """Persist a session."""

    @abstractmethod
    def load(self, session_id: str) -> Session:
        """Load a session by ID and set it active."""

    @abstractmethod
    def list(self) -> List[SessionSummary]:
        """List all persisted sessions."""

    @abstractmethod
    def delete(self, session_id: str) -> bool:
        """Delete a session by ID. Returns True if removed."""

    @abstractmethod
    def get_active(self) -> Optional[Session]:
        """Get the active session, if any."""

    @abstractmethod
    def clear_active(self) -> None:
        """Clear active-session pointer without deleting saved sessions."""
