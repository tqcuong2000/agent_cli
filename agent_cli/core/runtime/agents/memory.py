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
from typing import Any, Dict, List, Optional

from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.cost.budget import TokenBudget, budget_for_model
from agent_cli.core.providers.cost.token_counter import BaseTokenCounter, HeuristicTokenCounter

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
    def count_tokens(self) -> int:
        """Return token usage estimate for the current working context."""

    @abstractmethod
    def should_compact(self) -> bool:
        """Whether memory compaction should be triggered."""

    @abstractmethod
    async def summarize_and_compact(self) -> None:
        """Reduce context size when approaching the token limit.

        Called automatically when ``ContextLengthExceededError`` is
        caught in the agent loop.
        """

    @abstractmethod
    async def on_model_changed(
        self,
        model_name: str,
        *,
        token_counter: Optional[BaseTokenCounter] = None,
        token_budget: Optional[TokenBudget] = None,
    ) -> bool:
        """Apply a model switch and compact if the new budget is tighter.

        Returns:
            ``True`` if compaction was performed, ``False`` otherwise.
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

    def __init__(
        self,
        keep_recent: int = 10,
        token_counter: Optional[BaseTokenCounter] = None,
        token_budget: Optional[TokenBudget] = None,
        model_name: str = "unknown",
        *,
        data_registry: DataRegistry,
    ) -> None:
        self._messages: List[Dict[str, Any]] = []
        self._keep_recent = keep_recent
        self._model_name = model_name
        self._data_registry = data_registry
        self._token_counter = token_counter or HeuristicTokenCounter(
            data_registry=self._data_registry
        )
        self._token_budget = token_budget or budget_for_model(
            model_name,
            data_registry=self._data_registry,
        )

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

    def count_tokens(self) -> int:
        """Estimate token usage for current working memory."""
        return self._count_messages(self._messages)

    def should_compact(self) -> bool:
        """Whether token budget threshold has been crossed."""
        current_tokens = self.count_tokens()
        return self._token_budget.should_compact(current_tokens)

    async def summarize_and_compact(self) -> None:
        """Compact working memory by dropping old middle messages.

        Keeps:
        - System prompt (index 0, if role == "system").
        - The most recent ``keep_recent`` messages.

        Drops everything in between and inserts a compaction marker.
        """
        if not self.should_compact():
            logger.debug(
                "Compaction skipped: token usage %d below threshold.",
                self.count_tokens(),
            )
            return

        if len(self._messages) <= 2:
            logger.debug(
                "Working memory too short to compact (%d messages)", len(self._messages)
            )
            return

        system_msgs, other_msgs = self._split_system_and_other_messages(self._messages)

        if len(other_msgs) <= 1:
            return

        # Token-driven compaction: progressively reduce preserved recent turns
        # until budget threshold is met or only one non-system message remains.
        keep_recent = min(self._keep_recent, len(other_msgs))
        compacted = False

        while keep_recent >= 1:
            dropped_count = len(other_msgs) - keep_recent
            kept = other_msgs[-keep_recent:]

            candidate = list(system_msgs)
            if dropped_count > 0:
                candidate.append(self._build_compaction_marker(dropped_count))
            candidate.extend(kept)

            candidate_tokens = self._count_messages(candidate)
            if (
                not self._token_budget.should_compact(candidate_tokens)
                or keep_recent == 1
            ):
                self._messages = candidate
                compacted = dropped_count > 0
                logger.info(
                    "Working memory compacted: dropped=%d kept=%d tokens=%d model=%s",
                    dropped_count,
                    len(self._messages),
                    candidate_tokens,
                    self._model_name,
                )
                break

            keep_recent -= 1

        if not compacted:
            logger.debug("Compaction did not drop messages; context already minimal.")

    async def on_model_changed(
        self,
        model_name: str,
        *,
        token_counter: Optional[BaseTokenCounter] = None,
        token_budget: Optional[TokenBudget] = None,
    ) -> bool:
        """Update token counting/budget strategy for a newly selected model."""
        previous_available = self._token_budget.available_for_context()

        self._model_name = model_name
        if token_counter is not None:
            self._token_counter = token_counter
        if token_budget is not None:
            self._token_budget = token_budget
        else:
            self._token_budget = budget_for_model(
                model_name,
                data_registry=self._data_registry,
            )

        new_available = self._token_budget.available_for_context()
        needs_compaction = self.should_compact()
        if new_available < previous_available and needs_compaction:
            await self.summarize_and_compact()
            return True
        return False

    # ── Utilities ────────────────────────────────────────────────

    def _count_messages(self, messages: List[Dict[str, Any]]) -> int:
        return self._token_counter.count(messages, self._model_name)

    @staticmethod
    def _split_system_and_other_messages(
        messages: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Split leading system prompt(s) from the rest of the context."""
        system_msgs: List[Dict[str, Any]] = []
        other_msgs: List[Dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == "system" and not other_msgs:
                system_msgs.append(msg)
            else:
                other_msgs.append(msg)
        return system_msgs, other_msgs

    @staticmethod
    def _build_compaction_marker(dropped_count: int) -> Dict[str, Any]:
        """Create a standard marker message for dropped historical context."""
        return {
            "role": "user",
            "content": (
                f"[Context compacted: {dropped_count} older messages were "
                f"removed to fit within the context window. Focus on the "
                f"remaining context to complete the task.]"
            ),
        }

    @property
    def message_count(self) -> int:
        """Number of messages in working memory."""
        return len(self._messages)

    @property
    def token_budget(self) -> TokenBudget:
        """Current token budget."""
        return self._token_budget
