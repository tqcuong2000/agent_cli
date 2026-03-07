"""
Unit tests for the OpenAIProvider adapter.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.base.models import ProviderRequestOptions, ToolCallMode
from agent_cli.core.providers.adapters.openai_provider import OpenAIProvider


def _mock_chat_response(message: str = "from chat") -> MagicMock:
    response = MagicMock()
    msg = response.choices[0].message
    msg.content = (
        '{"title":"Fallback","thought":"ok","decision":{"type":"notify_user","message":"'
        + message
        + '"}}'
    )
    msg.tool_calls = []
    response.choices[0].finish_reason = "stop"
    response.usage.prompt_tokens = 2
    response.usage.completion_tokens = 3
    return response


def get_mocked_openai():
    """Returns a mocked OpenAIProvider."""
    provider = OpenAIProvider(
        "gpt-5",
        api_key="sk-test",
        data_registry=DataRegistry(),
    )
    # Mock the client
    provider.client = MagicMock()
    provider.client.chat.completions.create = AsyncMock()
    return provider


def get_mocked_azure(
    data_registry: DataRegistry | None = None,
    *,
    model_name: str = "azure-gpt-4.1",
    api_surface: str = "responses_api",
    api_profile: dict | None = None,
):
    """Returns an OpenAIProvider configured with Azure runtime identity."""
    provider = OpenAIProvider(
        model_name,
        api_key="az-test",
        api_surface=api_surface,
        api_profile=api_profile,
        data_registry=data_registry or DataRegistry(),
    )
    provider._runtime_provider_name = "azure"
    provider.client = MagicMock()
    provider.client.chat.completions.create = AsyncMock()
    provider.client.responses.create = AsyncMock()
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


@pytest.mark.asyncio
async def test_azure_generate_uses_responses_api_for_web_search():
    provider = get_mocked_azure()

    mock_response = MagicMock()
    mock_response.output_text = "Super Bowl winner was ... "
    mock_response.usage.input_tokens = 12
    mock_response.usage.output_tokens = 9
    provider.client.responses.create.return_value = mock_response

    res = await provider.generate(
        [
            {"role": "system", "content": "Return concise answer"},
            {"role": "user", "content": "who won?"},
        ],
        request_options=ProviderRequestOptions(provider_managed_tools=["web_search"]),
    )

    provider.client.responses.create.assert_awaited_once()
    provider.client.chat.completions.create.assert_not_awaited()

    call_kwargs = provider.client.responses.create.await_args.kwargs
    assert call_kwargs["model"] == "azure-gpt-4.1"
    assert call_kwargs["tools"] == [{"type": "web_search_preview"}]
    assert "instructions" in call_kwargs
    assert "input" in call_kwargs

    payload = json.loads(res.text_content)
    assert payload["decision"]["type"] == "notify_user"
    assert "Super Bowl winner" in payload["decision"]["message"]
    assert res.provider == "azure"
    assert res.input_tokens == 12
    assert res.output_tokens == 9


@pytest.mark.asyncio
async def test_azure_stream_web_search_falls_back_to_single_chunk():
    provider = get_mocked_azure()

    mock_response = MagicMock()
    mock_response.output_text = "Answer from web search."
    mock_response.usage.input_tokens = 5
    mock_response.usage.output_tokens = 3
    provider.client.responses.create.return_value = mock_response

    chunks = []
    async for chunk in provider.stream(
        [{"role": "user", "content": "hi"}],
        request_options=ProviderRequestOptions(provider_managed_tools=["web_search"]),
    ):
        chunks.append(chunk)

    assert len(chunks) == 2
    assert chunks[0].text
    assert chunks[1].is_final
    assert chunks[1].usage == {"input_tokens": 5, "output_tokens": 3}


@pytest.mark.asyncio
async def test_azure_generate_falls_back_when_responses_api_unsupported():
    provider = get_mocked_azure()
    provider.client.responses.create.side_effect = Exception(
        "Error code: 400 - {'error': {'message': 'This model is not supported by Responses API.', 'type': 'invalid_request_error'}}"
    )

    provider.client.chat.completions.create.return_value = _mock_chat_response()

    res = await provider.generate(
        [{"role": "user", "content": "who won?"}],
        request_options=ProviderRequestOptions(provider_managed_tools=["web_search"]),
    )

    provider.client.responses.create.assert_awaited_once()
    provider.client.chat.completions.create.assert_awaited_once()
    assert json.loads(res.text_content)["decision"]["message"] == "from chat"


@pytest.mark.asyncio
async def test_azure_generate_falls_back_when_unsupported_error_is_body_only():
    provider = get_mocked_azure()

    class BodyOnlyUnsupportedError(Exception):
        status_code = 400
        body = {
            "error": {
                "message": "This model is not supported by Responses API.",
                "type": "invalid_request_error",
            }
        }

        def __str__(self) -> str:
            return "Error code: 400"

    provider.client.responses.create.side_effect = BodyOnlyUnsupportedError()
    provider.client.chat.completions.create.return_value = _mock_chat_response(
        "body-only"
    )

    res = await provider.generate(
        [{"role": "user", "content": "who won?"}],
        request_options=ProviderRequestOptions(provider_managed_tools=["web_search"]),
    )

    provider.client.responses.create.assert_awaited_once()
    provider.client.chat.completions.create.assert_awaited_once()
    assert json.loads(res.text_content)["decision"]["message"] == "body-only"


@pytest.mark.asyncio
async def test_azure_generate_disables_responses_after_first_unsupported_error():
    provider = get_mocked_azure()
    provider.client.responses.create.side_effect = Exception(
        "Error code: 400 - {'error': {'message': 'This model is not supported by Responses API.', 'type': 'invalid_request_error'}}"
    )
    provider.client.chat.completions.create.return_value = _mock_chat_response("first")

    first = await provider.generate(
        [{"role": "user", "content": "first"}],
        request_options=ProviderRequestOptions(provider_managed_tools=["web_search"]),
    )
    assert json.loads(first.text_content)["decision"]["message"] == "first"
    provider.client.responses.create.assert_awaited_once()

    provider.client.responses.create.reset_mock()
    provider.client.chat.completions.create.reset_mock()
    provider.client.chat.completions.create.return_value = _mock_chat_response("second")

    second = await provider.generate(
        [{"role": "user", "content": "second"}],
        request_options=ProviderRequestOptions(provider_managed_tools=["web_search"]),
    )
    provider.client.responses.create.assert_not_awaited()
    provider.client.chat.completions.create.assert_awaited_once()
    assert json.loads(second.text_content)["decision"]["message"] == "second"


@pytest.mark.asyncio
async def test_azure_generate_reports_provider_error_for_explicit_web_search_request():
    provider = get_mocked_azure()
    provider.client.responses.create.side_effect = Exception(
        "Error code: 400 - {'error': {'message': 'This model is not supported by Responses API.', 'type': 'invalid_request_error'}}"
    )
    provider.client.chat.completions.create.return_value = _mock_chat_response(
        "should-not-run"
    )

    res = await provider.generate(
        [
            {
                "role": "user",
                "content": "Please use web search for latest Python version",
            }
        ],
        request_options=ProviderRequestOptions(provider_managed_tools=["web_search"]),
    )

    provider.client.responses.create.assert_awaited_once()
    provider.client.chat.completions.create.assert_not_awaited()
    payload = json.loads(res.text_content)
    assert payload["title"] == "Web Search Unavailable"
    assert "System response:" in payload["decision"]["message"]


@pytest.mark.asyncio
async def test_azure_stream_reports_provider_error_for_explicit_web_search_request():
    provider = get_mocked_azure()
    provider.client.responses.create.side_effect = Exception(
        "Error code: 400 - {'error': {'message': 'This model is not supported by Responses API.', 'type': 'invalid_request_error'}}"
    )

    chunks = []
    async for chunk in provider.stream(
        [{"role": "user", "content": "web search this please"}],
        request_options=ProviderRequestOptions(provider_managed_tools=["web_search"]),
    ):
        chunks.append(chunk)

    provider.client.responses.create.assert_awaited_once()
    provider.client.chat.completions.create.assert_not_awaited()
    assert len(chunks) == 2
    assert chunks[0].text
    assert chunks[1].is_final


@pytest.mark.asyncio
async def test_azure_generate_records_web_search_observation_on_runtime_rejection():
    registry = DataRegistry()
    provider = get_mocked_azure(registry)
    provider.client.responses.create.side_effect = Exception(
        "Error code: 400 - {'error': {'message': 'This model is not supported by Responses API.', 'type': 'invalid_request_error'}}"
    )
    provider.client.chat.completions.create.return_value = _mock_chat_response(
        "fallback"
    )

    await provider.generate(
        [{"role": "user", "content": "who won?"}],
        request_options=ProviderRequestOptions(provider_managed_tools=["web_search"]),
    )

    snapshot = registry.get_capability_snapshot(
        provider="azure",
        model="azure-gpt-4.1",
        deployment_id="azure:azure-gpt-4.1",
    )
    assert snapshot.effective["web_search"].status == "unsupported"
    assert snapshot.effective["web_search"].source == "runtime"


@pytest.mark.asyncio
async def test_azure_generate_records_web_search_observation_on_runtime_success():
    registry = DataRegistry()
    provider = get_mocked_azure(registry)

    mock_response = MagicMock()
    mock_response.output_text = "Search result."
    mock_response.usage.input_tokens = 2
    mock_response.usage.output_tokens = 1
    provider.client.responses.create.return_value = mock_response

    await provider.generate(
        [{"role": "user", "content": "search now"}],
        request_options=ProviderRequestOptions(provider_managed_tools=["web_search"]),
    )

    snapshot = registry.get_capability_snapshot(
        provider="azure",
        model="azure-gpt-4.1",
        deployment_id="azure:azure-gpt-4.1",
    )
    assert snapshot.effective["web_search"].status == "supported"
    assert snapshot.effective["web_search"].source == "runtime"


@pytest.mark.asyncio
async def test_azure_web_search_mode_none_stays_on_chat_completions():
    provider = get_mocked_azure(api_surface="chat_completions")
    provider.client.chat.completions.create.return_value = _mock_chat_response(
        "chat-only"
    )

    res = await provider.generate(
        [{"role": "user", "content": "search now"}],
        request_options=ProviderRequestOptions(provider_managed_tools=["web_search"]),
    )

    provider.client.responses.create.assert_not_awaited()
    provider.client.chat.completions.create.assert_awaited_once()
    call_kwargs = provider.client.chat.completions.create.await_args.kwargs
    assert call_kwargs["extra_body"]["tools"] == [{"type": "web_search_preview"}]
    assert json.loads(res.text_content)["decision"]["message"] == "chat-only"


@pytest.mark.asyncio
async def test_azure_chat_completions_web_search_uses_profile_mutations():
    provider = get_mocked_azure(
        model_name="azure-grok-4-fast-reasoning",
        api_surface="chat_completions",
        api_profile={
            "web_search": {
                "chat_completions": {
                    "mutations": [
                        {"op": "append_model_suffix", "value": ":online"},
                        {
                            "op": "merge_body",
                            "value": {"plugins": [{"id": "web"}]},
                        },
                    ]
                }
            }
        },
    )
    provider.client.chat.completions.create.return_value = _mock_chat_response(
        "chat-with-profile"
    )

    res = await provider.generate(
        [{"role": "user", "content": "search now"}],
        request_options=ProviderRequestOptions(provider_managed_tools=["web_search"]),
    )

    provider.client.responses.create.assert_not_awaited()
    provider.client.chat.completions.create.assert_awaited_once()
    call_kwargs = provider.client.chat.completions.create.await_args.kwargs
    assert call_kwargs["model"] == "azure-grok-4-fast-reasoning:online"
    assert call_kwargs["extra_body"]["plugins"] == [{"id": "web"}]
    assert json.loads(res.text_content)["decision"]["message"] == "chat-with-profile"
