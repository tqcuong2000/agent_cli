"""Dynamic runtime system-information snapshots for agent prompts."""

from __future__ import annotations

import platform
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from collections.abc import Callable

from agent_cli.core.runtime._subprocess import ShellProfile


@dataclass(frozen=True, slots=True)
class SystemInfoSnapshot:
    """Small, prompt-safe runtime facts for the current app instance."""

    operating_system: str
    architecture: str
    python_version: str
    local_date: str
    timezone: str
    workspace_root: str
    command_working_directory: str
    shell_name: str
    shell_executable: str


class SystemInfoProvider:
    """Collect prompt-safe system information for agent prompts."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        shell_profile: ShellProfile,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._shell_profile = shell_profile
        self._now_provider = now_provider or datetime.now

    def snapshot(self) -> SystemInfoSnapshot:
        """Return the current system-information snapshot."""
        now = self._now_provider().astimezone()
        return SystemInfoSnapshot(
            operating_system=_operating_system_label(),
            architecture=platform.machine().strip() or "Unknown",
            python_version=platform.python_version(),
            local_date=now.date().isoformat(),
            timezone=_timezone_label(now),
            workspace_root=str(self._workspace_root),
            command_working_directory=str(self._workspace_root),
            shell_name=self._shell_profile.display_name,
            shell_executable=self._shell_profile.executable,
        )


def _operating_system_label() -> str:
    system = platform.system().strip() or "Unknown"
    release = platform.release().strip()
    version = f"{system} {release}".strip()
    return version or system


def _timezone_label(now: datetime) -> str:
    tzinfo = now.tzinfo
    if tzinfo is None:
        return "Unknown"
    tz_name = now.tzname() or str(tzinfo) or "Unknown"
    offset = now.utcoffset()
    if offset is None:
        return tz_name
    total_seconds = int(offset.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"{tz_name} (UTC{sign}{hours:02d}:{minutes:02d})"
