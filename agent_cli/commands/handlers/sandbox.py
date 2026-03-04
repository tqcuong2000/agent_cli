"""Sandbox command handlers."""

from __future__ import annotations

from typing import List, Optional

from agent_cli.commands.base import CommandContext, CommandResult
from agent_cli.workspace.sandbox import SandboxWorkspaceManager


async def cmd_sandbox(args: List[str], ctx: CommandContext) -> CommandResult:
    manager = _get_sandbox_manager(ctx)
    if manager is None:
        return CommandResult(
            success=False,
            message="Sandbox manager is not configured.",
        )

    if not args:
        status = manager.status()
        mode = status.mode.upper()
        return CommandResult(
            success=True,
            message=(
                f"Sandbox status: {mode}\n"
                "Usage:\n"
                "  /sandbox on\n"
                "  /sandbox ls\n"
                "  /sandbox off <apply|discard>"
            ),
        )

    subcommand = args[0].lower()
    if subcommand == "on":
        status = manager.enable()
        return CommandResult(
            success=True,
            message=f"{status.message}\nMode: {status.mode.upper()}",
        )

    if subcommand == "ls":
        changes = manager.list_changes()
        if not changes:
            return CommandResult(success=True, message="Sandbox changes: none.")
        lines = ["Sandbox changes:"]
        lines.extend(f"  {line}" for line in changes)
        return CommandResult(success=True, message="\n".join(lines))

    if subcommand == "off":
        if len(args) < 2:
            changes = manager.list_changes()
            preview = "\n".join(f"  {line}" for line in changes[:20]) or "  (none)"
            return CommandResult(
                success=False,
                message=(
                    f"Usage: /sandbox off <apply|discard>\nPending changes:\n{preview}"
                ),
            )
        action = args[1].lower()
        if action not in ("apply", "discard"):
            return CommandResult(
                success=False,
                message="Usage: /sandbox off <apply|discard>",
            )
        status = manager.disable(action)
        return CommandResult(success=True, message=status.message)

    return CommandResult(
        success=False,
        message="Unknown /sandbox subcommand. Use: on, ls, off",
    )


def _get_sandbox_manager(ctx: CommandContext) -> Optional[SandboxWorkspaceManager]:
    app_context = ctx.app_context
    if app_context is None:
        return None

    manager = getattr(app_context, "workspace_manager", None)
    if isinstance(manager, SandboxWorkspaceManager):
        return manager
    return None
