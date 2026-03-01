"""JSON file-backed session storage with atomic writes."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_cli.session.base import (
    AbstractSessionManager,
    Session,
    SessionSummary,
    utc_now,
)

logger = logging.getLogger(__name__)


class FileSessionManager(AbstractSessionManager):
    """Persist sessions as JSON files under ``~/.agent_cli/sessions``."""

    def __init__(
        self,
        session_dir: Optional[Path] = None,
        *,
        default_model: str = "",
    ) -> None:
        self._session_dir = session_dir or (Path.home() / ".agent_cli" / "sessions")
        self._active_index_path = self._session_dir / "active_session.json"
        self._default_model = default_model
        self._active_session: Optional[Session] = None

    def create_session(self, name: Optional[str] = None) -> Session:
        now = utc_now()
        session = Session(
            session_id=str(uuid.uuid4()),
            name=name,
            created_at=now,
            updated_at=now,
            messages=[],
            active_model=self._default_model,
            total_cost=0.0,
            task_ids=[],
        )
        self._active_session = session
        return session

    def save(self, session: Session) -> None:
        session.updated_at = utc_now()
        path = self._session_path(session.session_id)
        payload = self._session_to_dict(session)
        self._atomic_write_json(path, payload)
        self._set_active_id(session.session_id)
        self._active_session = session
        logger.debug("Saved session %s", session.session_id)

    def load(self, session_id: str) -> Session:
        path = self._session_path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session '{session_id}' not found")

        payload = self._read_json(path)
        session = self._session_from_dict(payload)
        self._active_session = session
        self._set_active_id(session.session_id)
        return session

    def list(self) -> List[SessionSummary]:
        summaries: List[SessionSummary] = []

        for path in self._session_dir.glob("*.json"):
            if path.name == self._active_index_path.name:
                continue
            try:
                payload = self._read_json(path)
                session = self._session_from_dict(payload)
                summaries.append(
                    SessionSummary(
                        session_id=session.session_id,
                        name=session.name,
                        created_at=session.created_at,
                        updated_at=session.updated_at,
                        message_count=len(session.messages),
                        active_model=session.active_model,
                        total_cost=session.total_cost,
                    )
                )
            except Exception as exc:
                logger.warning("Skipping unreadable session file '%s': %s", path, exc)

        summaries.sort(key=lambda s: s.updated_at, reverse=True)
        return summaries

    def delete(self, session_id: str) -> bool:
        path = self._session_path(session_id)
        if not path.exists():
            return False

        path.unlink()

        active_id = self._get_active_id()
        if active_id == session_id:
            self._clear_active_id()
            self._active_session = None
        elif self._active_session and self._active_session.session_id == session_id:
            self._active_session = None

        logger.debug("Deleted session %s", session_id)
        return True

    def get_active(self) -> Optional[Session]:
        if self._active_session is not None:
            return self._active_session

        active_id = self._get_active_id()
        if not active_id:
            return None

        try:
            return self.load(active_id)
        except FileNotFoundError:
            self._clear_active_id()
            return None

    def clear_active(self) -> None:
        self._active_session = None
        self._clear_active_id()

    # ── Internal helpers ───────────────────────────────────────

    def _session_path(self, session_id: str) -> Path:
        return self._session_dir / f"{session_id}.json"

    def _set_active_id(self, session_id: str) -> None:
        self._atomic_write_json(
            self._active_index_path, {"active_session_id": session_id}
        )

    def _get_active_id(self) -> Optional[str]:
        if not self._active_index_path.exists():
            return None
        payload = self._read_json(self._active_index_path)
        value = payload.get("active_session_id")
        if isinstance(value, str) and value:
            return value
        return None

    def _clear_active_id(self) -> None:
        if self._active_index_path.exists():
            self._active_index_path.unlink()

    def _atomic_write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=True, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _session_to_dict(session: Session) -> Dict[str, Any]:
        return {
            "session_id": session.session_id,
            "name": session.name,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "messages": session.messages,
            "active_model": session.active_model,
            "total_cost": session.total_cost,
            "task_ids": session.task_ids,
        }

    @staticmethod
    def _session_from_dict(payload: Dict[str, Any]) -> Session:
        return Session(
            session_id=str(payload.get("session_id", "")),
            name=payload.get("name"),
            created_at=_parse_datetime(payload.get("created_at")),
            updated_at=_parse_datetime(payload.get("updated_at")),
            messages=_coerce_messages(payload.get("messages")),
            active_model=str(payload.get("active_model", "")),
            total_cost=float(payload.get("total_cost", 0.0)),
            task_ids=[str(v) for v in payload.get("task_ids", [])],
        )


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return utc_now()


def _coerce_messages(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [m for m in value if isinstance(m, dict)]
    return []
