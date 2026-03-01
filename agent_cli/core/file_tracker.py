from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from agent_cli.core.events.event_bus import AbstractEventBus
from agent_cli.core.events.events import FileChangedEvent

logger = logging.getLogger(__name__)


class ChangeType(str, Enum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass
class FileChange:
    path: Path
    change_type: ChangeType
    original_content: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)


class FileChangeTracker:
    def __init__(self, event_bus: AbstractEventBus):
        self.event_bus = event_bus
        self.workspace_root: Optional[Path] = None
        self._changes: Dict[Path, FileChange] = {}

    def start_tracking(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self._changes.clear()
        logger.info(f"File tracking started at workspace: {self.workspace_root}")

    async def record_change(
        self,
        path: Path | str,
        change_type: ChangeType,
        agent_name: str = "Assistant",
    ) -> None:
        abs_path = self._resolve_to_abs_path(path)

        if abs_path not in self._changes:
            # Snapshot original file content at first observation.
            original_content = None
            if abs_path.exists() and abs_path.is_file():
                try:
                    original_content = abs_path.read_text(encoding="utf-8")
                except Exception as e:
                    logger.warning(
                        f"Could not read content for snapshot of {abs_path}: {e}"
                    )

            change = FileChange(
                path=abs_path,
                change_type=change_type,
                original_content=original_content,
            )
            self._changes[abs_path] = change

            await self._publish_file_changed(
                abs_path=abs_path,
                change_type=change_type,
                agent_name=agent_name,
            )
            return

        # Existing tracked file: keep original snapshot, only refresh timestamp.
        current_change = self._changes[abs_path]
        current_change.timestamp = datetime.now()

        await self._publish_file_changed(
            abs_path=abs_path,
            change_type=current_change.change_type,
            agent_name=agent_name,
        )

    def get_changes(self) -> List[FileChange]:
        return list(self._changes.values())

    def total_files(self) -> int:
        return len(self._changes)

    def is_empty(self) -> bool:
        return len(self._changes) == 0

    def reset(self) -> None:
        self._changes.clear()

    def has_change(self, path: Path | str) -> bool:
        abs_path = self._resolve_to_abs_path(path)
        return abs_path in self._changes

    def get_change(self, path: Path | str) -> Optional[FileChange]:
        abs_path = self._resolve_to_abs_path(path)
        return self._changes.get(abs_path)

    async def accept_file(self, path: Path | str) -> bool:
        """Accept a single tracked file change (remove it from pending review)."""
        abs_path = self._resolve_to_abs_path(path)
        if abs_path not in self._changes:
            return False
        self._changes.pop(abs_path, None)
        return True

    async def reject_file(self, path: Path | str) -> bool:
        """Reject a single tracked file change by reverting it from snapshot."""
        abs_path = self._resolve_to_abs_path(path)
        change = self._changes.get(abs_path)
        if change is None:
            return False

        reverted = await self._revert_change(change)
        if reverted:
            self._changes.pop(abs_path, None)
        return reverted

    async def revert_all(self) -> None:
        for change in list(self._changes.values()):
            await self._revert_change(change)
        self.reset()

    async def _revert_change(self, change: FileChange) -> bool:
        try:
            if change.change_type == ChangeType.CREATED:
                if change.path.exists():
                    change.path.unlink()
                return True

            if change.change_type == ChangeType.MODIFIED:
                if change.original_content is None:
                    logger.warning(
                        "No original snapshot for MODIFIED file; cannot revert: %s",
                        change.path,
                    )
                    return False
                change.path.parent.mkdir(parents=True, exist_ok=True)
                change.path.write_text(change.original_content, encoding="utf-8")
                return True

            if change.change_type == ChangeType.DELETED:
                if change.original_content is None:
                    logger.warning(
                        "No original snapshot for DELETED file; cannot restore: %s",
                        change.path,
                    )
                    return False
                change.path.parent.mkdir(parents=True, exist_ok=True)
                change.path.write_text(change.original_content, encoding="utf-8")
                return True

            logger.warning(
                "Unknown change type for %s: %s", change.path, change.change_type
            )
            return False
        except Exception as e:
            logger.error(f"Failed to revert {change.path}: {e}")
            return False

    async def _publish_file_changed(
        self,
        *,
        abs_path: Path,
        change_type: ChangeType,
        agent_name: str,
    ) -> None:
        event = FileChangedEvent(
            file_path=self.to_relative_path_str(abs_path),
            change_type=change_type.value,
            agent_name=agent_name,
        )
        await self.event_bus.publish(event)

    def to_relative_path_str(self, path: Path | str) -> str:
        abs_path = self._resolve_to_abs_path(path)
        if self.workspace_root:
            try:
                return str(abs_path.relative_to(self.workspace_root))
            except ValueError:
                pass
        return str(abs_path)

    def _resolve_to_abs_path(self, path: Path | str) -> Path:
        p = Path(path)
        if p.is_absolute():
            return p.resolve()

        if self.workspace_root is not None:
            return (self.workspace_root / p).resolve()

        return p.resolve()
