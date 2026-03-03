"""
Unit tests for the OpenAICompatibleProvider adapter.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_cli.providers.models import ToolCallMode
from agent_cli.providers.provider.openai_compat import OpenAICompatibleProvider


def get_mocked_compat(native_tools=False):
    """Returns a mocked OpenAICompatibleProvider."""
    provider = OpenAICompatibleProvider(
        "llama-3-8b", 
        base_url="http://localhost:11434/v1",
        native_tools=native_tools
    )
    # Mock the client
    provider.client = MagicMock()
    provider.client.chat.completions.create = AsyncMock()
    return provider


@pytest.mark.asyncio
async def test_compat_generate_xml_mode():
    """Verify XML fallback works natively via generate()."""
    provider = get_mocked_compat()
    
    # Setup mock response
    mock_response = MagicMock()
    mock_msg = mock_response.choices[0].message
    mock_msg.content = "<thinking>ok</thinking>\n<action>\n  <tool>do_something</tool>\n  <args>{}</args>\n</action>"
    mock_msg.tool_calls = None
    
    mock_response.usage.prompt_tokens = 200
    mock_response.usage.completion_tokens = 150
    
    provider.client.chat.completions.create.return_value = mock_response

    # Execute
    context = [{"role": "system", "content": "You are a bot."}, {"role": "user", "content": "Hi"}]
    tools = [{"name": "do_something", "description": ""}]
    res = await provider.generate(context, tools=tools)
    
    # Verify client call intercepted the tools and mutated the system prompt
    call_args = provider.client.chat.completions.create.call_args[1]
    assert "tools" not in call_args
    assert len(call_args["messages"]) == 2  # Added system prompt containing XML
    assert "## Available Tools" in call_args["messages"][0]["content"]
    
    # Assert
    assert "<thinking>ok</thinking>" in res.text_content
    # tool_calls should be exactly zero internally, until SchemaValidator runs
    assert len(res.tool_calls) == 0
    assert res.tool_mode == ToolCallMode.XML
    assert res.cost_usd == 0.0  # open source zero pricing


@pytest.mark.asyncio
async def test_compat_stream_native_mode():
    """Verify native function calling streaming for instances hosting compatible endpoints (vLLM)."""
    provider = get_mocked_compat(native_tools=False)  # Testing XML stream
    
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
    assert res.tool_mode == ToolCallMode.XML
    assert res.input_tokens == 5
    assert res.output_tokens == 10
    assert res.cost_usd == 0.0
