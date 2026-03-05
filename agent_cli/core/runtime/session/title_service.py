"""Session Title generation and management."""

from __future__ import annotations

import logging
import re
from typing import Any

from agent_cli.core.providers.base.base import BaseLLMProvider
from agent_cli.core.runtime.session.base import Session
from agent_cli.core.infra.registry.registry import DataRegistry

logger = logging.getLogger(__name__)


class SessionTitleService:
    """Generates and manages session titles independently from agents."""

    _DEFAULT_SESSION_TITLE = "Untitled session"

    def __init__(self, data_registry: DataRegistry) -> None:
        self._data_registry = data_registry
        defaults = self._data_registry.get_title_generation_defaults()
        self._min_turns = int(defaults.get("min_turns", 3))

    async def generate_title(
        self,
        provider: BaseLLMProvider,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 32,
    ) -> str:
        """Generate a title from conversation context using the given LLM."""
        if not messages:
            return self._DEFAULT_SESSION_TITLE

        preview = self._build_session_preview(messages)
        prompt_template = self._data_registry.get_prompt_template("title_generator")
        final_prompt = prompt_template.replace("{preview}", preview)

        try:
            response = await provider.safe_generate(
                context=[{"role": "user", "content": final_prompt}],
                tools=None,
                max_tokens=max_tokens,
                # Force minimal effort for simple title generation
                effort="minimal",
            )
            raw_title = str(response.text_content or "").strip()
            title = self.normalize_title(raw_title)
            return title or self._DEFAULT_SESSION_TITLE
        except Exception as e:
            logger.warning("Failed to generate session title: %s", e)
            return self._DEFAULT_SESSION_TITLE

    def should_generate(
        self,
        session: Session,
        *,
        force: bool = False,
    ) -> bool:
        """Check if this session is eligible for title generation."""
        if force:
            return True

        current_name = str(session.name or "").strip()
        has_default_name = not current_name or current_name == self._DEFAULT_SESSION_TITLE
        
        if not has_default_name:
            # Already has a user-set or generated title
            return False

        # Only count user turns
        user_turns = sum(1 for m in session.messages if str(m.get("role", "")).lower() == "user")
        return user_turns >= self._min_turns

    @staticmethod
    def normalize_title(raw: str) -> str:
        """Clean and truncate raw LLM output into a valid title."""
        if not raw:
            return ""

        # Remove quotes, bolding, and whitespace
        clean = re.sub(r'[*_"]', "", raw).strip()
        # Remove "Title:" or "Session Title:" prefixes
        clean = re.sub(r"(?i)^(?:session\s*)?title:\s*", "", clean)

        # Truncate at first newline
        clean = clean.split("\n", maxsplit=1)[0].strip()

        words = [w for w in clean.split() if w]
        if not words:
            return ""

        # Limit to first 8 words
        return " ".join(words[:8])

    @staticmethod
    def _build_session_preview(messages: list[dict[str, Any]]) -> str:
        """Build a condensed preview of recent conversation history."""
        # Grab up to the last 16 messages for context
        recent = messages[-16:]
        lines = []
        for msg in recent:
            role = str(msg.get("role", "unknown")).upper()
            content = str(msg.get("content", "")).strip()
            # Truncate each message entry to 1000 chars to avoid prompt bloat
            if len(content) > 1000:
                content = content[:1000] + "... [truncated]"

            if content:
                lines.append(f"[{role}]\n{content}\n")
        return "\n".join(lines)
