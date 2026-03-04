# Multi-AI Provider Architecture

## Overview
A modern AI CLI must never be locked into a single vendor. Users need flexibility to use OpenAI/Anthropic for complex reasoning, Gemini for deep context windows, and local models (Ollama, LM Studio) for privacy and cost savings.

The **Multi-AI Provider Handler** isolates the core Agent logic from the different API structures using the **Adapter Pattern**. Every provider converts its API into a single `LLMResponse` object. The Agent never knows which provider is doing the thinking.

---

## 1. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Pattern** | Adapter (one interface, many implementations) | Agent code is provider-agnostic. Swap providers via config. |
| **Tool Calling** | Hybrid: Native FC for capable providers, XML fallback for others | Best of both worlds. Zero schema errors with native FC. Universal compatibility with XML. |
| **Streaming** | Stream text only, buffer tool calls | TUI shows progressive thinking. Tool calls arrive complete and validated. |
| **Cost Tracking** | Provider-level estimation with pricing table | Per-call cost in `LLMResponse`. Immediate. No external service. |
| **Retry** | `safe_generate()` wraps `generate()` with retry engine | Retry logic from `04_error_handling.md`. Provider just does the API call. |
| **Registration** | TOML-based via Config Management | Users add custom providers without code changes. See `02_config_management.md`. |

---

## 2. The Normalized Response Object

All providers convert their API-specific responses into this single dataclass:

```python
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum, auto


class ToolCallMode(Enum):
    """How tool calls were delivered in this response."""
    NATIVE = auto()   # Structured JSON from native FC API
    XML    = auto()   # Parsed from XML <action> tags in text


@dataclass
class ToolCall:
    """A single tool invocation extracted from the LLM response."""
    tool_name: str
    arguments: Dict[str, Any]
    mode: ToolCallMode              # How this tool call was obtained
    native_call_id: str = ""        # Provider's call ID (for native FC response pairing)


@dataclass
class LLMResponse:
    """
    Normalized response from any LLM provider.
    The Agent and Schema Validator only ever see this object.
    """
    # Raw text content from the model (may contain <thinking> tags)
    text_content: str = ""
    
    # Structured tool calls (populated by native FC providers)
    # For XML mode, this stays empty — Schema Validator parses them from text_content
    tool_calls: List[ToolCall] = field(default_factory=list)
    
    # Whether the response used native FC or XML prompting
    tool_mode: ToolCallMode = ToolCallMode.XML
    
    # Token usage metadata
    input_tokens: int = 0
    output_tokens: int = 0
    
    # Cost estimation (USD)
    cost_usd: float = 0.0
    
    # Provider metadata
    model: str = ""
    provider: str = ""
    
    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0
    
    @property
    def is_final_answer(self) -> bool:
        """True if the response contains a final answer (no tool calls)."""
        return not self.has_tool_calls and "<final_answer>" in self.text_content
```

---

## 3. The `BaseLLMProvider` Interface

