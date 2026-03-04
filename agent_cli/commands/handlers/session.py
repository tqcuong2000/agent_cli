"""UI session command handlers."""

from __future__ import annotations

import json
import re
from typing import List

from agent_cli.commands.base import CommandContext, CommandResult, command
from agent_cli.core.events.events import SettingsChangedEvent

_DEFAULT_SESSION_TITLE = "Untitled session"
_MAX_TITLE_WORDS = 8


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


@command(
    name="generate_title",
    description="Generate a new title for the active session",
    usage="/generate_title",
    category="Session",
)
async def cmd_generate_title(args: List[str], ctx: CommandContext) -> CommandResult:
    """Generate and persist a short title for the active session."""
    app_ctx = ctx.app_context
    if app_ctx is None or app_ctx.session_manager is None:
        return CommandResult(
            success=False,
            message="Session manager is not configured.",
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
        preview = _build_session_preview(session.messages)
        if preview:
            try:
                response = await provider.safe_generate(
                    context=[
                        {
                            "role": "system",
                            "content": (
                                "You generate short conversation titles. "
                                "Return only the title, plain text, 2-8 words."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                "Generate a concise title for this session.\n\n"
                                f"{preview}\n\n"
                                "Return only the title."
                            ),
                        },
                    ],
                    tools=None,
                    max_tokens=32,
                )
                title = _normalize_title(getattr(response, "text_content", ""))
            except Exception:
                title = ""

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


def _build_session_preview(messages: List[dict]) -> str:
    """Build a compact transcript preview for title generation."""
    lines: List[str] = []
    for message in messages[-16:]:
        role = str(message.get("role", "")).strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        single_line = " ".join(content.split())
        lines.append(f"{role}: {single_line[:180]}")
    return "\n".join(lines)


def _normalize_title(raw: str) -> str:
    """Normalize provider output into a short plain-text session title."""
    text = raw.strip()
    if not text:
        return ""

    # Accept either plain text or {"title": "..."} output.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            text = str(parsed.get("title", "")).strip()
        elif isinstance(parsed, str):
            text = parsed.strip()
    except json.JSONDecodeError:
        pass

    text = text.strip().strip("`").strip()
    if not text:
        return ""

    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    text = first_line or text
    text = re.sub(r"^\s*title\s*:\s*", "", text, flags=re.IGNORECASE).strip()
    text = text.strip("\"' ")
    text = " ".join(text.split())
    if not text:
        return ""

    words = text.split(" ")
    if len(words) > _MAX_TITLE_WORDS:
        text = " ".join(words[:_MAX_TITLE_WORDS])
    return text
