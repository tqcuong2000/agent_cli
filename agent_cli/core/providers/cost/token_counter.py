"""Token counting abstractions and provider-specific implementations."""

from __future__ import annotations

import importlib
import json
import logging
import math
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Sequence

from agent_cli.core.infra.registry.registry import DataRegistry

logger = logging.getLogger(__name__)

Message = Dict[str, Any]


class BaseTokenCounter(ABC):
    """Interface for counting message tokens for a target model."""

    @abstractmethod
    def count(self, messages: Sequence[Message], model_name: str) -> int:
        """Return an estimated token count for the provided message list."""


class HeuristicTokenCounter(BaseTokenCounter):
    """Provider-agnostic fallback token estimator.

    Approximates token count from character length with lightweight
    per-message overhead to mimic chat serialization framing.
    """

    def __init__(
        self,
        chars_per_token: float | None = None,
        *,
        data_registry: DataRegistry,
    ) -> None:
        registry = data_registry
        configured = registry.get_token_counter_defaults().get(
            "heuristic_chars_per_token",
            4.0,
        )
        value = configured if chars_per_token is None else chars_per_token
        self._chars_per_token = max(float(value), 1.0)

    def count(self, messages: Sequence[Message], model_name: str) -> int:
        if not messages:
            return 0

        chars = 0
        for message in messages:
            chars += len(_serialize_message(message))

        base = int(math.ceil(chars / self._chars_per_token))
        framing = (len(messages) * 4) + 2
        return max(base + framing, 0)


class TiktokenCounter(BaseTokenCounter):
    """OpenAI-oriented tokenizer using ``tiktoken`` encodings.

    Falls back to a heuristic counter if ``tiktoken`` is unavailable or
    if any runtime counting error occurs.
    """

    def __init__(
        self,
        fallback: Optional[BaseTokenCounter] = None,
        *,
        data_registry: DataRegistry,
    ) -> None:
        self._data_registry = data_registry
        self._fallback = fallback or HeuristicTokenCounter(
            data_registry=self._data_registry
        )

    def count(self, messages: Sequence[Message], model_name: str) -> int:
        if not messages:
            return 0

        try:
            tiktoken_mod = importlib.import_module("tiktoken")
            get_encoding = getattr(tiktoken_mod, "get_encoding")
            encoding = get_encoding(self._encoding_name(model_name))
        except Exception as exc:
            logger.debug("tiktoken unavailable for '%s': %s", model_name, exc)
            return self._fallback.count(messages, model_name)

        try:
            token_count = 2  # Priming overhead for assistant reply.
            for message in messages:
                token_count += 4  # Message framing overhead.
                for key, value in message.items():
                    token_count += len(encoding.encode(_value_to_text(value)))
                    if key == "name":
                        token_count -= 1
            return max(token_count, 0)
        except Exception as exc:
            logger.debug("tiktoken count failed for '%s': %s", model_name, exc)
            return self._fallback.count(messages, model_name)

    def _encoding_name(self, model_name: str) -> str:
        return self._data_registry.get_tokenizer_encoding(model_name)