```python
from abc import ABC, abstractmethod
from typing import List, AsyncGenerator


class BaseLLMProvider(ABC):
    """
    Abstract adapter for communicating with different LLM backends.
    
    Handles:
    - Payload translation (internal messages → provider-specific API format)
    - Tool mode selection (native FC or prompt injection)
    - Response normalization (provider response → LLMResponse)
    - Cost estimation
    
    Does NOT handle:
    - Retries (handled by safe_generate() using the retry engine)
    - Token counting (handled by BaseTokenCounter in memory management)
    """
    
    def __init__(self, model_name: str, api_key: str | None = None,
                 base_url: str | None = None):
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self._tool_formatter = self._create_tool_formatter()
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider identifier (e.g., 'openai', 'anthropic', 'google')."""
        pass
    
    @property
    @abstractmethod
    def supports_native_tools(self) -> bool:
        """
        Whether this provider supports native function calling.
        If True: tools are sent as API parameters, responses contain structured tool calls.
        If False: tools are injected into the system prompt, responses use XML <action> tags.
        """
        pass
    
    @abstractmethod
    async def generate(
        self,
        context: List[dict],
        tools: List[dict] | None = None,
        max_tokens: int = 4096
    ) -> LLMResponse:
        """
        Make a single API call and return a normalized LLMResponse.
        
        Args:
            context:    The Agent's working memory (list of message dicts).
            tools:      Tool definitions. The provider decides HOW to deliver them.
            max_tokens: Max tokens for the response (from TokenBudget.response_reserve).
        
        Raises:
            ProviderAPIError: On any API error (caught by safe_generate for retry).
        """
        pass
    
    @abstractmethod
    async def stream(
        self,
        context: List[dict],
        tools: List[dict] | None = None,
        max_tokens: int = 4096
    ) -> AsyncGenerator[str, None]:
        """
        Yield text chunks as they arrive from the API.
        
        Streaming strategy:
        - Text content (including <thinking>) is yielded chunk by chunk
        - Tool calls are NOT streamed — they are buffered internally
        - After the stream ends, call get_buffered_response() for the complete LLMResponse
        
        The TUI subscribes to the stream for progressive thinking display.
        """
        pass
    
    @abstractmethod
    def get_buffered_response(self) -> LLMResponse:
        """
        Return the complete LLMResponse after streaming finishes.
        Contains the full text, tool calls, and usage metadata.
        """
        pass
    
    @abstractmethod
    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost in USD for a single API call."""
        pass
    
    @abstractmethod
    def _create_tool_formatter(self) -> "BaseToolFormatter":
        """Create the provider-specific tool formatter."""
        pass
    
    # ── safe_generate: Retry Wrapper ─────────────────────────
    
    async def safe_generate(
        self,
        context: List[dict],
        tools: List[dict] | None = None,
        max_tokens: int = 4096,
        max_retries: int = 3
    ) -> LLMResponse:
        """
        Wraps generate() with the retry engine from 04_error_handling.md.
        
        Handles:
        - Rate limiting (429) → exponential backoff
        - Server errors (500/503) → retry with jitter
        - Auth errors (401/403) → fail immediately (no retry)
        - Context too long (400) → fail immediately (trigger compaction)
        """
        from core.error_handling import RetryEngine, RetryableError
        
        retry_engine = RetryEngine(
            max_retries=max_retries,
            base_delay=1.0,
            max_delay=30.0,
            backoff_factor=2.0
        )
        
        async def _attempt():
            try:
                return await self.generate(context, tools, max_tokens)
            except Exception as e:
                raise self._classify_error(e)
        
        return await retry_engine.execute(_attempt)
    
    def _classify_error(self, error: Exception) -> Exception:
        """
        Convert provider-specific errors into our error hierarchy.
        See 04_error_handling.md for the full taxonomy.
        """
        error_str = str(error).lower()
        
        if "rate_limit" in error_str or "429" in error_str:
            return RetryableError(f"Rate limited: {error}", retry_after=60)
        elif "500" in error_str or "503" in error_str or "overloaded" in error_str:
            return RetryableError(f"Server error: {error}")
        elif "401" in error_str or "403" in error_str or "invalid_api_key" in error_str:
            return FatalProviderError(f"Authentication failed: {error}")
        elif "context_length" in error_str or "too many tokens" in error_str:
            return ContextLengthExceededError(f"Context too long: {error}")
        else:
            return RecoverableError(f"Provider error: {error}")
```

---

## 4. The `BaseToolFormatter`

```python
from abc import ABC, abstractmethod
from typing import List, Any


class BaseToolFormatter(ABC):
    """
    Converts internal BaseTool definitions into provider-specific formats.
    Each provider implementation translates tools into its API's expected schema.
    """
    
    @abstractmethod
    def format_for_native_fc(self, tools: List[dict]) -> Any:
        """
        Convert tool definitions to the provider's native function calling format.
        
        Input: List of dicts from ToolRegistry.get_definitions_for_llm()
               Each dict has: name, description, parameters (JSON Schema from Pydantic)
        
        Output: Provider-specific format (OpenAI tools array, Anthropic tools, etc.)
        """
        pass
    
    @abstractmethod
    def format_for_prompt_injection(self, tools: List[dict]) -> str:
        """
        Convert tool definitions to a text block for system prompt injection.
        Used for providers that don't support native FC (Ollama, local models).
        
        Output: A formatted string listing all tools with their arguments,
                plus instructions on how to call them via <action> XML tags.
        """
        pass
```

