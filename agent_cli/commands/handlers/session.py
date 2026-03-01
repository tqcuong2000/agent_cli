"""Session command handlers for persisted multi-turn conversations."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from agent_cli.commands.base import CommandContext, CommandResult, command
from agent_cli.core.events.events import SettingsChangedEvent
from agent_cli.memory.token_counter import HeuristicTokenCounter
from agent_cli.session.base import AbstractSessionManager, Session


@command(
    name="session",
    description="Manage persisted sessions",
    usage="/session <save|list|restore|delete|info|new>",
    category="Session",
)
async def cmd_session(args: List[str], ctx: CommandContext) -> CommandResult:
    """Handle `/session` subcommands."""
    manager = _get_session_manager(ctx)
    if manager is None:
        return CommandResult(
            success=False,
            message="Session manager is not configured.",
        )

    if not args:
        return CommandResult(success=False, message=_usage())

    subcommand = args[0].lower()
    sub_args = args[1:]

    if subcommand == "save":
        return _cmd_session_save(sub_args, ctx, manager)
    if subcommand == "list":
        return _cmd_session_list(manager)
    if subcommand == "restore":
        return await _cmd_session_restore(sub_args, ctx, manager)
    if subcommand == "delete":
        return _cmd_session_delete(sub_args, ctx, manager)
    if subcommand == "info":
        return _cmd_session_info(ctx, manager)
    if subcommand == "new":
        return _cmd_session_new(sub_args, ctx, manager)

    return CommandResult(
        success=False,
        message=f"Unknown /session subcommand: {subcommand}\n{_usage()}",
    )


def _cmd_session_save(
    args: List[str],
    ctx: CommandContext,
    manager: AbstractSessionManager,
) -> CommandResult:
    name = " ".join(args).strip() or None
    session = manager.get_active()
    if session is None:
        session = manager.create_session(name=name)

    if name:
        session.name = name
    session.active_model = ctx.settings.default_model
    manager.save(session)

    return CommandResult(
        success=True,
        message=(
            f"Session saved: {session.session_id}"
            + (f" ({session.name})" if session.name else "")
        ),
    )


def _cmd_session_list(manager: AbstractSessionManager) -> CommandResult:
    summaries = manager.list()
    active = manager.get_active()
    active_id = active.session_id if active else ""

    if not summaries:
        return CommandResult(success=True, message="No saved sessions.")

    lines = ["Saved sessions:"]
    for summary in summaries:
        marker = "*" if summary.session_id == active_id else " "
        session_name = summary.name or "(unnamed)"
        updated = _format_datetime(summary.updated_at)
        lines.append(
            f"{marker} {summary.session_id} | {session_name} | {updated} | "
            f"messages={summary.message_count}"
        )
    lines.append("")
    lines.append("'*' marks the active session.")

    return CommandResult(success=True, message="\n".join(lines))


async def _cmd_session_restore(
    args: List[str],
    ctx: CommandContext,
    manager: AbstractSessionManager,
) -> CommandResult:
    if not args:
        return CommandResult(
            success=False,
            message="Usage: /session restore <id>",
        )

    session_id = _resolve_session_id(manager, args[0])
    if session_id is None:
        return CommandResult(
            success=False,
            message=f"Session not found: {args[0]}",
        )

    session = manager.load(session_id)
    _hydrate_memory_from_session(ctx, session)

    model_note = ""
    if session.active_model and session.active_model != ctx.settings.default_model:
        switch_error = await _switch_runtime_model(
            model_name=session.active_model,
            ctx=ctx,
        )
        if switch_error is None:
            model_note = f"\nModel switched to: {session.active_model}"
        else:
            model_note = (
                f"\nModel switch failed for '{session.active_model}': {switch_error}"
            )

    return CommandResult(
        success=True,
        message=(
            f"Restored session: {session.session_id}"
            f"\nMessages: {len(session.messages)}"
            f"{model_note}"
        ),
    )


def _cmd_session_delete(
    args: List[str],
    ctx: CommandContext,
    manager: AbstractSessionManager,
) -> CommandResult:
    if not args:
        return CommandResult(
            success=False,
            message="Usage: /session delete <id>",
        )

    session_id = _resolve_session_id(manager, args[0])
    if session_id is None:
        return CommandResult(
            success=False,
            message=f"Session not found: {args[0]}",
        )

    active = manager.get_active()
    was_active = active is not None and active.session_id == session_id
    removed = manager.delete(session_id)
    if not removed:
        return CommandResult(
            success=False,
            message=f"Could not delete session: {session_id}",
        )

    if was_active:
        replacement = manager.create_session()
        replacement.active_model = ctx.settings.default_model
        manager.save(replacement)
        ctx.memory_manager.reset_working()
        return CommandResult(
            success=True,
            message=(
                f"Deleted active session: {session_id}\n"
                f"Started new session: {replacement.session_id}"
            ),
        )

    return CommandResult(
        success=True,
        message=f"Deleted session: {session_id}",
    )


def _cmd_session_info(
    ctx: CommandContext,
    manager: AbstractSessionManager,
) -> CommandResult:
    session = manager.get_active()
    if session is None:
        return CommandResult(success=False, message="No active session.")

    token_count = _estimate_session_tokens(ctx, session)
    lines = [
        "Active session:",
        f"  id: {session.session_id}",
        f"  name: {session.name or '(unnamed)'}",
        f"  model: {session.active_model or ctx.settings.default_model}",
        f"  messages: {len(session.messages)}",
        f"  tasks: {len(session.task_ids)}",
        f"  estimated tokens: {token_count}",
        f"  total cost: ${session.total_cost:.6f}",
        f"  updated: {_format_datetime(session.updated_at)}",
    ]
    return CommandResult(success=True, message="\n".join(lines))


def _cmd_session_new(
    args: List[str],
    ctx: CommandContext,
    manager: AbstractSessionManager,
) -> CommandResult:
    current = manager.get_active()
    if current is not None:
        manager.save(current)

    name = " ".join(args).strip() or None
    session = manager.create_session(name=name)
    session.active_model = ctx.settings.default_model
    manager.save(session)
    ctx.memory_manager.reset_working()

    return CommandResult(
        success=True,
        message=f"Started new session: {session.session_id}",
    )


def _usage() -> str:
    return (
        "Usage:\n"
        "  /session save [name]\n"
        "  /session list\n"
        "  /session restore <id>\n"
        "  /session delete <id>\n"
        "  /session info\n"
        "  /session new [name]"
    )


def _get_session_manager(ctx: CommandContext) -> Optional[AbstractSessionManager]:
    if ctx.app_context is None:
        return None
    return ctx.app_context.session_manager


def _resolve_session_id(
    manager: AbstractSessionManager,
    identifier: str,
) -> Optional[str]:
    session_id = identifier.strip()
    if not session_id:
        return None

    summaries = manager.list()
    for summary in summaries:
        if summary.session_id == session_id:
            return summary.session_id

    prefix_matches = [
        summary.session_id
        for summary in summaries
        if summary.session_id.startswith(session_id)
    ]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    return None


def _hydrate_memory_from_session(ctx: CommandContext, session: Session) -> None:
    ctx.memory_manager.reset_working()
    for message in session.messages:
        if isinstance(message, dict):
            ctx.memory_manager.add_working_event(message)


def _estimate_session_tokens(ctx: CommandContext, session: Session) -> int:
    model_name = session.active_model or ctx.settings.default_model

    if ctx.app_context is not None:
        try:
            counter = ctx.app_context.providers.get_token_counter(model_name)
            return counter.count(session.messages, model_name)
        except Exception:
            pass

    return HeuristicTokenCounter().count(session.messages, model_name)


async def _switch_runtime_model(
    model_name: str,
    ctx: CommandContext,
) -> Optional[str]:
    ctx.settings.default_model = model_name

    if ctx.app_context and ctx.app_context.orchestrator:
        try:
            agent = ctx.app_context.orchestrator._default_agent
            new_provider = ctx.app_context.providers.get_provider(model_name)
            agent.provider = new_provider
        except Exception as exc:
            return str(exc)

    if ctx.app_context:
        try:
            token_counter = ctx.app_context.providers.get_token_counter(model_name)
            token_budget = ctx.app_context.providers.get_token_budget(
                model_name,
                response_reserve=4096,
                compaction_threshold=ctx.settings.context_compaction_threshold,
            )
            await ctx.memory_manager.on_model_changed(
                model_name,
                token_counter=token_counter,
                token_budget=token_budget,
            )
        except Exception as exc:
            return str(exc)

    if ctx.event_bus:
        await ctx.event_bus.publish(
            SettingsChangedEvent(
                setting_name="default_model",
                new_value=model_name,
                source="cmd_session_restore",
            )
        )
    return None


def _format_datetime(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")
