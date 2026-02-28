"""Tests for ToolRegistry and ToolOutputFormatter."""

import pytest
from pydantic import BaseModel

from agent_cli.tools.base import BaseTool, ToolCategory
from agent_cli.tools.registry import ToolRegistry
from agent_cli.tools.output_formatter import ToolOutputFormatter


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


def test_tool_output_formatter():
    formatter = ToolOutputFormatter(max_output_length=20)
    
    # Success, short output
    res = formatter.format("test_tool", "short result")
    assert "[Tool: test_tool]" in res
    assert "short result" in res
    assert "trunc" not in res.lower()
    
    # Failure, short output
    res = formatter.format("test_tool", "short error", success=False)
    assert "Error" in res
    assert "short error" in res
    
    # Success, long output (should truncate)
    long_res = "A" * 15 + "B" * 15
    res = formatter.format("test_tool", long_res)
    assert "truncated" in res.lower()
    assert long_res[:10] in res
    assert long_res[-10:] in res
