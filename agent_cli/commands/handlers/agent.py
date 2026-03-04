"""Agent management command handlers."""

from __future__ import annotations

from typing import List

from agent_cli.agent.session_registry import AgentStatus
from agent_cli.commands.base import CommandContext, CommandResult
from agent_cli.core.events.events import SettingsChangedEvent


async def cmd_agent(args: List[str], ctx: CommandContext) -> CommandResult:
    app_ctx = ctx.app_context
    if app_ctx is None or app_ctx.orchestrator is None:
        return CommandResult(success=False, message="Agent system is not configured.")

    session_agents = app_ctx.orchestrator.session_agents
    agent_registry = app_ctx.orchestrator.agent_registry
    if session_agents is None or agent_registry is None:
        return CommandResult(success=False, message="Agent registries are unavailable.")

    if not args:
        return CommandResult(
            success=True, message=_format_session_agents(session_agents)
        )

    sub = args[0].lower()
    name = args[1] if len(args) > 1 else ""

    if sub == "list":
        return CommandResult(
            success=True,
            message=_format_available_agents(agent_registry, session_agents),
        )

    if sub == "add":
        if not name:
            return CommandResult(success=False, message="Usage: /agent add <name>")
        candidate = agent_registry.get(name)
        if candidate is None:
            return CommandResult(
                success=False,
                message=(
                    f"Unknown agent '{name}'. "
                    f"Available: {', '.join(agent_registry.names())}"
                ),
            )
        try:
            session_agents.add(candidate, activate=False)
        except ValueError as exc:
            return CommandResult(success=False, message=str(exc))
        return CommandResult(
            success=True,
            message=f"Added agent '{name}' to session (IDLE). Use !{name} to switch.",
        )

    if sub == "remove":
        if not name:
            return CommandResult(success=False, message="Usage: /agent remove <name>")
        try:
            session_agents.remove(name)
            _update_agent_ui(ctx, active_name=session_agents.active_name or "")
            return CommandResult(success=True, message=f"Removed agent '{name}'.")
        except (KeyError, ValueError) as exc:
            return CommandResult(success=False, message=str(exc))

    if sub == "enable":
        if not name:
            return CommandResult(success=False, message="Usage: /agent enable <name>")
        try:
            session_agents.enable(name)
            return CommandResult(success=True, message=f"Enabled agent '{name}'.")
        except KeyError as exc:
            return CommandResult(success=False, message=str(exc))

    if sub == "disable":
        if not name:
            return CommandResult(success=False, message="Usage: /agent disable <name>")
        try:
            session_agents.disable(name)
            _update_agent_ui(ctx, active_name=session_agents.active_name or "")
            return CommandResult(success=True, message=f"Disabled agent '{name}'.")
        except (KeyError, ValueError) as exc:
            return CommandResult(success=False, message=str(exc))

    if sub == "default":
        if not name:
            return CommandResult(success=False, message="Usage: /agent default <name>")
        if not agent_registry.has(name):
            return CommandResult(
                success=False,
                message=f"Unknown agent '{name}'.",
            )
        ctx.settings.default_agent = name
        if ctx.event_bus:
            await ctx.event_bus.publish(
                SettingsChangedEvent(
                    source="cmd_agent",
                    setting_name="default_agent",
                    new_value=name,
                )
            )
        return CommandResult(
            success=True,
            message=f"Default agent set to '{name}' (applies on next app start).",
        )

    return CommandResult(
        success=False,
        message="Usage: /agent [list|add|remove|enable|disable|default] [name]",
    )


def _format_session_agents(session_agents) -> str:
    entries = session_agents.list_agents()
    if not entries:
        return "Session agents: none"

    icon = {
        AgentStatus.ACTIVE: "●",
        AgentStatus.IDLE: "○",
        AgentStatus.INACTIVE: "×",
    }
    lines = ["Session agents:"]
    for entry in entries:
        marker = icon.get(entry.status, "-")
        lines.append(f"  {marker} {entry.name} [{entry.status.value}]")
    return "\n".join(lines)


def _format_available_agents(agent_registry, session_agents) -> str:
    names = agent_registry.names()
    if not names:
        return "Available agents: none"
    in_session = {entry.name for entry in session_agents.list_agents()}
    lines = ["Available agents:"]
    for name in names:
        marker = "in-session" if name in in_session else "available"
        lines.append(f"  - {name} ({marker})")
    return "\n".join(lines)


def _update_agent_ui(ctx: CommandContext, *, active_name: str) -> None:
    if ctx.app is None:
        return
    try:
        from agent_cli.ux.tui.views.header.agent_badge import AgentBadgeComponent
        from agent_cli.ux.tui.views.header.status import StatusContainer

        badge = ctx.app.query_one(AgentBadgeComponent)
        badge.update(active_name or "No Agent")
        status = ctx.app.query_one(StatusContainer)
        status.update_active_agent(active_name or "none")
    except Exception:
        pass
