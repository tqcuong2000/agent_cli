"""Tests for SchemaValidator (JSON protocol only)."""

import json

import pytest

from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.runtime.agents.parsers import AgentDecision
from agent_cli.core.runtime.agents.schema import SchemaValidator
from agent_cli.core.infra.events.errors import SchemaValidationError
from agent_cli.core.infra.config.config_models import ProtocolMode
from agent_cli.core.providers.base.models import LLMResponse, ToolCall, ToolCallMode


@pytest.fixture
def validator() -> SchemaValidator:
    return SchemaValidator(
        registered_tools=["foo", "bar"],
        protocol_mode=ProtocolMode.JSON_ONLY,
        data_registry=DataRegistry(),
    )


@pytest.fixture
def validator_multi() -> SchemaValidator:
    return SchemaValidator(
        registered_tools=["foo", "bar"],
        protocol_mode=ProtocolMode.JSON_ONLY,
        data_registry=DataRegistry(),
        multi_action_enabled=True,
    )


def _json_response(
    decision_type: str,
    *,
    tool: str = "",
    args: dict | None = None,
    message: str = "",
    title: str = "Plan next step",
    thought: str = "I will continue.",
) -> str:
    payload: dict = {
        "title": title,
        "thought": thought,
        "decision": {"type": decision_type},
    }
    if tool:
        payload["decision"]["tool"] = tool
    if args is not None:
        payload["decision"]["args"] = args
    if message:
        payload["decision"]["message"] = message
    return json.dumps(payload)


def test_extract_thinking_from_json_payload(validator: SchemaValidator) -> None:
    text = _json_response(
        "reflect",
        title="Think through options",
        thought="Need to choose the safest path.",
    )
    thinking = validator.extract_thinking(text)
    assert "Title: Think through options" in thinking
    assert "Need to choose the safest path." in thinking


def test_extract_thinking_returns_empty_when_missing_json(
    validator: SchemaValidator,
) -> None:
    assert validator.extract_thinking("just text") == ""


def test_agent_decision_includes_execute_actions() -> None:
    assert AgentDecision.EXECUTE_ACTIONS.value == "execute_actions"


def test_parse_native_fc_success(validator: SchemaValidator) -> None:
    response = LLMResponse(
        text_content=_json_response("execute_action", title="Use tool", thought="go"),
        tool_mode=ToolCallMode.NATIVE,
        tool_calls=[
            ToolCall(tool_name="foo", arguments={"x": 1}, native_call_id="call_1")
        ],
    )

    result = validator.parse_and_validate(response)

    assert result.decision == AgentDecision.EXECUTE_ACTION
    assert result.action is not None
    assert result.action.tool_name == "foo"
    assert result.action.arguments == {"x": 1}
    assert result.action.native_call_id == "call_1"
    assert result.action.action_id == ""
    assert result.actions is None
    assert result.title == "Use tool"


def test_parse_native_fc_unknown_tool(validator: SchemaValidator) -> None:
    response = LLMResponse(
        text_content=_json_response("execute_action"),
        tool_mode=ToolCallMode.NATIVE,
        tool_calls=[ToolCall(tool_name="unknown", arguments={"x": 1})],
    )
    with pytest.raises(SchemaValidationError, match="Unknown tool"):
        validator.parse_and_validate(response)


def test_parse_prompt_json_execute_action(validator: SchemaValidator) -> None:
    response = LLMResponse(
        text_content=_json_response(
            "execute_action",
            tool="foo",
            args={"x": 1},
            title="Use foo safely",
            thought="Need tool output first.",
        ),
        tool_mode=ToolCallMode.PROMPT_JSON,
    )

    result = validator.parse_and_validate(response)
    assert result.decision == AgentDecision.EXECUTE_ACTION
    assert result.action is not None
    assert result.action.tool_name == "foo"
    assert result.action.arguments == {"x": 1}
    assert result.action.action_id == ""
    assert result.actions is None
    assert result.title == "Use foo safely"
    assert "Title: Use foo safely" in result.thought


def test_parse_prompt_json_execute_actions(validator_multi: SchemaValidator) -> None:
    payload = {
        "title": "Batch read",
        "thought": "Run both tools.",
        "decision": {
            "type": "execute_actions",
            "actions": [
                {"tool": "foo", "args": {"x": 1}},
                {"tool": "bar", "args": {"y": 2}},
            ],
        },
    }
    response = LLMResponse(
        text_content=json.dumps(payload),
        tool_mode=ToolCallMode.PROMPT_JSON,
    )
    result = validator_multi.parse_and_validate(response)
    assert result.decision == AgentDecision.EXECUTE_ACTIONS
    assert result.actions is not None
    assert len(result.actions) == 2
    assert result.actions[0].tool_name == "foo"
    assert result.actions[0].action_id == "act_0"
    assert result.actions[1].tool_name == "bar"
    assert result.actions[1].action_id == "act_1"


