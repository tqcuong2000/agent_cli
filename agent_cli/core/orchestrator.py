"""
Orchestrator — the bridge between user requests and agents.

The Orchestrator subscribes to ``UserRequestEvent`` from the Event Bus
and routes each request to the appropriate agent.  It manages the full
task lifecycle:

1. Intercept slash commands (``/exit``, ``/clear``, etc.) → handle
   directly, no agent invocation.
2. Create a ``TaskRecord`` via the State Manager.
3. Select the target agent (single default for Phase 3; LLM-based
   routing in Phase 6).
4. Transition task through ``PENDING → ROUTING → WORKING``.
5. Invoke ``agent.handle_task()``.
6. Transition to ``SUCCESS`` or ``FAILED`` and emit ``TaskResultEvent``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Dict, List, Optional

from agent_cli.agent.base import BaseAgent
from agent_cli.core.error_handler.errors import AgentCLIError
from agent_cli.core.events.event_bus import AbstractEventBus
from agent_cli.core.events.events import (
    AgentMessageEvent,
    TaskDelegatedEvent,
    TaskResultEvent,
    UserRequestEvent,
)
from agent_cli.core.state.state_manager import AbstractStateManager
from agent_cli.core.state.state_models import TaskState

if TYPE_CHECKING:
    from agent_cli.commands.parser import CommandParser

logger = logging.getLogger(__name__)

# ── Type alias for slash-command handlers ────────────────────────────

CommandHandler = Callable[[str], Coroutine[Any, Any, Optional[str]]]


# ══════════════════════════════════════════════════════════════════════
# Orchestrator
# ══════════════════════════════════════════════════════════════════════


class Orchestrator:
    """Routes user requests to agents and manages the task lifecycle.

    Args:
        event_bus:      Event Bus for subscribing to requests and
                        publishing results.
        state_manager:  State Manager for task creation and transitions.
        default_agent:  The ``BaseAgent`` instance to use for all
                        requests (single-agent mode for Phase 3).
    """

    def __init__(
        self,
        event_bus: AbstractEventBus,
        state_manager: AbstractStateManager,
        default_agent: BaseAgent,
        command_parser: Optional[CommandParser] = None,
    ) -> None:
        self._event_bus = event_bus
        self._state_manager = state_manager
        self._default_agent = default_agent
        self._command_parser = command_parser

        # Legacy slash-command registry (kept for backward compat)
        self._commands: Dict[str, CommandHandler] = {}

        # Subscribe to UserRequestEvent
        self._subscription_id = self._event_bus.subscribe(
            "UserRequestEvent",
            self._on_user_request,
            priority=10,  # Orchestrator processes at priority 10
        )

    # ── Public API ───────────────────────────────────────────────

    def register_command(
        self, name: str, handler: CommandHandler
    ) -> None:
        """Register a slash-command handler.

        Args:
            name:     Command name without the ``/`` prefix
                      (e.g. ``"exit"``).
            handler:  Async function receiving the full input text
                      and returning an optional response string.
        """
        self._commands[name.lower()] = handler
        logger.debug("Registered command: /%s", name)

    async def handle_request(self, text: str) -> Optional[str]:
        """Process a user request directly (useful for testing).

        This is the core routing logic, also called from the
        event handler.

        Returns:
            The agent's final answer, or ``None`` for commands.
        """
        text = text.strip()

        # ── Slash-command interception ────────────────────────────
        if text.startswith("/"):
            # Prefer the new CommandParser if available
            if self._command_parser is not None:
                result = await self._command_parser.execute(text)
                if result.message:
                    await self._event_bus.publish(
                        AgentMessageEvent(
                            source="command_system",
                            content=result.message,
                            is_monologue=False,
                        )
                    )
                return result.message

            # Fallback to legacy dict-based commands
            return await self._handle_command(text)

        # ── Normal request → agent routing ───────────────────────
        return await self._route_to_agent(text)

    # ── Event Handler ────────────────────────────────────────────

    async def _on_user_request(self, event: UserRequestEvent) -> None:
        """Handle ``UserRequestEvent`` from the Event Bus."""
        try:
            result = await self.handle_request(event.text)
            # Result may be None for slash-commands (they handle
            # their own output).
        except Exception as e:
            logger.error(
                "Orchestrator failed to handle request: %s",
                e,
                exc_info=True,
            )

    # ── Command Handling ─────────────────────────────────────────

    async def _handle_command(self, text: str) -> Optional[str]:
        """Intercept and dispatch slash-commands.

        Returns:
            Response string from the command handler, or an error
            message if the command is unknown.
        """
        # Parse: "/exit foo bar" → name="exit", rest="foo bar"
        parts = text[1:].split(maxsplit=1)
        name = parts[0].lower() if parts else ""
        # rest = parts[1] if len(parts) > 1 else ""

        handler = self._commands.get(name)
        if handler:
            logger.info("Executing command: /%s", name)
            return await handler(text)

        logger.warning("Unknown command: /%s", name)
        return f"Unknown command: /{name}. Type /help for available commands."

    # ── Agent Routing ────────────────────────────────────────────

    async def _route_to_agent(self, text: str) -> str:
        """Create a task and delegate to the default agent.

        Full lifecycle:
        ``create_task → ROUTING → WORKING → handle_task → SUCCESS/FAILED``
        """
        # 1. Create task
        task = await self._state_manager.create_task(
            description=text[:100],
            assigned_agent=self._default_agent.name,
        )
        task_id = task.task_id

        try:
            # 2. PENDING → ROUTING
            await self._state_manager.transition(
                task_id, TaskState.ROUTING
            )

            # 3. Emit delegation event
            await self._event_bus.emit(
                TaskDelegatedEvent(
                    source="orchestrator",
                    task_id=task_id,
                    agent_name=self._default_agent.name,
                    description=text[:100],
                )
            )

            # 4. ROUTING → WORKING
            await self._state_manager.transition(
                task_id, TaskState.WORKING
            )

            # 5. Run agent
            result = await self._default_agent.handle_task(
                task_id=task_id,
                task_description=text,
            )

            # 6. WORKING → SUCCESS
            await self._state_manager.transition(
                task_id, TaskState.SUCCESS, result=result
            )

            # 7. Emit result
            await self._event_bus.publish(
                TaskResultEvent(
                    source="orchestrator",
                    task_id=task_id,
                    result=result,
                    is_success=True,
                )
            )

            return result

        except AgentCLIError as e:
            # Transition to FAILED
            await self._safe_transition_to_failed(
                task_id, e.user_message
            )

            await self._event_bus.publish(
                TaskResultEvent(
                    source="orchestrator",
                    task_id=task_id,
                    result=e.user_message,
                    is_success=False,
                )
            )
            return e.user_message

        except Exception as e:
            error_msg = f"Unexpected error: {e}"
            logger.error(
                "Orchestrator caught unexpected error on task %s: %s",
                task_id,
                e,
                exc_info=True,
            )

            await self._safe_transition_to_failed(task_id, error_msg)

            await self._event_bus.publish(
                TaskResultEvent(
                    source="orchestrator",
                    task_id=task_id,
                    result=error_msg,
                    is_success=False,
                )
            )
            return error_msg

    # ── Helpers ──────────────────────────────────────────────────

    async def _safe_transition_to_failed(
        self, task_id: str, error: str
    ) -> None:
        """Attempt to transition to FAILED, ignoring transition errors."""
        try:
            await self._state_manager.transition(
                task_id, TaskState.FAILED, error=error
            )
        except Exception as e:
            logger.warning(
                "Could not transition task %s to FAILED: %s",
                task_id,
                e,
            )

    async def shutdown(self) -> None:
        """Unsubscribe from the event bus."""
        self._event_bus.unsubscribe(self._subscription_id)
        logger.info("Orchestrator shut down.")
