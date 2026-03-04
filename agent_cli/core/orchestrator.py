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

import asyncio
import inspect
import logging
import re
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Optional, cast

from agent_cli.agent.base import BaseAgent
from agent_cli.agent.registry import AgentRegistry
from agent_cli.agent.session_registry import SessionAgentRegistry
from agent_cli.core.error_handler.errors import AgentCLIError
from agent_cli.core.events.event_bus import AbstractEventBus
from agent_cli.core.events.events import (
    AgentMessageEvent,
    BaseEvent,
    SettingsChangedEvent,
    TaskDelegatedEvent,
    TaskResultEvent,
    UserRequestEvent,
)
from agent_cli.core.logging import get_observability
from agent_cli.core.state.state_manager import AbstractStateManager
from agent_cli.core.state.state_models import TaskState
from agent_cli.core.tracing import bind_trace, new_trace_id
from agent_cli.session.base import AbstractSessionManager, Session

if TYPE_CHECKING:
    from agent_cli.commands.parser import CommandParser
    from agent_cli.providers.capability_probe import CapabilityProbeService

logger = logging.getLogger(__name__)

# ── Type alias for slash-command handlers ────────────────────────────

CommandHandler = Callable[[str], Awaitable[Optional[str]]]


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
        session_manager: Optional[AbstractSessionManager] = None,
        agent_registry: Optional[AgentRegistry] = None,
        session_agents: Optional[SessionAgentRegistry] = None,
        capability_probe: Optional[CapabilityProbeService] = None,
    ) -> None:
        self._event_bus = event_bus
        self._state_manager = state_manager
        self._default_agent = default_agent
        self._command_parser = command_parser
        self._session_manager = session_manager
        self._agent_registry = agent_registry
        self._session_agents = session_agents
        self._capability_probe = capability_probe
        self._request_lock = asyncio.Lock()
        self._running_callbacks: set[asyncio.Task[Any]] = set()
        self._active_request_task: Optional[asyncio.Task[Any]] = None
        self._active_task_id: Optional[str] = None

        # Legacy slash-command registry (kept for backward compat)
        self._commands: Dict[str, CommandHandler] = {}

        # Subscribe to UserRequestEvent
        self._subscription_id = self._event_bus.subscribe(
            "UserRequestEvent",
            self._on_user_request,
            priority=10,  # Orchestrator processes at priority 10
        )

    # ── Public API ───────────────────────────────────────────────

    @property
    def active_agent(self) -> BaseAgent:
        if (
            self._session_agents is not None
            and self._session_agents.active_agent is not None
        ):
            return self._session_agents.active_agent
        return self._default_agent

    @property
    def active_agent_name(self) -> str:
        return self.active_agent.name

    @property
    def session_agents(self) -> Optional[SessionAgentRegistry]:
        return self._session_agents

    @property
    def agent_registry(self) -> Optional[AgentRegistry]:
        return self._agent_registry

    def register_command(self, name: str, handler: CommandHandler) -> None:
        """Register a slash-command handler.

        Args:
            name:     Command name without the ``/`` prefix
                      (e.g. ``"exit"``).
            handler:  Async function receiving the full input text
                      and returning an optional response string.
        """
        self._commands[name.lower()] = handler
        logger.debug("Registered command: /%s", name)

    async def interrupt_active_task(self) -> bool:
        """Cancel the currently running request, if any.

        Returns:
            ``True`` if an in-flight request was cancelled, else ``False``.
        """
        request_task = self._active_request_task
        if request_task is None or request_task.done():
            return False

        logger.info(
            "Interrupt requested for active task",
            extra={
                "source": "orchestrator",
                "task_id": self._active_task_id or "",
                "data": {"active_task_id": self._active_task_id or ""},
            },
        )
        request_task.cancel()
        await asyncio.sleep(0)
        return True

    async def handle_request(self, text: str) -> Optional[str]:
        """Process a user request directly (useful for testing).

        This is the core routing logic, also called from the
        event handler.

        Returns:
            The agent's final answer, or ``None`` for commands.
        """
        text = text.strip()
        request_trace = new_trace_id()
        with bind_trace(trace_id=request_trace):
            logger.info(
                "User request received",
                extra={
                    "source": "orchestrator",
                    "data": {
                        "message_length": len(text),
                        "is_command": text.startswith("/"),
                    },
                },
            )

            # ── Slash-command interception ────────────────────────
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

            if self._request_lock.locked():
                await self._emit_error(
                    "Agent is already processing another request. Please wait."
                )
                return None

            target_name, clean_message = self._parse_mention(text)
            prior_context = ""
            use_session_messages = True
            routed_text = clean_message

            if target_name:
                if self._session_agents is None:
                    await self._emit_error("Session agent switching is not configured.")
                    return None
                if not self._session_agents.has(target_name):
                    await self._emit_error(
                        f"Agent '{target_name}' is not in this session. "
                        f"Use /agent add {target_name} first."
                    )
                    return None
                if target_name != self._session_agents.active_name:
                    prior_context = await self._switch_agent(target_name)
                    # On cross-agent handoff, summary replaces raw history.
                    use_session_messages = False

            if not routed_text.strip():
                await self._emit_error("Message is empty after mention tag.")
                return None

            # ── Normal request → agent routing ───────────────────────
            async with self._request_lock:
                request_task = asyncio.current_task()
                self._active_request_task = request_task
                try:
                    return await self._route_to_agent(
                        routed_text,
                        prior_context=prior_context,
                        use_session_messages=use_session_messages,
                    )
                finally:
                    if self._active_request_task is request_task:
                        self._active_request_task = None
                    self._active_task_id = None

    # ── Event Handler ────────────────────────────────────────────

    async def _on_user_request(self, event: BaseEvent) -> None:
        """Handle ``UserRequestEvent`` from the Event Bus."""
        if not isinstance(event, UserRequestEvent):
            return
        callback_task = asyncio.current_task()
        if callback_task is not None:
            self._running_callbacks.add(callback_task)
        try:
            await self.handle_request(event.text)
            # Result may be None for slash-commands (they handle
            # their own output).
        except Exception as e:
            logger.error(
                "Orchestrator failed to handle request: %s",
                e,
                exc_info=True,
            )
        finally:
            if callback_task is not None:
                self._running_callbacks.discard(callback_task)

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
            maybe_awaitable = handler(text)
            if not inspect.isawaitable(maybe_awaitable):
                raise TypeError(
                    f"Command handler '/{name}' returned a non-awaitable result."
                )
            return await cast(Awaitable[Optional[str]], maybe_awaitable)

        logger.warning("Unknown command: /%s", name)
        return f"Unknown command: /{name}. Type /help for available commands."

    # ── Agent Routing ────────────────────────────────────────────

    async def _route_to_agent(
        self,
        text: str,
        *,
        prior_context: str = "",
        use_session_messages: bool = True,
    ) -> str:
        """Create a task and delegate to the default agent.

        Full lifecycle:
        ``create_task → ROUTING → WORKING → handle_task → SUCCESS/FAILED``
        """
        agent = self.active_agent

        # 0. Resolve active session (if persistence is configured)
        active_session = self._get_or_create_active_session()
        session_messages = (
            list(active_session.messages)
            if active_session and use_session_messages
            else None
        )
        session_desired_effort = (
            str(getattr(active_session, "desired_effort", "")).strip()
            if active_session is not None
            else ""
        )

        # 1. Create task
        task = await self._state_manager.create_task(
            description=text[:100],
            assigned_agent=agent.name,
        )
        task_id = task.task_id
        self._active_task_id = task_id

        try:
            with bind_trace(trace_id=task_id, task_id=task_id):
                logger.info(
                    "Routing task to agent",
                    extra={
                        "source": "orchestrator",
                        "task_id": task_id,
                        "data": {"agent": agent.name},
                    },
                )

                # 2. PENDING → ROUTING
                await self._state_manager.transition(task_id, TaskState.ROUTING)

                # 3. Emit delegation event
                await self._event_bus.emit(
                    TaskDelegatedEvent(
                        source="orchestrator",
                        task_id=task_id,
                        agent_name=agent.name,
                        description=text[:100],
                    )
                )

                # 4. ROUTING → WORKING
                await self._state_manager.transition(task_id, TaskState.WORKING)

                # 5. Run agent
                result = await agent.handle_task(
                    task_id=task_id,
                    task_description=text,
                    prior_context=prior_context,
                    session_messages=session_messages,
                    desired_effort=session_desired_effort or None,
                )

                await self._persist_session_after_task(
                    active_session, task_id, agent=agent
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
                self._log_task_metrics(task_id, is_success=True)

                return result
        except asyncio.CancelledError:
            cancel_msg = "Task cancelled by user."
            await self._safe_transition_to_cancelled(task_id)
            await self._persist_session_after_task(active_session, task_id, agent=agent)
            await self._event_bus.publish(
                TaskResultEvent(
                    source="orchestrator",
                    task_id=task_id,
                    result=cancel_msg,
                    is_success=False,
                )
            )
            await self._event_bus.publish(
                AgentMessageEvent(
                    source="orchestrator",
                    agent_name="system",
                    content=cancel_msg,
                    is_monologue=False,
                )
            )
            self._log_task_metrics(task_id, is_success=False)
            logger.info(
                "Task cancelled",
                extra={
                    "source": "orchestrator",
                    "task_id": task_id,
                    "data": {"agent": agent.name},
                },
            )
            return cancel_msg

        except AgentCLIError as e:
            # Transition to FAILED
            await self._safe_transition_to_failed(task_id, e.user_message)
            await self._persist_session_after_task(active_session, task_id, agent=agent)

            await self._event_bus.publish(
                TaskResultEvent(
                    source="orchestrator",
                    task_id=task_id,
                    result=e.user_message,
                    is_success=False,
                )
            )
            self._log_task_metrics(task_id, is_success=False)
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
            await self._persist_session_after_task(active_session, task_id, agent=agent)

            await self._event_bus.publish(
                TaskResultEvent(
                    source="orchestrator",
                    task_id=task_id,
                    result=error_msg,
                    is_success=False,
                )
            )
            self._log_task_metrics(task_id, is_success=False)
            return error_msg

    # ── Helpers ──────────────────────────────────────────────────

    async def _safe_transition_to_failed(self, task_id: str, error: str) -> None:
        """Attempt to transition to FAILED, ignoring transition errors."""
        try:
            await self._state_manager.transition(task_id, TaskState.FAILED, error=error)
        except Exception as e:
            logger.warning(
                "Could not transition task %s to FAILED: %s",
                task_id,
                e,
            )

    async def _safe_transition_to_cancelled(self, task_id: str) -> None:
        """Attempt to transition to CANCELLED, ignoring transition errors."""
        try:
            await self._state_manager.transition(task_id, TaskState.CANCELLED)
        except Exception as e:
            logger.warning(
                "Could not transition task %s to CANCELLED: %s",
                task_id,
                e,
            )

    async def shutdown(self) -> None:
        """Unsubscribe from the event bus."""
        self._event_bus.unsubscribe(self._subscription_id)
        active_callbacks = list(self._running_callbacks)
        for task in active_callbacks:
            task.cancel()
        if active_callbacks:
            await asyncio.gather(*active_callbacks, return_exceptions=True)
        logger.info("Orchestrator shut down.")

    def _get_or_create_active_session(self) -> Optional[Session]:
        """Get active session from manager, creating one if missing."""
        if self._session_manager is None:
            return None

        active = self._session_manager.get_active()
        if active is not None:
            return active

        active = self._session_manager.create_session()
        self._session_manager.save(active)
        self._probe_active_provider_capabilities(trigger="session_start")
        return active

    def _probe_active_provider_capabilities(self, *, trigger: str) -> None:
        """Best-effort capability probe for the active provider identity."""
        if self._capability_probe is None:
            return
        provider = getattr(self.active_agent, "provider", None)
        if provider is None:
            return
        try:
            self._capability_probe.probe_provider(provider, trigger=trigger)
        except Exception:
            logger.exception("Capability probe failed in orchestrator (%s)", trigger)

    async def _persist_session_after_task(
        self,
        session: Optional[Session],
        task_id: str,
        *,
        agent: BaseAgent,
    ) -> None:
        """Append task-local messages and persist session state."""
        if self._session_manager is None or session is None:
            return

        if task_id not in session.task_ids:
            session.task_ids.append(task_id)

        model_name = getattr(agent.provider, "model_name", "")
        if isinstance(model_name, str):
            session.active_model = model_name

        new_messages = agent.get_last_task_messages()
        if new_messages:
            session.messages.extend(new_messages)

        # Accumulate task-specific cost into the session's cumulative total
        obs = get_observability()
        if obs is not None:
            task_metrics = obs.get_task_metrics(task_id)
            task_cost = float(task_metrics.get("cost_usd", 0.0))
            session.total_cost = round(session.total_cost + task_cost, 6)

        session_title_changed = False
        current_name = str(session.name or "").strip()
        if not current_name:
            candidate = agent.get_last_task_title().strip()
            session.name = candidate or "Untitled session"
            session_title_changed = True

        self._session_manager.save(session)
        if session_title_changed:
            await self._event_bus.publish(
                SettingsChangedEvent(
                    source="orchestrator",
                    setting_name="session_title",
                    new_value=session.name,
                )
            )

    @staticmethod
    def _parse_mention(message: str) -> tuple[Optional[str], str]:
        match = re.match(r"^!(\w+)\s*(.*)$", message, re.DOTALL)
        if not match:
            return None, message
        return match.group(1), match.group(2).strip()

    async def _switch_agent(self, target_name: str) -> str:
        if self._session_agents is None:
            return ""

        old_name = self._session_agents.active_name or self._default_agent.name
        summary = await self._generate_session_summary()
        new_agent = self._session_agents.switch_to(target_name)

        # Stateless re-activation.
        new_agent.memory.reset_working()
        if self._command_parser is not None:
            try:
                self._command_parser.set_memory_manager(new_agent.memory)
            except Exception:
                pass
        system_prompt = await new_agent.build_system_prompt("")
        new_agent.memory.add_working_event({"role": "system", "content": system_prompt})
        if summary:
            new_agent.memory.add_working_event(
                {
                    "role": "system",
                    "content": f"[Session Context Summary]\n{summary}",
                }
            )

        await self._event_bus.emit(
            AgentMessageEvent(
                source="orchestrator",
                agent_name="system",
                content=f"Switched from {old_name} to {target_name}.",
                is_monologue=False,
            )
        )
        await self._event_bus.publish(
            SettingsChangedEvent(
                source="orchestrator",
                setting_name="active_agent",
                new_value=target_name,
            )
        )
        return summary

    async def _generate_session_summary(self) -> str:
        session = self._get_or_create_active_session()
        if session is None or not session.messages:
            return ""

        recent = session.messages[-30:]
        filtered = [
            message
            for message in recent
            if str(message.get("role", "")).lower() in ("user", "assistant")
        ]
        if not filtered:
            return ""

        agent = self.active_agent
        summarize = getattr(agent.memory, "_summarize_middle_messages", None)
        if callable(summarize):
            try:
                maybe_awaitable = summarize(filtered)
                summary = (
                    await cast(Awaitable[Any], maybe_awaitable)
                    if inspect.isawaitable(maybe_awaitable)
                    else maybe_awaitable
                )
                if summary:
                    return str(summary).strip()
            except Exception:
                logger.warning("Session handoff summarization failed", exc_info=True)

        lines: list[str] = []
        for message in filtered[-10:]:
            role = str(message.get("role", "?"))
            content = str(message.get("content", "")).strip()
            if not content:
                continue
            lines.append(f"[{role}] {' '.join(content.split())[:200]}")
        return "\n".join(lines)

    async def _emit_error(self, message: str) -> None:
        await self._event_bus.publish(
            AgentMessageEvent(
                source="orchestrator",
                agent_name="system",
                content=f"⚠ {message}",
                is_monologue=False,
            )
        )

    @staticmethod
    def _log_task_metrics(task_id: str, *, is_success: bool) -> None:
        observability = get_observability()
        if observability is None:
            return
        observability.log_task_summary(task_id, is_success=is_success)