---

## 5. Hybrid Tool Calling: Native FC + XML Prompting

### The Design Decision
Each provider declares `supports_native_tools`. The Agent loop and Schema Validator adapt automatically:

| Provider | `supports_native_tools` | Tool Delivery | Tool Calls Returned As |
|---|---|---|---|
| OpenAI (GPT-4o, etc.) | `True` | Native FC API parameters | Structured `tool_calls` JSON |
| Anthropic (Claude 3.5, etc.) | `True` | Native `tools` parameter | Structured `tool_use` blocks |
| Gemini / Vertex | `True` | Native function declarations | Structured `function_call` |
| Ollama / Local models | `False` | Injected into system prompt | XML `<action>` tags in text |

### The `<thinking>` Consistency Rule
Regardless of tool calling mode, **all providers emit `<thinking>` tags in their text content**:

- **Native FC providers**: System prompt instructs the model to output `<thinking>` before making tool calls. Anthropic and OpenAI support returning text AND tool calls in the same response.
- **XML providers**: Model outputs `<thinking>` and `<action>` entirely in text.

This ensures one consistent parsing path for the TUI streaming renderer.

---

## 6. Streaming Architecture

### Text Streaming + Tool Call Buffering

```
API Response Stream:

  Chunk 1: "<thinking>\nLet me "
  Chunk 2: "analyze the code"
  Chunk 3: "base...\n</thinking>"
  Chunk 4: [tool_call: read_file(path="app.py")]   ← NOT streamed to TUI
  
  ┌─────────────────────────────┐
  │ TUI receives chunks 1-3     │  → Progressive thinking display
  │ (text only, real-time)       │
  └─────────────────────────────┘
  
  ┌─────────────────────────────┐
  │ get_buffered_response()      │  → Complete LLMResponse with
  │ (called after stream ends)   │     text + tool_calls + usage
  └─────────────────────────────┘
```

### Streaming Implementation Pattern

```python
class AnthropicProvider(BaseLLMProvider):
    
    async def stream(
        self, context: List[dict], tools: List[dict] | None = None,
        max_tokens: int = 4096
    ) -> AsyncGenerator[str, None]:
        """
        Stream text content. Buffer tool calls for get_buffered_response().
        """
        self._buffered_text = []
        self._buffered_tool_calls = []
        self._buffered_usage = {"input": 0, "output": 0}
        
        system_msg, chat_history = self._split_system(context)
        
        kwargs = {
            "model": self.model_name,
            "system": system_msg,
            "messages": chat_history,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = self._tool_formatter.format_for_native_fc(tools)
        
        async with self.client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        # Stream text to TUI
                        chunk = event.delta.text
                        self._buffered_text.append(chunk)
                        yield chunk
                    
                    elif event.delta.type == "input_json_delta":
                        # Buffer tool call arguments (don't stream)
                        pass
                
                elif event.type == "content_block_stop":
                    if hasattr(event, "content_block") and event.content_block.type == "tool_use":
                        self._buffered_tool_calls.append(ToolCall(
                            tool_name=event.content_block.name,
                            arguments=event.content_block.input,
                            mode=ToolCallMode.NATIVE,
                            native_call_id=event.content_block.id
                        ))
                
                elif event.type == "message_delta":
                    self._buffered_usage["output"] = event.usage.output_tokens
            
            # Capture input tokens from final message
            final = await stream.get_final_message()
            self._buffered_usage["input"] = final.usage.input_tokens
    
    def get_buffered_response(self) -> LLMResponse:
        text = "".join(self._buffered_text)
        cost = self.estimate_cost(
            self._buffered_usage["input"],
            self._buffered_usage["output"]
        )
        return LLMResponse(
            text_content=text,
            tool_calls=self._buffered_tool_calls,
            tool_mode=ToolCallMode.NATIVE,
            input_tokens=self._buffered_usage["input"],
            output_tokens=self._buffered_usage["output"],
            cost_usd=cost,
            model=self.model_name,
            provider="anthropic"
        )
```

