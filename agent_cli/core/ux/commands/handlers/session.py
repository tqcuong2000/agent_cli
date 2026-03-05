"""UI session command handlers."""

from __future__ import annotations

from agent_cli.core.ux.commands.base import CommandContext, CommandResult
from agent_cli.core.infra.events.events import SettingsChangedEvent

_DEFAULT_SESSION_TITLE = "Untitled session"


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


async def cmd_generate_title(args: List[str], ctx: CommandContext) -> CommandResult:
    """Generate and persist a short title for the active session."""
    app_ctx = ctx.app_context
    if app_ctx is None or app_ctx.session_manager is None:
        return CommandResult(
            success=False,
            message="Session manager is not configured.",
        )

    title_service = getattr(app_ctx, "title_service", None)
    if title_service is None:
        return CommandResult(
            success=False,
            message="Title service is not available.",
        )

    session = app_ctx.session_manager.get_active()
    if session is None:
        session = app_ctx.session_manager.create_session()

    title = ""
    orchestrator = app_ctx.orchestrator
    provider = (
        getattr(orchestrator.active_agent, "provider", None) if orchestrator else None
    )
    if provider is not None:
        title = await title_service.generate_title(provider, session.messages)

    if not title:
        title = _DEFAULT_SESSION_TITLE

    session.name = title
    app_ctx.session_manager.save(session)

    if ctx.event_bus is not None:
        await ctx.event_bus.publish(
            SettingsChangedEvent(
                source="cmd_generate_title",
                setting_name="session_title",
                new_value=title,
            )
        )

    return CommandResult(success=True, message=f"Session title updated: {title}")
