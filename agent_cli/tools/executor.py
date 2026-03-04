"""
Tool Executor — the safety + observability wrapper around tool calls.

The Tool Executor sits between the Agent loop and ``BaseTool.execute()``.
It handles:

1. **Validation** — arguments checked against the Pydantic schema.
2. **Safety** — unsafe tools trigger approval events via the Event Bus.
3. **Observability** — emit ``ToolExecutionStartEvent`` / ``ToolExecutionResultEvent``.
4. **Output formatting** — truncation and JSON envelope normalization.
5. **Error shielding** — catch exceptions and return formatted error strings.

The Agent loop calls ``ToolExecutor.execute()`` — never
``BaseTool.execute()`` directly.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional, cast

from agent_cli.core.error_handler.errors import ToolExecutionError
from agent_cli.core.events.event_bus import AbstractEventBus
from agent_cli.core.events.events import (
    BaseEvent,
    ToolExecutionResultEvent,
    ToolExecutionStartEvent,
    UserApprovalRequestEvent,
    UserApprovalResponseEvent,
)
from agent_cli.core.file_tracker import ChangeType, FileChangeTracker
from agent_cli.core.interaction import (
    InteractionType,
    UserInteractionRequest,
)
from agent_cli.core.logging import get_observability
from agent_cli.core.tracing import start_span
from agent_cli.core.registry import DataRegistry
from agent_cli.tools.output_formatter import ToolOutputFormatter
from agent_cli.tools.registry import ToolRegistry
from agent_cli.tools.shell_tool import is_safe_command

if TYPE_CHECKING:
    from agent_cli.core.events.event_bus import EventCallback
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
        file_tracker: Optional[FileChangeTracker] = None,
        approval_timeout_seconds: float | None = None,
        data_registry: DataRegistry | None = None,
    ) -> None:
        self.registry = registry
        self.event_bus = event_bus
        self.output_formatter = output_formatter
        self._auto_approve = auto_approve
        self._interaction_handler = interaction_handler
        self._file_tracker = file_tracker
        defaults = (
            (data_registry or DataRegistry()).get_tool_defaults().get("executor", {})
        )
        self._approval_timeout_seconds = float(
            approval_timeout_seconds
            if approval_timeout_seconds is not None
            else defaults.get("approval_timeout_seconds", 300.0)
        )

        # Approval response handling (Phase 4 HITL will improve this)
        self._pending_approvals: Dict[str, asyncio.Event] = {}
        self._approval_results: Dict[str, bool] = {}
        self.event_bus.subscribe(
            "UserApprovalResponseEvent",
            cast("EventCallback", self._on_approval_response),
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
            return self.output_formatter.format(
                tool_name,
                f"Unknown tool: '{tool_name}'",
                success=False,
                task_id=task_id,
                native_call_id=native_call_id,
            )

        # ── 1. Validate arguments ────────────────────────────────
        try:
            validated = tool.validate_args(**arguments)
        except Exception as e:
            return self.output_formatter.format(
                tool_name,
                f"Invalid arguments for '{tool_name}': {e}",
                success=False,
                task_id=task_id,
                native_call_id=native_call_id,
            )

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
                return self.output_formatter.format(
                    tool_name,
                    "User denied execution.",
                    success=False,
                    task_id=task_id,
                    native_call_id=native_call_id,
                )

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

        span = start_span("tool_exec", task_id=task_id)
        success = True
        raw_result = ""

        try:
            execution_args = validated.model_dump()
            if tool.name == "ask_user":
                execution_args["_interaction_handler"] = self._interaction_handler
                execution_args["_task_id"] = task_id

            # ── 3.3 Record Changes Before Execution (Phase 4.4) ──────
            if self._file_tracker:
                if tool_name == "write_file":
                    path = arguments.get("path")
                    if path:
                        from pathlib import Path

                        full_path = path
                        if self._file_tracker.workspace_root:
                            full_path = self._file_tracker.workspace_root / path

                        # Detect if it's a MODIFIED or CREATED change
                        change_type = (
                            ChangeType.MODIFIED
                            if Path(full_path).exists()
                            else ChangeType.CREATED
                        )
                        await self._file_tracker.record_change(path, change_type)
                elif tool_name in ("str_replace", "insert_lines"):
                    path = arguments.get("path")
                    if path:
                        await self._file_tracker.record_change(
                            path, ChangeType.MODIFIED
                        )
                elif tool_name == "run_command":
                    cmd = arguments.get("command", "")
                    if cmd:
                        import shlex

                        try:
                            # Use shlex to parse the command (posix=False for Windows compatibility might be needed but posix=True is safer for standard parsing)
                            parts = shlex.split(cmd)
                            if parts and parts[0] in ("rm", "del"):
                                # Extract files after rm/del, skipping flags like -rf
                                files_to_delete = [
                                    p for p in parts[1:] if not p.startswith("-")
                                ]
                                for file_path in files_to_delete:
                                    # Try to determine if it's actually a file, though it might be a dir
                                    # File change tracker doesn't recursively track directories right now, but we track the path.
                                    await self._file_tracker.record_change(
                                        file_path, ChangeType.DELETED
                                    )
                        except Exception:
                            pass  # Safely ignore parsing errors

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

        timing = span.finish()
        duration_ms = int(timing["duration_ms"])

        # ── 4. Log ───────────────────────────────────────────────
        observability = get_observability()
        if observability is not None:
            observability.record_tool_call(
                task_id=task_id,
                tool_name=tool_name,
                success=success,
                duration_ms=duration_ms,
                result_length=len(raw_result),
            )
        else:
            logger.info(
                "Tool '%s' %s in %dms (result_len=%d)",
                tool_name,
                "completed" if success else "failed",
                duration_ms,
                len(raw_result),
                extra={
                    "source": "tool_executor",
                    "task_id": task_id,
                    "span_id": timing["span_id"],
                    "span_type": timing["span_type"],
                    "data": {
                        "tool": tool_name,
                        "success": success,
                        "duration_ms": duration_ms,
                        "result_length": len(raw_result),
                    },
                },
            )

        # ── 5. Format output ─────────────────────────────────────
        formatted = self.output_formatter.format(
            tool_name,
            raw_result,
            success,
            task_id=task_id,
            native_call_id=native_call_id,
        )

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

        # Wait for response (timeout from tools defaults, <=0 means wait forever)
        try:
            if self._approval_timeout_seconds <= 0:
                await wait_event.wait()
            else:
                await asyncio.wait_for(
                    wait_event.wait(),
                    timeout=self._approval_timeout_seconds,
                )
        except asyncio.TimeoutError:
            logger.warning("Approval timeout for tool '%s'", tool_name)
            return False
        finally:
            self._pending_approvals.pop(approval_key, None)

        return self._approval_results.pop(approval_key, False)

    async def _on_approval_response(self, event: "BaseEvent") -> None:
        """Handle incoming approval responses from the TUI."""
        if not isinstance(event, UserApprovalResponseEvent):
            return

        # Find the matching pending approval
        for key, wait_event in self._pending_approvals.items():
            if key.startswith(f"{event.task_id}:"):
                self._approval_results[key] = event.approved
                wait_event.set()
                break