---

## 7. Cost Tracking

Each provider implements `estimate_cost()` using a pricing table:

```python
# Pricing per 1M tokens (USD), updated periodically
PRICING_TABLE = {
    # OpenAI
    "gpt-4o":           {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":      {"input": 0.15,  "output": 0.60},
    "o1":               {"input": 15.00, "output": 60.00},
    "o1-mini":          {"input": 3.00,  "output": 12.00},
    
    # Anthropic
    "claude-3-5-sonnet-20241022": {"input": 3.00,  "output": 15.00},
    "claude-3-5-haiku-20241022":  {"input": 0.80,  "output": 4.00},
    "claude-3-opus-20240229":     {"input": 15.00, "output": 75.00},
    
    # Google
    "gemini-2.0-flash":  {"input": 0.075, "output": 0.30},
    "gemini-2.0-pro":    {"input": 1.25,  "output": 5.00},
    
    # Local models (free)
    "llama-3-8b":    {"input": 0.0, "output": 0.0},
    "codestral":     {"input": 0.0, "output": 0.0},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """
    Estimate API cost in USD.
    Returns 0.0 for unknown models (safe default).
    """
    pricing = PRICING_TABLE.get(model, {"input": 0.0, "output": 0.0})
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return round(input_cost + output_cost, 6)
```

### Session-Level Cost Aggregation

The Session Info panel accumulates costs across all LLM calls in the session:

```python
@dataclass
class SessionMetrics:
    """Extended from 04_session_persistence.md."""
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    llm_calls: int = 0
    
    def record_call(self, response: LLMResponse) -> None:
        self.total_cost_usd += response.cost_usd
        self.total_input_tokens += response.input_tokens
        self.total_output_tokens += response.output_tokens
        self.llm_calls += 1
```

---

## 8. Concrete Adapters

### OpenAI Provider

```python
class OpenAIProvider(BaseLLMProvider):
    
    def __init__(self, model_name: str, api_key: str | None = None,
                 base_url: str | None = None):
        super().__init__(model_name, api_key, base_url)
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url  # Supports custom endpoints
        )
    
    @property
    def provider_name(self) -> str:
        return "openai"
    
    @property
    def supports_native_tools(self) -> bool:
        return True
    
    async def generate(self, context, tools=None, max_tokens=4096) -> LLMResponse:
        kwargs = {
            "model": self.model_name,
            "messages": context,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = self._tool_formatter.format_for_native_fc(tools)
        
        response = await self.client.chat.completions.create(**kwargs)
        return self._normalize(response)
    
    def _normalize(self, response) -> LLMResponse:
        choice = response.choices[0]
        msg = choice.message
        
        tool_calls = []
        if msg.tool_calls:
            import json
            for tc in msg.tool_calls:
                tool_calls.append(ToolCall(
                    tool_name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                    mode=ToolCallMode.NATIVE,
                    native_call_id=tc.id
                ))
        
        cost = self.estimate_cost(
            response.usage.prompt_tokens,
            response.usage.completion_tokens
        )
        
        return LLMResponse(
            text_content=msg.content or "",
            tool_calls=tool_calls,
            tool_mode=ToolCallMode.NATIVE if tool_calls else ToolCallMode.XML,
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            cost_usd=cost,
            model=self.model_name,
            provider="openai"
        )
    
    def estimate_cost(self, input_tokens, output_tokens) -> float:
        return estimate_cost(self.model_name, input_tokens, output_tokens)
    
    def _create_tool_formatter(self):
        return OpenAIToolFormatter()


class OpenAIToolFormatter(BaseToolFormatter):
    def format_for_native_fc(self, tools: List[dict]) -> List[dict]:
        """Convert to OpenAI function calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"]  # Already JSON Schema from Pydantic
                }
            }
            for t in tools
        ]
    
    def format_for_prompt_injection(self, tools: List[dict]) -> str:
        # OpenAI supports native FC, so this is rarely used
        return XMLToolFormatter().format_for_prompt_injection(tools)
```

