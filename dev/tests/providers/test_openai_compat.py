"""
Unit tests for the OpenAICompatibleProvider adapter.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.base.models import ToolCallMode
from agent_cli.core.providers.adapters.openai_compat import OpenAICompatibleProvider


def get_mocked_compat(native_tools=False):
    """Returns a mocked OpenAICompatibleProvider."""
    provider = OpenAICompatibleProvider(
        "llama-3-8b",
        base_url="http://localhost:11434/v1",
        native_tools=native_tools,
        data_registry=DataRegistry(),
    )
    # Mock the client
    provider.client = MagicMock()
    provider.client.chat.completions.create = AsyncMock()
    return provider


@pytest.mark.asyncio
async def test_compat_generate_prompt_json_mode():
    """Verify prompt-injected JSON mode works via generate()."""
    provider = get_mocked_compat()

    # Setup mock response
    mock_response = MagicMock()
    mock_msg = mock_response.choices[0].message
    mock_msg.content = (
        '{"title":"plan","thought":"ok","decision":{"type":"execute_action",'
        '"tool":"do_something","args":{}}}'
    )
    mock_msg.tool_calls = None

    mock_response.usage.prompt_tokens = 200
    mock_response.usage.completion_tokens = 150

    provider.client.chat.completions.create.return_value = mock_response

    # Execute
    context = [
        {"role": "system", "content": "You are a bot."},
        {"role": "user", "content": "Hi"},
    ]
    tools = [{"name": "do_something", "description": ""}]
    res = await provider.generate(context, tools=tools)

    # Verify client call intercepted the tools and mutated the system prompt
    call_args = provider.client.chat.completions.create.call_args[1]
    assert "tools" not in call_args
    assert (
        len(call_args["messages"]) == 2
    )  # Added system prompt containing JSON contract
    assert "## Available Tools" in call_args["messages"][0]["content"]

    # Assert
    assert '"type":"execute_action"' in res.text_content
    # tool_calls should be exactly zero internally, until SchemaValidator runs
    assert len(res.tool_calls) == 0
    assert res.tool_mode == ToolCallMode.PROMPT_JSON
    assert res.cost_usd == 0.0  # open source zero pricing


@pytest.mark.asyncio
async def test_compat_stream_native_mode():
    """Verify native function calling streaming for instances hosting compatible endpoints (vLLM)."""
    provider = get_mocked_compat(native_tools=False)  # Testing prompt JSON stream

    # Setup mock streaming response
    async def mock_stream():
        chunk1 = MagicMock()
        chunk1.choices[0].delta.content = "Here "
        chunk1.usage = None
        yield chunk1

        chunk2 = MagicMock()
        chunk2.choices[0].delta.content = "is some streamed content."
        chunk2.usage.prompt_tokens = 5
        chunk2.usage.completion_tokens = 10
        yield chunk2

    provider.client.chat.completions.create.return_value = mock_stream()

    # Execute stream
    chunks = []
    async for chunk in provider.stream([{"role": "user", "content": "Hi"}]):
        chunks.append(chunk)

    assert chunks[0].text == "Here "
    assert chunks[1].text == "is some streamed content."
    assert chunks[-1].is_final
    assert chunks[-1].usage == {"input_tokens": 5, "output_tokens": 10}

    # Buffered response checks
    res = provider.get_buffered_response()
    assert res.text_content == "Here is some streamed content."
    assert len(res.tool_calls) == 0
    assert res.tool_mode == ToolCallMode.PROMPT_JSON
    assert res.input_tokens == 5
    assert res.output_tokens == 10
    assert res.cost_usd == 0.0
