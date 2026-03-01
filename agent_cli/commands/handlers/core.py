"""
Core command handlers — /help, /clear, /exit, /mode, /model,
/effort, /config, /cost, /context.

All handlers are registered at import time via the ``@command``
decorator.  The bootstrap absorbs them into the live
``CommandRegistry``.
"""

from __future__ import annotations

from typing import List

from agent_cli.commands.base import CommandContext, CommandResult, command
from agent_cli.core.events.events import SettingsChangedEvent


# ══════════════════════════════════════════════════════════════════════
# System
# ══════════════════════════════════════════════════════════════════════


@command(
    name="help",
    description="Show all available commands",
    usage="/help [command]",
    shortcut="ctrl+?",
    category="System",
)
async def cmd_help(args: List[str], ctx: CommandContext) -> CommandResult:
    """List all commands, or show details for a specific one."""
    from agent_cli.commands.base import _DEFAULT_REGISTRY

    # Use the live registry if attached, otherwise fall back
    registry = _DEFAULT_REGISTRY

    if args:
        cmd_def = registry.get(args[0])
        if not cmd_def:
            return CommandResult(
                success=False,
                message=f"Unknown command: /{args[0]}",
            )
        lines = [f"/{cmd_def.name} — {cmd_def.description}"]
        lines.append(f"Usage: {cmd_def.usage}")
        if cmd_def.shortcut:
            lines.append(f"Shortcut: {cmd_def.shortcut}")
        return CommandResult(success=True, message="\n".join(lines))

    # List all grouped by category
    commands = registry.all()
    lines = ["Available commands:\n"]
    current_category = ""

    for cmd in commands:
        if cmd.category != current_category:
            current_category = cmd.category
            lines.append(f"  {current_category}:")

        shortcut = f"  ({cmd.shortcut})" if cmd.shortcut else ""
        lines.append(f"    /{cmd.name:<12} {cmd.description}{shortcut}")

    lines.append("\nType /help <command> for details.")
    return CommandResult(success=True, message="\n".join(lines))


@command(
    name="exit",
    description="Exit the CLI",
    usage="/exit",
    shortcut="ctrl+q",
    category="System",
)
async def cmd_exit(args: List[str], ctx: CommandContext) -> CommandResult:
    """Exit the application."""
    if ctx.app is not None:
        ctx.app.exit()
    return CommandResult(success=True, message="Shutting down...")


# ══════════════════════════════════════════════════════════════════════
# Memory
# ══════════════════════════════════════════════════════════════════════


@command(
    name="clear",
    description="Clear working memory (start fresh context)",
    usage="/clear",
    shortcut="ctrl+l",
    category="Memory",
)
async def cmd_clear(args: List[str], ctx: CommandContext) -> CommandResult:
    """Reset working memory."""
    ctx.memory_manager.reset_working()
    return CommandResult(
        success=True,
        message="Working memory cleared. Starting fresh context.",
    )


@command(
    name="context",
    description="Show context window usage",
    usage="/context",
    category="Memory",
)
async def cmd_context(args: List[str], ctx: CommandContext) -> CommandResult:
    """Show current token usage from working memory."""
    msg_count = ctx.memory_manager.message_count
    return CommandResult(
        success=True,
        message=(
            f"Context usage:\n"
            f"  Messages in working memory: {msg_count}"
        ),
    )


@command(
    name="cost",
    description="Show session cost breakdown",
    usage="/cost",
    category="Memory",
)
async def cmd_cost(args: List[str], ctx: CommandContext) -> CommandResult:
    """Show session cost information (placeholder)."""
    return CommandResult(
        success=True,
        message=(
            "Cost tracking:\n"
            "  Session cost data will be available when provider "
            "cost tracking is wired."
        ),
    )


# ══════════════════════════════════════════════════════════════════════
# Navigation & Mode
# ══════════════════════════════════════════════════════════════════════