### Anthropic Provider

```python
class AnthropicProvider(BaseLLMProvider):
    
    def __init__(self, model_name: str, api_key: str | None = None,
                 base_url: str | None = None):
        super().__init__(model_name, api_key, base_url)
        from anthropic import AsyncAnthropic
        self.client = AsyncAnthropic(api_key=api_key)
    
    @property
    def provider_name(self) -> str:
        return "anthropic"
    
    @property
    def supports_native_tools(self) -> bool:
        return True
    
    async def generate(self, context, tools=None, max_tokens=4096) -> LLMResponse:
        system_msg, chat_history = self._split_system(context)
        
        kwargs = {
            "model": self.model_name,
            "system": system_msg,
            "messages": chat_history,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = self._tool_formatter.format_for_native_fc(tools)
        
        response = await self.client.messages.create(**kwargs)
        return self._normalize(response)
    
    def _split_system(self, context: List[dict]):
        """Anthropic requires system message separate from messages array."""
        system = ""
        messages = []
        for msg in context:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                messages.append(msg)
        return system, messages
    
    def _normalize(self, response) -> LLMResponse:
        text_parts = []
        tool_calls = []
        
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    tool_name=block.name,
                    arguments=block.input,
                    mode=ToolCallMode.NATIVE,
                    native_call_id=block.id
                ))
        
        cost = self.estimate_cost(
            response.usage.input_tokens,
            response.usage.output_tokens
        )
        
        return LLMResponse(
            text_content="\n".join(text_parts),
            tool_calls=tool_calls,
            tool_mode=ToolCallMode.NATIVE,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=cost,
            model=self.model_name,
            provider="anthropic"
        )
    
    def estimate_cost(self, input_tokens, output_tokens) -> float:
        return estimate_cost(self.model_name, input_tokens, output_tokens)
    
    def _create_tool_formatter(self):
        return AnthropicToolFormatter()


class AnthropicToolFormatter(BaseToolFormatter):
    def format_for_native_fc(self, tools: List[dict]) -> List[dict]:
        """Convert to Anthropic tool format."""
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["parameters"]  # JSON Schema from Pydantic
            }
            for t in tools
        ]
    
    def format_for_prompt_injection(self, tools: List[dict]) -> str:
        return XMLToolFormatter().format_for_prompt_injection(tools)
```

### Google / Vertex Provider

```python
class GoogleProvider(BaseLLMProvider):
    
    def __init__(self, model_name: str, api_key: str | None = None,
                 base_url: str | None = None):
        super().__init__(model_name, api_key, base_url)
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)
    
    @property
    def provider_name(self) -> str:
        return "google"
    
    @property
    def supports_native_tools(self) -> bool:
        return True
    
    async def generate(self, context, tools=None, max_tokens=4096) -> LLMResponse:
        # Convert messages to Gemini format
        gemini_history = self._convert_messages(context)
        
        config = {"max_output_tokens": max_tokens}
        
        tool_config = None
        if tools:
            tool_config = self._tool_formatter.format_for_native_fc(tools)
        
        response = await self.model.generate_content_async(
            gemini_history,
            generation_config=config,
            tools=tool_config
        )
        
        return self._normalize(response)
    
    def _convert_messages(self, context: List[dict]) -> List:
        """Convert OpenAI-style messages to Gemini format."""
        from google.generativeai.types import ContentDict
        
        converted = []
        for msg in context:
            role = "model" if msg["role"] == "assistant" else msg["role"]
            if role == "system":
                role = "user"  # Gemini doesn't have a system role in history
            converted.append(ContentDict(role=role, parts=[msg["content"]]))
        return converted
    
    def _normalize(self, response) -> LLMResponse:
        text = ""
        tool_calls = []
        
        for part in response.candidates[0].content.parts:
            if hasattr(part, "text") and part.text:
                text += part.text
            elif hasattr(part, "function_call"):
                fc = part.function_call
                tool_calls.append(ToolCall(
                    tool_name=fc.name,
                    arguments=dict(fc.args),
                    mode=ToolCallMode.NATIVE
                ))
        
        input_tokens = response.usage_metadata.prompt_token_count
        output_tokens = response.usage_metadata.candidates_token_count
        cost = self.estimate_cost(input_tokens, output_tokens)
        
        return LLMResponse(
            text_content=text,
            tool_calls=tool_calls,
            tool_mode=ToolCallMode.NATIVE,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            model=self.model_name,
            provider="google"
        )
    
    def estimate_cost(self, input_tokens, output_tokens) -> float:
        return estimate_cost(self.model_name, input_tokens, output_tokens)
    
    def _create_tool_formatter(self):
        return GoogleToolFormatter()
```

