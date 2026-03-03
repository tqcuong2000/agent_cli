"""Tests for BaseTool and ToolResult."""

import pytest
from pydantic import BaseModel, Field, ValidationError

from agent_cli.tools.base import BaseTool, ToolCategory, ToolResult


class DummyArgs(BaseModel):
    arg1: str = Field(description="First argument")
    arg2: int = Field(default=42, description="Second argument")


class DummyTool(BaseTool):
    name = "dummy_tool"
    description = "A dummy tool for testing."
    category = ToolCategory.UTILITY
    is_safe = True

    @property
    def args_schema(self) -> type[BaseModel]:
        return DummyArgs

    async def execute(self, arg1: str, arg2: int = 42, **kwargs) -> str:
        return f"Executed {arg1} with {arg2}"


def test_tool_result():
    result = ToolResult(success=True, output="Test", metadata={"key": "val"})
    assert result.success is True
    assert result.output == "Test"
    assert result.error == ""
    assert result.metadata == {"key": "val"}


def test_base_tool_validation():
    tool = DummyTool()
    
    # Valid arguments
    validated = tool.validate_args(arg1="hello", arg2=10)
    assert validated.arg1 == "hello"
    assert validated.arg2 == 10
    
    # Missing required argument
    with pytest.raises(ValidationError):
        tool.validate_args(arg2=10)
        
    # Invalid type
    with pytest.raises(ValidationError):
        tool.validate_args(arg1="hello", arg2="not_an_int")


def test_get_json_schema():
    tool = DummyTool()
    schema = tool.get_json_schema()
    
    assert schema["type"] == "object"
    assert "arg1" in schema["properties"]
    assert "arg2" in schema["properties"]
    assert "arg1" in schema["required"]
    assert "arg2" not in schema["required"]  # Has default
