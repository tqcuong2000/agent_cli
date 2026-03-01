"""Workspace file index with gitignore filtering and disk cache."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import time
from pathlib import Path
from typing import Any, List, Optional

from agent_cli.core.events.event_bus import AbstractEventBus
from agent_cli.core.events.events import BaseEvent, FileChangedEvent

logger = logging.getLogger(__name__)


class FileIndexer:
    """Build and query a cached workspace file index."""

    def __init__(
        self,
        root_path: Path,
        *,
        cache_path: Optional[Path] = None,
        max_files: int = 5000,
    ) -> None:
        self.root_path = root_path.resolve()
        self.cache_path = cache_path or (
            Path.home() / ".agent_cli" / "cache" / "file_index.json"
        )
        self.max_files = max_files

        self._files: List[str] = []
        self._stale = True
        self._loaded_from_cache = False

        self._event_bus: Optional[AbstractEventBus] = None
        self._subscription_id: Optional[str] = None
        self._build_task: Optional[asyncio.Task[None]] = None

        self._gitignore_matcher: Optional[Any] = None
        self._fallback_gitignore_patterns: List[str] = []

        self._load_cache()
        self._loaded_from_cache = True

    @property
    def files(self) -> List[str]:
        return list(self._files)

    @property
    def is_stale(self) -> bool:
        return self._stale

    def start(self, event_bus: Optional[AbstractEventBus] = None) -> None:
        """Load cache, subscribe to file-change events, kick off background scan."""
        if self._loaded_from_cache is False:
            self._load_cache()
            self._loaded_from_cache = True

        if event_bus is not None and self._subscription_id is None:
            self._event_bus = event_bus
            self._subscription_id = event_bus.subscribe(
                "FileChangedEvent",
                self._on_file_changed,
                priority=40,
            )

        self.start_background_scan()

    async def shutdown(self) -> None:
        """Unsubscribe and wait for in-flight indexing task."""
        if self._event_bus is not None and self._subscription_id is not None:
            self._event_bus.unsubscribe(self._subscription_id)
        self._subscription_id = None
        self._event_bus = None

        if self._build_task is not None:
            try:
                await self._build_task
            finally:
                self._build_task = None

    def invalidate(self) -> None:
        self._stale = True

    def start_background_scan(self) -> None:
        if self._build_task is not None and not self._build_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.rebuild_sync()
            return
        self._build_task = loop.create_task(self._rebuild_async(), name="file-indexer")

    def get_index(self) -> List[str]:
        if self._stale and (self._build_task is None or self._build_task.done()):
            # Opportunistic sync build when requested without active background task.
            self.rebuild_sync()
        return list(self._files)

    def query(self, text: str) -> List[str]:
        needle = text.strip().lower()
        files = self.get_index()
        if not needle:
            return files
        return [p for p in files if needle in p.lower()]

    def rebuild_sync(self) -> None:
        files = self._scan_workspace()
        self._files = files
        self._stale = False
        self._save_cache()

    async def _rebuild_async(self) -> None:
        files = await asyncio.to_thread(self._scan_workspace)
        self._files = files
        self._stale = False
        await asyncio.to_thread(self._save_cache)

    async def _on_file_changed(self, event: BaseEvent) -> None:
        if not isinstance(event, FileChangedEvent):
            return
        self.invalidate()
        self.start_background_scan()

    def _scan_workspace(self) -> List[str]:
        self._load_gitignore_matcher()
        files: List[str] = []

        for path in self.root_path.rglob("*"):
            if len(files) >= self.max_files:
                break
            if path.is_dir():
                continue

            rel = path.relative_to(self.root_path).as_posix()
            if self._is_ignored(rel, is_dir=False):
                continue
            files.append(rel)

        files.sort()
        return files

    def _load_gitignore_matcher(self) -> None:
        gitignore_path = self.root_path / ".gitignore"
        patterns: List[str] = []

        if gitignore_path.exists():
            try:
                for raw in gitignore_path.read_text(encoding="utf-8").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    patterns.append(line)
            except OSError:
                patterns = []

        # Always exclude .git internals from index.
        patterns.append(".git/")

        self._gitignore_matcher = None
        self._fallback_gitignore_patterns = patterns

        if not patterns:
            return

        try:
            import pathspec  # type: ignore

            self._gitignore_matcher = pathspec.PathSpec.from_lines(
                "gitwildmatch", patterns
            )
        except Exception:
            self._gitignore_matcher = None

    def _is_ignored(self, rel_path: str, *, is_dir: bool) -> bool:
        target = rel_path.replace("\\", "/")
        matcher = self._gitignore_matcher
        match_file = (
            getattr(matcher, "match_file", None) if matcher is not None else None
        )
        if callable(match_file):
            pathspec_target = target + "/" if is_dir else target
            try:
                return bool(match_file(pathspec_target))
            except Exception:
                pass

        basename = Path(target).name
        for pattern in self._fallback_gitignore_patterns:
            pat = pattern.replace("\\", "/").strip()
            if not pat:
                continue
            if pat.endswith("/"):
                prefix = pat.rstrip("/")
                if target == prefix or target.startswith(prefix + "/"):
                    return True
                continue
            if fnmatch.fnmatch(target, pat) or fnmatch.fnmatch(basename, pat):
                return True
        return False

    def _load_cache(self) -> None:
        if not self.cache_path.exists():
            return

        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return

        if payload.get("version") != 1:
            return
        if payload.get("root") != str(self.root_path):
            return
        if int(payload.get("max_files", 0)) != self.max_files:
            return

        files = payload.get("files", [])
        if not isinstance(files, list):
            return

        normalized = [str(v) for v in files if isinstance(v, str)]
        self._files = normalized[: self.max_files]
        self._stale = False

    def _save_cache(self) -> None:
        payload = {
            "version": 1,
            "root": str(self.root_path),
            "max_files": self.max_files,
            "generated_at": time.time(),
            "files": self._files[: self.max_files],
        }
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