### OpenAI-Compatible Provider (Ollama, LM Studio, vLLM)

```python
class OpenAICompatibleProvider(BaseLLMProvider):
    """
    Adapter for any OpenAI-compatible API (Ollama, LM Studio, vLLM, etc.).
    Defaults to XML prompting (no native FC) unless overridden.
    
    Registered via TOML:
    [providers.local_ollama]
    adapter_type = "openai_compatible"
    base_url = "http://localhost:11434/v1"
    models = ["llama-3-8b", "codestral"]
    """
    
    def __init__(self, model_name: str, api_key: str | None = None,
                 base_url: str = "http://localhost:11434/v1",
                 native_tools: bool = False):
        super().__init__(model_name, api_key, base_url)
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(
            api_key=api_key or "ollama",  # Ollama doesn't need a key
            base_url=base_url
        )
        self._native_tools = native_tools
    
    @property
    def provider_name(self) -> str:
        return "openai_compatible"
    
    @property
    def supports_native_tools(self) -> bool:
        return self._native_tools
    
    async def generate(self, context, tools=None, max_tokens=4096) -> LLMResponse:
        if tools and not self._native_tools:
            # Inject tools into system prompt as text
            tool_text = self._tool_formatter.format_for_prompt_injection(tools)
            context = self._inject_tools_into_system_prompt(context, tool_text)
            tools = None  # Don't send to API
        
        kwargs = {
            "model": self.model_name,
            "messages": context,
            "max_tokens": max_tokens,
        }
        if tools and self._native_tools:
            kwargs["tools"] = self._tool_formatter.format_for_native_fc(tools)
        
        response = await self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        
        return LLMResponse(
            text_content=choice.message.content or "",
            tool_calls=[],  # Schema Validator parses XML from text_content
            tool_mode=ToolCallMode.XML,
            model=self.model_name,
            provider="openai_compatible",
            cost_usd=0.0  # Local models are free
        )
    
    def _inject_tools_into_system_prompt(self, context, tool_text):
        """Append tool definitions to the system prompt for XML mode."""
        modified = []
        for msg in context:
            if msg["role"] == "system":
                modified.append({
                    "role": "system",
                    "content": msg["content"] + "\n\n" + tool_text
                })
            else:
                modified.append(msg)
        return modified
    
    def estimate_cost(self, input_tokens, output_tokens) -> float:
        return 0.0  # Local models are free
    
    def _create_tool_formatter(self):
        return XMLToolFormatter()
```

### XML Tool Formatter (Shared by XML-mode providers)

