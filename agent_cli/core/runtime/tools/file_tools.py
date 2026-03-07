"""
File tools for reading, writing, editing, and listing workspace files.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any, Optional, Type

from pydantic import BaseModel, Field

from agent_cli.core.infra.events.errors import ToolExecutionError
from agent_cli.core.runtime.tools.base import BaseTool, ToolCategory
from agent_cli.core.ux.interaction.base import BaseWorkspaceManager

_LIST_DIRECTORY_DEFAULT_DEPTH = 2
_DIFF_CONTEXT_LINES = 2
_DIFF_MAX_LINES = 60
_READ_FILE_MAX_BYTES = 1_048_576


def _is_probably_binary(data: bytes) -> bool:
    """Heuristic check for binary content."""
    if not data:
        return False
    sample = data[:4096]
    if b"\x00" in sample:
        return True
    text_bytes = sum(
        byte in (9, 10, 13) or 32 <= byte <= 126 or byte >= 128 for byte in sample
    )
    ratio = text_bytes / max(len(sample), 1)
    return ratio < 0.80


def _decode_text_bytes(data: bytes) -> tuple[str, bool]:
    """Decode text bytes with UTF-8 fallback replacement."""
    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), True


def _format_with_line_numbers(
    lines: list[str],
    *,
    start_line_number: int = 1,
) -> str:
    """Prefix text lines with right-aligned 1-indexed line numbers."""
    if not lines:
        return ""

    last_line_number = start_line_number + len(lines) - 1
    width = max(3, len(str(last_line_number)))
    return "\n".join(
        f"{line_no:>{width}}: {line}"
        for line_no, line in enumerate(lines, start=start_line_number)
    )


class ReadFileArgs(BaseModel):
    """Arguments for the ``read_file`` tool."""

    path: str = Field(
        description="Path to the file to read (relative to workspace root)."
    )
    start_line: Optional[int] = Field(
        default=None,
        description="Starting line number (1-indexed, inclusive).",
        json_schema_extra={"type": "integer"},
    )
    end_line: Optional[int] = Field(
        default=None,
        description="Ending line number (1-indexed, inclusive).",
        json_schema_extra={"type": "integer"},
    )


class ReadFileTool(BaseTool):
    """Read the contents of a file with optional line-range slicing."""

    name = "read_file"
    description = (
        "Read the contents of a file. Supports optional line range "
        "slicing with start_line and end_line (1-indexed, inclusive)."
    )
    is_safe = True
    category = ToolCategory.FILE

    def __init__(
        self,
        workspace: BaseWorkspaceManager,
        *,
        show_line_numbers: bool = True,
        max_bytes: int = _READ_FILE_MAX_BYTES,
    ) -> None:
        self.workspace = workspace
        self._show_line_numbers = bool(show_line_numbers)
        self._max_bytes = max(int(max_bytes), 1)

    @property
    def args_schema(self) -> Type[BaseModel]:
        return ReadFileArgs

    async def execute(self, **kwargs: Any) -> str:
        path = str(kwargs.get("path", ""))
        start_line = kwargs.get("start_line")
        end_line = kwargs.get("end_line")

        resolved = self.workspace.resolve_path(path, must_exist=True)

        if resolved.is_dir():
            raise ToolExecutionError(
                f"'{path}' is a directory, not a file. Use list_directory instead.",
                tool_name=self.name,
            )

        raw = resolved.read_bytes()
        file_size_bytes = len(raw)
        if _is_probably_binary(raw):
            raise ToolExecutionError(
                f"Cannot read '{path}': file appears to be binary or unsupported text.",
                tool_name=self.name,
            )

        was_truncated = file_size_bytes > self._max_bytes
        clipped = raw[: self._max_bytes]
        content, had_decode_replacement = _decode_text_bytes(clipped)
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        all_lines = content.splitlines()
        total_lines = len(all_lines)
        display_lines = all_lines
        showing_clause = ""
        line_offset = 1

        if start_line is not None or end_line is not None:
            start = max((start_line or 1) - 1, 0)
            end = min(end_line or total_lines, total_lines)
            display_lines = all_lines[start:end]
            showing_clause = f" | Showing: {start + 1}-{end} of {total_lines}"
            line_offset = start + 1

        if self._show_line_numbers:
            display_content = _format_with_line_numbers(
                display_lines,
                start_line_number=line_offset,
            )
        else:
            display_content = "\n".join(display_lines)

        notices: list[str] = []
        if was_truncated:
            notices.append(
                f"File is large ({file_size_bytes:,} bytes). Showing first {self._max_bytes:,} bytes."
            )
        if had_decode_replacement:
            notices.append("Non-UTF-8 bytes were decoded with replacement characters.")

        encoding = "utf-8 (replacement)" if had_decode_replacement else "utf-8"
        header = (
            f"File: {path} | Lines: {total_lines} | "
            f"Size: {file_size_bytes} bytes | Encoding: {encoding}{showing_clause}"
        )
        parts = [header]
        parts.extend(notices)
        parts.append(display_content)
        return "\n".join(part for part in parts if part != "")


class WriteFileArgs(BaseModel):
    """Arguments for the ``write_file`` tool."""

    path: str = Field(
        description="Path to write the file (relative to workspace root)."
    )
    content: str = Field(description="The full content to write to the file.")
    create_dirs: bool = Field(
        default=True,
        description="If True, create parent directories as needed.",
    )


class WriteFileTool(BaseTool):
    """Create or overwrite a file with the given content."""

    name = "write_file"
    description = (
        "Create or overwrite a file with the given content. "
        "If the file already exists, it will be completely overwritten. "
        "Parent directories are created automatically."
    )
    is_safe = False
    parallel_safe = False
    category = ToolCategory.FILE

    def __init__(self, workspace: BaseWorkspaceManager) -> None:
        self.workspace = workspace

    @property
    def args_schema(self) -> Type[BaseModel]:
        return WriteFileArgs

    async def execute(self, **kwargs: Any) -> str:
        path = str(kwargs.get("path", ""))
        content = str(kwargs.get("content", ""))
        create_dirs = bool(kwargs.get("create_dirs", True))

        resolved = self.workspace.resolve_path(path, writable=True)

        if create_dirs:
            resolved.parent.mkdir(parents=True, exist_ok=True)

        resolved.write_text(content, encoding="utf-8")

        lines = content.count("\n") + 1
        size = len(content.encode("utf-8"))
        return f"Successfully wrote {lines} lines ({size:,} bytes) to {path}"


def _line_numbers_for_exact_match(content: str, needle: str) -> list[int]:
    """Return 1-indexed start line numbers where ``needle`` appears exactly."""
    if not needle:
        return []

    starts: list[int] = []
    idx = 0
    while True:
        pos = content.find(needle, idx)
        if pos == -1:
            break
        starts.append(content.count("\n", 0, pos) + 1)
        idx = pos + 1
    return starts


def _line_number_for_case_insensitive_line_hint(
    content: str,
    old_str: str,
) -> int | None:
    """Return first line number where old_str's first line appears."""
    first_line = old_str.splitlines()[0].strip() if old_str else ""
    if not first_line:
        return None

    target = first_line.lower()
    for i, line in enumerate(content.splitlines(), start=1):
        if target in line.lower():
            return i
    return None


