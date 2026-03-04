"""
Unit tests for the GoogleProvider adapter.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_cli.providers.models import ProviderRequestOptions, ToolCallMode
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


def test_convert_messages_combines_all_system_instructions():
    context = [
        {"role": "system", "content": "Primary system prompt."},
        {"role": "user", "content": "Hello"},
        {"role": "system", "content": "Schema recovery instruction."},
        {"role": "assistant", "content": "Working on it."},
    ]

    system, history = GoogleProvider._convert_messages(context)

    assert system == "Primary system prompt.\n\nSchema recovery instruction."
    assert history == [
        {"role": "user", "parts": [{"text": "Hello"}]},
        {"role": "model", "parts": [{"text": "Working on it."}]},
    ]


def test_resolve_google_thinking_level_maps_canonical_values():
    types = __import__("google.genai", fromlist=["types"]).types
    assert (
        GoogleProvider._resolve_google_thinking_level("minimal", types)
        == types.ThinkingLevel.MINIMAL
    )
    assert (
        GoogleProvider._resolve_google_thinking_level("low", types)
        == types.ThinkingLevel.LOW
    )
    assert (
        GoogleProvider._resolve_google_thinking_level("medium", types)
        == types.ThinkingLevel.MEDIUM
    )
    assert (
        GoogleProvider._resolve_google_thinking_level("high", types)
        == types.ThinkingLevel.HIGH
    )
    assert GoogleProvider._resolve_google_thinking_level("auto", types) is None
    assert GoogleProvider._resolve_google_thinking_level("invalid", types) is None


@pytest.mark.asyncio
async def test_google_generate_applies_effort_to_request_config():
    provider = get_mocked_google()

    mock_response = MagicMock()
    text_part = MagicMock()
    text_part.text = "ok"
    text_part.function_call = None
    mock_response.candidates[0].content.parts = [text_part]
    mock_response.usage_metadata.prompt_token_count = 1
    mock_response.usage_metadata.candidates_token_count = 1
    provider.client.aio.models.generate_content.return_value = mock_response

    await provider.generate([{"role": "user", "content": "Hi"}], effort="high")

    call_kwargs = provider.client.aio.models.generate_content.await_args.kwargs
    config = call_kwargs["config"]
    types = __import__("google.genai", fromlist=["types"]).types
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_level == types.ThinkingLevel.HIGH


@pytest.mark.asyncio
async def test_google_generate_adds_web_search_tool_when_requested(monkeypatch):
    provider = get_mocked_google()

    mock_response = MagicMock()
    text_part = MagicMock()
    text_part.text = "ok"
    text_part.function_call = None
    mock_response.candidates[0].content.parts = [text_part]
    mock_response.usage_metadata.prompt_token_count = 1
    mock_response.usage_metadata.candidates_token_count = 1
    provider.client.aio.models.generate_content.return_value = mock_response

    monkeypatch.setattr(
        provider,
        "_build_web_search_tool",
        lambda types: "web-search-tool",
    )

    await provider.generate(
        [{"role": "user", "content": "search this"}],
        request_options=ProviderRequestOptions(provider_managed_tools=["web_search"]),
    )

    call_kwargs = provider.client.aio.models.generate_content.await_args.kwargs
    config = call_kwargs["config"]
    assert getattr(config, "tools", []) == ["web-search-tool"]


@pytest.mark.asyncio
async def test_google_generate_prefers_web_search_when_custom_tools_exist(monkeypatch):
    provider = get_mocked_google()

    mock_response = MagicMock()
    text_part = MagicMock()
    text_part.text = "ok"
    text_part.function_call = None
    mock_response.candidates[0].content.parts = [text_part]
    mock_response.usage_metadata.prompt_token_count = 1
    mock_response.usage_metadata.candidates_token_count = 1
    provider.client.aio.models.generate_content.return_value = mock_response

    monkeypatch.setattr(provider, "_build_web_search_tool", lambda types: "web-only")
    provider._tool_formatter.format_for_native_fc = MagicMock(return_value=["fn-tool"])

    await provider.generate(
        [{"role": "user", "content": "search and act"}],
        tools=[{"name": "read_file", "description": "", "parameters": {}}],
        request_options=ProviderRequestOptions(provider_managed_tools=["web_search"]),
    )

    call_kwargs = provider.client.aio.models.generate_content.await_args.kwargs
    config = call_kwargs["config"]
    assert getattr(config, "tools", []) == ["web-only"]
    provider._tool_formatter.format_for_native_fc.assert_not_called()


@pytest.mark.asyncio
async def test_google_generate_wraps_plain_text_to_notify_user_json_in_web_mode():
    provider = get_mocked_google()

    mock_response = MagicMock()
    text_part = MagicMock()
    text_part.text = "The winner is Team X."
    text_part.function_call = None
    mock_response.candidates[0].content.parts = [text_part]
    mock_response.usage_metadata.prompt_token_count = 1
    mock_response.usage_metadata.candidates_token_count = 1
    provider.client.aio.models.generate_content.return_value = mock_response

    res = await provider.generate(
        [{"role": "user", "content": "who won?"}],
        request_options=ProviderRequestOptions(provider_managed_tools=["web_search"]),
    )

    payload = json.loads(res.text_content)
    assert payload["decision"]["type"] == "notify_user"
    assert "winner is Team X" in payload["decision"]["message"]


def test_google_coerce_to_notify_user_json_preserves_valid_json():
    valid = '{"title":"Done","thought":"ok","decision":{"type":"notify_user","message":"hello"}}'
    assert GoogleProvider._coerce_to_notify_user_json(valid) == valid
