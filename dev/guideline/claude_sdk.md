# Claude API — Python SDK Developer Guide

> **Sources:** [Anthropic Python SDK Docs](https://platform.claude.com/docs/en/api/sdks/python) · [Models Overview](https://platform.claude.com/docs/en/about-claude/models/overview) · [Pricing](https://platform.claude.com/docs/en/about-claude/pricing)  
> **SDK Version:** `anthropic >= 0.84.0` · **Python:** 3.9+

---

## 1. Installation

```bash
# Base SDK
pip install anthropic

# Optional extras
pip install anthropic[bedrock]   # AWS Bedrock support
pip install anthropic[vertex]    # Google Vertex AI support
pip install anthropic[aiohttp]   # Improved async performance
```

---

## 2. Model Reference

### 2.1 Current Models (as of March 2026)

| Model | API Model ID | Description |
|-------|-------------|-------------|
| **Claude Opus 4.6** | `claude-opus-4-6` | Most intelligent — agents, deep reasoning, 1M context |
| **Claude Sonnet 4.6** | `claude-sonnet-4-6` | Best speed/intelligence balance — daily coding & tasks |
| **Claude Haiku 4.5** | `claude-haiku-4-5-20251001` | Fastest, cheapest — high-throughput, near-frontier |

### 2.2 Context Windows & Output Limits

| Model | Standard Context | Extended Context (Beta) | Max Output Tokens |
|-------|-----------------|------------------------|-------------------|
| **Claude Opus 4.6** | 200,000 tokens | 1,000,000 tokens ¹ | 32,000 |
| **Claude Sonnet 4.6** | 200,000 tokens | 1,000,000 tokens ¹ | 64,000 |
| **Claude Haiku 4.5** | 200,000 tokens | — | 64,000 |

> ¹ 1M context window is currently in **beta** (usage tier 4 and above). Requires the `context-1m-2025-08-07` beta header. Long-context pricing applies to requests exceeding 200K input tokens.

### 2.3 Pricing (per 1 Million Tokens)

| Model | Input | Output | Cache Write (5 min) | Cache Read | Long Context (>200K input) |
|-------|-------|--------|---------------------|------------|---------------------------|
| **Claude Opus 4.6** | $5.00 | $25.00 | $6.25 | $0.50 | 2× standard rates |
| **Claude Opus 4.6 Fast Mode** ² | $30.00 | $150.00 | — | — | — |
| **Claude Sonnet 4.6** | $3.00 | $15.00 | $3.75 | $0.30 | $6.00 input / $22.50 output |
| **Claude Haiku 4.5** | $1.00 | $5.00 | $1.25 | $0.10 | — |

> ² **Fast Mode** (research preview) — available for Opus 4.6 only; significantly faster at 6× standard rates.  
> **Batch API** — 50% discount on all models for async/batch workloads.  
> **Data Residency (US-only routing)** — 1.1× multiplier on Opus 4.6 and newer via `inference_geo` parameter.

#### When to Use Each Model

- **Opus 4.6** → Complex reasoning, multi-agent orchestration, massive codebases, research tasks
- **Sonnet 4.6** → Everyday coding, agentic workflows, document comprehension, most production use cases
- **Haiku 4.5** → High-volume pipelines, real-time chat, simple Q&A, cost-sensitive workloads

---

## 3. Quick Start

### 3.1 Environment Setup

```bash
export ANTHROPIC_API_KEY="sk-ant-api03-..."
```

### 3.2 Basic Synchronous Call

```python
import os
from anthropic import Anthropic

client = Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY"),  # default — can be omitted
)

message = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[
        {"role": "user", "content": "Explain the difference between sync and async Python."}
    ],
)

print(message.content[0].text)
```

### 3.3 Async Client

```python
import os
import asyncio
from anthropic import AsyncAnthropic

client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

async def main() -> None:
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": "Hello, Claude"}],
    )
    print(message.content[0].text)

asyncio.run(main())
```

For improved async throughput, swap to the `aiohttp` backend:

```python
from anthropic import AsyncAnthropic, DefaultAioHttpClient

async with AsyncAnthropic(http_client=DefaultAioHttpClient()) as client:
    response = await client.messages.create(...)
```

---

## 4. Core Usage Patterns

### 4.1 System Prompt

```python
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=2048,
    system="You are a senior Python engineer. Be concise and precise.",
    messages=[
        {"role": "user", "content": "Review this function for edge cases."}
    ],
)
```

### 4.2 Multi-Turn Conversation

```python
messages = [
    {"role": "user",      "content": "What is Python's GIL?"},
    {"role": "assistant", "content": "The GIL (Global Interpreter Lock) is a mutex..."},
    {"role": "user",      "content": "How does asyncio work around it?"},
]

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=messages,
)
```

### 4.3 Streaming

```python
with client.messages.stream(
    model="claude-haiku-4-5-20251001",
    max_tokens=512,
    messages=[{"role": "user", "content": "Write a Python quicksort."}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

### 4.4 Vision (Image Input)

```python
import base64

with open("diagram.png", "rb") as f:
    image_data = base64.b64encode(f.read()).decode()

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_data,
                },
            },
            {"type": "text", "text": "Describe this architecture diagram."},
        ],
    }],
)
```

### 4.5 Tool Use (Function Calling)

```python
tools = [
    {
        "name": "get_weather",
        "description": "Get current weather for a location.",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City and country"},
                "unit":     {"type": "string", "enum": ["celsius", "fahrenheit"]},
            },
            "required": ["location"],
        },
    }
]

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    tools=tools,
    messages=[{"role": "user", "content": "What's the weather in Tokyo?"}],
)