def _compact_unified_diff(
    before: str,
    after: str,
    *,
    fromfile: str,
    tofile: str,
    context_lines: int = 2,
    max_lines: int = 60,
) -> str:
    """Build unified diff and cap output length."""
    diff_lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
            n=context_lines,
        )
    )
    if len(diff_lines) <= max_lines:
        return "\n".join(diff_lines)

    head = diff_lines[:max_lines]
    remaining = len(diff_lines) - max_lines
    head.append(f"... [diff truncated: {remaining} more line(s)]")
    return "\n".join(head)


class StrReplaceArgs(BaseModel):
    """Arguments for ``str_replace``."""

    path: str = Field(description="File path relative to workspace root.")
    old_str: str = Field(
        description="Exact string to replace (must match exactly once)."
    )
    new_str: str = Field(default="", description="Replacement string.")


class StrReplaceTool(BaseTool):
    """Replace exactly one occurrence of a string in a file and return a diff."""

    name = "str_replace"
    description = (
        "Replace exactly one occurrence of old_str with new_str in a text file. "
        "Fails if zero or multiple matches are found. Returns a unified diff."
    )
    is_safe = False
    parallel_safe = False
    category = ToolCategory.FILE

    def __init__(
        self,
        workspace: BaseWorkspaceManager,
        *,
        diff_context_lines: int = _DIFF_CONTEXT_LINES,
        diff_max_lines: int = _DIFF_MAX_LINES,
    ) -> None:
        self.workspace = workspace
        self._diff_context_lines = max(int(diff_context_lines), 0)
        self._diff_max_lines = max(int(diff_max_lines), 1)

    @property
    def args_schema(self) -> Type[BaseModel]:
        return StrReplaceArgs

    async def execute(self, **kwargs: Any) -> str:
        path = str(kwargs.get("path", ""))
        old_str = str(kwargs.get("old_str", ""))
        new_str = str(kwargs.get("new_str", ""))

        if not path:
            raise ToolExecutionError("path is required.", tool_name=self.name)
        if old_str == "":
            raise ToolExecutionError("old_str must not be empty.", tool_name=self.name)

        resolved = self.workspace.resolve_path(path, must_exist=True, writable=True)

        if resolved.is_dir():
            raise ToolExecutionError(
                f"'{path}' is a directory, not a file.",
                tool_name=self.name,
            )

        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ToolExecutionError(
                f"Cannot edit '{path}': file appears to be binary.",
                tool_name=self.name,
            )

        matches = _line_numbers_for_exact_match(content, old_str)
        count = len(matches)

        if count == 0:
            hint_line = _line_number_for_case_insensitive_line_hint(content, old_str)
            hint = (
                f" Near match hint: first line of old_str appears around line {hint_line} (case-insensitive)."
                if hint_line is not None
                else " No near-match hint found."
            )
            raise ToolExecutionError(
                f"str_replace found 0 matches in '{path}'.{hint}",
                tool_name=self.name,
            )

        if count > 1:
            lines_preview = ", ".join(str(n) for n in matches[:20])
            more = f" (+{count - 20} more)" if count > 20 else ""
            raise ToolExecutionError(
                "str_replace found multiple matches "
                f"({count}) in '{path}' at line(s): {lines_preview}{more}. "
                "Add more unique context to old_str so only one location matches.",
                tool_name=self.name,
            )

        updated = content.replace(old_str, new_str, 1)
        resolved.write_text(updated, encoding="utf-8")

        diff = _compact_unified_diff(
            content,
            updated,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            context_lines=self._diff_context_lines,
            max_lines=self._diff_max_lines,
        )

        if not diff.strip():
            return f"Applied replacement in {path}, but no textual diff was produced."
        return diff


