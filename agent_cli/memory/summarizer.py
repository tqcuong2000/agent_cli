"""Adaptive summarization memory manager for token-aware compaction."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Sequence

from agent_cli.agent.memory import WorkingMemoryManager
from agent_cli.data import DataRegistry
from agent_cli.memory.token_counter import HeuristicTokenCounter

logger = logging.getLogger(__name__)

_PATH_PATTERN = re.compile(r"(?:[A-Za-z]:[\\/]|/)?(?:[\w.-]+[\\/])+[\w.-]+")
_TOOL_PATTERN = re.compile(
    r'(?:\[Tool:\s*([^\]]+)\])|(?:"tool"\s*:\s*"([^"]+)")',
    re.IGNORECASE,
)

SummarizerProviderFactory = Callable[[str], Any]


class SummarizingMemoryManager(WorkingMemoryManager):
    """Compacts memory by summarizing older turns into one context message."""

    def __init__(
        self,
        *args: Any,
        keep_recent_turns: int | None = None,
        summarization_model: str | None = None,
        summarizer_provider_factory: Optional[SummarizerProviderFactory] = None,
        summary_budget_tokens: int | None = None,
        summary_response_tokens: int | None = None,
        data_registry: Optional[DataRegistry] = None,
        **kwargs: Any,
    ) -> None:
        registry = data_registry or DataRegistry()
        summarizer_defaults = registry.get_summarizer_defaults()
        internal_models = registry.get_internal_models()
        heuristic_limits = summarizer_defaults.get("heuristic_limits", {})

        effective_keep_recent_turns = int(
            keep_recent_turns
            if keep_recent_turns is not None
            else summarizer_defaults.get("keep_recent_turns", 5)
        )
        effective_summarization_model = summarization_model or internal_models.get(
            "summarization_model",
            "gpt-4o-mini",
        )
        effective_summary_budget_tokens = int(
            summary_budget_tokens
            if summary_budget_tokens is not None
            else summarizer_defaults.get("summary_budget_tokens", 2000)
        )
        effective_summary_response_tokens = int(
            summary_response_tokens
            if summary_response_tokens is not None
            else summarizer_defaults.get("summary_response_tokens", 600)
        )

        # Keep enough recent messages for emergency drop-based fallback.
        keep_recent_messages = max(effective_keep_recent_turns * 2, 6)
        if "keep_recent" not in kwargs:
            kwargs["keep_recent"] = keep_recent_messages
        super().__init__(*args, **kwargs)

        self._keep_recent_turns = effective_keep_recent_turns
        self._summarization_model = effective_summarization_model
        self._summarizer_provider_factory = summarizer_provider_factory
        self._summary_budget_tokens = max(effective_summary_budget_tokens, 400)
        self._summary_response_tokens = max(
            min(effective_summary_response_tokens, self._summary_budget_tokens - 100),
            100,
        )
        self._summary_max_words = int(summarizer_defaults.get("summary_max_words", 250))
        self._min_summary_length = int(
            summarizer_defaults.get("min_summary_length", 240)
        )
        self._summary_truncation_factor = float(
            summarizer_defaults.get("summary_truncation_factor", 0.8)
        )
        self._heuristic_limits = {
            "max_goals": int(heuristic_limits.get("max_goals", 4)),
            "max_decisions": int(heuristic_limits.get("max_decisions", 4)),
            "max_actions": int(heuristic_limits.get("max_actions", 6)),
            "max_tools": int(heuristic_limits.get("max_tools", 6)),
            "max_files": int(heuristic_limits.get("max_files", 8)),
            "max_open_items": int(heuristic_limits.get("max_open_items", 4)),
            "condensed_line_max_chars": int(
                heuristic_limits.get("condensed_line_max_chars", 140)
            ),
            "single_line_max_chars": int(
                heuristic_limits.get("single_line_max_chars", 500)
            ),
        }
        self._prompt_counter = HeuristicTokenCounter(data_registry=registry)
        self._summarizer_provider: Any = None

    async def summarize_and_compact(self) -> None:
        """Replace middle messages with a generated context summary."""
        if not self.should_compact():
            return

        if len(self._messages) <= 2:
            return

        system_msgs, other_msgs = self._split_system_and_other_messages(self._messages)
        if len(other_msgs) <= 1:
            return

        older, recent = self._partition_by_recent_turns(other_msgs)
        if not older:
            # No "middle" section to summarize; use sliding-window fallback.
            await super().summarize_and_compact()
            return

        summary = await self._summarize_middle_messages(older)
        summary_msg = {
            "role": "user",
            "content": "[Context Summary]\n" + summary.strip(),
        }

        candidate = list(system_msgs)
        candidate.append(summary_msg)
        candidate.extend(recent)
        self._messages = candidate

        # Emergency fit: shrink recent history before dropping summary.
        await self._fit_summary_context(system_msgs, summary_msg, recent)

        logger.info(
            "Summarized context: summarized=%d kept_recent=%d total_messages=%d",
            len(older),
            len(recent),
            len(self._messages),
        )

    def _partition_by_recent_turns(
        self, messages: List[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Split messages into older vs recent N-turn segment."""
        user_indices = [
            idx for idx, msg in enumerate(messages) if msg.get("role") == "user"
        ]

        if not user_indices:
            # Fallback if no explicit user boundaries exist.
            keep_from = max(len(messages) - (self._keep_recent_turns * 2), 0)
            return messages[:keep_from], messages[keep_from:]

        if len(user_indices) <= self._keep_recent_turns:
            return [], messages

        recent_start = user_indices[-self._keep_recent_turns]
        return messages[:recent_start], messages[recent_start:]

    async def _summarize_middle_messages(
        self, messages: Sequence[Dict[str, Any]]
    ) -> str:
        """Summarize middle messages with cheap model, fallback to heuristic."""
        provider = self._get_summarizer_provider()
        if provider is not None:
            summary = await self._summarize_with_model(provider, messages)
            if summary:
                return summary

        return self._heuristic_summary(messages)

    def _get_summarizer_provider(self) -> Any:
        if self._summarizer_provider is not None:
            return self._summarizer_provider

        if self._summarizer_provider_factory is None:
            return None

        try:
            self._summarizer_provider = self._summarizer_provider_factory(
                self._summarization_model
            )
        except Exception as exc:
            logger.warning(
                "Could not initialize summarization provider for '%s': %s",
                self._summarization_model,
                exc,
            )
            self._summarizer_provider = None

        return self._summarizer_provider

    async def _summarize_with_model(
        self,
        provider: Any,
        messages: Sequence[Dict[str, Any]],
    ) -> Optional[str]:
        prompt_budget = max(
            self._summary_budget_tokens - self._summary_response_tokens,
            200,
        )
        prompt_text = self._build_summary_prompt(messages, prompt_budget)
        summary_context = [
            {
                "role": "system",
                "content": (
                    "You summarize older conversation context for a coding agent. "
                    "Return plain text only. Keep it factual and concise."
                ),
            },
            {"role": "user", "content": prompt_text},
        ]

        try:
            response = await provider.safe_generate(
                context=summary_context,
                tools=None,
                max_tokens=self._summary_response_tokens,
                max_retries=1,
            )
            text = str(getattr(response, "text_content", "")).strip()
            if not text:
                return None
            return self._normalize_summary(text)
        except Exception as exc:
            logger.warning(
                "Model summarization failed; falling back to heuristic: %s", exc
            )
            return None

    def _build_summary_prompt(
        self,
        messages: Sequence[Dict[str, Any]],
        prompt_budget: int,
    ) -> str:
        header = (
            "Summarize these earlier conversation messages.\n"
            "Required sections:\n"
            "1) Goals\n"
            "2) Decisions\n"
            "3) Actions Taken\n"
            "4) Tool Usage\n"
            "5) Files Mentioned\n"
            "6) Open Items\n"
            f"Keep under {self._summary_max_words} words.\n\n"
            "Messages:\n"
        )

        lines: List[str] = []
        # Keep the newest part of the middle segment when prompt budget is tight.
        for msg in reversed(messages):
            role = str(msg.get("role", "unknown"))
            content = str(msg.get("content", "")).strip()
            if not content:
                continue
            single_line = " ".join(content.split())
            single_line = single_line[: self._heuristic_limits["single_line_max_chars"]]
            line = f"- [{role}] {single_line}"
            candidate = header + "\n".join(reversed(lines + [line]))
            prompt_tokens = self._prompt_counter.count(
                [{"role": "user", "content": candidate}],
                self._summarization_model,
            )
            if prompt_tokens > prompt_budget:
                break
            lines.append(line)

        if not lines:
            lines = [
                "- [system] Context is long; capture key outcomes and pending work."
            ]

        return header + "\n".join(reversed(lines))

    async def _fit_summary_context(
        self,
        system_msgs: List[Dict[str, Any]],
        summary_msg: Dict[str, Any],
        recent_messages: List[Dict[str, Any]],
    ) -> None:
        trimmed_recent = list(recent_messages)

        while self._token_budget.should_compact(
            self._count_messages(system_msgs + [summary_msg] + trimmed_recent)
        ):
            if not trimmed_recent:
                break
            trimmed_recent.pop(0)

        candidate = system_msgs + [summary_msg] + trimmed_recent
        self._messages = candidate
        if not self.should_compact():
            return

        # If still too large, shorten the summary text before falling back.
        summary_content = str(summary_msg.get("content", ""))
        while self.should_compact() and len(summary_content) > self._min_summary_length:
            summary_content = summary_content[
                : int(len(summary_content) * self._summary_truncation_factor)
            ].rstrip()
            summary_msg["content"] = (
                summary_content + "\n[Summary truncated for token budget.]"
            )
            self._messages = system_msgs + [summary_msg] + trimmed_recent

        if self.should_compact():
            await super().summarize_and_compact()

    def _heuristic_summary(self, messages: Sequence[Dict[str, Any]]) -> str:
        """Local fallback summary when no cheap model is available."""
        goals: List[str] = []
        decisions: List[str] = []
        actions: List[str] = []
        tools: List[str] = []
        files: List[str] = []
        open_items: List[str] = []

        for msg in messages:
            role = str(msg.get("role", ""))
            content = str(msg.get("content", "")).strip()
            if not content:
                continue

            condensed = " ".join(content.split())
            lowered = condensed.lower()

            if role == "user" and len(goals) < self._heuristic_limits["max_goals"]:
                goals.append(
                    condensed[: self._heuristic_limits["condensed_line_max_chars"]]
                )

            if role == "assistant":
                if any(
                    word in lowered for word in ("decided", "will", "plan", "approach")
                ):
                    if len(decisions) < self._heuristic_limits["max_decisions"]:
                        decisions.append(
                            condensed[
                                : self._heuristic_limits["condensed_line_max_chars"]
                            ]
                        )
                if any(
                    word in lowered
                    for word in (
                        "implemented",
                        "updated",
                        "fixed",
                        "completed",
                        "added",
                    )
                ):
                    if len(actions) < self._heuristic_limits["max_actions"]:
                        actions.append(
                            condensed[
                                : self._heuristic_limits["condensed_line_max_chars"]
                            ]
                        )
                if any(
                    word in lowered for word in ("todo", "next", "pending", "follow-up")
                ):
                    if len(open_items) < self._heuristic_limits["max_open_items"]:
                        open_items.append(
                            condensed[
                                : self._heuristic_limits["condensed_line_max_chars"]
                            ]
                        )

            if role == "tool":
                match = _TOOL_PATTERN.search(condensed)
                if match:
                    tool_name = (match.group(1) or match.group(2) or "").strip()
                    if tool_name:
                        tools.append(tool_name)
                elif len(tools) < self._heuristic_limits["max_tools"]:
                    tools.append("tool-output")

            for path in _PATH_PATTERN.findall(condensed):
                if len(path) <= 120:
                    files.append(path)

        tools = _dedupe_preserve_order(tools)[: self._heuristic_limits["max_tools"]]
        files = _dedupe_preserve_order(files)[: self._heuristic_limits["max_files"]]

        lines = [
            "Goals:",
            _format_bullets(goals, fallback="Continue the active user-request thread."),
            "Decisions:",
            _format_bullets(decisions, fallback="No explicit decisions captured."),
            "Actions Taken:",
            _format_bullets(
                actions, fallback="Prior steps executed across prior turns."
            ),
            "Tool Usage:",
            _format_bullets(tools, fallback="No tool usage extracted."),
            "Files Mentioned:",
            _format_bullets(files, fallback="No file paths extracted."),
            "Open Items:",
            _format_bullets(open_items, fallback="No explicit open items captured."),
        ]
        return "\n".join(lines)

    @staticmethod
    def _normalize_summary(text: str) -> str:
        normalized = text.strip()
        try:
            payload = json.loads(normalized)
            if isinstance(payload, dict):
                decision = payload.get("decision")
                if isinstance(decision, dict):
                    message = decision.get("message")
                    if isinstance(message, str) and message.strip():
                        return message.strip()
        except json.JSONDecodeError:
            pass
        return normalized.strip()


def _dedupe_preserve_order(values: Sequence[str]) -> List[str]:
    seen = set()
    output: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _format_bullets(values: Sequence[str], *, fallback: str) -> str:
    if not values:
        return f"- {fallback}"
    return "\n".join(f"- {value}" for value in values)
