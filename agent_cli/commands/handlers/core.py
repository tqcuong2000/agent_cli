"""
Core command handlers — /help, /clear, /exit, /model,
/config, /cost, /context.

All handlers are registered at import time via the ``@command``
decorator.  The bootstrap absorbs them into the live
``CommandRegistry``.
"""

from __future__ import annotations

from typing import Any, List

from agent_cli.commands.base import CommandContext, CommandResult, command
from agent_cli.core.events.events import SettingsChangedEvent
from agent_cli.core.logging import get_observability
from agent_cli.core.models.config_models import (
    EffortLevel,
    effort_values,
    normalize_effort,
)

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
    msg_count = len(ctx.memory_manager.get_working_context())
    token_count = ctx.memory_manager.count_tokens()
    budget = getattr(ctx.memory_manager, "token_budget", None)
    if budget is not None:
        usage_line = (
            f"  Estimated tokens: {token_count} / {budget.available_for_context()} "
            f"(threshold {int(budget.compaction_threshold * 100)}%)"
        )
    else:
        usage_line = f"  Estimated tokens: {token_count}"
    return CommandResult(
        success=True,
        message=(
            f"Context usage:\n  Messages in working memory: {msg_count}\n{usage_line}"
        ),
    )


@command(
    name="cost",
    description="Show session cost breakdown",
    usage="/cost",
    category="Memory",
)
async def cmd_cost(args: List[str], ctx: CommandContext) -> CommandResult:
    """Show session cost information."""
    observability = get_observability()
    if observability is None:
        return CommandResult(
            success=True,
            message="Cost tracking is not initialized.",
        )

    metrics = observability.metrics.to_summary()
    return CommandResult(
        success=True,
        message=(
            "Cost tracking:\n"
            f"  Total cost: ${metrics['cost_usd']:.6f}\n"
            f"  LLM calls: {metrics['llm_calls']}\n"
            f"  Input tokens: {metrics['tokens']['input']}\n"
            f"  Output tokens: {metrics['tokens']['output']}\n"
            f"  Total tokens: {metrics['tokens']['total']}"
        ),
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
    """Switch model for the active agent."""
    active_agent = None
    if ctx.app_context and ctx.app_context.orchestrator:
        active_agent = ctx.app_context.orchestrator.active_agent

    if not args:
        if active_agent is not None:
            current = active_agent.config.model or ctx.settings.default_model
            return CommandResult(
                success=True,
                message=f"Current model ({active_agent.name}): {current}",
            )
        current = ctx.settings.default_model
        return CommandResult(
            success=True,
            message=f"Current model: {current}",
        )

    model_name = args[0]
    target_agent_name = "global"
    target_memory = ctx.memory_manager

    # Check if we have app_context and orchestrator connected
    if ctx.app_context and ctx.app_context.orchestrator:
        try:
            agent = ctx.app_context.orchestrator.active_agent
            new_provider = ctx.app_context.providers.get_provider(model_name)
            agent.provider = new_provider
            agent.config.model = model_name
            target_agent_name = agent.name
            target_memory = agent.memory
            _persist_agent_model_override(ctx, agent.name, model_name)

            # Keep settings.default_model aligned with the configured default agent.
            if agent.name == getattr(ctx.settings, "default_agent", "default"):
                ctx.settings.default_model = model_name
        except Exception as e:
            return CommandResult(
                success=False,
                message=f"Failed to load provider for '{model_name}': {e}",
            )
    else:
        ctx.settings.default_model = model_name

    # Refresh memory token counter + budget for the new model and compact if needed.
    if ctx.app_context:
        try:
            context_budget = ctx.app_context.data_registry.get_context_budget()
            token_counter = ctx.app_context.providers.get_token_counter(model_name)
            token_budget = ctx.app_context.providers.get_token_budget(
                model_name,
                response_reserve=4096,
                compaction_threshold=float(
                    context_budget.get("compaction_threshold", 0.80)
                ),
            )
            await target_memory.on_model_changed(
                model_name,
                token_counter=token_counter,
                token_budget=token_budget,
            )
            if target_memory is not ctx.memory_manager:
                ctx.memory_manager = target_memory
                ctx.app_context.memory_manager = target_memory
        except Exception as e:
            return CommandResult(
                success=False,
                message=f"Failed to update token budget for '{model_name}': {e}",
            )

    # Best-effort runtime capability probe after successful provider/model switch.
    if ctx.app_context and ctx.app_context.orchestrator:
        capability_probe = getattr(ctx.app_context, "capability_probe", None)
        if capability_probe is not None:
            try:
                active_provider = getattr(
                    ctx.app_context.orchestrator.active_agent,
                    "provider",
                    None,
                )
                if active_provider is not None:
                    capability_probe.probe_provider(
                        active_provider,
                        trigger="model_switch",
                    )
            except Exception:
                pass

    # Emit event so reactive components can update when model changes.
    if ctx.event_bus:
        await ctx.event_bus.publish(
            SettingsChangedEvent(
                setting_name="default_model",
                new_value=model_name,
                source="cmd_model",
            )
        )

    # Update status bar
    _update_status_bar(
        ctx,
        model=model_name,
        active_agent=target_agent_name,
    )

    return CommandResult(
        success=True,
        message=f"Switched model for '{target_agent_name}' to: {model_name}",
    )


@command(
    name="effort",
    description="Get or set reasoning effort",
    usage="/effort [auto|minimal|low|medium|high]",
    category="Model",
)
async def cmd_effort(args: List[str], ctx: CommandContext) -> CommandResult:
    """Get or set reasoning effort for the active session/runtime."""
    if not args:
        desired = _get_desired_effort(ctx)
        return CommandResult(
            success=True,
            message=_format_effort_status(ctx, desired),
        )

    if len(args) != 1:
        allowed = "|".join(effort_values())
        return CommandResult(
            success=False,
            message=f"Usage: /effort [{allowed}]",
        )

    try:
        desired = normalize_effort(args[0]).value
    except Exception:
        allowed = ", ".join(effort_values())
        return CommandResult(
            success=False,
            message=f"Invalid effort value. Allowed: {allowed}",
        )

    saved_to_session = False
    app_ctx = ctx.app_context
    session_manager = getattr(app_ctx, "session_manager", None) if app_ctx else None
    if session_manager is not None:
        session = session_manager.get_active()
        if session is None:
            session = session_manager.create_session()
        session.desired_effort = desired
        session_manager.save(session)
        saved_to_session = True
    else:
        ctx.settings.default_effort = desired

    if ctx.event_bus is not None:
        await ctx.event_bus.publish(
            SettingsChangedEvent(
                setting_name="effort",
                new_value=desired,
                source="cmd_effort",
            )
        )

    scope = "session" if saved_to_session else "settings default"
    message = (
        f"Effort updated ({scope}): {desired}\n{_format_effort_status(ctx, desired)}"
    )
    return CommandResult(success=True, message=message)


@command(
    name="debug",
    description="Toggle debug logging",
    usage="/debug [on|off]",
    category="Model",
)
async def cmd_debug(args: List[str], ctx: CommandContext) -> CommandResult:
    """Enable/disable DEBUG log level at runtime."""
    observability = get_observability()
    if not args:
        return CommandResult(
            success=True,
            message=f"Current log level: {ctx.settings.log_level}",
        )

    value = args[0].lower()
    if value not in ("on", "off"):
        return CommandResult(
            success=False,
            message="Usage: /debug [on|off]",
        )

    level = "DEBUG" if value == "on" else "INFO"
    ctx.settings.log_level = level
    if observability is not None:
        observability.set_level(level)

    if ctx.event_bus:
        await ctx.event_bus.publish(
            SettingsChangedEvent(
                setting_name="log_level",
                new_value=level,
                source="cmd_debug",
            )
        )

    return CommandResult(
        success=True,
        message=f"Log level set to: {level}",
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
        f"  default_agent:        {getattr(s, 'default_agent', 'default')}",
        f"  max_iterations:       {getattr(s, 'max_iterations', 100)}",
        f"  default_effort:       {getattr(s, 'default_effort', 'auto')}",
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
    model: str | None = None,
    active_agent: str | None = None,
) -> None:
    """Update the TUI status bar if app is available."""
    if ctx.app is None:
        return
    try:
        from agent_cli.ux.tui.views.header.agent_badge import AgentBadgeComponent
        from agent_cli.ux.tui.views.header.status import StatusContainer

        status = ctx.app.query_one(StatusContainer)
        if model is not None:
            status.update_model(model)
        if active_agent is not None:
            status.update_active_agent(active_agent)
            badge = ctx.app.query_one(AgentBadgeComponent)
            badge.update(active_agent)
    except Exception:
        pass  # Status bar may not be mounted in test environments


def _persist_agent_model_override(
    ctx: CommandContext,
    agent_name: str,
    model_name: str,
) -> None:
    """Persist model override under settings.agents.<name>.model."""
    agents = getattr(ctx.settings, "agents", None)
    if not isinstance(agents, dict):
        return

    raw_entry = agents.get(agent_name)
    entry = raw_entry if isinstance(raw_entry, dict) else {}
    entry["model"] = model_name
    agents[agent_name] = entry


def _get_desired_effort(ctx: CommandContext) -> str:
    """Resolve desired effort with session override precedence."""
    app_ctx = ctx.app_context
    session_manager = getattr(app_ctx, "session_manager", None) if app_ctx else None
    if session_manager is not None:
        active = session_manager.get_active()
        if active is not None:
            try:
                return normalize_effort(getattr(active, "desired_effort", None)).value
            except Exception:
                pass
    try:
        return normalize_effort(getattr(ctx.settings, "default_effort", None)).value
    except Exception:
        return EffortLevel.AUTO.value


def _format_effort_status(ctx: CommandContext, desired: str) -> str:
    """Build a user-facing desired/effective effort status message."""
    desired_norm = normalize_effort(desired).value
    provider = None
    model_name = ""
    provider_name = "unknown"

    app_ctx = ctx.app_context
    orchestrator = getattr(app_ctx, "orchestrator", None) if app_ctx else None
    if orchestrator is not None:
        agent = getattr(orchestrator, "active_agent", None)
        if agent is not None:
            provider = getattr(agent, "provider", None)
    if provider is not None:
        provider_name = str(getattr(provider, "provider_name", "unknown"))
        model_name = str(getattr(provider, "model_name", ""))
    else:
        model_name = str(getattr(ctx.settings, "default_model", ""))

    effective = EffortLevel.AUTO.value
    note = ""
    if provider is not None:
        status, reason = _get_effective_capability_status(
            ctx=ctx,
            provider=provider,
            capability_name="effort",
        )
        if desired_norm == EffortLevel.AUTO.value:
            effective = EffortLevel.AUTO.value
        elif status == "supported":
            effective = desired_norm
        else:
            effective = EffortLevel.AUTO.value

        if desired_norm != EffortLevel.AUTO.value and status != "supported":
            if reason:
                note = f" (effective capability: {status}; {reason})"
            else:
                note = f" (effective capability: {status})"
    else:
        note = " (no active provider)"

    return (
        f"Desired effort: {desired_norm}\n"
        f"Effective effort ({provider_name}/{model_name}): {effective}{note}"
    )


def _get_effective_capability_status(
    *,
    ctx: CommandContext,
    provider: Any,
    capability_name: str,
) -> tuple[str, str]:
    """Return effective capability status/reason from capability snapshot."""
    app_ctx = ctx.app_context
    data_registry = getattr(app_ctx, "data_registry", None) if app_ctx else None
    if data_registry is None:
        return "unknown", "missing_data_registry"

    provider_name = str(getattr(provider, "provider_name", "")).strip()
    model_name = str(getattr(provider, "model_name", "")).strip()
    base_url = str(getattr(provider, "base_url", "") or "").strip()
    if not provider_name or not model_name:
        return "unknown", "missing_provider_identity"

    deployment_id = _build_deployment_id(
        provider_name=provider_name,
        model_name=model_name,
        base_url=base_url,
    )
    try:
        snapshot = data_registry.get_capability_snapshot(
            provider=provider_name,
            model=model_name,
            deployment_id=deployment_id,
        )
    except Exception:
        return "unknown", "snapshot_lookup_failed"

    observation = snapshot.effective.get(str(capability_name).strip())
    if observation is None:
        return "unknown", "capability_missing"
    status = str(getattr(observation, "status", "unknown")).strip().lower() or "unknown"
    reason = str(getattr(observation, "reason", "")).strip()
    return status, reason


def _build_deployment_id(
    *, provider_name: str, model_name: str, base_url: str = ""
) -> str:
    """Build stable provider/model deployment identity."""
    provider = str(provider_name).strip() or "unknown"
    model = str(model_name).strip() or "unknown"
    base = str(base_url).strip()
    if base:
        return f"{provider}:{model}@{base}"
    return f"{provider}:{model}"
