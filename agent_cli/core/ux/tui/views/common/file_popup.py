"""
FileDiscoveryPopup — '@' triggered file search popup.

Shows workspace files with fuzzy filtering by path.
Triggered when the user types '@' in the input bar.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Set

from agent_cli.core.ux.tui.views.common.popup_list import BasePopupListView, PopupItem

if TYPE_CHECKING:
    from agent_cli.core.infra.registry.bootstrap import AppContext


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

    DEFAULT_CSS = ""

    def __init__(
        self,
        workspace_root: str | Path | None = None,
        app_context: Optional["AppContext"] = None,
        **kwargs,
    ):
        kwargs.setdefault("id", "file_popup")
        super().__init__(max_visible=12, **kwargs)
        self._workspace_root = Path(workspace_root) if workspace_root else None
        self._app_context = app_context
        self._cached_files: List[PopupItem] | None = None

    def set_workspace_root(self, root: str | Path) -> None:
        """Set or update the workspace root. Invalidates file cache."""
        self._workspace_root = Path(root)
        self._cached_files = None

    def set_app_context(self, app_context: "AppContext") -> None:
        """Bind the app context so popup can use file index and change tracker."""
        self._app_context = app_context
        self._cached_files = None

    def get_trigger_char(self) -> str:
        return "@"

    def get_all_items(self) -> List[PopupItem]:
        """
        Load workspace files and return as PopupItems.
        Results are cached until workspace root changes.
        """
        if self._cached_files is not None:
            return self._cached_files

        indexer = getattr(self._app_context, "file_indexer", None)
        root = self._workspace_root
        if root is None and indexer is not None:
            root_attr = getattr(indexer, "root_path", None)
            if isinstance(root_attr, Path):
                root = root_attr
                self._workspace_root = root

        if root is None or not root.exists():
            return []

        items = []
        try:
            indexed_paths = self._get_indexed_paths()
            if indexed_paths:
                rel_paths = [Path(p) for p in indexed_paths]
            else:
                rel_paths = [p.relative_to(root) for p in self._scan_workspace(root)]

            for rel_path in rel_paths:
                file_path = root / rel_path
                rel_path_str = rel_path.as_posix()
                ext = rel_path.suffix.lower()
                icon = _ICONS.get(ext, _DEFAULT_ICON)

                try:
                    size = file_path.stat().st_size
                    size_str = _format_size(size)
                except OSError:
                    size_str = ""

                items.append(
                    PopupItem(
                        label=rel_path_str,
                        description=size_str,
                        icon=icon,
                        hint=ext or "file",
                        value=rel_path_str,
                        data=file_path,
                    )
                )
        except PermissionError:
            pass

        # Sort by path
        items.sort(key=lambda i: i.label.lower())
        self._cached_files = items
        return items

    def filter_items(self, query: str, items: List[PopupItem]) -> List[PopupItem]:
        """
        Weighted fuzzy ranking:
        - filename match > full-path match
        - recent changed files are boosted
        - shorter / shallower paths are preferred
        """
        query_lower = query.strip().lower()
        recent_paths = self._get_recent_paths()
        scored: List[tuple[float, PopupItem]] = []

        for item in items:
            score = self._score_item(item, query_lower, recent_paths)
            if score is None:
                continue
            scored.append((score, item))

        scored.sort(key=lambda row: (-row[0], row[1].label.lower()))
        return [item for _, item in scored]

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
                        if (
                            entry.name not in _EXCLUDED_DIRS
                            and not entry.name.startswith(".")
                        ):
                            _walk(entry, depth + 1)
                    elif entry.is_file():
                        files.append(entry)
            except (PermissionError, OSError):
                pass

        _walk(root)
        return files

    def _get_indexed_paths(self) -> List[str]:
        indexer = getattr(self._app_context, "file_indexer", None)
        if indexer is None or not hasattr(indexer, "get_index"):
            return []

        try:
            paths = indexer.get_index()
        except Exception:
            return []

        if not isinstance(paths, list):
            return []
        return [str(p).replace("\\", "/") for p in paths if isinstance(p, str)]

    def _get_recent_paths(self) -> Set[str]:
        tracker = getattr(self._app_context, "file_tracker", None)
        if tracker is None or not hasattr(tracker, "get_changes"):
            return set()

        root = self._workspace_root
        recent: Set[str] = set()
        try:
            changes = tracker.get_changes()
        except Exception:
            return recent

        for change in changes:
            raw_path = getattr(change, "path", None)
            if raw_path is None:
                continue
            path_obj = Path(str(raw_path))
            try:
                if root is not None and path_obj.is_absolute():
                    rel = path_obj.relative_to(root.resolve()).as_posix()
                    recent.add(rel)
                else:
                    recent.add(path_obj.as_posix())
            except Exception:
                recent.add(path_obj.as_posix())
        return recent

    def _score_item(
        self, item: PopupItem, query: str, recent_paths: Set[str]
    ) -> Optional[float]:
        path_text = item.label.replace("\\", "/")
        path_lower = path_text.lower()
        filename_lower = Path(path_lower).name

        score = 0.0
        if query:
            filename_score = self._match_score(filename_lower, query)
            path_score = self._match_score(path_lower, query)

            if filename_score is None and path_score is None:
                return None

            # Filename relevance is weighted higher than folder/path relevance.
            score += (filename_score or 0.0) * 1.5
            score += path_score or 0.0

        if path_text in recent_paths:
            score += 25.0

        depth = path_text.count("/")
        score -= min(depth * 2.0, 12.0)
        score -= min(len(path_text) / 18.0, 12.0)
        return score

    def _match_score(self, text: str, query: str) -> Optional[float]:
        if text.startswith(query):
            return 120.0
        if query in text:
            return 95.0

        subseq = self._subsequence_score(text, query)
        if subseq is None:
            return None
        return 40.0 + subseq

    def _subsequence_score(self, text: str, query: str) -> Optional[float]:
        if not query:
            return 0.0

        indices: List[int] = []
        search_from = 0
        for ch in query:
            idx = text.find(ch, search_from)
            if idx < 0:
                return None
            indices.append(idx)
            search_from = idx + 1

        if not indices:
            return 0.0

        gaps = 0
        for i in range(1, len(indices)):
            gaps += max(0, indices[i] - indices[i - 1] - 1)
        span = indices[-1] - indices[0] + 1

        base = len(query) * 7.0
        return max(1.0, base - float(gaps) - max(0.0, float(span - len(query))))

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

    def show_popup(self, initial_filter: str = "") -> None:
        """Always re-scan the workspace when the popup is triggered."""
        self.invalidate_cache()
        super().show_popup(initial_filter)
