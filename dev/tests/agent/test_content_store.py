from __future__ import annotations

from agent_cli.core.runtime.agents.content_store import ContentStore


def test_content_store_round_trip() -> None:
    store = ContentStore(max_entries=3)
    content_ref = store.store("hello world")

    assert content_ref.startswith("sha256:")
    assert store.has(content_ref) is True
    assert store.resolve(content_ref) == "hello world"


def test_content_store_evicts_oldest_entry() -> None:
    store = ContentStore(max_entries=2)
    ref_a = store.store("A")
    ref_b = store.store("B")
    _ = store.store("C")

    assert store.has(ref_a) is False
    assert store.has(ref_b) is True
    assert store.size == 2