# Check if Claude wants to call a tool
if response.stop_reason == "tool_use":
    for block in response.content:
        if block.type == "tool_use":
            print(f"Tool: {block.name}")
            print(f"Input: {block.input}")
```

### 4.6 Prompt Caching

Reduce costs on repeated long prompts by caching up to the marked breakpoint:

```python
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system=[
        {
            "type": "text",
            "text": "<large_document_or_codebase_here>",
            "cache_control": {"type": "ephemeral"},  # 5-min cache (default)
        }
    ],
    messages=[{"role": "user", "content": "Summarise the key classes."}],
)
```

> Cache writes cost **1.25×** base input price; cache reads cost **0.1×** base price.

### 4.7 1M Token Context Window (Beta)

Available for **Opus 4.6** and **Sonnet 4.6** (usage tier 4+):

```python
response = client.beta.messages.create(
    model="claude-opus-4-6",
    max_tokens=4096,
    messages=[{"role": "user", "content": "Analyse this entire codebase..."}],
    betas=["context-1m-2025-08-07"],
)
```

> Requests exceeding 200K input tokens are automatically charged at **long-context rates** (2×).

### 4.8 Batch Processing (50% Discount)

```python
import anthropic

batch = client.messages.batches.create(
    requests=[
        {
            "custom_id": "req-1",
            "params": {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 256,
                "messages": [{"role": "user", "content": "Translate to French: Hello"}],
            },
        },
        # ... more requests
    ]
)

print(f"Batch ID: {batch.id}")
```

---

## 5. Error Handling

```python
from anthropic import (
    Anthropic,
    APIError,
    RateLimitError,
    APIConnectionError,
    AuthenticationError,
)

client = Anthropic()

try:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": "Hello"}],
    )
except AuthenticationError:
    print("Invalid API key — check ANTHROPIC_API_KEY.")
except RateLimitError:
    print("Rate limited — back off and retry.")
except APIConnectionError:
    print("Network error — check your connection.")
except APIError as e:
    print(f"API error {e.status_code}: {e.message}")
```

---

## 6. Model Selection Quick Guide

```python
def pick_model(task: str) -> str:
    """Rough heuristic for model selection."""
    if task in ("simple_qa", "classification", "routing", "translation"):
        return "claude-haiku-4-5-20251001"   # cheapest, fastest
    elif task in ("coding", "analysis", "summarisation", "agents"):
        return "claude-sonnet-4-6"            # best price/performance
    else:  # deep reasoning, large codebase, multi-agent orchestration
        return "claude-opus-4-6"              # maximum intelligence
```

---

## 7. Complete Production Example

```python
import os
import time
from typing import Optional
from anthropic import Anthropic, RateLimitError, APIError

class ClaudeClient:
    def __init__(self, model: str = "claude-sonnet-4-6", max_retries: int = 3):
        self.client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model
        self.max_retries = max_retries

    def chat(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 1.0,
    ) -> str:
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        if system:
            kwargs["system"] = system
        if temperature != 1.0:
            kwargs["temperature"] = temperature

        for attempt in range(self.max_retries):
            try:
                response = self.client.messages.create(**kwargs)
                return response.content[0].text
            except RateLimitError:
                wait = 2 ** attempt
                print(f"Rate limited — retrying in {wait}s…")
                time.sleep(wait)
            except APIError as e:
                print(f"API error: {e}")
                raise
        raise RuntimeError("Max retries exceeded.")


if __name__ == "__main__":
    client = ClaudeClient(model="claude-sonnet-4-6")
    print(client.chat(
        prompt="Write a Python function to flatten a nested list.",
        system="You are a concise Python expert. Return only code.",
    ))
```

---

## 8. Key API Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `model` | `str` | Model ID (see Section 2.1) |
| `max_tokens` | `int` | Maximum output tokens (required) |
| `messages` | `list` | Conversation turns (`role` + `content`) |
| `system` | `str \| list` | System prompt or list with cache control |
| `temperature` | `float` | Randomness 0.0–1.0 (default: 1.0) |
| `top_p` | `float` | Nucleus sampling — use temperature **or** top_p, not both |
| `stream` | `bool` | Enable SSE streaming |
| `tools` | `list` | Tool/function definitions |
| `tool_choice` | `dict` | Force or auto tool selection |
| `stop_sequences` | `list[str]` | Custom stop tokens |
| `betas` | `list[str]` | Enable beta features (e.g., `context-1m-2025-08-07`) |

---

## 9. Useful Links

| Resource | URL |
|----------|-----|
| Python SDK Docs | https://platform.claude.com/docs/en/api/sdks/python |
| Models Overview | https://platform.claude.com/docs/en/about-claude/models/overview |
| Pricing | https://platform.claude.com/docs/en/about-claude/pricing |
| Prompt Caching | https://platform.claude.com/docs/en/build-with-claude/prompt-caching |
| Batch Processing | https://platform.claude.com/docs/en/build-with-claude/batch-processing |
| Tool Use | https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview |
| Anthropic Console | https://console.anthropic.com |
| PyPI Package | https://pypi.org/project/anthropic/ |
| GitHub SDK | https://github.com/anthropics/anthropic-sdk-python |