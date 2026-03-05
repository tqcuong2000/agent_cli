"""Tests for ToolRegistry and ToolOutputFormatter."""

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.runtime.tools.base import BaseTool, ToolCategory
from agent_cli.core.runtime.tools.output_formatter import ToolOutputFormatter
from agent_cli.core.runtime.tools.registry import ToolRegistry


class EmptyArgs(BaseModel):
    pass


class DummyFileTool(BaseTool):
    name = "file_tool"
    description = "A file tool."
    category = ToolCategory.FILE

    @property
    def args_schema(self):
        return EmptyArgs

    async def execute(self, **kwargs):
        return "file"


class DummyExecTool(BaseTool):
    name = "exec_tool"
    description = "An exec tool."
    category = ToolCategory.EXECUTION

    @property
    def args_schema(self):
        return EmptyArgs

    async def execute(self, **kwargs):
        return "exec"


def _parse_tool_output(output: str) -> dict:
    if output.startswith("[tool_result "):
        header, body = output.split("\n", 1)
        body = body.rsplit("\n[/tool_result]", 1)[0]
        attrs: dict[str, str] = {}
        for part in header[len("[tool_result ") : -1].split():
            key, value = part.split("=", 1)
            attrs[key] = value

        return {
            "type": "tool_result",
            "version": "1.0",
            "payload": {
                "tool": attrs.get("tool", ""),
                "status": attrs.get("status", ""),
                "output": body,
                "truncated": attrs.get("truncated", "false") == "true",
                "truncated_chars": int(attrs.get("truncated_chars", "0")),
                "error_code": attrs.get("error_code", ""),
                "retryable": attrs.get("retryable", "") == "true"
                if "retryable" in attrs
                else None,
                "total_chars": int(attrs.get("total_chars", "0"))
                if "total_chars" in attrs
                else None,
                "total_lines": int(attrs.get("total_lines", "0"))
                if "total_lines" in attrs
                else None,
            },
            "metadata": {
                "task_id": attrs.get("task_id", ""),
                "native_call_id": attrs.get("native_call_id", ""),
                "action_id": attrs.get("action_id", ""),
                "batch_id": attrs.get("batch_id", ""),
                "content_ref": attrs.get("content_ref", ""),
            },
        }

    return json.loads(output)


def test_tool_registry_register_and_get():
    registry = ToolRegistry()
    t1 = DummyFileTool()
    t2 = DummyExecTool()

    registry.register(t1)
    registry.register(t2)

    assert registry.get("file_tool") is t1
    assert registry.get("exec_tool") is t2
    assert registry.get("unknown") is None

    assert len(registry) == 2
    assert "file_tool" in registry


def test_tool_registry_duplicate_registration_fails():
    registry = ToolRegistry()
    registry.register(DummyFileTool())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(DummyFileTool())


def test_tool_registry_get_by_category():
    registry = ToolRegistry()
    t1 = DummyFileTool()
    t2 = DummyExecTool()
    registry.register(t1)
    registry.register(t2)

    file_tools = registry.get_by_category(ToolCategory.FILE)
    assert len(file_tools) == 1
    assert file_tools[0] is t1


def test_tool_registry_get_for_agent():
    registry = ToolRegistry()
    t1 = DummyFileTool()
    registry.register(t1)

    agent_tools = registry.get_for_agent(["file_tool"])
    assert len(agent_tools) == 1
    assert agent_tools[0] is t1

    with pytest.raises(ValueError, match="not found in registry"):
        registry.get_for_agent(["file_tool", "unknown"])


def test_tool_registry_get_definitions_for_llm():
    registry = ToolRegistry()
    registry.register(DummyFileTool())

    defs = registry.get_definitions_for_llm(["file_tool"])
    assert len(defs) == 1

    d = defs[0]
    assert d["name"] == "file_tool"
    assert d["description"] == "A file tool."
    assert "parameters" in d
    assert d["category"] == "FILE"


def test_tool_registry_freeze_blocks_register():
    registry = ToolRegistry()
    registry.register(DummyFileTool())
    registry.freeze()

    with pytest.raises(RuntimeError, match="frozen"):
        registry.register(DummyExecTool())