class AnthropicTokenCounter(BaseTokenCounter):
    """Token counter that prefers Anthropic's native ``count_tokens`` API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        fallback: Optional[BaseTokenCounter] = None,
        *,
        data_registry: DataRegistry,
    ) -> None:
        self._api_key = api_key
        self._fallback = fallback or HeuristicTokenCounter(
            data_registry=data_registry
        )
        self._client: Any = None
        self._client_ready = False

    def count(self, messages: Sequence[Message], model_name: str) -> int:
        client = self._get_client()
        if client is None:
            return self._fallback.count(messages, model_name)

        try:
            system_text, anthropic_messages = _to_anthropic_payload(messages)
            kwargs: Dict[str, Any] = {
                "model": model_name,
                "messages": anthropic_messages,
            }
            if system_text:
                kwargs["system"] = system_text

            result = client.messages.count_tokens(**kwargs)
            return _extract_token_count(
                result,
                fallback=lambda: self._fallback.count(messages, model_name),
            )
        except Exception as exc:
            logger.debug("Anthropic count_tokens failed for '%s': %s", model_name, exc)
            return self._fallback.count(messages, model_name)

    def _get_client(self) -> Any:
        if self._client_ready:
            return self._client

        self._client_ready = True
        if not self._api_key:
            return None

        try:
            anthropic_mod = importlib.import_module("anthropic")
            anthropic_cls = getattr(anthropic_mod, "Anthropic")
            self._client = anthropic_cls(api_key=self._api_key)
            return self._client
        except Exception as exc:
            logger.debug("Unable to initialize Anthropic count client: %s", exc)
            return None


class GeminiTokenCounter(BaseTokenCounter):
    """Token counter that prefers Gemini SDK ``count_tokens``."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        fallback: Optional[BaseTokenCounter] = None,
        *,
        data_registry: DataRegistry,
    ) -> None:
        self._api_key = api_key
        self._fallback = fallback or HeuristicTokenCounter(
            data_registry=data_registry
        )
        self._client: Any = None
        self._client_ready = False

    def count(self, messages: Sequence[Message], model_name: str) -> int:
        client = self._get_client()
        if client is None:
            return self._fallback.count(messages, model_name)

        try:
            payload = _to_gemini_contents(messages)
            result = client.models.count_tokens(model=model_name, contents=payload)
            return _extract_token_count(
                result,
                fallback=lambda: self._fallback.count(messages, model_name),
            )
        except Exception as exc:
            logger.debug("Gemini count_tokens failed for '%s': %s", model_name, exc)
            return self._fallback.count(messages, model_name)

    def _get_client(self) -> Any:
        if self._client_ready:
            return self._client

        self._client_ready = True
        if not self._api_key:
            return None

        try:
            genai_mod = importlib.import_module("google.genai")
            client_cls = getattr(genai_mod, "Client")
            self._client = client_cls(api_key=self._api_key)
            return self._client
        except Exception as exc:
            logger.debug("Unable to initialize Gemini count client: %s", exc)
            return None


def _extract_token_count(result: Any, fallback: Callable[[], int]) -> int:
    """Extract a token count from SDK response objects or dict-like payloads."""
    if result is None:
        return fallback()

    for key in ("total_tokens", "input_tokens", "prompt_token_count"):
        val = getattr(result, key, None)
        if isinstance(val, int):
            return max(val, 0)

    if isinstance(result, dict):
        for key in ("total_tokens", "input_tokens", "prompt_token_count"):
            val = result.get(key)
            if isinstance(val, int):
                return max(val, 0)

    return fallback()


def _to_anthropic_payload(
    messages: Sequence[Message],
) -> tuple[str, List[Dict[str, str]]]:
    system_parts: List[str] = []
    converted: List[Dict[str, str]] = []

    for message in messages:
        role = str(message.get("role", "user"))
        content = _value_to_text(message.get("content", ""))

        if role == "system":
            if content:
                system_parts.append(content)
            continue

        if role not in {"user", "assistant"}:
            content = f"[{role}] {content}".strip()
            role = "user"

        converted.append({"role": role, "content": content})

    return "\n\n".join(system_parts), converted


def _to_gemini_contents(messages: Sequence[Message]) -> str:
    """Flatten conversation into text payload compatible with count_tokens."""
    lines: List[str] = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = _value_to_text(message.get("content", ""))
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _serialize_message(message: Message) -> str:
    parts = []
    for key in sorted(message.keys()):
        parts.append(f"{key}:{_value_to_text(message[key])}")
    return "\n".join(parts)


def _value_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return str(value)
    if isinstance(value, list):
        return "\n".join(_value_to_text(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, ensure_ascii=True)
    return str(value)
