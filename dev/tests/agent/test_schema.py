"""Tests for SchemaValidator."""

import pytest

from agent_cli.agent.parsers import AgentDecision
from agent_cli.agent.schema import SchemaValidator
from agent_cli.core.error_handler.errors import SchemaValidationError
from agent_cli.providers.models import LLMResponse, ToolCall, ToolCallMode


@pytest.fixture
def validator():
    return SchemaValidator(registered_tools=["foo", "bar"])


def _reasoning(title: str, thoughts: str) -> str:
    return f"<title>{title}</title>\n<thinking>{thoughts}</thinking>"


# ── Step 1: Extract thinking ──────────────────────────────────────────


def test_extract_thinking(validator):
    # Single thinking block
    text1 = _reasoning("Plan safe execution in clear steps", "I need to think.")
    assert validator.extract_thinking(text1) == _reasoning(
        "Plan safe execution in clear steps", "I need to think."
    )

    # Multiple thinking blocks (concat with newline)
    text2 = (
        "<title>Break task into small executable parts</title>\n"
        "<thinking>Part 1</thinking>\nSome noise\n<thinking>Part 2</thinking>"
    )
    assert validator.extract_thinking(text2) == _reasoning(
        "Break task into small executable parts", "Part 1\nPart 2"
    )

    # Multiline thinking
    text3 = (
        "<title>Review details before deciding next action</title>\n"
        "<thinking>\nLine 1\nLine 2\n</thinking>"
    )
    assert validator.extract_thinking(text3) == _reasoning(
        "Review details before deciding next action", "Line 1\nLine 2"
    )

    # No thinking
    assert validator.extract_thinking("Just some text") == ""


def test_extract_thinking_inserts_default_title(validator):
    result = validator.extract_thinking("<thinking>Need to act.</thinking>")
    assert "<title>Untitled Action</title>" in result
    assert "<thinking>Need to act.</thinking>" in result


def test_extract_thinking_clamps_invalid_title_length(validator):
    result = validator.extract_thinking(
        "<title>A</title>\n<thinking>Need to act.</thinking>"
    )
    assert "<title>A</title>" in result


# ── Step 2: Native FC Mode ───────────────────────────────────────────


def test_parse_native_fc_success(validator):
    response = LLMResponse(
        text_content=_reasoning(
            "Use tool to gather required data safely", "Using tools"
        ),
        tool_mode=ToolCallMode.NATIVE,
        tool_calls=[
            ToolCall(tool_name="foo", arguments={"x": 1}, native_call_id="call_1")
        ],
    )

    result = validator.parse_and_validate(response)

    assert result.decision == AgentDecision.EXECUTE_ACTION
    assert result.thought == _reasoning(
        "Use tool to gather required data safely", "Using tools"
    )
    assert result.action is not None
    assert result.action.tool_name == "foo"
    assert result.action.arguments == {"x": 1}
    assert result.action.native_call_id == "call_1"
    assert result.final_answer is None


def test_parse_native_fc_unknown_tool(validator):
    response = LLMResponse(
        text_content=_reasoning(
            "Use tool to gather required data safely", "Using tools"
        ),
        tool_mode=ToolCallMode.NATIVE,
        tool_calls=[ToolCall(tool_name="unknown", arguments={"x": 1})],
    )

    with pytest.raises(SchemaValidationError, match="Unknown tool 'unknown'"):
        validator.parse_and_validate(response)


# ── Step 3: XML Prompting Mode ───────────────────────────────────────


