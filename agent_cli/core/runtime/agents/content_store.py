"""Session-scoped store for referenceable content blocks."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from threading import Lock
from typing import Optional


class ContentStore:
    """Thread-safe content reference store with bounded capacity."""

    def __init__(self, max_entries: int = 50) -> None:
        self._max_entries = max(int(max_entries), 1)
        self._store: OrderedDict[str, str] = OrderedDict()
        self._lock = Lock()

    def store(self, content: str) -> str:
        content_ref = self._build_ref(content)
        with self._lock:
            self._store[content_ref] = content
            self._store.move_to_end(content_ref)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)
        return content_ref

    def resolve(self, ref: str) -> Optional[str]:
        with self._lock:
            value = self._store.get(ref)
            if value is not None:
                self._store.move_to_end(ref)
            return value

    def has(self, ref: str) -> bool:
        with self._lock:
            return ref in self._store

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._store)

    @staticmethod
    def _build_ref(content: str) -> str:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        return f"sha256:{digest}"
