from pathlib import Path

import pytest

from agent_cli.core.infra.config.config import AgentSettings
from agent_cli.core.runtime.tools.search_tools import FindByNameTool, GrepSearchTool
from agent_cli.core.ux.interaction.strict import StrictWorkspaceManager


@pytest.fixture
def workspace(tmp_path: Path):
    settings = AgentSettings()
    return StrictWorkspaceManager(
        root_path=tmp_path,
        deny_patterns=settings.workspace_deny_patterns,
        allow_overrides=settings.workspace_allow_overrides,
    )


def test_search_tool_parallel_safety_flags(workspace):
    assert FindByNameTool(workspace).parallel_safe is True
    assert GrepSearchTool(workspace).parallel_safe is True


@pytest.mark.asyncio
async def test_find_by_name_filters_by_type_extensions_excludes_and_depth(
    workspace,
    tmp_path: Path,
):
    tool = FindByNameTool(workspace)

    (tmp_path / "app.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "module.py").write_text("x = 1", encoding="utf-8")
    nested = src / "nested"
    nested.mkdir()
    (nested / "deep.py").write_text("y = 2", encoding="utf-8")
    ignored = tmp_path / "__pycache__"
    ignored.mkdir()
    (ignored / "junk.py").write_text("z = 3", encoding="utf-8")

    files_only = await tool.execute(
        pattern="*.py",
        type="file",
        extensions=["py"],
        excludes=["__pycache__"],
        max_depth=2,
    )
    assert "app.py" in files_only
    assert "src/module.py" in files_only
    assert "deep.py" not in files_only
    assert "__pycache__" not in files_only

    directories_only = await tool.execute(pattern="src", type="directory", max_depth=1)
    assert "[dir]  src/" in directories_only
    assert "module.py" not in directories_only


@pytest.mark.asyncio
async def test_grep_search_returns_line_matches_and_file_only_results(
    workspace,
    tmp_path: Path,
):
    tool = GrepSearchTool(workspace)

    (tmp_path / "app.py").write_text("def Hello():\n    return 'ok'\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("hello world\nbye\n", encoding="utf-8")

    line_matches = await tool.execute(query="hello", case_insensitive=True)
    assert "Found 2 matches" in line_matches
    assert "app.py:1:" in line_matches
    assert "notes.txt:1:" in line_matches

    file_matches = await tool.execute(
        query="hello",
        case_insensitive=True,
        match_per_line=False,
    )
    assert "Found 2 files containing 'hello'" in file_matches
    assert "app.py" in file_matches
    assert "notes.txt" in file_matches


@pytest.mark.asyncio
async def test_grep_search_python_fallback_respects_includes_and_result_cap(
    workspace,
    tmp_path: Path,
):
    tool = GrepSearchTool(workspace, max_file_size_bytes=1024)
    tool._rg_executable = None

    (tmp_path / "keep.py").write_text("TODO one\nTODO two\n", encoding="utf-8")
    (tmp_path / "skip.txt").write_text("TODO skip\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TODO secret\n", encoding="utf-8")

    result = await tool.execute(
        query="TODO",
        includes=["*.py"],
        max_results=1,
    )
    assert "Found 1 matches" in result
    assert "keep.py:1: TODO one" in result
    assert "Stopped at 1 results" in result
    assert "skip.txt" not in result
    assert ".env" not in result
