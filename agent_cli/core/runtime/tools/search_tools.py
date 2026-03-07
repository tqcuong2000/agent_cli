"""
Search tools for workspace file discovery and content search.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Type

from pydantic import BaseModel, Field

from agent_cli.core.infra.events.errors import ToolExecutionError
from agent_cli.core.runtime.tools.base import BaseTool, ToolCategory
from agent_cli.core.runtime.tools.file_tools import (
    _decode_text_bytes,
    _is_probably_binary,
)
from agent_cli.core.ux.interaction.base import BaseWorkspaceManager

_FIND_BY_NAME_DEFAULT_MAX_RESULTS = 50
_FIND_BY_NAME_DEFAULT_MAX_DEPTH = 10
_GREP_SEARCH_DEFAULT_MAX_RESULTS = 50
_GREP_SEARCH_MAX_FILE_BYTES = 524_288


def _to_workspace_relative(path: Path, workspace_root: Path) -> str:
    """Return a stable workspace-relative path string."""
    try:
        rel = path.relative_to(workspace_root)
        text = rel.as_posix()
        return text if text else "."
    except ValueError:
        return path.as_posix()


def _matches_glob(value: str, patterns: list[str]) -> bool:
    """Match a path string against glob patterns using path and basename."""
    normalized = value.replace("\\", "/")
    basename = Path(normalized).name
    return any(
        fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(basename, pattern)
        for pattern in patterns
    )


def _normalize_globs(values: list[str] | None) -> list[str]:
    """Normalize glob patterns to POSIX-like strings."""
    if not values:
        return []
    return [
        str(value).replace("\\", "/").strip()
        for value in values
        if str(value).strip()
    ]


def _normalize_extensions(values: list[str] | None) -> set[str]:
    """Normalize extension filters without leading dots."""
    if not values:
        return set()
    return {
        str(value).strip().lower().lstrip(".")
        for value in values
        if str(value).strip().lstrip(".")
    }


def _format_size(size_bytes: int) -> str:
    """Format file size in human-readable form."""
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _format_mtime(path: Path) -> str:
    """Format mtime using local wall-clock time."""
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


class FindByNameArgs(BaseModel):
    """Arguments for the ``find_by_name`` tool."""

    pattern: str = Field(
        description="Glob pattern to search for (for example '*.py' or 'test_*')."
    )
    path: str = Field(
        default=".",
        description="Directory to search in (relative to workspace root).",
    )
    type: Literal["file", "directory", "any"] = Field(
        default="any",
        description="Filter by result type: 'file', 'directory', or 'any'.",
    )
    extensions: list[str] | None = Field(
        default=None,
        description="Optional file extensions to include, without leading dots.",
    )
    excludes: list[str] | None = Field(
        default=None,
        description="Optional glob patterns to exclude from results.",
    )
    max_depth: int | None = Field(
        default=None,
        description="Maximum depth to search relative to the starting directory.",
        ge=0,
    )
    max_results: int = Field(
        default=_FIND_BY_NAME_DEFAULT_MAX_RESULTS,
        description="Maximum number of results to return.",
        ge=1,
    )


class FindByNameTool(BaseTool):
    """Search for files and directories by glob pattern."""

    name = "find_by_name"
    description = (
        "Find files or directories by glob pattern. Supports filtering by "
        "type, extension, excludes, and max depth."
    )
    is_safe = True
    category = ToolCategory.SEARCH

    def __init__(
        self,
        workspace: BaseWorkspaceManager,
        *,
        max_results: int = _FIND_BY_NAME_DEFAULT_MAX_RESULTS,
        default_max_depth: int = _FIND_BY_NAME_DEFAULT_MAX_DEPTH,
    ) -> None:
        self.workspace = workspace
        self._default_max_results = max(int(max_results), 1)
        self._default_max_depth = max(int(default_max_depth), 0)

    @property
    def args_schema(self) -> Type[BaseModel]:
        return FindByNameArgs

    async def execute(self, **kwargs: Any) -> str:
        pattern = str(kwargs.get("pattern", "")).strip()
        path = str(kwargs.get("path", "."))
        entry_type = str(kwargs.get("type", "any")).strip().lower() or "any"
        extensions = _normalize_extensions(kwargs.get("extensions"))
        excludes = _normalize_globs(kwargs.get("excludes"))
        max_depth_raw = kwargs.get("max_depth")
        max_depth = (
            self._default_max_depth if max_depth_raw is None else max(int(max_depth_raw), 0)
        )
        max_results = max(int(kwargs.get("max_results", self._default_max_results)), 1)

        if not pattern:
            raise ToolExecutionError("pattern is required.", tool_name=self.name)

        resolved = self.workspace.resolve_path(path, must_exist=True)
        if not resolved.is_dir():
            raise ToolExecutionError(
                f"'{path}' is not a directory.",
                tool_name=self.name,
            )

        workspace_root = self.workspace.get_root()
        results: list[str] = []
        truncated = False

        for current_root, dir_names, file_names in os.walk(resolved):
            current_path = Path(current_root)
            if not self.workspace.is_allowed(current_path):
                dir_names[:] = []
                continue

            kept_dirs: list[str] = []
            for dir_name in sorted(dir_names):
                directory = current_path / dir_name
                rel = directory.relative_to(workspace_root).as_posix()
                entry_depth = len(directory.relative_to(resolved).parts)

                if not self.workspace.is_allowed(directory):
                    continue
                if excludes and _matches_glob(rel, excludes):
                    continue

                if entry_depth <= max_depth:
                    if entry_type in {"directory", "any"} and (
                        fnmatch.fnmatch(directory.name, pattern)
                        or fnmatch.fnmatch(rel, pattern)
                    ):
                        results.append(f"[dir]  {rel}/  ({_format_mtime(directory)})")
                        if len(results) >= max_results:
                            truncated = True
                            dir_names[:] = []
                            break

                if entry_depth < max_depth:
                    kept_dirs.append(dir_name)

            if truncated:
                break

            dir_names[:] = kept_dirs

            for file_name in sorted(file_names):
                file_path = current_path / file_name
                rel = file_path.relative_to(workspace_root).as_posix()
                entry_depth = len(file_path.relative_to(resolved).parts)

                if entry_depth > max_depth:
                    continue
                if not self.workspace.is_allowed(file_path):
                    continue
                if excludes and _matches_glob(rel, excludes):
                    continue
                if extensions and file_path.suffix.lower().lstrip(".") not in extensions:
                    continue
                if entry_type not in {"file", "any"}:
                    continue
                if not (
                    fnmatch.fnmatch(file_path.name, pattern)
                    or fnmatch.fnmatch(rel, pattern)
                ):
                    continue

                size = file_path.stat().st_size
                results.append(
                    f"[file] {rel}  ({_format_size(size)}, {_format_mtime(file_path)})"
                )
                if len(results) >= max_results:
                    truncated = True
                    break

            if truncated:
                break

        if not results:
            return f"No matches found for '{pattern}' in '{path}'."

        header = f"Found {len(results)} matches in '{path}':"
        if truncated:
            results.append(f"[Stopped at {max_results} results. Narrow your pattern.]")
        return "\n".join([header, *results])


class GrepSearchArgs(BaseModel):
    """Arguments for the ``grep_search`` tool."""

    query: str = Field(description="Text or regex pattern to search for.")
    search_path: str = Field(
        default=".",
        description="Directory or file to search in (relative to workspace root).",
    )
    is_regex: bool = Field(
        default=False,
        description="If true, treat query as a regex. Otherwise search literally.",
    )
    case_insensitive: bool = Field(
        default=True,
        description="If true, match case-insensitively.",
    )
    includes: list[str] | None = Field(
        default=None,
        description="Optional glob patterns to include (for example ['*.py']).",
    )
    match_per_line: bool = Field(
        default=True,
        description="If true, return matching lines. If false, return matching files only.",
    )
    max_results: int = Field(
        default=_GREP_SEARCH_DEFAULT_MAX_RESULTS,
        description="Maximum number of matches or files to return.",
        ge=1,
    )


class GrepSearchTool(BaseTool):
    """Search file contents using ripgrep with a Python fallback."""

    name = "grep_search"
    description = (
        "Search file contents using literal or regex matching. Returns "
        "matching lines with line numbers, or matching filenames only."
    )
    is_safe = True
    category = ToolCategory.SEARCH

    def __init__(
        self,
        workspace: BaseWorkspaceManager,
        *,
        max_results: int = _GREP_SEARCH_DEFAULT_MAX_RESULTS,
        max_file_size_bytes: int = _GREP_SEARCH_MAX_FILE_BYTES,
    ) -> None:
        self.workspace = workspace
        self._default_max_results = max(int(max_results), 1)
        self._max_file_size_bytes = max(int(max_file_size_bytes), 1)
        self._rg_executable = shutil.which("rg")

    @property
    def args_schema(self) -> Type[BaseModel]:
        return GrepSearchArgs

    async def execute(self, **kwargs: Any) -> str:
        query = str(kwargs.get("query", "")).strip()
        search_path = str(kwargs.get("search_path", "."))
        is_regex = bool(kwargs.get("is_regex", False))
        case_insensitive = bool(kwargs.get("case_insensitive", True))
        includes = _normalize_globs(kwargs.get("includes"))
        match_per_line = bool(kwargs.get("match_per_line", True))
        max_results = max(int(kwargs.get("max_results", self._default_max_results)), 1)

        if not query:
            raise ToolExecutionError("query is required.", tool_name=self.name)

        resolved = self.workspace.resolve_path(search_path, must_exist=True)

        try:
            if self._rg_executable:
                if match_per_line:
                    results, truncated = await self._run_ripgrep_line_mode(
                        query=query,
                        resolved=resolved,
                        is_regex=is_regex,
                        case_insensitive=case_insensitive,
                        includes=includes,
                        max_results=max_results,
                    )
                else:
                    results, truncated = await self._run_ripgrep_file_mode(
                        query=query,
                        resolved=resolved,
                        is_regex=is_regex,
                        case_insensitive=case_insensitive,
                        includes=includes,
                        max_results=max_results,
                    )
            else:
                results, truncated = self._run_python_search(
                    query=query,
                    resolved=resolved,
                    is_regex=is_regex,
                    case_insensitive=case_insensitive,
                    includes=includes,
                    match_per_line=match_per_line,
                    max_results=max_results,
                )
        except (FileNotFoundError, PermissionError, OSError):
            results, truncated = self._run_python_search(
                query=query,
                resolved=resolved,
                is_regex=is_regex,
                case_insensitive=case_insensitive,
                includes=includes,
                match_per_line=match_per_line,
                max_results=max_results,
            )

        if not results:
            return f"No matches found for '{query}' in '{search_path}'."

        if match_per_line:
            header = f"Found {len(results)} matches:"
        else:
            header = f"Found {len(results)} files containing '{query}':"
        if truncated:
            results.append(f"[Stopped at {max_results} results. Narrow your search.]")
        return "\n".join([header, *results])

    async def _run_ripgrep_line_mode(
        self,
        *,
        query: str,
        resolved: Path,
        is_regex: bool,
        case_insensitive: bool,
        includes: list[str],
        max_results: int,
    ) -> tuple[list[str], bool]:
        argv = [
            str(self._rg_executable),
            "--json",
            "--line-number",
            "--color",
            "never",
        ]
        if not is_regex:
            argv.append("--fixed-strings")
        if case_insensitive:
            argv.append("--ignore-case")
        for pattern in includes:
            argv.extend(["-g", pattern])
        argv.extend([query, self._target_arg(resolved)])

        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self.workspace.get_root()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        assert proc.stdout is not None
        assert proc.stderr is not None

        results: list[str] = []
        truncated = False

        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            payload = json.loads(line.decode("utf-8", errors="replace"))
            if payload.get("type") != "match":
                continue

            data = payload.get("data", {})
            path_text = str(data.get("path", {}).get("text", "")).replace("\\", "/")
            line_number = int(data.get("line_number", 0))
            line_text = str(data.get("lines", {}).get("text", "")).rstrip("\r\n")
            results.append(f"{path_text}:{line_number}: {line_text}")

            if len(results) >= max_results:
                truncated = True
                proc.kill()
                break

        stderr = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
        returncode = await proc.wait()
        if not truncated and returncode not in (0, 1):
            raise ToolExecutionError(
                f"ripgrep failed: {stderr or f'exit code {returncode}'}",
                tool_name=self.name,
            )
        return results, truncated

    async def _run_ripgrep_file_mode(
        self,
        *,
        query: str,
        resolved: Path,
        is_regex: bool,
        case_insensitive: bool,
        includes: list[str],
        max_results: int,
    ) -> tuple[list[str], bool]:
        argv = [str(self._rg_executable), "-l", "--color", "never"]
        if not is_regex:
            argv.append("--fixed-strings")
        if case_insensitive:
            argv.append("--ignore-case")
        for pattern in includes:
            argv.extend(["-g", pattern])
        argv.extend([query, self._target_arg(resolved)])

        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self.workspace.get_root()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        assert proc.stdout is not None
        assert proc.stderr is not None

        results: list[str] = []
        truncated = False

        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            path_text = line.decode("utf-8", errors="replace").strip().replace("\\", "/")
            if not path_text:
                continue
            results.append(path_text)
            if len(results) >= max_results:
                truncated = True
                proc.kill()
                break

        stderr = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
        returncode = await proc.wait()
        if not truncated and returncode not in (0, 1):
            raise ToolExecutionError(
                f"ripgrep failed: {stderr or f'exit code {returncode}'}",
                tool_name=self.name,
            )
        return results, truncated

    def _run_python_search(
        self,
        *,
        query: str,
        resolved: Path,
        is_regex: bool,
        case_insensitive: bool,
        includes: list[str],
        match_per_line: bool,
        max_results: int,
    ) -> tuple[list[str], bool]:
        pattern: re.Pattern[str] | None = None
        if is_regex:
            flags = re.IGNORECASE if case_insensitive else 0
            try:
                pattern = re.compile(query, flags)
            except re.error as exc:
                raise ToolExecutionError(
                    f"Invalid regex for grep_search: {exc}",
                    tool_name=self.name,
                ) from exc

        workspace_root = self.workspace.get_root()
        results: list[str] = []
        truncated = False

        for file_path in self._iter_candidate_files(resolved, includes):
            try:
                size_bytes = file_path.stat().st_size
            except OSError:
                continue

            if size_bytes > self._max_file_size_bytes:
                continue

            try:
                raw = file_path.read_bytes()
            except OSError:
                continue

            if _is_probably_binary(raw):
                continue

            text, _ = _decode_text_bytes(raw)
            rel = _to_workspace_relative(file_path, workspace_root)

            if match_per_line:
                for line_number, line in enumerate(text.splitlines(), start=1):
                    if self._line_matches(
                        line=line,
                        query=query,
                        pattern=pattern,
                        case_insensitive=case_insensitive,
                    ):
                        results.append(f"{rel}:{line_number}: {line}")
                        if len(results) >= max_results:
                            truncated = True
                            break
                if truncated:
                    break
            else:
                if any(
                    self._line_matches(
                        line=line,
                        query=query,
                        pattern=pattern,
                        case_insensitive=case_insensitive,
                    )
                    for line in text.splitlines()
                ):
                    results.append(rel)
                    if len(results) >= max_results:
                        truncated = True
                        break

        return results, truncated

    def _iter_candidate_files(
        self,
        resolved: Path,
        includes: list[str],
    ) -> list[Path]:
        workspace_root = self.workspace.get_root()
        if resolved.is_file():
            rel = _to_workspace_relative(resolved, workspace_root)
            if includes and not _matches_glob(rel, includes):
                return []
            return [resolved] if self.workspace.is_allowed(resolved) else []

        results: list[Path] = []
        for current_root, dir_names, file_names in os.walk(resolved):
            current_path = Path(current_root)
            if not self.workspace.is_allowed(current_path):
                dir_names[:] = []
                continue

            kept_dirs: list[str] = []
            for dir_name in sorted(dir_names):
                directory = current_path / dir_name
                if self.workspace.is_allowed(directory):
                    kept_dirs.append(dir_name)
            dir_names[:] = kept_dirs

            for file_name in sorted(file_names):
                file_path = current_path / file_name
                if not self.workspace.is_allowed(file_path):
                    continue
                rel = _to_workspace_relative(file_path, workspace_root)
                if includes and not _matches_glob(rel, includes):
                    continue
                results.append(file_path)

        return results

    def _line_matches(
        self,
        *,
        line: str,
        query: str,
        pattern: re.Pattern[str] | None,
        case_insensitive: bool,
    ) -> bool:
        if pattern is not None:
            return pattern.search(line) is not None
        if case_insensitive:
            return query.lower() in line.lower()
        return query in line

    def _target_arg(self, resolved: Path) -> str:
        return _to_workspace_relative(resolved, self.workspace.get_root())
