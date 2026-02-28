"""
FileDiscoveryPopup — '@' triggered file search popup.

Shows workspace files with fuzzy filtering by path.
Triggered when the user types '@' in the input bar.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from agent_cli.ux.tui.views.common.popup_list import BasePopupListView, PopupItem


# File type icons
_ICONS: dict[str, str] = {
    ".py": "🐍",
    ".js": "📜",
    ".ts": "📘",
    ".json": "📋",
    ".toml": "⚙",
    ".yaml": "⚙",
    ".yml": "⚙",
    ".md": "📝",
    ".txt": "📄",
    ".html": "🌍",
    ".css": "🎨",
    ".sh": "🔧",
    ".sql": "🗃",
    ".rs": "🦀",
    ".go": "🐹",
}

_DEFAULT_ICON = "📄"

# Common directories to exclude from file discovery
_EXCLUDED_DIRS = {
    ".git",
    ".agent_cli",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
    ".egg-info",
}


def _format_size(size_bytes: int) -> str:
    """Format file size in a human-readable way."""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}K"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}M"


class FileDiscoveryPopup(BasePopupListView):
    """
    Popup showing workspace files with fuzzy path filtering.
    Appears when the user types '@' in the input bar.
    """

    DEFAULT_CSS = """
    FileDiscoveryPopup {
        width: 64;
        border: solid $accent-darken-2;
    }
    """

    def __init__(self, workspace_root: str | Path | None = None, **kwargs):
        kwargs.setdefault("id", "file_popup")
        super().__init__(max_visible=12, **kwargs)
        self._workspace_root = Path(workspace_root) if workspace_root else None
        self._cached_files: List[PopupItem] | None = None

    def set_workspace_root(self, root: str | Path) -> None:
        """Set or update the workspace root. Invalidates file cache."""
        self._workspace_root = Path(root)
        self._cached_files = None

    def get_trigger_char(self) -> str:
        return "@"

    def get_all_items(self) -> List[PopupItem]:
        """
        Scan the workspace for files and return as PopupItems.
        Results are cached until workspace root changes.
        """
        if self._cached_files is not None:
            return self._cached_files

        if self._workspace_root is None or not self._workspace_root.exists():
            return []

        items = []
        try:
            for file_path in self._scan_workspace(self._workspace_root):
                rel_path = file_path.relative_to(self._workspace_root)
                ext = file_path.suffix.lower()
                icon = _ICONS.get(ext, _DEFAULT_ICON)

                try:
                    size = file_path.stat().st_size
                    size_str = _format_size(size)
                except OSError:
                    size_str = ""

                items.append(
                    PopupItem(
                        label=str(rel_path),
                        description=size_str,
                        icon=icon,
                        hint=ext or "file",
                        value=str(rel_path),
                        data=file_path,
                    )
                )
        except PermissionError:
            pass

        # Sort by path
        items.sort(key=lambda i: i.label.lower())
        self._cached_files = items
        return items

    def _scan_workspace(self, root: Path, max_files: int = 500) -> List[Path]:
        """
        Walk the workspace tree collecting files.
        Excludes common non-source directories.
        Caps at max_files to avoid scanning huge repos.
        """
        files: List[Path] = []

        def _walk(directory: Path, depth: int = 0) -> None:
            if depth > 10 or len(files) >= max_files:
                return
            try:
                for entry in sorted(directory.iterdir()):
                    if len(files) >= max_files:
                        return
                    if entry.is_dir():
                        if entry.name not in _EXCLUDED_DIRS and not entry.name.startswith(
                            "."
                        ):
                            _walk(entry, depth + 1)
                    elif entry.is_file():
                        files.append(entry)
            except (PermissionError, OSError):
                pass

        _walk(root)
        return files

    def render_item(self, item: PopupItem, is_selected: bool) -> str:
        """Render a file row: icon  relative/path  size."""
        prefix = "▸ " if is_selected else "  "
        bg = "[on #1a3a5c]" if is_selected else ""
        bg_end = "[/]" if is_selected else ""

        icon = item.icon
        path = f"[bold]{item.label}[/]" if is_selected else item.label

        if item.description:
            size = f"[dim]{item.description}[/]"
            return f"{bg}{prefix}{icon} {path}  {size}{bg_end}"
        else:
            return f"{bg}{prefix}{icon} {path}{bg_end}"

    def on_item_selected(self, item: PopupItem) -> str:
        """Return the relative file path to insert into the input bar."""
        return f"@{item.value} "

    def invalidate_cache(self) -> None:
        """Force re-scan on next show (e.g., after file changes)."""
        self._cached_files = None
