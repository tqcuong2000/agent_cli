"""
Multi-action batch validation rules.

This validator runs after schema parsing for ``execute_actions`` decisions
and before any batch execution logic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List

from agent_cli.core.infra.events.errors import SchemaValidationError
from agent_cli.core.runtime.agents.parsers import ParsedAction
from agent_cli.core.runtime.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from agent_cli.core.infra.logging.logging import ObservabilityManager

logger = logging.getLogger(__name__)


class MultiActionValidator:
    """Validates a batch of parsed actions before execution."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        *,
        max_batch_size: int = 5,
        observability: "ObservabilityManager | None" = None,
    ) -> None:
        self._registry = tool_registry
        self._max_batch_size = max(int(max_batch_size), 1)
        self._observability = observability

    def validate(
        self,
        actions: List[ParsedAction],
        *,
        task_id: str = "",
    ) -> List[ParsedAction]:
        """Validate and normalize an action batch.

        Rules:
        1. Non-empty batch
        2. Batch size <= max_batch_size
        3. Unique action IDs
        4. All tool names must exist in registry
        5. ``ask_user`` is singleton: if mixed with others, keep only ask_user
        """
        if not actions:
            raise SchemaValidationError(
                "execute_actions requires a non-empty actions list.",
            )

        if len(actions) > self._max_batch_size:
            raise SchemaValidationError(
                f"Batch size {len(actions)} exceeds maximum of {self._max_batch_size}.",
            )

        seen_ids: set[str] = set()
        for idx, action in enumerate(actions):
            action_id = str(action.action_id).strip()
            if action_id:
                if action_id in seen_ids:
                    raise SchemaValidationError(
                        f"Duplicate action_id '{action_id}' in actions batch.",
                    )
                seen_ids.add(action_id)

            if not self._registry.get(action.tool_name):
                raise SchemaValidationError(
                    f"Unknown tool '{action.tool_name}' in action[{idx}].",
                )

        ask_user_actions = [a for a in actions if a.tool_name == "ask_user"]
        if ask_user_actions and len(actions) > 1:
            first = ask_user_actions[0]
            logger.warning(
                "ask_user appeared with other actions; stripping batch to ask_user only"
            )
            if self._observability is not None:
                self._observability.record_multi_action_ask_user_strip(
                    task_id=task_id,
                    batch_size=len(actions),
                )
            return [first]

        return list(actions)