```python
class XMLToolFormatter(BaseToolFormatter):
    """
    Formats tools as text for system prompt injection.
    Used by providers without native FC (Ollama, local models).
    """
    
    def format_for_native_fc(self, tools: List[dict]) -> Any:
        raise NotImplementedError("XML formatter does not support native FC")
    
    def format_for_prompt_injection(self, tools: List[dict]) -> str:
        lines = ["## Available Tools\n"]
        lines.append("You MUST use these tools by outputting XML tags.\n")
        
        for tool in tools:
            lines.append(f"### {tool['name']}")
            lines.append(f"{tool['description']}\n")
            
            params = tool.get("parameters", {}).get("properties", {})
            required = tool.get("parameters", {}).get("required", [])
            
            if params:
                lines.append("Arguments:")
                for param_name, param_info in params.items():
                    req = "(required)" if param_name in required else "(optional)"
                    desc = param_info.get("description", "")
                    lines.append(f"  - {param_name} {req}: {desc}")
            
            lines.append("")
        
        lines.append("## Tool Call Format")
        lines.append("To call a tool, output:")
        lines.append("```")
        lines.append('<action>')
        lines.append('    <tool>tool_name</tool>')
        lines.append('    <args>{"param": "value"}</args>')
        lines.append('</action>')
        lines.append("```")
        lines.append("")
        lines.append("To give a final answer, output:")
        lines.append("```")
        lines.append("<final_answer>Your response here</final_answer>")
        lines.append("```")
        
        return "\n".join(lines)
```

---

## 9. Provider Factory (Unified with Config Management)

```python
from typing import Dict


# Adapter class registry
ADAPTER_TYPES: Dict[str, type] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "google": GoogleProvider,
    "openai_compatible": OpenAICompatibleProvider,
}


class ProviderManager:
    """
    Creates and caches LLM provider instances.
    Reads provider configurations from AgentSettings (TOML-based).
    See 02_config_management.md Section 6.
    """
    
    def __init__(self, settings: "AgentSettings"):
        self._settings = settings
        self._providers: Dict[str, BaseLLMProvider] = {}
        self._provider_configs = load_providers(settings)
    
    def get_provider(self, model_name: str) -> BaseLLMProvider:
        """
        Get or create a provider for the given model.
        Matches model name to a registered provider config.
        """
        if model_name in self._providers:
            return self._providers[model_name]
        
        # Find which provider owns this model
        for name, config in self._provider_configs.items():
            if model_name in config.models or model_name == config.default_model:
                provider = self._create_provider(config, model_name)
                self._providers[model_name] = provider
                return provider
        
        # Fallback: try to infer from model name prefix
        provider = self._infer_provider(model_name)
        self._providers[model_name] = provider
        return provider
    
    def _create_provider(
        self, config: "ProviderConfig", model_name: str
    ) -> BaseLLMProvider:
        """Instantiate a provider from its config."""
        adapter_cls = ADAPTER_TYPES.get(config.adapter_type)
        if not adapter_cls:
            raise ValueError(f"Unknown adapter type: {config.adapter_type}")
        
        api_key = self._settings.resolve_api_key(config.adapter_type)
        
        return adapter_cls(
            model_name=model_name,
            api_key=api_key,
            base_url=config.base_url
        )
    
    def _infer_provider(self, model_name: str) -> BaseLLMProvider:
        """Fallback: infer provider from model name prefix."""
        if model_name.startswith("gpt") or model_name.startswith("o1"):
            return OpenAIProvider(model_name, self._settings.resolve_api_key("openai"))
        elif model_name.startswith("claude"):
            return AnthropicProvider(model_name, self._settings.resolve_api_key("anthropic"))
        elif model_name.startswith("gemini"):
            return GoogleProvider(model_name, self._settings.resolve_api_key("google"))
        else:
            raise ValueError(
                f"Cannot infer provider for model '{model_name}'. "
                f"Register it in config.toml under [providers.*]."
            )
```

---

## 10. Integration with Agent Reasoning Loop

```python
# In BaseAgent.handle_task() (from 01_reasoning_loop.md):

# The agent uses safe_generate (with retry) — never raw generate
response: LLMResponse = await self.provider.safe_generate(
    context=self.memory.get_working_context(),
    tools=self.tool_executor.registry.get_definitions_for_llm(self.config.tools),
    max_tokens=self.memory.budget.response_reserve  # From TokenBudget
)