def test_parse_xml_mode_success(validator):
    text = (
        _reasoning("Use foo tool for this quick lookup", "Using foo")
        + "\n"
        + "<action>\n"
        + "  <tool>foo</tool>\n"
        + "  <args><x>1</x></args>\n"
        + "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)

    assert result.thought == _reasoning(
        "Use foo tool for this quick lookup", "Using foo"
    )
    assert result.action is not None
    assert result.action.tool_name == "foo"
    assert result.action.arguments == {"x": 1}
    assert result.action.native_call_id == ""  # Empty for XML
    assert result.final_answer is None


def test_parse_xml_mode_unknown_tool(validator):
    text = (
        _reasoning("Attempt unknown tool call for test coverage", "Trying unknown tool")
        + "\n"
        + "<action>\n"
        + "  <tool>unknown</tool>\n"
        + "  <args><x>1</x></args>\n"
        + "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    with pytest.raises(SchemaValidationError, match="Unknown tool 'unknown'"):
        validator.parse_and_validate(response)


def test_parse_xml_mode_missing_tool_tag(validator):
    text = (
        _reasoning(
            "Check malformed action block handling here", "Will parse malformed action"
        )
        + "\n"
        + "<action>\n"
        + "  <args><x>1</x></args>\n"
        + "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    with pytest.raises(SchemaValidationError, match="missing <tool> tag"):
        validator.parse_and_validate(response)


def test_parse_xml_mode_missing_args_tag(validator):
    text = (
        _reasoning(
            "Check missing args handling in action parser",
            "Will parse malformed action",
        )
        + "\n"
        + "<action>\n"
        + "  <tool>foo</tool>\n"
        + "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    with pytest.raises(SchemaValidationError, match="missing <args> block"):
        validator.parse_and_validate(response)


def test_parse_xml_mode_invalid_xml(validator):
    text = (
        _reasoning(
            "Check malformed xml handling in parser", "Will parse malformed args"
        )
        + "\n"
        + "<action>\n"
        + "  <tool>foo</tool>\n"
        + "  <args><x>1</x><y></args>\n"
        + "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    with pytest.raises(SchemaValidationError, match="Invalid XML"):
        validator.parse_and_validate(response)


# ── Step 4: XML Args Parsing ─────────────────────────────────────────


def test_xml_args_scalar_coercion(validator):
    text = (
        _reasoning("Coerce xml scalar values before execution", "Attempt coercion")
        + "\n"
        + "<action>\n"
        + "  <tool>foo</tool>\n"
        + "  <args><x>1</x><y>true</y><z>3.5</z><n>null</n></args>\n"
        + "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)
    assert result.action.arguments == {"x": 1, "y": True, "z": 3.5, "n": None}


def test_xml_args_nested_objects_and_lists(validator):
    text = (
        _reasoning("Parse nested xml argument structures correctly", "Attempt coercion")
        + "\n"
        + "<action>\n"
        + "  <tool>foo</tool>\n"
        + "  <args><payload><name>alpha</name><flags><item>true</item><item>false</item></flags></payload></args>\n"
        + "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)
    assert result.action.arguments == {
        "payload": {"name": "alpha", "flags": [True, False]}
    }


# ── Step 5: Final Answer ─────────────────────────────────────────────


def test_final_answer_explicit_tag(validator):
    text = (
        _reasoning("Conclude work and provide final response", "Done.")
        + "\n"
        + "<final_answer>Here is your result.</final_answer>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)

    assert result.decision == AgentDecision.NOTIFY_USER
    assert result.action is None
    assert result.final_answer == "Here is your result."


def test_text_leakage_rejected(validator):
    text = (
        _reasoning("Reason for tool", "Done.")
        + "\n<final_answer>Result</final_answer>\n"
        + "Here is your leaked conversational text."
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    with pytest.raises(SchemaValidationError, match="Found raw text outside"):
        validator.parse_and_validate(response)


def test_text_leakage_with_action_rejected(validator):
    text = (
        _reasoning("Reason for tool", "Doing it.")
        + "\n<action><tool>foo</tool><args><query>foo</query></args></action>"
        + "\nHere is your leaked conversational text."
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    with pytest.raises(SchemaValidationError, match="Found raw text outside"):
        validator.parse_and_validate(response)


def test_action_and_final_answer_mutually_exclusive(validator):
    text = (
        _reasoning(
            "Prefer action path when both outputs are present", "Choose action first"
        )
        + "\n"
        + "<action>\n"
        + "  <tool>foo</tool>\n"
        + "  <args><x>1</x></args>\n"
        + "</action>\n"
        + "<final_answer>I also answered.</final_answer>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)

    assert result.decision == AgentDecision.EXECUTE_ACTION
    assert result.action is not None
    assert result.final_answer is None


def test_empty_response(validator):
    response = LLMResponse(text_content="   \n   ", tool_mode=ToolCallMode.XML)

    with pytest.raises(
        SchemaValidationError,
        match="no reasoning",
    ):
        validator.parse_and_validate(response)


def test_thinking_only_is_reflect(validator):
    text = _reasoning("Just pondering", "I don't know what tool to call")
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)
    result = validator.parse_and_validate(response)
    assert result.decision == AgentDecision.REFLECT
    assert result.thought != ""
    assert result.action is None
    assert result.final_answer is None


def test_action_without_reasoning_is_accepted(validator):
    text = "<action>\n  <tool>foo</tool>\n  <args><x>1</x></args>\n</action>"
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)
    result = validator.parse_and_validate(response)
    assert result.decision == AgentDecision.EXECUTE_ACTION
    assert result.action is not None
    assert result.action.tool_name == "foo"
