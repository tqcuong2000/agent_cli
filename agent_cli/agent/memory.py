"""
Agent Memory — ``BaseMemoryManager`` ABC and ``WorkingMemoryManager``.

Working Memory is the short-term context that the agent passes to the
LLM on every iteration.  It contains the system prompt, conversation
messages, tool results, and error feedback.

The ``WorkingMemoryManager`` implements a sliding-window strategy:
when context grows too large, the oldest non-system messages are
dropped (or summarized in a future phase).

.. note::

    Persistent memory (long-term storage, session replay) is a
    Phase 5 concern.  This module only handles ephemeral per-task
    working memory.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Abstract Interface
# ══════════════════════════════════════════════════════════════════════


class BaseMemoryManager(ABC):
    """Interface for managing agent working memory.

    All agents receive a ``BaseMemoryManager`` instance.  The concrete
    implementation controls how context is stored, retrieved, and
    compacted.
    """

    @abstractmethod
    def reset_working(self) -> None:
        """Clear all working memory (called at task start)."""

    @abstractmethod
    def add_working_event(self, message: Dict[str, Any]) -> None:
        """Append a message to working memory.

        Args:
            message: A dict with at least ``role`` and ``content`` keys.
                     Example: ``{"role": "user", "content": "Hello"}``
        """

    @abstractmethod
    def get_working_context(self) -> List[Dict[str, Any]]:
        """Return the current working memory as a message list.

        This is what gets sent to the LLM on each iteration.
        """

    @abstractmethod
    async def summarize_and_compact(self) -> None:
        """Reduce context size when approaching the token limit.

        Called automatically when ``ContextLengthExceededError`` is
        caught in the agent loop.
        """


# ══════════════════════════════════════════════════════════════════════
# Concrete Implementation
# ══════════════════════════════════════════════════════════════════════


class WorkingMemoryManager(BaseMemoryManager):
    """In-memory sliding-window working memory.

    Strategy for ``summarize_and_compact()``:
    - Keep the system prompt (first message) intact.
    - Keep the last ``keep_recent`` messages.
    - Drop the oldest messages in between.
    - Insert a "[context compacted]" marker.

    In a future phase, compaction could be LLM-based summarization
    instead of simple truncation.

    Args:
        keep_recent: Number of recent messages to preserve during
                     compaction (excluding system prompt).
    """

    def __init__(self, keep_recent: int = 10) -> None:
        self._messages: List[Dict[str, Any]] = []
        self._keep_recent = keep_recent

    # ── Public API ───────────────────────────────────────────────

    def reset_working(self) -> None:
        """Clear all working memory."""
        self._messages.clear()

    def add_working_event(self, message: Dict[str, Any]) -> None:
        """Append a message to working memory."""
        self._messages.append(message)

    def get_working_context(self) -> List[Dict[str, Any]]:
        """Return the current message list for the LLM."""
        return list(self._messages)

    async def summarize_and_compact(self) -> None:
        """Compact working memory by dropping old middle messages.

        Keeps:
        - System prompt (index 0, if role == "system").
        - The most recent ``keep_recent`` messages.

        Drops everything in between and inserts a compaction marker.
        """
        if len(self._messages) <= self._keep_recent + 1:
            # Too few messages to compact
            logger.debug("Working memory too short to compact (%d messages)", len(self._messages))
            return

        # Separate system prompt from the rest
        system_msgs: List[Dict[str, Any]] = []
        other_msgs: List[Dict[str, Any]] = []

        for msg in self._messages:
            if msg.get("role") == "system" and not other_msgs:
                system_msgs.append(msg)
            else:
                other_msgs.append(msg)

        if len(other_msgs) <= self._keep_recent:
            return

        # Keep only the most recent messages
        dropped_count = len(other_msgs) - self._keep_recent
        kept = other_msgs[-self._keep_recent:]

        compaction_marker = {
            "role": "user",
            "content": (
                f"[Context compacted: {dropped_count} older messages were "
                f"removed to fit within the context window. Focus on the "
                f"remaining context to complete the task.]"
            ),
        }

        self._messages = system_msgs + [compaction_marker] + kept

        logger.info(
            "Working memory compacted: dropped %d messages, kept %d",
            dropped_count,
            len(self._messages),
        )

    # ── Utilities ────────────────────────────────────────────────

    @property
    def message_count(self) -> int:
        """Number of messages in working memory."""
        return len(self._messages)
