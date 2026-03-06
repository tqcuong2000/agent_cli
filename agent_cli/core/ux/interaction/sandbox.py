"""Workspace sandbox manager with git-first and lazy-copy strategies."""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Optional

from agent_cli.core.infra.events.errors import ToolExecutionError
from agent_cli.core.ux.interaction.base import BaseWorkspaceManager

SandboxMode = Literal["off", "git", "lazy"]


@dataclass
class SandboxStatus:
    active: bool
    mode: SandboxMode
    message: str = ""
    changes: List[str] = field(default_factory=list)


@dataclass
class _LazySnapshot:
    existed: bool
    content: bytes | None


class SandboxWorkspaceManager(BaseWorkspaceManager):
    """Wrap a workspace manager to provide reversible sandbox sessions."""

    def __init__(self, base_manager: BaseWorkspaceManager) -> None:
        self._base = base_manager
        self._active = False
        self._mode: SandboxMode = "off"

        # Git strategy state
        self._git_base_ref: Optional[str] = None
        self._git_branch: Optional[str] = None

        # Lazy strategy state
        self._snapshots: Dict[Path, _LazySnapshot] = {}

    def resolve_path(
        self,
        path: str,
        *,
        must_exist: bool = False,
        writable: bool = False,
    ) -> Path:
        resolved = self._base.resolve_path(
            path, must_exist=must_exist, writable=writable
        )
        if self._active and self._mode == "lazy" and writable:
            self._capture_lazy_snapshot(resolved)
        return resolved

    def is_allowed(self, path: str | Path) -> bool:
        return self._base.is_allowed(path)

    def get_root(self) -> Path:
        return self._base.get_root()

    def status(self) -> SandboxStatus:
        return SandboxStatus(
            active=self._active,
            mode=self._mode,
            message="Sandbox active." if self._active else "Sandbox disabled.",
            changes=self.list_changes(),
        )

    def enable(self) -> SandboxStatus:
        if self._active:
            return self.status()

        if self._is_git_repo() and self._git_worktree_clean():
            self._enable_git()
            return SandboxStatus(
                active=True,
                mode="git",
                message=f"Sandbox enabled with git branch '{self._git_branch}'.",
                changes=self.list_changes(),
            )

        self._enable_lazy()
        reason = "git unavailable or worktree not clean"
        return SandboxStatus(
            active=True,
            mode="lazy",
            message=f"Sandbox enabled with lazy-copy fallback ({reason}).",
            changes=[],
        )

    def disable(self, action: str) -> SandboxStatus:
        action_norm = action.strip().lower()
        if action_norm not in ("apply", "discard"):
            raise ToolExecutionError(
                "Invalid sandbox action. Use 'apply' or 'discard'.",
                tool_name="sandbox",
            )

        if not self._active:
            return SandboxStatus(
                active=False,
                mode="off",
                message="Sandbox is not active.",
                changes=[],
            )

        if self._mode == "git":
            self._disable_git(action_norm)
        elif self._mode == "lazy":
            self._disable_lazy(action_norm)

        return SandboxStatus(
            active=False,
            mode="off",
            message=f"Sandbox disabled with action: {action_norm}.",
            changes=[],
        )

    def list_changes(self) -> List[str]:
        if not self._active:
            return []
        if self._mode == "git":
            return self._list_git_changes()
        if self._mode == "lazy":
            return self._list_lazy_changes()
        return []

    # ── Git strategy ─────────────────────────────────────────────

    def _is_git_repo(self) -> bool:
        result = self._run_git(["rev-parse", "--is-inside-work-tree"], check=False)
        return result.returncode == 0 and result.stdout.strip().lower() == "true"

    def _git_worktree_clean(self) -> bool:
        result = self._run_git(["status", "--porcelain"], check=False)
        return result.returncode == 0 and result.stdout.strip() == ""

    def _enable_git(self) -> None:
        base_ref = self._git_current_ref()
        sandbox_branch = f"agent-cli-sandbox-{int(time.time())}"
        self._run_git(["checkout", "-b", sandbox_branch], check=True)

        self._git_base_ref = base_ref
        self._git_branch = sandbox_branch
        self._active = True
        self._mode = "git"

    def _disable_git(self, action: str) -> None:
        assert self._git_base_ref is not None
        assert self._git_branch is not None

        if action == "apply":
            self._run_git(["checkout", self._git_base_ref], check=True)
            self._run_git(["merge", "--ff-only", self._git_branch], check=False)
            self._run_git(["branch", "-D", self._git_branch], check=False)
        else:  # discard
            self._run_git(["checkout", self._git_branch], check=True)
            self._run_git(["reset", "--hard"], check=True)
            self._run_git(["clean", "-fd"], check=True)
            self._run_git(["checkout", self._git_base_ref], check=True)
            self._run_git(["branch", "-D", self._git_branch], check=False)

        self._git_base_ref = None
        self._git_branch = None
        self._active = False
        self._mode = "off"

    def _list_git_changes(self) -> List[str]:
        result = self._run_git(["status", "--short"], check=False)
        if result.returncode != 0:
            return []
        return [line.rstrip() for line in result.stdout.splitlines() if line.strip()]

    def _git_current_ref(self) -> str:
        branch = self._run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"], check=True
        ).stdout.strip()
        if branch and branch != "HEAD":
            return branch
        return self._run_git(["rev-parse", "HEAD"], check=True).stdout.strip()

    def _run_git(
        self, args: List[str], *, check: bool
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.get_root(),
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ToolExecutionError(
                "Git is not installed or not available in PATH.",
                tool_name="sandbox",
            ) from exc

        if check and result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            detail = stderr or stdout or "Unknown git error."
            raise ToolExecutionError(
                f"Git command failed: {' '.join(args)}\n{detail}",
                tool_name="sandbox",
            )
        return result

    # ── Lazy-copy strategy ───────────────────────────────────────

    def _enable_lazy(self) -> None:
        self._snapshots = {}
        self._active = True
        self._mode = "lazy"

    def _disable_lazy(self, action: str) -> None:
        if action == "discard":
            self._restore_lazy_snapshots()

        self._snapshots = {}
        self._active = False
        self._mode = "off"

    def _capture_lazy_snapshot(self, path: Path) -> None:
        if path in self._snapshots:
            return

        if path.exists() and path.is_file():
            self._snapshots[path] = _LazySnapshot(
                existed=True, content=path.read_bytes()
            )
            return
        if path.exists():
            self._snapshots[path] = _LazySnapshot(existed=True, content=None)
            return
        self._snapshots[path] = _LazySnapshot(existed=False, content=None)

    def _restore_lazy_snapshots(self) -> None:
        for path, snapshot in sorted(
            self._snapshots.items(), key=lambda item: len(item[0].parts), reverse=True
        ):
            if snapshot.existed:
                if snapshot.content is None:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(snapshot.content)
                continue

            # Did not exist before sandbox: delete created path.
            if not path.exists():
                continue
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)

    def _list_lazy_changes(self) -> List[str]:
        root = self.get_root()
        changes: List[str] = []

        for path, snapshot in sorted(self._snapshots.items()):
            rel = path.relative_to(root).as_posix()

            if not snapshot.existed:
                if path.exists():
                    changes.append(f"A {rel}")
                continue

            if not path.exists():
                changes.append(f"D {rel}")
                continue

            if snapshot.content is None:
                continue

            try:
                current = path.read_bytes()
            except OSError:
                continue
            if current != snapshot.content:
                changes.append(f"M {rel}")

        return changes
