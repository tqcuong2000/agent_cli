"""
Unit tests for the OpenAIProvider adapter.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_cli.providers.models import ToolCallMode
from agent_cli.providers.provider.openai_provider import OpenAIProvider


def get_mocked_openai():
    """Returns a mocked OpenAIProvider."""
    provider = OpenAIProvider("gpt-5", api_key="sk-test")
    # Mock the client
    provider.client = MagicMock()
    provider.client.chat.completions.create = AsyncMock()
    return provider


@pytest.mark.asyncio
async def test_openai_generate():
    """Verify OpenAIProvider generates text and translates tool calls correctly."""
    provider = get_mocked_openai()

    # Setup mock response
    mock_response = MagicMock()
    mock_msg = mock_response.choices[0].message
    mock_msg.content = "I am thinking."

    # Mock a tool call
    mock_tc = MagicMock()
    mock_tc.function.name = "write_code"
    mock_tc.function.arguments = '{"language": "python"}'
    mock_tc.id = "call_abc123"
    mock_msg.tool_calls = [mock_tc]

    mock_response.choices[0].finish_reason = "tool_calls"
    mock_response.usage.prompt_tokens = 50
    mock_response.usage.completion_tokens = 20

    provider.client.chat.completions.create.return_value = mock_response

    # Execute
    res = await provider.generate([{"role": "user", "content": "Hi"}])

    # Assert
    assert res.text_content == "I am thinking."
    assert len(res.tool_calls) == 1
    assert res.tool_calls[0].tool_name == "write_code"
    assert res.tool_calls[0].arguments == {"language": "python"}
    assert res.tool_calls[0].mode == ToolCallMode.NATIVE
    assert res.tool_calls[0].native_call_id == "call_abc123"
    assert res.input_tokens == 50
    assert res.output_tokens == 20
    assert res.provider == "openai"


@pytest.mark.asyncio
async def test_openai_generate_uses_max_completion_tokens_for_gpt5():
    provider = get_mocked_openai()
    provider.model_name = "gpt-5-nano"

    mock_response = MagicMock()
    mock_msg = mock_response.choices[0].message
    mock_msg.content = "ok"
    mock_msg.tool_calls = []
    mock_response.choices[0].finish_reason = "stop"
    mock_response.usage.prompt_tokens = 1
    mock_response.usage.completion_tokens = 1

    provider.client.chat.completions.create.return_value = mock_response

    await provider.generate([{"role": "user", "content": "Hi"}], max_tokens=123)

    call_kwargs = provider.client.chat.completions.create.call_args.kwargs
    assert "max_completion_tokens" in call_kwargs
    assert call_kwargs["max_completion_tokens"] == 123
    assert "max_tokens" not in call_kwargs


@pytest.mark.asyncio
async def test_openai_stream():
    """Verify OpenAIProvider correctly assembles streamed deltas and buffers tool calls."""
    provider = get_mocked_openai()

    # Setup mock streaming response (AsyncGenerator)
    async def mock_stream():
        # Chunk 1: Text delta
        chunk1 = MagicMock()
        chunk1.choices[0].delta.content = "Here "
        chunk1.choices[0].delta.tool_calls = None
        chunk1.usage = None
        yield chunk1

        # Chunk 2: Tool call starts
        chunk2 = MagicMock()
        chunk2.choices[0].delta.content = None
        tc1 = MagicMock()
        tc1.index = 0
        tc1.id = "call_xyz"
        tc1.function.name = "run_t"
        tc1.function.arguments = '{"com'
        chunk2.choices[0].delta.tool_calls = [tc1]
        chunk2.usage = None
        yield chunk2

        # Chunk 3: Tool call finishes, usage reported
        chunk3 = MagicMock()
        chunk3.choices[0].delta.content = None
        tc2 = MagicMock()
        tc2.index = 0
        tc2.id = None
        tc2.function.name = None
        tc2.function.arguments = 'mand": "ls"}'
        chunk3.choices[0].delta.tool_calls = [tc2]
        chunk3.usage.prompt_tokens = 10
        chunk3.usage.completion_tokens = 15
        chunk3.choices[0].finish_reason = "tool_calls"
        yield chunk3

    provider.client.chat.completions.create.return_value = mock_stream()

    # Execute stream
    chunks = []
    async for chunk in provider.stream([{"role": "user", "content": "Hi"}]):
        chunks.append(chunk)

    # First chunk should have the text delta
    assert chunks[0].text == "Here "
    assert not chunks[0].is_final

    # Last chunk should be the final marker with usage
    assert chunks[-1].is_final
    assert chunks[-1].usage == {"input_tokens": 10, "output_tokens": 15}

    # Check buffered response
    res = provider.get_buffered_response()
    assert res.text_content == "Here "
    assert len(res.tool_calls) == 1
    assert res.tool_calls[0].tool_name == "run_t"
    assert res.tool_calls[0].arguments == {"command": "ls"}
    assert res.input_tokens == 10
    assert res.output_tokens == 15


@pytest.mark.asyncio
async def test_openai_stream_uses_max_completion_tokens_for_gpt5():
    provider = get_mocked_openai()
    provider.model_name = "gpt-5-nano"

    async def mock_stream():
        chunk = MagicMock()
        chunk.choices[0].delta.content = None
        chunk.choices[0].delta.tool_calls = None
        chunk.choices[0].finish_reason = "stop"
        chunk.usage.prompt_tokens = 1
        chunk.usage.completion_tokens = 1
        yield chunk

    provider.client.chat.completions.create.return_value = mock_stream()

    chunks = []
    async for chunk in provider.stream(
        [{"role": "user", "content": "Hi"}], max_tokens=77
    ):
        chunks.append(chunk)

    assert chunks[-1].is_final
    call_kwargs = provider.client.chat.completions.create.call_args.kwargs
    assert "max_completion_tokens" in call_kwargs
    assert call_kwargs["max_completion_tokens"] == 77
    assert "max_tokens" not in call_kwargs
