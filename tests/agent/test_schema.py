"""Tests for SchemaValidator."""

import pytest

from agent_cli.agent.schema import SchemaValidator
from agent_cli.core.error_handler.errors import SchemaValidationError
from agent_cli.providers.models import LLMResponse, ToolCall, ToolCallMode


@pytest.fixture
def validator():
    return SchemaValidator(registered_tools=["foo", "bar"])


# ── Step 1: Extract thinking ──────────────────────────────────────────

def test_extract_thinking(validator):
    # Single thinking block
    text1 = "<thinking>I need to think.</thinking>"
    assert validator.extract_thinking(text1) == "I need to think."

    # Multiple thinking blocks (concat with newline)
    text2 = "<thinking>Part 1</thinking>\nSome noise\n<thinking>Part 2</thinking>"
    assert validator.extract_thinking(text2) == "Part 1\nPart 2"

    # Multiline thinking
    text3 = "<thinking>\nLine 1\nLine 2\n</thinking>"
    assert validator.extract_thinking(text3) == "Line 1\nLine 2"

    # No thinking
    assert validator.extract_thinking("Just some text") == ""


# ── Step 2: Native FC Mode ───────────────────────────────────────────

def test_parse_native_fc_success(validator):
    response = LLMResponse(
        text_content="<thinking>Using tools</thinking>",
        tool_mode=ToolCallMode.NATIVE,
        tool_calls=[
            ToolCall(tool_name="foo", arguments={"x": 1}, native_call_id="call_1")
        ]
    )

    result = validator.parse_and_validate(response)
    
    assert result.thought == "Using tools"
    assert result.action is not None
    assert result.action.tool_name == "foo"
    assert result.action.arguments == {"x": 1}
    assert result.action.native_call_id == "call_1"
    assert result.final_answer is None


def test_parse_native_fc_unknown_tool(validator):
    response = LLMResponse(
        text_content="<thinking>Using tools</thinking>",
        tool_mode=ToolCallMode.NATIVE,
        tool_calls=[
            ToolCall(tool_name="unknown", arguments={"x": 1})
        ]
    )

    with pytest.raises(SchemaValidationError, match="Unknown tool 'unknown'"):
        validator.parse_and_validate(response)


# ── Step 3: XML Prompting Mode ───────────────────────────────────────

def test_parse_xml_mode_success(validator):
    text = (
        "<thinking>Using foo</thinking>\n"
        "<action>\n"
        "  <tool>foo</tool>\n"
        '  <args>{"x": 1}</args>\n'
        "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)
    
    assert result.thought == "Using foo"
    assert result.action is not None
    assert result.action.tool_name == "foo"
    assert result.action.arguments == {"x": 1}
    assert result.action.native_call_id == ""  # Empty for XML
    assert result.final_answer is None


def test_parse_xml_mode_unknown_tool(validator):
    text = (
        "<action>\n"
        "  <tool>unknown</tool>\n"
        '  <args>{"x": 1}</args>\n'
        "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    with pytest.raises(SchemaValidationError, match="Unknown tool 'unknown'"):
        validator.parse_and_validate(response)


def test_parse_xml_mode_missing_tool_tag(validator):
    text = (
        "<action>\n"
        '  <args>{"x": 1}</args>\n'
        "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    with pytest.raises(SchemaValidationError, match="missing <tool> tag"):
        validator.parse_and_validate(response)


def test_parse_xml_mode_missing_args_tag(validator):
    text = (
        "<action>\n"
        "  <tool>foo</tool>\n"
        "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    with pytest.raises(SchemaValidationError, match="missing <args> block"):
        validator.parse_and_validate(response)


def test_parse_xml_mode_invalid_json(validator):
    text = (
        "<action>\n"
        "  <tool>foo</tool>\n"
        "  <args>{invalid json}</args>\n"
        "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    with pytest.raises(SchemaValidationError, match="Invalid JSON"):
        validator.parse_and_validate(response)


# ── Step 4: JSON Coercion ────────────────────────────────────────────

def test_json_coercion_single_quotes(validator):
    # args JSON uses single quotes (invalid JSON, but common LLM mistake)
    text = (
        "<action>\n"
        "  <tool>foo</tool>\n"
        "  <args>{'x': 'hello'}</args>\n"
        "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)
    
    assert result.action.arguments == {"x": "hello"}


def test_json_coercion_trailing_comma(validator):
    # args JSON has a trailing comma
    text = (
        "<action>\n"
        "  <tool>foo</tool>\n"
        '  <args>{"x": 1,}</args>\n'
        "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)
    
    assert result.action.arguments == {"x": 1}


def test_json_coercion_combined(validator):
    # Both single quotes and trailing comma
    text = (
        "<action>\n"
        "  <tool>foo</tool>\n"
        "  <args>{'x': 'hello',}</args>\n"
        "</action>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)
    
    assert result.action.arguments == {"x": "hello"}


# ── Step 5: Final Answer ─────────────────────────────────────────────

def test_final_answer_explicit_tag(validator):
    # Has <final_answer> tag
    text = (
        "<thinking>Done.</thinking>\n"
        "<final_answer>Here is your result.</final_answer>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)
    
    assert result.action is None
    assert result.final_answer == "Here is your result."


def test_final_answer_implicit(validator):
    # No tags, just raw text (after thinking is removed)
    text = "<thinking>Done.</thinking>\nHere is your implicit result."
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)
    
    assert result.action is None
    assert result.final_answer == "Here is your implicit result."


def test_action_and_final_answer_mutually_exclusive(validator):
    # Both action and final_answer tag present. Action parsing happens
    # first, so `final_answer` should be ignored.
    text = (
        "<action>\n"
        "  <tool>foo</tool>\n"
        '  <args>{"x": 1}</args>\n'
        "</action>\n"
        "<final_answer>I also answered.</final_answer>"
    )
    response = LLMResponse(text_content=text, tool_mode=ToolCallMode.XML)

    result = validator.parse_and_validate(response)
    
    assert result.action is not None
    assert result.final_answer is None


def test_empty_response(validator):
    # Nothing found at all
    response = LLMResponse(text_content="   \n   ", tool_mode=ToolCallMode.XML)

    with pytest.raises(SchemaValidationError, match="no <thinking>, no tool call, and no final answer"):
        validator.parse_and_validate(response)