@command(
    name="mode",
    description="Set execution mode (plan/fast)",
    usage="/mode <plan|fast>",
    shortcut="ctrl+m",
    category="Navigation",
)
async def cmd_mode(args: List[str], ctx: CommandContext) -> CommandResult:
    """Set execution mode for the next request."""
    if not args:
        current = getattr(ctx.settings, "execution_mode", "plan")
        return CommandResult(
            success=True,
            message=f"Current mode: {current.upper()}",
        )

    mode = args[0].lower()
    if mode not in ("plan", "fast"):
        return CommandResult(
            success=False,
            message="Usage: /mode <plan|fast>",
        )

    ctx.settings.execution_mode = mode

    # Update status bar if app is available
    _update_status_bar(ctx, mode=mode)

    return CommandResult(
        success=True,
        message=f"Execution mode set to: {mode.upper()}",
    )


# ══════════════════════════════════════════════════════════════════════
# Model & Provider
# ══════════════════════════════════════════════════════════════════════


@command(
    name="model",
    description="Switch LLM model",
    usage="/model <name>",
    category="Model",
)
async def cmd_model(args: List[str], ctx: CommandContext) -> CommandResult:
    """Switch the default LLM model."""
    if not args:
        current = ctx.settings.default_model
        return CommandResult(
            success=True,
            message=f"Current model: {current}",
        )

    model_name = args[0]
    ctx.settings.default_model = model_name

    # Check if we have app_context and orchestrator connected
    if ctx.app_context and ctx.app_context.orchestrator:
        try:
            agent = ctx.app_context.orchestrator._default_agent
            new_provider = ctx.app_context.providers.get_provider(model_name)
            agent.provider = new_provider
        except Exception as e:
            return CommandResult(
                success=False,
                message=f"Failed to load provider for '{model_name}': {e}",
            )

    # Update status bar
    _update_status_bar(ctx, model=model_name)

    return CommandResult(
        success=True,
        message=f"Switched model to: {model_name}",
    )


@command(
    name="effort",
    description="Set default effort level",
    usage="/effort <low|medium|high|xhigh>",
    shortcut="ctrl+e",
    category="Model",
)
async def cmd_effort(args: List[str], ctx: CommandContext) -> CommandResult:
    """Set the default effort level."""
    from agent_cli.core.models.config_models import EffortLevel

    if not args:
        current = ctx.settings.default_effort_level
        return CommandResult(
            success=True,
            message=f"Current effort: {current}",
        )

    level = args[0].upper()
    if level not in ("LOW", "MEDIUM", "HIGH", "XHIGH"):
        return CommandResult(
            success=False,
            message="Usage: /effort <LOW|MEDIUM|HIGH|XHIGH>",
        )

    ctx.settings.default_effort_level = EffortLevel(level)

    # Emit event so agents can update reactive caches
    if ctx.event_bus:
        await ctx.event_bus.publish(
            SettingsChangedEvent(
                setting_name="default_effort_level",
                new_value=level,
                source="cmd_effort",
            )
        )

    # Update status bar
    _update_status_bar(ctx, effort=level)

    return CommandResult(
        success=True,
        message=f"Effort level set to: {level}",
    )


# ══════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════


@command(
    name="config",
    description="View current settings (read-only)",
    usage="/config",
    category="Configuration",
)
async def cmd_config(args: List[str], ctx: CommandContext) -> CommandResult:
    """Display current configuration values."""
    s = ctx.settings
    lines = [
        "Current configuration:",
        f"  default_model:        {s.default_model}",
        f"  default_effort_level: {s.default_effort_level.value}",
        f"  execution_mode:       {getattr(s, 'execution_mode', 'plan')}",
        f"  auto_approve_tools:   {s.auto_approve_tools}",
        f"  show_agent_thinking:  {s.show_agent_thinking}",
        f"  log_level:            {s.log_level}",
        f"  tool_output_max_chars:{s.tool_output_max_chars}",
    ]
    return CommandResult(success=True, message="\n".join(lines))


# ══════════════════════════════════════════════════════════════════════
# Status Bar Helper
# ══════════════════════════════════════════════════════════════════════


def _update_status_bar(
    ctx: CommandContext,
    *,
    mode: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> None:
    """Update the TUI status bar if app is available."""
    if ctx.app is None:
        return
    try:
        from agent_cli.ux.tui.views.header.status import StatusContainer

        status = ctx.app.query_one(StatusContainer)
        if mode is not None:
            status.update_mode(mode)
        if model is not None:
            status.update_model(model)
        if effort is not None:
            status.update_effort(effort)
    except Exception:
        pass  # Status bar may not be mounted in test environments