class InsertLinesArgs(BaseModel):
    """Arguments for ``insert_lines``."""

    path: str = Field(description="File path relative to workspace root.")
    insert_after_line: int = Field(
        description="Insert content after this line number. Use 0 to insert at file start."
    )
    content: str = Field(description="Text content to insert.")


class InsertLinesTool(BaseTool):
    """Insert content after a specified line and return insertion summary."""

    name = "insert_lines"
    description = (
        "Insert content into a file after the specified line number "
        "(use 0 to insert at the top)."
    )
    is_safe = False
    parallel_safe = False
    category = ToolCategory.FILE

    def __init__(self, workspace: BaseWorkspaceManager) -> None:
        self.workspace = workspace

    @property
    def args_schema(self) -> Type[BaseModel]:
        return InsertLinesArgs

    async def execute(self, **kwargs: Any) -> str:
        path = str(kwargs.get("path", ""))
        insert_after_line = int(kwargs.get("insert_after_line", 0))
        insertion_content = str(kwargs.get("content", ""))

        if not path:
            raise ToolExecutionError("path is required.", tool_name=self.name)

        resolved = self.workspace.resolve_path(path, must_exist=True, writable=True)

        if resolved.is_dir():
            raise ToolExecutionError(
                f"'{path}' is a directory, not a file.",
                tool_name=self.name,
            )

        try:
            original = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ToolExecutionError(
                f"Cannot edit '{path}': file appears to be binary.",
                tool_name=self.name,
            )

        lines = original.splitlines(keepends=True)
        total_lines = len(lines)

        if insert_after_line < 0 or insert_after_line > total_lines:
            raise ToolExecutionError(
                f"insert_after_line out of range for '{path}': got {insert_after_line}, "
                f"expected 0..{total_lines}.",
                tool_name=self.name,
            )

        if insertion_content and not insertion_content.endswith("\n"):
            insertion_content += "\n"

        insertion_lines = insertion_content.splitlines(keepends=True)
        new_lines = (
            lines[:insert_after_line] + insertion_lines + lines[insert_after_line:]
        )
        updated = "".join(new_lines)
        resolved.write_text(updated, encoding="utf-8")

        inserted_count = len(insertion_lines)
        new_total = len(updated.splitlines())
        return (
            f"Inserted {inserted_count} line(s) into {path} after line {insert_after_line}. "
            f"New total: {new_total} line(s)."
        )


