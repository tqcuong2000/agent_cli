from pathlib import Path

import pytest

from agent_cli.core.infra.config.config import AgentSettings
from agent_cli.core.infra.events.errors import ToolExecutionError
from agent_cli.core.runtime.tools.search_tools import FindByNameTool, GrepSearchTool
from agent_cli.core.ux.interaction.strict import StrictWorkspaceManager


class _FakeStream:
    def __init__(self, lines: list[str]) -> None:
        self._lines = [line.encode("utf-8") for line in lines]

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)

    async def read(self) -> bytes:
        data = b"".join(self._lines)
        self._lines.clear()
        return data


class _FakeProcess:
    def __init__(
        self,
        *,
        stdout_lines: list[str],
        stderr_text: str = "",
        returncode: int = 0,
    ) -> None:
        self.stdout = _FakeStream(stdout_lines)
        self.stderr = _FakeStream([stderr_text] if stderr_text else [])
        self.returncode = returncode
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode


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
async def test_grep_search_regex_and_case_sensitive_matching(workspace, tmp_path: Path):
    tool = GrepSearchTool(workspace)
    tool._rg_executable = None

    (tmp_path / "app.py").write_text("HelloOne\nhelloTwo\n", encoding="utf-8")

    regex_matches = await tool.execute(
        query=r"Hello\w+",
        is_regex=True,
        case_insensitive=False,
    )
    assert "Found 1 matches" in regex_matches
    assert "app.py:1: HelloOne" in regex_matches
    assert "helloTwo" not in regex_matches

    sensitive_literal = await tool.execute(
        query="hello",
        case_insensitive=False,
    )
    assert "Found 1 matches" in sensitive_literal
    assert "app.py:2: helloTwo" in sensitive_literal
    assert "app.py:1: HelloOne" not in sensitive_literal


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


@pytest.mark.asyncio
async def test_grep_search_raises_for_empty_query(workspace):
    tool = GrepSearchTool(workspace)

    with pytest.raises(ToolExecutionError, match="query is required"):
        await tool.execute(query="")


@pytest.mark.asyncio
async def test_grep_search_invalid_regex_reports_clear_error(workspace, tmp_path: Path):
    tool = GrepSearchTool(workspace)
    tool._rg_executable = None
    (tmp_path / "app.py").write_text("hello\n", encoding="utf-8")

    with pytest.raises(ToolExecutionError, match="Invalid regex for grep_search"):
        await tool.execute(query="(", is_regex=True)


@pytest.mark.asyncio
async def test_grep_search_no_matches_message(workspace, tmp_path: Path):
    tool = GrepSearchTool(workspace)
    tool._rg_executable = None
    (tmp_path / "app.py").write_text("hello\n", encoding="utf-8")

    result = await tool.execute(query="missing")
    assert result == "No matches found for 'missing' in '.'."


@pytest.mark.asyncio
async def test_grep_search_ripgrep_json_path_parses_matches(monkeypatch, workspace):
    tool = GrepSearchTool(workspace)
    tool._rg_executable = "rg"

    stdout_lines = [
        '{"type":"begin","data":{"path":{"text":"src/app.py"}}}\n',
        '{"type":"match","data":{"path":{"text":"src/app.py"},"line_number":12,"lines":{"text":"class User:\\n"}}}\n',
        '{"type":"match","data":{"path":{"text":"src/models.py"},"line_number":4,"lines":{"text":"class Order:\\n"}}}\n',
    ]

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProcess(stdout_lines=stdout_lines)

    monkeypatch.setattr(
        "agent_cli.core.runtime.tools.search_tools.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    result = await tool.execute(query="class ", is_regex=False, max_results=10)
    assert "Found 2 matches" in result
    assert "src/app.py:12: class User:" in result
    assert "src/models.py:4: class Order:" in result


@pytest.mark.asyncio
async def test_grep_search_ripgrep_file_mode_respects_cap(monkeypatch, workspace):
    tool = GrepSearchTool(workspace)
    tool._rg_executable = "rg"

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProcess(stdout_lines=["src/a.py\n", "src/b.py\n", "src/c.py\n"])

    monkeypatch.setattr(
        "agent_cli.core.runtime.tools.search_tools.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    result = await tool.execute(query="needle", match_per_line=False, max_results=2)
    assert "Found 2 files containing 'needle'" in result
    assert "src/a.py" in result
    assert "src/b.py" in result
    assert "Stopped at 2 results" in result


@pytest.mark.asyncio
async def test_grep_search_falls_back_when_ripgrep_launch_fails(monkeypatch, workspace, tmp_path: Path):
    tool = GrepSearchTool(workspace)
    tool._rg_executable = "rg"
    (tmp_path / "app.py").write_text("needle\n", encoding="utf-8")

    async def _fake_create_subprocess_exec(*args, **kwargs):
        raise PermissionError("Access is denied")

    monkeypatch.setattr(
        "agent_cli.core.runtime.tools.search_tools.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    result = await tool.execute(query="needle")
    assert "Found 1 matches" in result
    assert "app.py:1: needle" in result
