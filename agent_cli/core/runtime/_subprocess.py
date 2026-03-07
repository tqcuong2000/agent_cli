"""Shared subprocess execution helpers."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Mapping

_PYTHON_ENV_DEFAULTS = {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
}

_WINDOWS_DEFAULT_EXECUTABLE = "powershell.exe"
_POSIX_DEFAULT_EXECUTABLE = "/bin/sh"


@dataclass(frozen=True, slots=True)
class ShellProfile:
    """Resolved shell contract for command execution."""

    executable: str
    flavor: str
    display_name: str


def build_subprocess_env(
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a subprocess environment with UTF-8-safe Python defaults."""
    env = dict(os.environ if base_env is None else base_env)
    for key, value in _PYTHON_ENV_DEFAULTS.items():
        env.setdefault(key, value)
    return env


def resolve_shell_profile(
    tool_defaults: Mapping[str, Any] | None = None,
) -> ShellProfile:
    """Resolve the configured shell profile from data defaults."""
    shell_defaults = _mapping(tool_defaults)
    subprocess_defaults = _mapping(shell_defaults.get("subprocess"))

    configured_executable = str(
        subprocess_defaults.get("shell_executable", "")
    ).strip()
    executable = configured_executable or _default_shell_executable()
    configured_flavor = str(subprocess_defaults.get("shell_flavor", "")).strip()
    flavor = _resolve_shell_flavor(
        configured_flavor,
        executable=executable,
    )
    return ShellProfile(
        executable=executable,
        flavor=flavor,
        display_name=_display_name_for_shell(executable, flavor),
    )


def build_shell_command(shell_profile: ShellProfile, command: str) -> tuple[str, ...]:
    """Return argv for executing *command* under the configured shell."""
    normalized_command = str(command)
    if shell_profile.flavor == "powershell":
        normalized_command = _build_powershell_script(normalized_command)
        return (
            shell_profile.executable,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            normalized_command,
        )
    if shell_profile.flavor == "cmd":
        return (
            shell_profile.executable,
            "/d",
            "/s",
            "/c",
            normalized_command,
        )
    return (shell_profile.executable, "-c", normalized_command)


async def create_shell_subprocess(
    command: str,
    *,
    shell_profile: ShellProfile,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    **kwargs: Any,
) -> asyncio.subprocess.Process:
    """Create a subprocess using the configured shell profile."""
    argv = build_shell_command(shell_profile, command)
    return await asyncio.create_subprocess_exec(
        *argv,
        cwd=None if cwd is None else str(cwd),
        env=build_subprocess_env(env),
        **kwargs,
    )


def _default_shell_executable() -> str:
    return _WINDOWS_DEFAULT_EXECUTABLE if os.name == "nt" else _POSIX_DEFAULT_EXECUTABLE


def _resolve_shell_flavor(configured_flavor: str, *, executable: str) -> str:
    normalized = configured_flavor.strip().lower()
    if normalized in {"powershell", "cmd", "posix"}:
        return normalized

    executable_name = Path(executable).name.lower()
    if executable_name in {"powershell.exe", "powershell", "pwsh.exe", "pwsh"}:
        return "powershell"
    if executable_name in {"cmd.exe", "cmd"}:
        return "cmd"
    if executable_name in {"sh", "bash", "dash", "zsh"}:
        return "posix"
    return "powershell" if os.name == "nt" else "posix"


def _display_name_for_shell(executable: str, flavor: str) -> str:
    executable_name = Path(executable).stem or executable
    if flavor == "powershell":
        if executable_name.lower() == "pwsh":
            return "PowerShell"
        return "Windows PowerShell"
    if flavor == "cmd":
        return "Command Prompt"
    return executable_name


def _normalize_powershell_command(command: str) -> str:
    stripped = command.strip()
    if not stripped or stripped.startswith("&"):
        return stripped

    token = _leading_token(stripped)
    candidate = token.strip().strip("'\"")
    if not candidate:
        return stripped
    if _looks_like_executable_path(candidate):
        return f"& {stripped}"
    return stripped


def _build_powershell_script(command: str) -> str:
    normalized = _normalize_powershell_command(command)
    return (
        "$global:LASTEXITCODE = 0; "
        f"& {{ {normalized} }}; "
        "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; "
        "if (-not $?) { exit 1 }; "
        "exit 0"
    )


def _leading_token(command: str) -> str:
    if not command:
        return ""
    if command[0] in {'"', "'"}:
        quote = command[0]
        closing_index = command.find(quote, 1)
        if closing_index != -1:
            return command[: closing_index + 1]
    return command.split(None, 1)[0]


def _looks_like_executable_path(candidate: str) -> bool:
    normalized = candidate.strip()
    if not normalized:
        return False
    if normalized.startswith((".", "\\", "/", "~")):
        return True
    if len(normalized) >= 2 and normalized[1] == ":":
        return True
    if "\\" in normalized or "/" in normalized:
        return True
    if Path(normalized).suffix.lower() in {
        ".bat",
        ".cmd",
        ".com",
        ".exe",
        ".ps1",
        ".py",
        ".sh",
    }:
        return True
    return False


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
