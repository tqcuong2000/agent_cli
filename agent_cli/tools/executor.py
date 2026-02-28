"""
Tool Executor — the safety + observability wrapper around tool calls.

The Tool Executor sits between the Agent loop and ``BaseTool.execute()``.
It handles:

1. **Validation** — arguments checked against the Pydantic schema.
2. **Safety** — unsafe tools trigger approval events via the Event Bus.
3. **Observability** — emit ``ToolExecutionStartEvent`` / ``ToolExecutionResultEvent``.
4. **Output formatting** — truncation and consistent prefixing.
5. **Error shielding** — catch exceptions and return formatted error strings.

The Agent loop calls ``ToolExecutor.execute()`` — never
``BaseTool.execute()`` directly.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

from agent_cli.core.error_handler.errors import ToolExecutionError
from agent_cli.core.events.event_bus import AbstractEventBus
from agent_cli.core.events.events import (
    ToolExecutionResultEvent,
    ToolExecutionStartEvent,
    UserApprovalRequestEvent,
    UserApprovalResponseEvent,
)
from agent_cli.core.interaction import (
    InteractionType,
    UserInteractionRequest,
)
from agent_cli.tools.output_formatter import ToolOutputFormatter
from agent_cli.tools.registry import ToolRegistry
from agent_cli.tools.shell_tool import is_safe_command

if TYPE_CHECKING:
    from agent_cli.core.interaction import BaseInteractionHandler

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Tool Executor
# ══════════════════════════════════════════════════════════════════════


class ToolExecutor:
    """Execute tool calls with safety checks, observability, and output
    formatting.

    This is what the Agent loop calls — never ``BaseTool.execute()``
    directly.

    Args:
        registry:         Tool catalog for name → tool lookup.
        event_bus:        For emitting tool events and HITL requests.
        output_formatter: Truncation and consistent formatting.
        auto_approve:     If ``True``, skip user approval for all tools.
                          Useful for testing.  Full HITL modal is Phase 4.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        event_bus: AbstractEventBus,
        output_formatter: ToolOutputFormatter,
        *,
        auto_approve: bool = False,
        interaction_handler: Optional["BaseInteractionHandler"] = None,
    ) -> None:
        self.registry = registry
        self.event_bus = event_bus
        self.output_formatter = output_formatter
        self._auto_approve = auto_approve
        self._interaction_handler = interaction_handler

        # Approval response handling (Phase 4 HITL will improve this)
        self._pending_approvals: Dict[str, asyncio.Event] = {}
        self._approval_results: Dict[str, bool] = {}
        self.event_bus.subscribe(
            "UserApprovalResponseEvent",
            self._on_approval_response,
        )

    # ── Public API ───────────────────────────────────────────────

    async def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        task_id: str = "",
        *,
        native_call_id: str = "",
    ) -> str:
        """Execute a validated tool call.

        Flow:
        1. Look up tool in registry
        2. Validate arguments via Pydantic schema
        3. Check safety (is_safe flag + dynamic regex for commands)
        4. If unsafe → request user approval (AWAITING_INPUT)
        5. Emit ToolExecutionStartEvent
        6. Execute with error shielding
        7. Format output via ToolOutputFormatter
        8. Emit ToolExecutionResultEvent
        9. Return formatted result string
        """
        tool = self.registry.get(tool_name)
        if tool is None:
            return f"[Tool Error] Unknown tool: '{tool_name}'"

        # ── 1. Validate arguments ────────────────────────────────
        try:
            validated = tool.validate_args(**arguments)
        except Exception as e:
            return f"[Tool Error] Invalid arguments for '{tool_name}': {e}"

        # ── 2. Safety check ──────────────────────────────────────
        requires_approval = not tool.is_safe
        # Dynamic override: safe shell commands skip approval
        if requires_approval and tool.name == "run_command":
            cmd = arguments.get("command", "")
            if is_safe_command(cmd):
                requires_approval = False

        if requires_approval and not self._auto_approve:
            if self._interaction_handler is not None:
                approved = await self._request_approval_via_handler(
                    tool_name=tool_name,
                    arguments=arguments,
                    task_id=task_id,
                )
            else:
                # Backward-compatible fallback while migrating older flows.
                approved = await self._request_approval(
                    tool_name=tool_name,
                    arguments=arguments,
                    task_id=task_id,
                )
            if not approved:
                return f"[Tool: {tool_name}] User denied execution."

        # Use publish() (synchronous) so the TUI handler mounts the
        # ToolStepWidget before the tool executes.  emit() (fire-and-forget)
        # would schedule it as a background task that races with tool output.
        await self.event_bus.publish(
            ToolExecutionStartEvent(
                source="tool_executor",
                task_id=task_id,
                tool_name=tool_name,
                arguments=arguments,
            )
        )

        start_time = time.monotonic()
        success = True
        raw_result = ""

        try:
            execution_args = validated.model_dump()
            if tool.name == "ask_user":
                execution_args["_interaction_handler"] = self._interaction_handler
                execution_args["_task_id"] = task_id
            raw_result = await tool.execute(**execution_args)

        except ToolExecutionError as e:
            raw_result = str(e)
            success = False

        except Exception as e:
            # Shield: catch unexpected OS errors
            raw_result = f"{type(e).__name__}: {e}"
            success = False
            logger.warning(
                "Unexpected error in tool '%s': %s", tool_name, e, exc_info=True
            )

        duration_ms = (time.monotonic() - start_time) * 1000

        # ── 4. Log ───────────────────────────────────────────────
        logger.info(
            "Tool '%s' %s in %.1fms (result_len=%d)",
            tool_name,
            "completed" if success else "failed",
            duration_ms,
            len(raw_result),
        )

        # ── 5. Format output ─────────────────────────────────────
        formatted = self.output_formatter.format(tool_name, raw_result, success)

        await self.event_bus.publish(
            ToolExecutionResultEvent(
                source="tool_executor",
                task_id=task_id,
                tool_name=tool_name,
                output=formatted,
                is_error=not success,
            )
        )

        return formatted

    # ── Approval Handling ────────────────────────────────────────

    def set_interaction_handler(
        self,
        interaction_handler: Optional["BaseInteractionHandler"],
    ) -> None:
        """Attach or replace the HITL interaction handler."""
        self._interaction_handler = interaction_handler

    async def _request_approval_via_handler(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        task_id: str,
    ) -> bool:
        if self._interaction_handler is None:
            return False

        response = await self._interaction_handler.request_human_input(
            UserInteractionRequest(
                interaction_type=InteractionType.APPROVAL,
                message=f"Tool '{tool_name}' requires approval.",
                task_id=task_id,
                source="tool_executor",
                tool_name=tool_name,
                tool_args=dict(arguments),
                options=["approve", "deny"],
            )
        )
        return response.action.lower() == "approve"

    async def _request_approval(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        task_id: str,
    ) -> bool:
        """Request user approval before executing an unsafe tool.

        Emits ``UserApprovalRequestEvent`` and waits for the
        corresponding ``UserApprovalResponseEvent``.

        .. note::

            In Phase 4, this will integrate with the TUI's modal
            system.  For now, it uses a simple event-based handshake
            with a timeout fallback.
        """
        approval_key = f"{task_id}:{tool_name}:{id(arguments)}"
        wait_event = asyncio.Event()
        self._pending_approvals[approval_key] = wait_event

        await self.event_bus.emit(
            UserApprovalRequestEvent(
                source="tool_executor",
                task_id=task_id,
                tool_name=tool_name,
                arguments=arguments,
                risk_description=f"Tool '{tool_name}' requires approval.",
            )
        )

        # Wait for response (timeout after 5 minutes)
        try:
            await asyncio.wait_for(wait_event.wait(), timeout=300.0)
        except asyncio.TimeoutError:
            logger.warning("Approval timeout for tool '%s'", tool_name)
            return False
        finally:
            self._pending_approvals.pop(approval_key, None)

        return self._approval_results.pop(approval_key, False)

    async def _on_approval_response(self, event: UserApprovalResponseEvent) -> None:
        """Handle incoming approval responses from the TUI."""
        # Find the matching pending approval
        for key, wait_event in self._pending_approvals.items():
            if key.startswith(f"{event.task_id}:"):
                self._approval_results[key] = event.approved
                wait_event.set()
                break
