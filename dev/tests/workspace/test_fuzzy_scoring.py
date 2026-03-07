"""Tests for weighted fuzzy selection in file discovery popup."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_cli.core.ux.tui.views.common.file_popup import FileDiscoveryPopup
from agent_cli.core.ux.tui.views.common.popup_list import PopupItem


class _StubIndexer:
    def __init__(self, files: list[str]):
        self._files = list(files)

    def get_index(self) -> list[str]:
        return list(self._files)


def _popup_item(path: str) -> PopupItem:
    return PopupItem(label=path, value=path)


def test_filename_match_ranks_above_path_only_match():
    popup = FileDiscoveryPopup()
    items = [
        _popup_item("docs/parser_notes/readme.txt"),
        _popup_item("src/parser.py"),
    ]

    ranked = popup.filter_items("par", items)

    assert [item.label for item in ranked] == [
        "src/parser.py",
        "docs/parser_notes/readme.txt",
    ]


def test_recently_changed_files_receive_boost(tmp_path: Path):
    changed_rel = "src/utility_beta.py"
    tracker = SimpleNamespace(
        get_changes=lambda: [
            SimpleNamespace(path=(tmp_path / changed_rel)),
        ]
    )
    app_context = SimpleNamespace(file_tracker=tracker, file_indexer=None)
    popup = FileDiscoveryPopup(workspace_root=tmp_path, app_context=app_context)

    items = [
        _popup_item("src/utility_alpha.py"),
        _popup_item(changed_rel),
    ]
    ranked = popup.filter_items("utility", items)

    assert ranked[0].label == changed_rel


def test_shorter_paths_rank_higher_for_equal_name_match():
    popup = FileDiscoveryPopup()
    items = [
        _popup_item("very/deep/nested/module/config.py"),
        _popup_item("src/config.py"),
    ]

    ranked = popup.filter_items("config", items)
    assert [item.label for item in ranked] == [
        "src/config.py",
        "very/deep/nested/module/config.py",
    ]


def test_popup_uses_file_indexer_results_instead_of_workspace_scan(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('x')", encoding="utf-8")
    (tmp_path / "README.md").write_text("# test", encoding="utf-8")

    indexer = _StubIndexer(["src/main.py", "README.md"])
    app_context = SimpleNamespace(file_indexer=indexer, file_tracker=None)
    popup = FileDiscoveryPopup(workspace_root=tmp_path, app_context=app_context)
    popup._scan_workspace = lambda *_: (_ for _ in ()).throw(AssertionError())  # type: ignore[assignment]

    labels = [item.label for item in popup.get_all_items()]

    assert labels == ["README.md", "src/main.py"]