def test_tool_registry_freeze_idempotent():
    registry = ToolRegistry()
    registry.register(DummyFileTool())
    registry.freeze()
    registry.freeze()
    assert registry.is_frozen is True


def test_tool_registry_freeze_rejects_empty_registry() -> None:
    registry = ToolRegistry()
    with pytest.raises(RuntimeError, match="at least one tool"):
        registry.freeze()


def test_tool_registry_rejects_missing_name():
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="non-empty 'name'"):
        registry.register(object())  # type: ignore[arg-type]


def test_tool_registry_rejects_missing_execute():
    registry = ToolRegistry()
    fake = SimpleNamespace(name="fake_tool", get_json_schema=lambda: {})
    with pytest.raises(ValueError, match="'execute' method"):
        registry.register(fake)  # type: ignore[arg-type]


def test_tool_registry_rejects_missing_get_json_schema():
    registry = ToolRegistry()
    fake = SimpleNamespace(name="fake_tool", execute=lambda **kwargs: None)
    with pytest.raises(ValueError, match="'get_json_schema' method"):
        registry.register(fake)  # type: ignore[arg-type]


def test_tool_output_formatter():
    formatter = ToolOutputFormatter(
        max_output_length=20,
        data_registry=DataRegistry(),
    )

    # Success, short output
    res = formatter.format("test_tool", "short result")
    parsed = _parse_tool_output(res)
    assert parsed["type"] == "tool_result"
    assert parsed["version"] == "1.0"
    assert parsed["payload"]["tool"] == "test_tool"
    assert parsed["payload"]["status"] == "success"
    assert parsed["payload"]["output"] == "short result"
    assert parsed["payload"]["truncated"] is False
    assert parsed["payload"]["truncated_chars"] == 0

    # Failure, short output
    res = formatter.format("test_tool", "short error", success=False)
    parsed = _parse_tool_output(res)
    assert parsed["payload"]["status"] == "error"
    assert parsed["payload"]["output"] == "short error"

    # Success, long output (should truncate)
    long_res = "A" * 15 + "B" * 15
    res = formatter.format("test_tool", long_res)
    parsed = _parse_tool_output(res)
    assert parsed["payload"]["truncated"] is True
    assert parsed["payload"]["total_chars"] == len(long_res)
    assert parsed["payload"]["total_lines"] == 1
    assert long_res[:10] in parsed["payload"]["output"]
    assert long_res[-10:] in parsed["payload"]["output"]


def test_tool_output_formatter_lean_envelope() -> None:
    formatter = ToolOutputFormatter(
        max_output_length=20,
        data_registry=DataRegistry(),
    )
    formatter.lean_envelope = True

    res = formatter.format("test_tool", "short result", task_id="task_1", action_id="act_1")
    parsed = _parse_tool_output(res)
    assert parsed["type"] == "tool_result"
    assert parsed["payload"]["tool"] == "test_tool"
    assert parsed["payload"]["status"] == "success"
    assert parsed["payload"]["output"] == "short result"
    assert parsed["metadata"]["task_id"] == "task_1"
    assert parsed["metadata"]["action_id"] == "act_1"


def test_tool_output_formatter_includes_error_code_metadata() -> None:
    formatter = ToolOutputFormatter(data_registry=DataRegistry())
    formatter.lean_envelope = False

    res = formatter.format(
        "read_file",
        "File not found",
        success=False,
        error_code="FILE_NOT_FOUND",
        retryable=False,
    )
    parsed = _parse_tool_output(res)
    assert parsed["payload"]["error_code"] == "FILE_NOT_FOUND"
    assert parsed["payload"]["retryable"] is False


def test_tool_output_formatter_includes_batch_id_metadata() -> None:
    formatter = ToolOutputFormatter(data_registry=DataRegistry())

    res = formatter.format(
        "read_file",
        "ok",
        success=True,
        action_id="act_1",
        batch_id="batch_abc123",
    )
    parsed = _parse_tool_output(res)
    assert parsed["metadata"]["batch_id"] == "batch_abc123"
