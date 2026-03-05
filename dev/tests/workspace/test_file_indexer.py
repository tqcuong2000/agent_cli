"""Tests for workspace file indexer (.gitignore + cache + invalidation)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_cli.core.infra.events.event_bus import AsyncEventBus
from agent_cli.core.infra.events.events import FileChangedEvent
from agent_cli.core.ux.interaction.file_index import FileIndexer


def test_file_indexer_respects_gitignore_and_writes_cache(tmp_path: Path):
    (tmp_path / ".gitignore").write_text(
        "*.log\nbuild/\n",
        encoding="utf-8",
    )
    (tmp_path / "a.py").write_text("print('a')", encoding="utf-8")
    (tmp_path / "b.log").write_text("ignore", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "x.txt").write_text("ignore", encoding="utf-8")

    cache_path = tmp_path / ".cache" / "file_index.json"
    indexer = FileIndexer(root_path=tmp_path, cache_path=cache_path, max_files=100)
    indexer.rebuild_sync()

    files = indexer.get_index()
    assert "a.py" in files
    assert "b.log" not in files
    assert "build/x.txt" not in files
    assert cache_path.exists()


def test_file_indexer_loads_from_cache(tmp_path: Path):
    (tmp_path / "a.py").write_text("print('a')", encoding="utf-8")
    cache_path = tmp_path / ".cache" / "file_index.json"

    builder = FileIndexer(root_path=tmp_path, cache_path=cache_path, max_files=100)
    builder.rebuild_sync()
    assert "a.py" in builder.get_index()

    # Remove source file after cache was created; new indexer should still load cache first.
    (tmp_path / "a.py").unlink()
    restored = FileIndexer(root_path=tmp_path, cache_path=cache_path, max_files=100)
    assert "a.py" in restored.files


def test_file_indexer_applies_max_file_cap(tmp_path: Path):
    for i in range(20):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")

    indexer = FileIndexer(
        root_path=tmp_path, cache_path=tmp_path / "idx.json", max_files=5
    )
    indexer.rebuild_sync()
    assert len(indexer.get_index()) == 5


@pytest.mark.asyncio
async def test_file_indexer_invalidates_on_file_changed_event(tmp_path: Path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    event_bus = AsyncEventBus()
    indexer = FileIndexer(
        root_path=tmp_path, cache_path=tmp_path / "idx.json", max_files=100
    )
    indexer.start(event_bus)

    await asyncio.sleep(0.05)  # allow initial background scan to settle
    assert indexer.is_stale is False

    indexer.start_background_scan = lambda: None  # type: ignore[assignment]
    await event_bus.publish(
        FileChangedEvent(
            source="test",
            file_path="a.txt",
            change_type="modified",
            agent_name="tester",
        )
    )
    assert indexer.is_stale is True

    await indexer.shutdown()
