"""Tests for SchemaValidator."""

import pytest

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


def test_extract_thinking_requires_title(validator):
    with pytest.raises(SchemaValidationError, match="Missing <title>"):
        validator.extract_thinking("<thinking>Need to act.</thinking>")


def test_extract_thinking_rejects_invalid_title_length(validator):
    with pytest.raises(SchemaValidationError, match="2 to 15 words"):
        validator.extract_thinking(
            "<title>T</title>\n<thinking>Need to act.</thinking>"
        )


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
        + '  <args>{"x": 1}</args>\n'
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
        + '  <args>{"x": 1}</args>\n'
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
        + '  <args>{"x": 1}</args>\n'
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


def test_parse_xml_mode_invalid_json(validator):
    text = (
        _reasoning(
            "Check malformed json handling in parser", "Will parse malformed args"
        )
        + "\n"
        + "<action>\n"
        + "  <tool>foo</tool>\n"
        + "  <args>{invalid json}</args>\n"
        + "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    with pytest.raises(SchemaValidationError, match="Invalid JSON"):
        validator.parse_and_validate(response)


# ── Step 4: JSON Coercion ────────────────────────────────────────────


def test_json_coercion_single_quotes(validator):
    text = (
        _reasoning("Fix single quote json before execution", "Attempt coercion")
        + "\n"
        + "<action>\n"
        + "  <tool>foo</tool>\n"
        + "  <args>{'x': 'hello'}</args>\n"
        + "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)
    assert result.action.arguments == {"x": "hello"}


def test_json_coercion_trailing_comma(validator):
    text = (
        _reasoning("Fix trailing comma json before execution", "Attempt coercion")
        + "\n"
        + "<action>\n"
        + "  <tool>foo</tool>\n"
        + '  <args>{"x": 1,}</args>\n'
        + "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)
    assert result.action.arguments == {"x": 1}


def test_json_coercion_combined(validator):
    text = (
        _reasoning("Fix combined json errors before execution", "Attempt coercion")
        + "\n"
        + "<action>\n"
        + "  <tool>foo</tool>\n"
        + "  <args>{'x': 'hello',}</args>\n"
        + "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)
    assert result.action.arguments == {"x": "hello"}


# ── Step 5: Final Answer ─────────────────────────────────────────────


def test_final_answer_explicit_tag(validator):
    text = (
        _reasoning("Conclude work and provide final response", "Done.")
        + "\n"
        + "<final_answer>Here is your result.</final_answer>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)

    assert result.action is None
    assert result.final_answer == "Here is your result."


def test_final_answer_implicit(validator):
    text = (
        _reasoning(
            "Conclude work and provide final response",
            "Done.",
        )
        + "\nHere is your implicit result."
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)

    assert result.action is None
    assert result.final_answer == "Here is your implicit result."


def test_action_and_final_answer_mutually_exclusive(validator):
    text = (
        _reasoning(
            "Prefer action path when both outputs are present", "Choose action first"
        )
        + "\n"
        + "<action>\n"
        + "  <tool>foo</tool>\n"
        + '  <args>{"x": 1}</args>\n'
        + "</action>\n"
        + "<final_answer>I also answered.</final_answer>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)

    assert result.action is not None
    assert result.final_answer is None


def test_empty_response(validator):
    response = LLMResponse(text_content="   \n   ", tool_mode=ToolCallMode.XML)

    with pytest.raises(
        SchemaValidationError,
        match="no <thinking>, no tool call, and no final answer",
    ):
        validator.parse_and_validate(response)


def test_action_without_reasoning_is_rejected(validator):
    text = '<action>\n  <tool>foo</tool>\n  <args>{"x": 1}</args>\n</action>'
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)
    with pytest.raises(
        SchemaValidationError, match="missing required <title> and <thinking>"
    ):
        validator.parse_and_validate(response)
