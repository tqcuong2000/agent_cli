"""Simulate the first LLM prompt payload for an incoming user request.

This script mirrors the runtime path used by Orchestrator -> BaseAgent.handle_task
for the first generation call, without invoking the model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_cli.core.infra.registry.bootstrap import create_app


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Simulate the first-turn prompt/context that will be sent to the active agent."
        )
    )
    parser.add_argument(
        "request",
        nargs="+",
        help="User request text (e.g. simulate_prompt.py explain this module)",
    )
    parser.add_argument(
        "--agent",
        default="",
        help="Agent name to simulate (defaults to current active agent).",
    )
    parser.add_argument(
        "--prior-context",
        default="",
        help="Optional cross-agent prior context injected before the request.",
    )
    parser.add_argument(
        "--no-session",
        action="store_true",
        help="Ignore active session history and simulate a fresh conversation.",
    )
    parser.add_argument(
        "--session-id",
        default="",
        help="Load messages from a specific session ID (read-only).",
    )
    parser.add_argument(
        "--session-file",
        default="",
        help="Load messages from a specific session JSON file path.",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Workspace root used for app bootstrap (default: current directory).",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=4096,
        help="Max output tokens used in provider request preview.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Write compact JSON output to file.",
    )
    parser.add_argument(
        "--output",
        default="",
        help=(
            "Optional output file path. Defaults to "
            "dev/generated/simulated_prompt_<timestamp>.json"
        ),
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_output_path() -> Path:
    scripts_dir = Path(__file__).resolve().parent
    generated_dir = scripts_dir.parent / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return generated_dir / f"simulated_prompt_{stamp}.json"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, Path):
        return str(value)

    if is_dataclass(value):
        return _json_safe(asdict(value))

    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return _json_safe(value.model_dump())
        except Exception:
            pass

    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]

    return repr(value)


def _read_session_messages_from_file(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_messages = payload.get("messages", [])
    if not isinstance(raw_messages, list):
        raise ValueError(f"Session file '{path}' has non-list 'messages'.")
    return [m for m in raw_messages if isinstance(m, dict)]


def _session_messages_from_id(
    session_id: str, session_dir: Path
) -> List[Dict[str, Any]]:
    path = session_dir / f"{session_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Session ID '{session_id}' not found at '{path}'.")
    return _read_session_messages_from_file(path)


def _resolve_agent(context: Any, requested_name: str):
    requested_name = requested_name.strip()
    if requested_name:
        registry = context.agent_registry
        if registry is None:
            raise RuntimeError("Agent registry is not configured.")
        agent = registry.get(requested_name)
        if agent is None:
            available = ", ".join(sorted(registry.get_all().keys()))
            raise ValueError(
                f"Unknown agent '{requested_name}'. Available agents: {available}"
            )
        return agent

    if (
        context.session_agents is not None
        and context.session_agents.active_agent is not None
    ):
        return context.session_agents.active_agent

    if context.orchestrator is not None:
        return context.orchestrator.active_agent

    raise RuntimeError("Unable to resolve active agent.")


def _provider_request_preview(
    provider: Any,
    context_messages: List[Dict[str, Any]],
    tool_defs: List[Dict[str, Any]],
    max_tokens: int,
) -> Dict[str, Any]:
    provider_name = str(getattr(provider, "provider_name", "unknown"))
    model_name = str(getattr(provider, "model_name", ""))
    supports_native_tools = bool(getattr(provider, "supports_native_tools", False))

    messages_for_provider: List[Dict[str, Any]] = [dict(m) for m in context_messages]
    tool_strategy = "none"
    formatted_tools: Any = None
    prompt_injected_tool_text = ""
    formatting_error = ""

    if tool_defs:
        if supports_native_tools:
            tool_strategy = "native"
            try:
                formatted_tools = provider._tool_formatter.format_for_native_fc(
                    tool_defs
                )
            except Exception as exc:
                formatting_error = str(exc)
        else:
            tool_strategy = "prompt_injection"
            try:
                prompt_injected_tool_text = (
                    provider._tool_formatter.format_for_prompt_injection(tool_defs)
                )
                if hasattr(provider, "_inject_tools_into_system_prompt"):
                    messages_for_provider = provider._inject_tools_into_system_prompt(
                        messages_for_provider,
                        prompt_injected_tool_text,
                    )
            except Exception as exc:
                formatting_error = str(exc)

    kwargs: Dict[str, Any] = {
        "model": model_name,
        "max_tokens": max_tokens,
    }

    lowered = provider_name.lower()
    if lowered == "google" and hasattr(provider, "_convert_messages"):
        system_msg, gemini_history = provider._convert_messages(messages_for_provider)
        kwargs = {
            "model": model_name,
            "contents": gemini_history,
            "config": {
                "max_output_tokens": max_tokens,
            },
        }
        if system_msg:
            kwargs["config"]["system_instruction"] = system_msg
        if supports_native_tools and formatted_tools is not None:
            kwargs["config"]["tools"] = formatted_tools

    elif lowered == "anthropic" and hasattr(provider, "_split_system_message"):
        system_msg, chat_history = provider._split_system_message(messages_for_provider)
        kwargs = {
            "model": model_name,
            "messages": chat_history,
            "max_tokens": max_tokens,
        }
        if system_msg:
            kwargs["system"] = system_msg
        if supports_native_tools and formatted_tools is not None:
            kwargs["tools"] = formatted_tools

    elif lowered == "ollama":
        kwargs = {
            "model": model_name,
            "messages": messages_for_provider,
            "options": {"num_predict": max_tokens},
        }
        if supports_native_tools and formatted_tools is not None:
            kwargs["tools"] = formatted_tools

    elif lowered == "openai_compatible":
        kwargs = {
            "model": model_name,
            "messages": messages_for_provider,
            "max_tokens": max_tokens,
        }
        if supports_native_tools and formatted_tools is not None:
            kwargs["tools"] = formatted_tools

    else:
        kwargs = {
            "model": model_name,
            "messages": messages_for_provider,
        }
        if hasattr(provider, "_max_tokens_kwargs"):
            kwargs.update(provider._max_tokens_kwargs(max_tokens))
        else:
            kwargs["max_tokens"] = max_tokens
        if supports_native_tools and formatted_tools is not None:
            kwargs["tools"] = formatted_tools

    return {
        "provider": provider_name,
        "model": model_name,
        "supports_native_tools": supports_native_tools,
        "tool_strategy": tool_strategy,
        "prompt_injected_tool_text": prompt_injected_tool_text,
        "tool_format_error": formatting_error,
        "request_kwargs": kwargs,
    }


async def _run(args: argparse.Namespace) -> Dict[str, Any]:
    root = Path(args.root).resolve()
    context = create_app(root_folder=root)

    agent = _resolve_agent(context, args.agent)

    session_messages: Optional[List[Dict[str, Any]]] = None
    session_source = "none"

    session_manager = context.session_manager
    session_dir = Path.home() / ".agent_cli" / "sessions"
    if session_manager is not None and hasattr(session_manager, "_session_dir"):
        session_dir = Path(getattr(session_manager, "_session_dir"))

    if args.session_file:
        session_path = Path(args.session_file).expanduser().resolve()
        session_messages = _read_session_messages_from_file(session_path)
        session_source = f"file:{session_path}"
    elif args.session_id:
        session_messages = _session_messages_from_id(args.session_id, session_dir)
        session_source = f"id:{args.session_id}"
    elif not args.no_session and session_manager is not None:
        active = session_manager.get_active()
        if active is not None:
            session_messages = list(active.messages)
            session_source = f"active:{active.session_id}"

    request_text = " ".join(args.request).strip()
    if not request_text:
        raise ValueError("Request cannot be empty.")

    system_prompt = await agent.build_system_prompt(request_text)

    if session_messages is not None:
        working_context = agent._hydrate_session_messages(
            session_messages, system_prompt
        )
    else:
        working_context = [{"role": "system", "content": system_prompt}]

    prior_context = args.prior_context.strip()
    if prior_context:
        working_context.append(
            {
                "role": "user",
                "content": f"Context from previous steps:\n{prior_context}",
            }
        )

    working_context.append({"role": "user", "content": request_text})

    tool_defs = agent._get_tool_definitions()
    provider_preview = _provider_request_preview(
        provider=agent.provider,
        context_messages=working_context,
        tool_defs=tool_defs,
        max_tokens=int(args.max_tokens),
    )

    return {
        "generated_at": _utc_now_iso(),
        "workspace_root": str(root),
        "task": {
            "request": request_text,
            "prior_context": prior_context,
        },
        "agent": {
            "name": agent.name,
            "model": getattr(agent.provider, "model_name", ""),
            "tool_count": len(tool_defs),
            "configured_tools": list(getattr(agent.config, "tools", [])),
        },
        "session": {
            "source": session_source,
            "loaded_message_count": len(session_messages or []),
        },
        "prompt": {
            "system_prompt": system_prompt,
            "working_context": working_context,
            "working_context_message_count": len(working_context),
            "tool_definitions": tool_defs,
            "provider_preview": provider_preview,
        },
    }


def main() -> int:
    args = _parse_args()
    try:
        result = asyncio.run(_run(args))
    except Exception as exc:
        error_payload = {
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        print(json.dumps(error_payload, ensure_ascii=True, indent=2))
        return 1

    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else _default_output_path()
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    indent = None if args.compact else 2
    output_payload = json.dumps(_json_safe(result), ensure_ascii=True, indent=indent)
    output_path.write_text(output_payload + "\n", encoding="utf-8")
    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
