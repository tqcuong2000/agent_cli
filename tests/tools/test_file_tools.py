import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_cli.core.error_handler.errors import ToolExecutionError
from agent_cli.tools.file_tools import (
    ListDirectoryTool,
    ReadFileTool,
    SearchFilesTool,
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
    content = await tool.execute("test.txt")
    assert content == "line1\nline2\nline3\nline4\nline5"

    # Read with slicing
    content_sliced = await tool.execute("test.txt", start_line=2, end_line=4)
    assert "Showing lines" in content_sliced
    assert "line2" in content_sliced
    assert "line4" in content_sliced
    assert "line1" not in content_sliced
    assert "line5" not in content_sliced

    # Read directory fails
    with pytest.raises(ToolExecutionError, match="directory, not a file"):
        await tool.execute(".")


@pytest.mark.asyncio
async def test_write_file_tool(workspace, tmp_path):
    tool = WriteFileTool(workspace)

    # Write to new file in new dir
    res = await tool.execute("new_dir/new_file.txt", "hello\nworld")
    assert "Successfully wrote" in res

    new_file = tmp_path / "new_dir" / "new_file.txt"
    assert new_file.exists()
    assert new_file.read_text() == "hello\nworld"


@pytest.mark.asyncio
async def test_list_directory_tool(workspace, tmp_path):
    tool = ListDirectoryTool(workspace)
    
    (tmp_path / "file1.txt").touch()
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "file2.txt").write_text("test")

    res = await tool.execute(".", max_depth=2)
    assert "file1.txt" in res
    assert "subdir/" in res
    assert "file2.txt" in res
    assert "B)" in res  # Check size formatting

    # Invalid dir
    with pytest.raises(ToolExecutionError, match="not a directory"):
        await tool.execute("file1.txt")


@pytest.mark.asyncio
async def test_search_files_tool(workspace, tmp_path):
    tool = SearchFilesTool(workspace)
    
    (tmp_path / "foo.py").write_text("def hello():\n    pass")
    (tmp_path / "bar.txt").write_text("Hello world!")
    subdir = tmp_path / "src"
    subdir.mkdir()
    (subdir / "baz.py").write_text("def another_hello():\n    return 'hello'")

    # Search all
    res = await tool.execute("hello")
    assert "Found 4 matches" in res
    assert "foo.py" in res
    assert "bar.txt" in res
    assert "baz.py" in res
    
    # Search with glob filter
    res2 = await tool.execute("hello", file_pattern="*.py")
    assert "Found 3 matches" in res2
    assert "foo.py" in res2
    assert "bar.txt" not in res2

    # No matches
    res3 = await tool.execute("notfound")
    assert "No matches found" in res3