# Record cost
self.session_metrics.record_call(response)

# Schema validator handles both native FC and XML based on response.tool_mode
parsed = self.validator.parse_and_validate(response)
```

---

## 11. Testing Strategy

```python
import pytest

@pytest.mark.asyncio
async def test_anthropic_normalize_text_and_tool():
    """Anthropic response with text + tool_use should produce text + tool_calls."""
    provider = AnthropicProvider("claude-3-5-sonnet-20241022", api_key="test")
    
    # Mock response with text + tool_use content blocks
    response = provider._normalize(mock_anthropic_response)
    
    assert response.text_content  # Has thinking text
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].tool_name == "read_file"
    assert response.tool_mode == ToolCallMode.NATIVE

@pytest.mark.asyncio
async def test_ollama_returns_xml_mode():
    """Ollama provider should return raw text with XML tool mode."""
    provider = OpenAICompatibleProvider("llama-3-8b", base_url="http://localhost:11434/v1")
    
    response = await provider.generate(
        context=[{"role": "user", "content": "test"}],
        tools=[{"name": "read_file", "description": "Read a file", "parameters": {}}]
    )
    
    assert response.tool_mode == ToolCallMode.XML
    assert response.tool_calls == []  # XML parsing is done by Schema Validator

def test_cost_estimation():
    assert estimate_cost("gpt-4o", 1000, 500) == pytest.approx(0.0075, abs=0.001)
    assert estimate_cost("claude-3-5-sonnet-20241022", 1000, 500) == pytest.approx(0.0105, abs=0.001)
    assert estimate_cost("llama-3-8b", 10000, 5000) == 0.0  # Free
    assert estimate_cost("unknown-model", 1000, 500) == 0.0  # Safe default

def test_cost_accumulation():
    metrics = SessionMetrics()
    metrics.record_call(LLMResponse(input_tokens=1000, output_tokens=500, cost_usd=0.01))
    metrics.record_call(LLMResponse(input_tokens=2000, output_tokens=1000, cost_usd=0.02))
    
    assert metrics.total_cost_usd == 0.03
    assert metrics.llm_calls == 2
    assert metrics.total_input_tokens == 3000

@pytest.mark.asyncio
async def test_safe_generate_retries_on_rate_limit():
    """safe_generate should retry on 429 with exponential backoff."""
    call_count = 0
    
    class MockProvider(BaseLLMProvider):
        async def generate(self, context, tools=None, max_tokens=4096):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("rate_limit: 429 Too Many Requests")
            return LLMResponse(text_content="Success")
    
    provider = MockProvider("test-model")
    response = await provider.safe_generate(context=[], max_retries=3)
    
    assert response.text_content == "Success"
    assert call_count == 3

@pytest.mark.asyncio
async def test_safe_generate_fails_on_auth_error():
    """safe_generate should NOT retry on 401/403."""
    class MockProvider(BaseLLMProvider):
        async def generate(self, context, tools=None, max_tokens=4096):
            raise Exception("401: invalid_api_key")
    
    provider = MockProvider("test-model")
    with pytest.raises(FatalProviderError):
        await provider.safe_generate(context=[])

def test_provider_manager_creates_correct_adapter():
    settings = AgentSettings()
    manager = ProviderManager(settings)
    
    provider = manager.get_provider("claude-3-5-sonnet-20241022")
    assert isinstance(provider, AnthropicProvider)
    
    provider = manager.get_provider("gpt-4o")
    assert isinstance(provider, OpenAIProvider)

def test_xml_tool_formatter_output():
    formatter = XMLToolFormatter()
    tools = [{
        "name": "read_file",
        "description": "Read a file's contents",
        "parameters": {
            "properties": {"path": {"description": "File path to read"}},
            "required": ["path"]
        }
    }]
    
    output = formatter.format_for_prompt_injection(tools)
    assert "read_file" in output
    assert "<action>" in output
    assert "path (required)" in output
```
