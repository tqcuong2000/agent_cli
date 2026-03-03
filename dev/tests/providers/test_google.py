"""
Unit tests for the GoogleProvider adapter.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_cli.providers.models import ToolCallMode
from agent_cli.providers.provider.google_provider import GoogleProvider


def get_mocked_google():
    """Returns a mocked GoogleProvider."""
    provider = GoogleProvider("gemini-1.5-pro", api_key="AIzaSy")
    # Mock the client
    provider.client = MagicMock()
    provider.client.aio.models.generate_content = AsyncMock()
    provider.client.aio.models.generate_content_stream = MagicMock()
    return provider


@pytest.mark.asyncio
async def test_google_generate():
    """Verify GoogleProvider generates text and translates tool calls correctly."""
    provider = get_mocked_google()
    
    # Setup mock response
    mock_response = MagicMock()
    
    # Setup text part
    text_part = MagicMock()
    text_part.text = "Here's the data."
    text_part.function_call = None
    
    # Setup tool call part
    tool_part = MagicMock()
    tool_part.text = None
    tool_part.function_call.name = "get_weather"
    tool_part.function_call.args = {"location": "London"}
    
    mock_response.candidates[0].content.parts = [text_part, tool_part]
    mock_response.usage_metadata.prompt_token_count = 50
    mock_response.usage_metadata.candidates_token_count = 20
    
    provider.client.aio.models.generate_content.return_value = mock_response

    # Execute
    res = await provider.generate([{"role": "user", "content": "Hi"}])
    
    # Assert
    assert res.text_content == "Here's the data."
    assert len(res.tool_calls) == 1
    assert res.tool_calls[0].tool_name == "get_weather"
    assert res.tool_calls[0].arguments == {"location": "London"}
    assert res.tool_calls[0].mode == ToolCallMode.NATIVE
    assert res.input_tokens == 50
    assert res.output_tokens == 20
    assert res.provider == "google"


@pytest.mark.asyncio
async def test_google_stream():
    """Verify GoogleProvider correctly streams parts continuously through the genai SDK."""
    provider = get_mocked_google()
    
    # Setup mock streaming response (AsyncGenerator)
    async def mock_stream():
        # Chunk 1: Text chunk
        chunk1 = MagicMock()
        text_part = MagicMock()
        text_part.text = "Hello "
        text_part.function_call = None
        chunk1.candidates[0].content.parts = [text_part]
        chunk1.usage_metadata = None
        yield chunk1
        
        # Chunk 2: Tool call
        chunk2 = MagicMock()
        tool_part = MagicMock()
        tool_part.text = None
        tool_part.function_call.name = "run_command"
        tool_part.function_call.args = {"cmd": "ls"}
        chunk2.candidates[0].content.parts = [tool_part]
        
        # Google attaches usage to the final chunk
        mock_usage = MagicMock()
        mock_usage.prompt_token_count = 100
        mock_usage.candidates_token_count = 30
        chunk2.usage_metadata = mock_usage
        yield chunk2

    provider.client.aio.models.generate_content_stream.return_value = mock_stream()
    
    # Execute stream
    chunks = []
    async for chunk in provider.stream([{"role": "user", "content": "Hi"}]):
        chunks.append(chunk)

    # First chunk should have the text delta
    assert chunks[0].text == "Hello "
    assert not chunks[0].is_final
    
    # Last chunk should be the final marker with usage
    assert chunks[-1].is_final
    assert chunks[-1].usage == {"input_tokens": 100, "output_tokens": 30}
    
    # Check buffered response
    res = provider.get_buffered_response()
    assert res.text_content == "Hello "
    assert len(res.tool_calls) == 1
    assert res.tool_calls[0].tool_name == "run_command"
    assert res.tool_calls[0].arguments == {"cmd": "ls"}
    assert res.input_tokens == 100
    assert res.output_tokens == 30
