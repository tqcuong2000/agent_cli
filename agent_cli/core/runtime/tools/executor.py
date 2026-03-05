"""
Tool Executor â€” the safety + observability wrapper around tool calls.

The Tool Executor sits between the Agent loop and ``BaseTool.execute()``.
It handles:

1. **Validation** â€” arguments checked against the Pydantic schema.
2. **Safety** â€” unsafe tools trigger approval events via the Event Bus.
3. **Observability** â€” emit ``ToolExecutionStartEvent`` / ``ToolExecutionResultEvent``.
4. **Output formatting** â€” truncation and JSON envelope normalization.
5. **Error shielding** â€” catch exceptions and return formatted error strings.

The Agent loop calls ``ToolExecutor.execute()`` â€” never
``BaseTool.execute()`` directly.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional, cast

from agent_cli.core.runtime.agents.content_store import ContentStore
from agent_cli.core.infra.events.errors import ToolExecutionError
from agent_cli.core.infra.events.event_bus import AbstractEventBus
from agent_cli.core.infra.events.events import (
    BaseEvent,
    ToolExecutionResultEvent,
    ToolExecutionStartEvent,
    UserApprovalRequestEvent,
    UserApprovalResponseEvent,
)
from agent_cli.core.runtime.orchestrator.file_tracker import ChangeType, FileChangeTracker
from agent_cli.core.ux.interaction.interaction import (
    InteractionType,
    UserInteractionRequest,
)
from agent_cli.core.infra.logging.tracing import start_span
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.runtime.tools.base import ToolResult
from agent_cli.core.runtime.tools.error_codes import ToolErrorCode
from agent_cli.core.runtime.tools.output_formatter import ToolOutputFormatter
from agent_cli.core.runtime.tools.registry import ToolRegistry
from agent_cli.core.runtime.tools.shell_tool import (
    compile_safe_command_patterns,
    is_safe_command,
)

if TYPE_CHECKING:
    from agent_cli.core.infra.events.event_bus import EventCallback
    from agent_cli.core.ux.interaction.interaction import BaseInteractionHandler
    from agent_cli.core.infra.logging.logging import ObservabilityManager

logger = logging.getLogger(__name__)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Tool Executor
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


class ToolExecutor:
    """Execute tool calls with safety checks, observability, and output
    formatting.

    This is what the Agent loop calls â€” never ``BaseTool.execute()``
    directly.

    Args:
        registry:         Tool catalog for name â†’ tool lookup.
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
        content_store: Optional[ContentStore] = None,
        data_registry: DataRegistry,
        observability: Optional["ObservabilityManager"] = None,
    ) -> None:
        self.registry = registry
        self.event_bus = event_bus
        self.output_formatter = output_formatter
        self._auto_approve = auto_approve
        self._interaction_handler = interaction_handler
        self._file_tracker = file_tracker
        self._observability = observability
        self._content_store = content_store or ContentStore()
        defaults = data_registry.get_tool_defaults().get("executor", {})
        self._safe_command_patterns = compile_safe_command_patterns(data_registry)
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

    # â”€â”€ Public API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    async def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        task_id: str = "",
        *,
        native_call_id: str = "",
        action_id: str = "",
        batch_id: str = "",
    ) -> ToolResult:
        """Execute a validated tool call."""
        tool = self.registry.get(tool_name)
        if tool is None:
            return self._build_error_result(
                tool_name=tool_name,
                error_message=f"Unknown tool: '{tool_name}'",
                error_code=ToolErrorCode.TOOL_NOT_FOUND,
                task_id=task_id,
                native_call_id=native_call_id,
                action_id=action_id,
                batch_id=batch_id,
            )

        resolved_arguments = self._resolve_content_refs(arguments, tool_name=tool_name)

        try:
            validated = tool.validate_args(**resolved_arguments)
        except Exception as exc:
            return self._build_error_result(
                tool_name=tool_name,
                error_message=f"Invalid arguments for '{tool_name}': {exc}",
                error_code=ToolErrorCode.INVALID_ARGUMENTS,
                task_id=task_id,
                native_call_id=native_call_id,
                action_id=action_id,
                batch_id=batch_id,
            )

        requires_approval = not tool.is_safe
        if requires_approval and tool.name == "run_command":
            cmd = resolved_arguments.get("command", "")
            if is_safe_command(cmd, self._safe_command_patterns):
                requires_approval = False

        if requires_approval and not self._auto_approve:
            approval_timed_out = False
            if self._interaction_handler is not None:
                approved = await self._request_approval_via_handler(
                    tool_name=tool_name,
                    arguments=resolved_arguments,
                    task_id=task_id,
                )
            else:
                approved, approval_timed_out = await self._request_approval(
                    tool_name=tool_name,
                    arguments=resolved_arguments,
                    task_id=task_id,
                )
            if not approved:
                return self._build_error_result(
                    tool_name=tool_name,
                    error_message="User denied execution.",
                    error_code=(
                        ToolErrorCode.APPROVAL_TIMEOUT
                        if approval_timed_out
                        else ToolErrorCode.APPROVAL_DENIED
                    ),
                    task_id=task_id,
                    native_call_id=native_call_id,
                    action_id=action_id,
                    batch_id=batch_id,
                )

        await self.event_bus.publish(
            ToolExecutionStartEvent(
                source="tool_executor",
                task_id=task_id,
                tool_name=tool_name,
                arguments=resolved_arguments,
            )
        )

        span = start_span("tool_exec", task_id=task_id)
        success = True
        raw_result = ""
        captured_exception: Exception | None = None

        try:
            execution_args = validated.model_dump()
            if tool.name == "ask_user":
                execution_args["_interaction_handler"] = self._interaction_handler
                execution_args["_task_id"] = task_id

            if self._file_tracker:
                if tool_name == "write_file":
                    path = resolved_arguments.get("path")
                    if path:
                        from pathlib import Path

                        full_path = path
                        if self._file_tracker.workspace_root:
                            full_path = self._file_tracker.workspace_root / path
                        change_type = (
                            ChangeType.MODIFIED
                            if Path(full_path).exists()
                            else ChangeType.CREATED
                        )
                        await self._file_tracker.record_change(path, change_type)
                elif tool_name in ("str_replace", "insert_lines"):
                    path = resolved_arguments.get("path")
                    if path:
                        await self._file_tracker.record_change(path, ChangeType.MODIFIED)
                elif tool_name == "run_command":
                    cmd = resolved_arguments.get("command", "")
                    if cmd:
                        import shlex

                        try:
                            parts = shlex.split(cmd)
                            if parts and parts[0] in ("rm", "del"):
                                files_to_delete = [
                                    p for p in parts[1:] if not p.startswith("-")
                                ]
                                for file_path in files_to_delete:
                                    await self._file_tracker.record_change(
                                        file_path, ChangeType.DELETED
                                    )
                        except Exception:
                            pass

            raw_result = await tool.execute(**execution_args)
        except ToolExecutionError as exc:
            raw_result = str(exc)
            success = False
            captured_exception = exc
        except Exception as exc:
            raw_result = f"{type(exc).__name__}: {exc}"
            success = False
            captured_exception = exc
            logger.warning(
                "Unexpected error in tool '%s': %s", tool_name, exc, exc_info=True
            )

        timing = span.finish()
        duration_ms = int(timing["duration_ms"])

        if self._observability is not None:
            self._observability.record_tool_call(
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

        content_ref = ""
        error_code = ToolErrorCode.UNKNOWN
        if success and tool_name == "read_file" and raw_result:
            content_ref = self.store_content(raw_result)

        if success:
            formatted = self.output_formatter.format(
                tool_name,
                raw_result,
                success=True,
                task_id=task_id,
                native_call_id=native_call_id,
                action_id=action_id,
                total_chars=len(raw_result),
                total_lines=self._count_lines(raw_result),
                content_ref=content_ref,
                batch_id=batch_id,
            )
        else:
            error_code = self._classify_error_code(
                tool_name=tool_name,
                message=raw_result,
                exc=captured_exception,
            )
            formatted = self.output_formatter.format(
                tool_name,
                raw_result,
                success=False,
                task_id=task_id,
                native_call_id=native_call_id,
                action_id=action_id,
                error_code=error_code.value,
                retryable=error_code.retryable,
                total_chars=len(raw_result),
                total_lines=self._count_lines(raw_result),
                batch_id=batch_id,
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

        metadata: Dict[str, Any] = {}
        if task_id:
            metadata["task_id"] = task_id
        if native_call_id:
            metadata["native_call_id"] = native_call_id
        if content_ref:
            metadata["content_ref"] = content_ref
        if batch_id:
            metadata["batch_id"] = batch_id
        if not success:
            metadata["error_code"] = error_code.value
            metadata["retryable"] = error_code.retryable

        return ToolResult(
            success=success,
            output=formatted,
            error="" if success else raw_result,
            metadata=metadata,
            action_id=action_id,
            tool_name=tool_name,
        )

    def store_content(self, content: str) -> str:
        """Store content and return a session-scoped reference hash."""
        return self._content_store.store(content)

    def _resolve_content_refs(
        self,
        arguments: Dict[str, Any],
        *,
        tool_name: str,
    ) -> Dict[str, Any]:
        resolved: Dict[str, Any] = {}
        for key, value in arguments.items():
            if key == "content_ref":
                continue
            resolved[key] = self._resolve_content_value(
                value,
                tool_name=tool_name,
                argument_name=key,
            )

        content_ref = arguments.get("content_ref")
        if isinstance(content_ref, str) and "content" not in resolved:
            content = self._content_store.resolve(content_ref)
            if content is not None:
                logger.info(
                    "Resolved content_ref for tool '%s' (arg=content, ref=%s, chars=%d)",
                    tool_name,
                    content_ref,
                    len(content),
                )
                resolved["content"] = content
            else:
                resolved["content"] = content_ref
        return resolved

    def _resolve_content_value(
        self,
        value: Any,
        *,
        tool_name: str,
        argument_name: str,
    ) -> Any:
        if isinstance(value, str) and value.startswith("sha256:"):
            content = self._content_store.resolve(value)
            if content is not None:
                logger.info(
                    "Resolved content_ref for tool '%s' (arg=%s, ref=%s, chars=%d)",
                    tool_name,
                    argument_name,
                    value,
                    len(content),
                )
                return content
            return value
        if isinstance(value, dict):
            return {
                k: self._resolve_content_value(
                    v,
                    tool_name=tool_name,
                    argument_name=f"{argument_name}.{k}",
                )
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [
                self._resolve_content_value(
                    item,
                    tool_name=tool_name,
                    argument_name=f"{argument_name}[{idx}]",
                )
                for idx, item in enumerate(value)
            ]
        return value

    def _build_error_result(
        self,
        *,
        tool_name: str,
        error_message: str,
        error_code: ToolErrorCode,
        task_id: str,
        native_call_id: str,
        action_id: str,
        batch_id: str,
    ) -> ToolResult:
        formatted = self.output_formatter.format(
            tool_name,
            error_message,
            success=False,
            task_id=task_id,
            native_call_id=native_call_id,
            action_id=action_id,
            error_code=error_code.value,
            retryable=error_code.retryable,
            total_chars=len(error_message),
            total_lines=self._count_lines(error_message),
            batch_id=batch_id,
        )
        metadata: Dict[str, Any] = {
            "error_code": error_code.value,
            "retryable": error_code.retryable,
        }
        if task_id:
            metadata["task_id"] = task_id
        if native_call_id:
            metadata["native_call_id"] = native_call_id
        if batch_id:
            metadata["batch_id"] = batch_id
        return ToolResult(
            success=False,
            output=formatted,
            error=error_message,
            metadata=metadata,
            action_id=action_id,
            tool_name=tool_name,
        )

    def _classify_error_code(
        self,
        *,
        tool_name: str,
        message: str,
        exc: Exception | None = None,
    ) -> ToolErrorCode:
        lowered = message.lower()
        if isinstance(exc, FileNotFoundError) or "file not found" in lowered:
            return ToolErrorCode.FILE_NOT_FOUND
        if isinstance(exc, PermissionError) or "permission denied" in lowered:
            return ToolErrorCode.PERMISSION_DENIED
        if "timed out" in lowered or isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            return ToolErrorCode.COMMAND_TIMEOUT
        if "invalid arguments" in lowered:
            return ToolErrorCode.INVALID_ARGUMENTS
        if "unknown tool" in lowered:
            return ToolErrorCode.TOOL_NOT_FOUND
        if "user denied execution" in lowered:
            return ToolErrorCode.APPROVAL_DENIED
        if "approval timeout" in lowered:
            return ToolErrorCode.APPROVAL_TIMEOUT
        if "encoding" in lowered or "utf-8" in lowered:
            return ToolErrorCode.ENCODING_ERROR
        if tool_name == "run_command":
            return ToolErrorCode.COMMAND_FAILED
        if isinstance(exc, ToolExecutionError):
            return ToolErrorCode.UNKNOWN
        return ToolErrorCode.INTERNAL_ERROR

    @staticmethod
    def _count_lines(value: str) -> int:
        if not value:
            return 0
        return value.count("\n") + 1

    def set_interaction_handler(
        self,
        interaction_handler: Optional["BaseInteractionHandler"],
    ) -> None:
        """Attach or replace the HITL interaction handler."""
        self._interaction_handler = interaction_handler

    def get_observability_manager(self) -> Optional["ObservabilityManager"]:
        """Return the attached observability manager, if configured."""
        return self._observability

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
    ) -> tuple[bool, bool]:
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
            return False, True
        finally:
            self._pending_approvals.pop(approval_key, None)

        return self._approval_results.pop(approval_key, False), False

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

