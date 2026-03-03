"""Session manager persistence tests."""

from __future__ import annotations

from pathlib import Path

from agent_cli.session.file_store import FileSessionManager


def test_file_session_manager_round_trip(tmp_path: Path):
    manager = FileSessionManager(session_dir=tmp_path, default_model="gpt-4o")

    session = manager.create_session(name="my-session")
    session.messages.append({"role": "user", "content": "hello"})
    session.messages.append({"role": "assistant", "content": "hi"})
    session.task_ids.append("task-1")
    session.total_cost = 0.42

    manager.save(session)

    loaded = manager.load(session.session_id)
    assert loaded.session_id == session.session_id
    assert loaded.name == "my-session"
    assert loaded.active_model == "gpt-4o"
    assert loaded.task_ids == ["task-1"]
    assert loaded.total_cost == 0.42
    assert len(loaded.messages) == 2

    listed = manager.list()
    assert len(listed) == 1
    assert listed[0].session_id == session.session_id
    assert listed[0].message_count == 2

    assert manager.delete(session.session_id) is True
    assert manager.delete(session.session_id) is False
    assert manager.list() == []


def test_file_session_manager_active_session_persists_across_instances(tmp_path: Path):
    manager_a = FileSessionManager(
        session_dir=tmp_path, default_model="claude-3-5-sonnet"
    )
    session = manager_a.create_session()
    session.messages.append({"role": "user", "content": "persist me"})
    manager_a.save(session)

    # Simulate app restart: fresh manager should recover active session from index file.
    manager_b = FileSessionManager(
        session_dir=tmp_path, default_model="gemini-2.5-flash"
    )
    active = manager_b.get_active()

    assert active is not None
    assert active.session_id == session.session_id
    assert active.active_model == "claude-3-5-sonnet"
    assert len(active.messages) == 1
