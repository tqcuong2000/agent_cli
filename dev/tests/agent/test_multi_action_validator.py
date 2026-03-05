from __future__ import annotations

import pytest
from pydantic import BaseModel

from agent_cli.core.infra.events.errors import SchemaValidationError
from agent_cli.core.runtime.agents.multi_action_validator import MultiActionValidator
from agent_cli.core.runtime.agents.parsers import ParsedAction
from agent_cli.core.runtime.tools.base import BaseTool, ToolCategory
from agent_cli.core.runtime.tools.registry import ToolRegistry


class _Args(BaseModel):
    value: str = ""


class _ReadTool(BaseTool):
    name = "read_file"
    description = "read"
    category = ToolCategory.FILE
    is_safe = True

    @property
    def args_schema(self) -> type[BaseModel]:
        return _Args

    async def execute(self, **kwargs) -> str:
        return "ok"


class _AskUserTool(BaseTool):
    name = "ask_user"
    description = "ask"
    category = ToolCategory.UTILITY
    is_safe = True
    parallel_safe = False

    @property
    def args_schema(self) -> type[BaseModel]:
        return _Args

    async def execute(self, **kwargs) -> str:
        return "ok"


class _ObservabilityStub:
    def __init__(self) -> None:
        self.strip_calls: list[tuple[str, int]] = []

    def record_multi_action_ask_user_strip(self, *, task_id: str, batch_size: int) -> None:
        self.strip_calls.append((task_id, batch_size))


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_ReadTool())
    reg.register(_AskUserTool())
    return reg


def _action(tool: str, idx: int) -> ParsedAction:
    return ParsedAction(
        tool_name=tool,
        arguments={"value": str(idx)},
        action_id=f"act_{idx}",
    )


def test_validate_valid_batch_passes_through(registry: ToolRegistry) -> None:
    validator = MultiActionValidator(registry)
    actions = [_action("read_file", 0), _action("read_file", 1)]
    validated = validator.validate(actions)
    assert [a.action_id for a in validated] == ["act_0", "act_1"]


def test_validate_ask_user_mixed_strips_other_actions(registry: ToolRegistry) -> None:
    validator = MultiActionValidator(registry)
    actions = [_action("read_file", 0), _action("ask_user", 1)]
    validated = validator.validate(actions)
    assert len(validated) == 1
    assert validated[0].tool_name == "ask_user"


def test_validate_ask_user_alone_passes(registry: ToolRegistry) -> None:
    validator = MultiActionValidator(registry)
    actions = [_action("ask_user", 0)]
    validated = validator.validate(actions)
    assert len(validated) == 1
    assert validated[0].tool_name == "ask_user"


def test_validate_empty_list_errors(registry: ToolRegistry) -> None:
    validator = MultiActionValidator(registry)
    with pytest.raises(SchemaValidationError, match="non-empty"):
        validator.validate([])


def test_validate_unknown_tool_errors(registry: ToolRegistry) -> None:
    validator = MultiActionValidator(registry)
    with pytest.raises(SchemaValidationError, match="Unknown tool"):
        validator.validate([_action("missing_tool", 0)])


def test_validate_duplicate_action_ids_error(registry: ToolRegistry) -> None:
    validator = MultiActionValidator(registry)
    a1 = _action("read_file", 0)
    a2 = _action("read_file", 1)
    a2.action_id = a1.action_id
    with pytest.raises(SchemaValidationError, match="Duplicate action_id"):
        validator.validate([a1, a2])


def test_validate_batch_size_limit_error(registry: ToolRegistry) -> None:
    validator = MultiActionValidator(registry, max_batch_size=1)
    with pytest.raises(SchemaValidationError, match="exceeds maximum"):
        validator.validate([_action("read_file", 0), _action("read_file", 1)])


def test_validate_ask_user_strip_records_metric(registry: ToolRegistry) -> None:
    observability = _ObservabilityStub()
    validator = MultiActionValidator(registry, observability=observability)
    actions = [_action("read_file", 0), _action("ask_user", 1)]
    validated = validator.validate(actions, task_id="task-1")
    assert len(validated) == 1
    assert validated[0].tool_name == "ask_user"
    assert observability.strip_calls == [("task-1", 2)]
