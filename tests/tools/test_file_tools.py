from pathlib import Path

import pytest

from agent_cli.core.error_handler.errors import ToolExecutionError
from agent_cli.tools.file_tools import (
    InsertLinesTool,
    ListDirectoryTool,
    ReadFileTool,
    SearchFilesTool,
    StrReplaceTool,
    WriteFileTool,
)
from agent_cli.tools.workspace import WorkspaceContext


@pytest.fixture
def workspace(tmp_path: Path):
    return WorkspaceContext(root_path=tmp_path)


def test_workspace_resolve_path_success(workspace, tmp_path):
    foo_file = tmp_path / "foo.txt"
    foo_file.touch()

    # Relative path
    resolved = workspace.resolve_path("foo.txt", must_exist=True)
    assert resolved == foo_file

    # Absolute path within workspace
    resolved2 = workspace.resolve_path(str(foo_file), must_exist=True)
    assert resolved2 == foo_file


def test_workspace_resolve_path_escape_fails(workspace, tmp_path):
    with pytest.raises(ToolExecutionError, match="outside the workspace"):
        workspace.resolve_path("../../etc/passwd")

    with pytest.raises(ToolExecutionError, match="outside the workspace"):
        workspace.resolve_path("/etc/passwd")


def test_workspace_resolve_path_must_exist(workspace):
    with pytest.raises(ToolExecutionError, match="File not found"):
        workspace.resolve_path("missing.txt", must_exist=True)


@pytest.mark.asyncio
async def test_read_file_tool(workspace, tmp_path):
    tool = ReadFileTool(workspace)

    file_path = tmp_path / "test.txt"
    file_path.write_text("line1\nline2\nline3\nline4\nline5")

    # Read entire file
    content = await tool.execute(path="test.txt")
    assert content == "line1\nline2\nline3\nline4\nline5"

    # Read with slicing
    content_sliced = await tool.execute(path="test.txt", start_line=2, end_line=4)
    assert "Showing lines" in content_sliced
    assert "line2" in content_sliced
    assert "line4" in content_sliced
    assert "line1" not in content_sliced
    assert "line5" not in content_sliced

    # Read directory fails
    with pytest.raises(ToolExecutionError, match="directory, not a file"):
        await tool.execute(path=".")


@pytest.mark.asyncio
async def test_write_file_tool(workspace, tmp_path):
    tool = WriteFileTool(workspace)

    # Write to new file in new dir
    res = await tool.execute(path="new_dir/new_file.txt", content="hello\nworld")
    assert "Successfully wrote" in res

    new_file = tmp_path / "new_dir" / "new_file.txt"
    assert new_file.exists()
    assert new_file.read_text() == "hello\nworld"


@pytest.mark.asyncio
async def test_str_replace_tool_single_match_returns_diff(workspace, tmp_path):
    tool = StrReplaceTool(workspace)
    target = tmp_path / "sample.txt"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    res = await tool.execute(
        path="sample.txt",
        old_str="beta",
        new_str="BETA",
    )

    assert "--- a/sample.txt" in res
    assert "+++ b/sample.txt" in res
    assert "-beta" in res
    assert "+BETA" in res
    assert target.read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n"


@pytest.mark.asyncio
async def test_str_replace_tool_zero_match_has_hint(workspace, tmp_path):
    tool = StrReplaceTool(workspace)
    target = tmp_path / "sample.txt"
    target.write_text("alpha\nBeta Value\ngamma\n", encoding="utf-8")

    with pytest.raises(ToolExecutionError, match="found 0 matches"):
        await tool.execute(
            path="sample.txt",
            old_str="beta value\nwith extra line",
            new_str="x",
        )


@pytest.mark.asyncio
async def test_str_replace_tool_multiple_matches_reports_lines(workspace, tmp_path):
    tool = StrReplaceTool(workspace)
    target = tmp_path / "sample.txt"
    target.write_text("one\nsame\ntwo\nsame\n", encoding="utf-8")

    with pytest.raises(ToolExecutionError, match="multiple matches"):
        await tool.execute(path="sample.txt", old_str="same", new_str="X")


@pytest.mark.asyncio
async def test_insert_lines_tool_inserts_and_reports_counts(workspace, tmp_path):
    tool = InsertLinesTool(workspace)
    target = tmp_path / "insert.txt"
    target.write_text("a\nb\nc\n", encoding="utf-8")

    res = await tool.execute(
        path="insert.txt",
        insert_after_line=1,
        content="x\ny",
    )

    assert "Inserted 2 line(s)" in res
    assert "New total: 5 line(s)." in res
    assert target.read_text(encoding="utf-8") == "a\nx\ny\nb\nc\n"


@pytest.mark.asyncio
async def test_insert_lines_tool_accepts_zero_for_top_insert(workspace, tmp_path):
    tool = InsertLinesTool(workspace)
    target = tmp_path / "insert.txt"
    target.write_text("line1\nline2\n", encoding="utf-8")

    await tool.execute(
        path="insert.txt",
        insert_after_line=0,
        content="top",
    )

    assert target.read_text(encoding="utf-8") == "top\nline1\nline2\n"


@pytest.mark.asyncio
async def test_insert_lines_tool_range_validation(workspace, tmp_path):
    tool = InsertLinesTool(workspace)
    target = tmp_path / "insert.txt"
    target.write_text("only\none\n", encoding="utf-8")

    with pytest.raises(ToolExecutionError, match="out of range"):
        await tool.execute(
            path="insert.txt",
            insert_after_line=99,
            content="x",
        )


@pytest.mark.asyncio
async def test_list_directory_tool(workspace, tmp_path):
    tool = ListDirectoryTool(workspace)

    (tmp_path / "file1.txt").touch()
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "file2.txt").write_text("test")

    res = await tool.execute(path=".", max_depth=2)
    assert "file1.txt" in res
    assert "subdir/" in res
    assert "file2.txt" in res
    assert "B)" in res  # Check size formatting

    # Invalid dir
    with pytest.raises(ToolExecutionError, match="not a directory"):
        await tool.execute(path="file1.txt")


@pytest.mark.asyncio
async def test_search_files_tool(workspace, tmp_path):
    tool = SearchFilesTool(workspace)

    (tmp_path / "foo.py").write_text("def hello():\n    pass")
    (tmp_path / "bar.txt").write_text("Hello world!")
    subdir = tmp_path / "src"
    subdir.mkdir()
    (subdir / "baz.py").write_text("def another_hello():\n    return 'hello'")

    # Search all
    res = await tool.execute(pattern="hello")
    assert "Found 4 matches" in res
    assert "foo.py" in res
    assert "bar.txt" in res
    assert "baz.py" in res

    # Search with glob filter
    res2 = await tool.execute(pattern="hello", file_pattern="*.py")
    assert "Found 3 matches" in res2
    assert "foo.py" in res2
    assert "bar.txt" not in res2

    # No matches
    res3 = await tool.execute(pattern="notfound")
    assert "No matches found" in res3
