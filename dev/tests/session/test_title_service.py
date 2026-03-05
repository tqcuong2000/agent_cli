import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent_cli.core.infra.config.config_models import (
    CapabilitySpec,
    EffortCapabilitySpec,
    NativeToolsCapabilitySpec,
    WebSearchCapabilitySpec,
)
from agent_cli.core.runtime.session.base import Session
from agent_cli.core.runtime.session.title_service import SessionTitleService
from agent_cli.core.providers.base.base import BaseLLMProvider


@pytest.fixture
def mock_registry():
    class MockRegistry:
        def get_title_generation_defaults(self):
            return {"min_turns": 3, "max_words": 8}
        def get_prompt_template(self, name):
            if name == "title_generator":
                return "Prompt: {preview}"
            return ""
        def get_model_capabilities(self, model_name):
            _ = model_name
            return CapabilitySpec(
                native_tools=NativeToolsCapabilitySpec(supported=True),
                effort=EffortCapabilitySpec(supported=True, levels=["auto", "minimal", "low"]),
                web_search=WebSearchCapabilitySpec(supported=False, mode="none"),
            )
    return MockRegistry()


def test_should_generate_returns_false_on_turn_1(mock_registry):
    service = SessionTitleService(mock_registry)
    session = Session(session_id="123")
    session.messages = [{"role": "user", "content": "hello"}]
    
    assert not service.should_generate(session)


def test_should_generate_returns_true_at_min_turns(mock_registry):
    service = SessionTitleService(mock_registry)
    session = Session(session_id="123")
    session.messages = [
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "response 1"},
        {"role": "user", "content": "turn 2"},
        {"role": "assistant", "content": "response 2"},
        {"role": "user", "content": "turn 3"},
    ]
    
    assert service.should_generate(session)


def test_should_generate_returns_true_when_forced(mock_registry):
    service = SessionTitleService(mock_registry)
    session = Session(session_id="123")
    session.messages = [{"role": "user", "content": "only 1 turn"}]
    
    assert service.should_generate(session, force=True)


def test_should_generate_returns_false_if_already_named(mock_registry):
    service = SessionTitleService(mock_registry)
    session = Session(session_id="123", name="User custom title")
    session.messages = [
        {"role": "user", "content": "turn 1"},
        {"role": "user", "content": "turn 2"},
        {"role": "user", "content": "turn 3"},
        {"role": "user", "content": "turn 4"},
    ]
    
    assert not service.should_generate(session)


@pytest.mark.asyncio
async def test_generate_title_calls_provider(mock_registry):
    service = SessionTitleService(mock_registry)
    provider = AsyncMock(spec=BaseLLMProvider)
    provider.model_name = "gemini-2.5-flash"
    provider.safe_generate.return_value = SimpleNamespace(text_content="Cool New Feature")
    messages = [{"role": "user", "content": "Please implement the new cool feature"}]
    
    title = await service.generate_title(provider, messages)
    
    assert title == "Cool New Feature"
    assert provider.safe_generate.call_count == 1
    call_args = provider.safe_generate.call_args[1]
    prompt_text = call_args["context"][0]["content"]
    assert "Please implement the new cool feature" in prompt_text
    assert call_args["effort"] == "minimal"


@pytest.mark.asyncio
async def test_generate_title_falls_back_on_empty(mock_registry):
    service = SessionTitleService(mock_registry)
    provider = AsyncMock(spec=BaseLLMProvider)
    provider.model_name = "gemini-2.5-flash"
    provider.safe_generate.return_value = SimpleNamespace(text_content="   ")
    messages = [{"role": "user", "content": "hello"}]
    
    title = await service.generate_title(provider, messages)
    assert title == "Untitled session"


@pytest.mark.asyncio
async def test_generate_title_uses_auto_effort_when_model_does_not_support_effort(mock_registry):
    class NoEffortRegistry:
        def get_title_generation_defaults(self):
            return {"min_turns": 3, "max_words": 8}
        def get_prompt_template(self, name):
            if name == "title_generator":
                return "Prompt: {preview}"
            return ""
        def get_model_capabilities(self, model_name):
            _ = model_name
            return CapabilitySpec(
                native_tools=NativeToolsCapabilitySpec(supported=True),
                effort=EffortCapabilitySpec(supported=False, levels=["auto"]),
                web_search=WebSearchCapabilitySpec(supported=False, mode="none"),
            )

    service = SessionTitleService(NoEffortRegistry())
    provider = AsyncMock(spec=BaseLLMProvider)
    provider.model_name = "gpt-4.1-mini"
    provider.safe_generate.return_value = SimpleNamespace(text_content="Release Plan")
    messages = [{"role": "user", "content": "help me with release planning"}]

    title = await service.generate_title(provider, messages)

    assert title == "Release Plan"
    call_args = provider.safe_generate.call_args[1]
    assert call_args["effort"] == "auto"


def test_normalize_title_strips_markdown_and_newlines():
    # Quotes and bolding
    assert SessionTitleService.normalize_title("**Test Title**") == "Test Title"
    assert SessionTitleService.normalize_title('"A quoted title"') == "A quoted title"
    assert SessionTitleService.normalize_title("*Italics*") == "Italics"
    
    # Prefix
    assert SessionTitleService.normalize_title("Title: My Title") == "My Title"
    assert SessionTitleService.normalize_title("Session Title: Cool Stuff") == "Cool Stuff"
    
    # Newlines
    assert SessionTitleService.normalize_title("Line 1\nLine 2") == "Line 1"
    
    # Max words (8 by default from splitting)
    long_title = "one two three four five six seven eight nine ten"
    assert SessionTitleService.normalize_title(long_title) == "one two three four five six seven eight"
