"""
Unit tests for the AnthropicProvider adapter.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_cli.providers.models import ProviderRequestOptions, ToolCallMode
from agent_cli.providers.provider.anthropic_provider import AnthropicProvider


def get_mocked_anthropic():
    """Returns a mocked AnthropicProvider."""
    provider = AnthropicProvider("claude-sonnet-4.6", api_key="sk-test")
    # Mock the client
    provider.client = MagicMock()
    provider.client.messages.create = AsyncMock()
    return provider


@pytest.mark.asyncio
async def test_anthropic_generate():
    """Verify AnthropicProvider generates text and translates tool calls correctly."""
    provider = get_mocked_anthropic()

    # Setup mock response with text and tool_use blocks
    mock_response = MagicMock()

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Here is the code."

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "tool_123"
    tool_block.name = "write_code"
    tool_block.input = {"language": "python"}

    mock_response.content = [text_block, tool_block]
    mock_response.stop_reason = "tool_use"
    mock_response.usage.input_tokens = 50
    mock_response.usage.output_tokens = 20

    provider.client.messages.create.return_value = mock_response

    # Execute
    res = await provider.generate(
        [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hi"},
        ]
    )

    # Assert
    assert res.text_content == "Here is the code."
    assert len(res.tool_calls) == 1
    assert res.tool_calls[0].tool_name == "write_code"
    assert res.tool_calls[0].arguments == {"language": "python"}
    assert res.tool_calls[0].mode == ToolCallMode.NATIVE
    assert res.tool_calls[0].native_call_id == "tool_123"
    assert res.input_tokens == 50
    assert res.output_tokens == 20
    assert res.provider == "anthropic"


@pytest.mark.asyncio
async def test_anthropic_stream():
    """Verify AnthropicProvider correctly streams text blocks and buffers tool_use logic."""
    provider = get_mocked_anthropic()

    # Setup mock streaming context manager and events
    class MockStreamContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

        async def __aiter__(self):
            # Chunk 1: Text delta
            event1 = MagicMock()
            event1.type = "content_block_delta"
            event1.delta.type = "text_delta"
            event1.delta.text = "Hello "
            yield event1

            # Chunk 2: Tool Use Block Stop
            event2 = MagicMock()
            event2.type = "content_block_stop"
            event2.content_block.type = "tool_use"
            event2.content_block.id = "tool_123"
            event2.content_block.name = "run_command"
            event2.content_block.input = {"cmd": "ls"}
            yield event2

            # Chunk 3: End of message (usage delta)
            event3 = MagicMock()
            event3.type = "message_delta"
            event3.usage.output_tokens = 30
            event3.delta.stop_reason = "tool_use"
            yield event3

        async def get_final_message(self):
            mock_final = MagicMock()
            mock_final.usage.input_tokens = 100
            return mock_final

    provider.client.messages.stream = MagicMock(return_value=MockStreamContext())

    # Execute stream
    chunks = []
    async for chunk in provider.stream([{"role": "user", "content": "Hi"}]):
        chunks.append(chunk)

    # Text chunk
    assert chunks[0].text == "Hello "

    # Final chunk
    assert chunks[-1].is_final
    assert chunks[-1].usage == {"input_tokens": 100, "output_tokens": 30}

    # Buffered response checks
    res = provider.get_buffered_response()
    assert res.text_content == "Hello "
    assert len(res.tool_calls) == 1
    assert res.tool_calls[0].tool_name == "run_command"
    assert res.tool_calls[0].arguments == {"cmd": "ls"}
    assert res.input_tokens == 100
    assert res.output_tokens == 30


@pytest.mark.asyncio
async def test_anthropic_generate_adds_data_driven_web_search_tool():
    provider = get_mocked_anthropic()

    mock_response = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "done"
    mock_response.content = [text_block]
    mock_response.stop_reason = "end_turn"
    mock_response.usage.input_tokens = 1
    mock_response.usage.output_tokens = 1
    provider.client.messages.create.return_value = mock_response

    await provider.generate(
        [{"role": "user", "content": "search"}],
        request_options=ProviderRequestOptions(provider_managed_tools=["web_search"]),
    )

    call_kwargs = provider.client.messages.create.await_args.kwargs
    tools = call_kwargs["tools"]
    web = [tool for tool in tools if tool.get("type") == "web_search_20250305"]
    assert len(web) == 1
    assert web[0]["max_uses"] == 10
    assert "allowed_domains" not in web[0]
