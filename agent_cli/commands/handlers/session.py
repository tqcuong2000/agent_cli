"""UI session command handlers."""

from __future__ import annotations

from typing import List

from agent_cli.commands.base import CommandContext, CommandResult, command


@command(
    name="sessions",
    description="Open session manager overlay",
    usage="/sessions",
    category="Session",
)
async def cmd_sessions(args: List[str], ctx: CommandContext) -> CommandResult:
    """Open the TUI session overlay."""
    if ctx.app is None:
        return CommandResult(
            success=False,
            message="`/sessions` is only available in the TUI.",
        )

    overlay = getattr(ctx.app, "session_overlay", None)
    if overlay is None:
        return CommandResult(
            success=False,
            message="Session overlay is not available.",
        )

    show_overlay = getattr(overlay, "show_overlay", None)
    if callable(show_overlay):
        show_overlay()
        return CommandResult(success=True, message="")

    return CommandResult(
        success=False,
        message="Session overlay cannot be opened.",
    )