def test_parse_prompt_json_execute_actions_invalid_shapes(
    validator_multi: SchemaValidator,
) -> None:
    bad_payloads = [
        {"decision": {"type": "execute_actions", "actions": []}},
        {"decision": {"type": "execute_actions", "actions": "not-list"}},
        {"decision": {"type": "execute_actions", "actions": [{"args": {}}]}},
        {"decision": {"type": "execute_actions", "actions": [{"tool": "missing"}]}},
        {"decision": {"type": "execute_actions", "actions": [{"tool": "foo", "args": "x"}]}},
    ]

    for payload in bad_payloads:
        response = LLMResponse(
            text_content=json.dumps(payload),
            tool_mode=ToolCallMode.PROMPT_JSON,
        )
        with pytest.raises(SchemaValidationError):
            validator_multi.parse_and_validate(response)


def test_parse_prompt_json_execute_actions_repairs_object_shape(
    validator_multi: SchemaValidator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = {
        "title": "Batch read",
        "thought": "Run both tools.",
        "decision": {
            "type": "execute_actions",
            "actions": {"tool": "foo", "args": {"x": 1}},
        },
    }
    response = LLMResponse(
        text_content=json.dumps(payload),
        tool_mode=ToolCallMode.PROMPT_JSON,
    )
    result = validator_multi.parse_and_validate(response)
    assert result.decision == AgentDecision.EXECUTE_ACTIONS
    assert result.actions is not None
    assert len(result.actions) == 1
    assert result.actions[0].tool_name == "foo"
    assert result.actions[0].action_id == "act_0"
    assert "Repairing execute_actions payload" in caplog.text


def test_parse_prompt_json_execute_actions_when_multi_disabled(
    validator: SchemaValidator,
) -> None:
    payload = {
        "title": "Batch read",
        "thought": "Run both tools.",
        "decision": {
            "type": "execute_actions",
            "actions": [{"tool": "foo", "args": {"x": 1}}],
        },
    }
    response = LLMResponse(text_content=json.dumps(payload), tool_mode=ToolCallMode.PROMPT_JSON)
    with pytest.raises(SchemaValidationError, match="execute_actions is disabled"):
        validator.parse_and_validate(response)


def test_single_execute_action_remains_single_when_multi_enabled(
    validator_multi: SchemaValidator,
) -> None:
    response = LLMResponse(
        text_content=_json_response("execute_action", tool="foo", args={"x": 1}),
        tool_mode=ToolCallMode.PROMPT_JSON,
    )
    result = validator_multi.parse_and_validate(response)
    assert result.decision == AgentDecision.EXECUTE_ACTION
    assert result.action is not None
    assert result.actions is None


def test_parse_native_fc_multiple_calls_when_multi_enabled(
    validator_multi: SchemaValidator,
) -> None:
    response = LLMResponse(
        text_content=_json_response("execute_actions", title="native multi", thought="go"),
        tool_mode=ToolCallMode.NATIVE,
        tool_calls=[
            ToolCall(tool_name="foo", arguments={"x": 1}, native_call_id="call_a"),
            ToolCall(tool_name="bar", arguments={"y": 2}),
        ],
    )
    result = validator_multi.parse_and_validate(response)
    assert result.decision == AgentDecision.EXECUTE_ACTIONS
    assert result.actions is not None
    assert len(result.actions) == 2
    assert result.actions[0].action_id == "call_a"
    assert result.actions[1].action_id == "act_1"


def test_parse_native_fc_multiple_calls_when_multi_disabled(
    validator: SchemaValidator,
) -> None:
    response = LLMResponse(
        text_content=_json_response("execute_action"),
        tool_mode=ToolCallMode.NATIVE,
        tool_calls=[
            ToolCall(tool_name="foo", arguments={"x": 1}),
            ToolCall(tool_name="bar", arguments={"y": 2}),
        ],
    )
    with pytest.raises(SchemaValidationError, match="Multiple native tool calls"):
        validator.parse_and_validate(response)


def test_parse_prompt_json_notify_user(validator: SchemaValidator) -> None:
    response = LLMResponse(
        text_content=_json_response(
            "notify_user",
            message="Here is the result.",
            title="Complete response",
            thought="Everything is finished.",
        ),
        tool_mode=ToolCallMode.PROMPT_JSON,
    )

    result = validator.parse_and_validate(response)
    assert result.decision == AgentDecision.NOTIFY_USER
    assert result.final_answer == "Here is the result."
    assert result.action is None
    assert result.title == "Complete response"


def test_parse_prompt_json_yield(validator: SchemaValidator) -> None:
    response = LLMResponse(
        text_content=_json_response(
            "yield",
            message="Missing permissions.",
            title="Blocked",
            thought="Cannot proceed safely.",
        ),
        tool_mode=ToolCallMode.PROMPT_JSON,
    )

    result = validator.parse_and_validate(response)
    assert result.decision == AgentDecision.YIELD
    assert result.final_answer == "Missing permissions."
    assert result.title == "Blocked"


def test_parse_prompt_json_reflect(validator: SchemaValidator) -> None:
    response = LLMResponse(
        text_content=_json_response(
            "reflect",
            title="Think first",
            thought="Still planning.",
        ),
        tool_mode=ToolCallMode.PROMPT_JSON,
    )

    result = validator.parse_and_validate(response)
    assert result.decision == AgentDecision.REFLECT
    assert result.action is None
    assert result.final_answer is None
    assert result.title == "Think first"


def test_parse_code_fenced_json(validator: SchemaValidator) -> None:
    body = _json_response("notify_user", message="done")
    response = LLMResponse(text_content=f"```json\n{body}\n```")
    result = validator.parse_and_validate(response)
    assert result.decision == AgentDecision.NOTIFY_USER
    assert result.final_answer == "done"


def test_parse_json_inside_extra_text(validator: SchemaValidator) -> None:
    body = _json_response("notify_user", message="done")
    response = LLMResponse(text_content=f"noise before {body} noise after")
    result = validator.parse_and_validate(response)
    assert result.decision == AgentDecision.NOTIFY_USER
    assert result.final_answer == "done"


def test_repairs_missing_trailing_brace_with_tool_markers(
    validator: SchemaValidator,
) -> None:
    malformed = (
        '{"title":"Move CSS","thought":"Write file now.",'
        '"decision":{"type":"execute_action","tool":"foo","args":{"x":1}}'
        " <|tool_call_end|> <|tool_calls_section_end|>"
    )
    response = LLMResponse(text_content=malformed, tool_mode=ToolCallMode.PROMPT_JSON)
    result = validator.parse_and_validate(response)

    assert result.decision == AgentDecision.EXECUTE_ACTION
    assert result.action is not None
    assert result.action.tool_name == "foo"
    assert result.action.arguments == {"x": 1}
    assert result.title == "Move CSS"


def test_repairs_valid_json_with_trailing_tool_markers(
    validator: SchemaValidator,
) -> None:
    payload = _json_response(
        "notify_user",
        message="complete",
        title="Done",
        thought="All done.",
    )
    malformed = payload + " <|tool_call_end|> <|tool_calls_section_end|>"
    response = LLMResponse(text_content=malformed, tool_mode=ToolCallMode.PROMPT_JSON)
    result = validator.parse_and_validate(response)

    assert result.decision == AgentDecision.NOTIFY_USER
    assert result.final_answer == "complete"
    assert result.title == "Done"


def test_rejects_legacy_tag_style_payload(validator: SchemaValidator) -> None:
    text = (
        "<legacy_title>Use foo</legacy_title>\n<legacy_thought>legacy</legacy_thought>\n"
        "<legacy_action><tool>foo</tool><args><x>1</x></args></legacy_action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.PROMPT_JSON)
    with pytest.raises(SchemaValidationError, match="not valid JSON"):
        validator.parse_and_validate(response)


def test_rejects_missing_decision_object(validator: SchemaValidator) -> None:
    response = LLMResponse(text_content=json.dumps({"title": "x", "thought": "y"}))
    with pytest.raises(SchemaValidationError, match="decision"):
        validator.parse_and_validate(response)


def test_rejects_unknown_decision_type(validator: SchemaValidator) -> None:
    response = LLMResponse(
        text_content=_json_response("run_tool", tool="foo", args={"x": 1})
    )
    with pytest.raises(SchemaValidationError, match="Unknown decision.type"):
        validator.parse_and_validate(response)


def test_rejects_execute_action_without_tool(validator: SchemaValidator) -> None:
    response = LLMResponse(text_content=_json_response("execute_action", args={"x": 1}))
    with pytest.raises(SchemaValidationError, match="decision.tool"):
        validator.parse_and_validate(response)


def test_rejects_non_object_args(validator: SchemaValidator) -> None:
    payload = {
        "title": "x",
        "thought": "y",
        "decision": {"type": "execute_action", "tool": "foo", "args": "bad"},
    }
    response = LLMResponse(text_content=json.dumps(payload))
    with pytest.raises(SchemaValidationError, match="decision.args must be an object"):
        validator.parse_and_validate(response)