class ListDirectoryArgs(BaseModel):
    """Arguments for the ``list_directory`` tool."""

    path: str = Field(
        default=".",
        description="Directory path to list (relative to workspace root).",
    )
    max_depth: int = Field(
        default=_LIST_DIRECTORY_DEFAULT_DEPTH,
        description="Maximum depth to recurse (1 = immediate children only).",
    )


class ListDirectoryTool(BaseTool):
    """List files and subdirectories within a directory."""

    name = "list_directory"
    description = (
        "List files and subdirectories within a directory. "
        "Returns a tree-like structure with file sizes."
    )
    is_safe = True
    category = ToolCategory.FILE

    def __init__(
        self,
        workspace: BaseWorkspaceManager,
        *,
        default_max_depth: int = _LIST_DIRECTORY_DEFAULT_DEPTH,
    ) -> None:
        self.workspace = workspace
        self._default_max_depth = max(int(default_max_depth), 0)

    @property
    def args_schema(self) -> Type[BaseModel]:
        return ListDirectoryArgs

    async def execute(self, **kwargs: Any) -> str:
        path = str(kwargs.get("path", "."))
        max_depth = int(kwargs.get("max_depth", self._default_max_depth))

        resolved = self.workspace.resolve_path(path, must_exist=True)

        if not resolved.is_dir():
            raise ToolExecutionError(
                f"'{path}' is not a directory.",
                tool_name=self.name,
            )

        lines: list[str] = []
        self._walk(resolved, resolved, max_depth, 0, lines)

        if not lines:
            return f"Directory '{path}' is empty."

        return "\n".join(lines)

    def _walk(
        self,
        base: Path,
        current: Path,
        max_depth: int,
        depth: int,
        lines: list[str],
    ) -> None:
        """Recursively walk the directory tree."""
        if depth >= max_depth:
            return

        try:
            entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        except PermissionError:
            lines.append(f"{'  ' * depth}[permission denied]")
            return

        for entry in entries:
            if not self.workspace.is_allowed(entry):
                continue

            indent = "  " * depth
            rel = entry.relative_to(base)

            if entry.is_dir():
                lines.append(f"{indent}{rel}/")
                self._walk(base, entry, max_depth, depth + 1, lines)
            else:
                size = entry.stat().st_size
                lines.append(f"{indent}{rel}  ({self._format_size(size)})")

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Format file size in human-readable form."""
        size = float(size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"
