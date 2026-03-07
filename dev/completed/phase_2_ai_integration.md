# Phase 2 — AI Provider Integration

## Goal
Connect the system to LLM APIs. Implement streaming, tool calling, cost tracking, and multi-provider support. After this phase, the system can send prompts and receive responses.

**Specs:** `04_utilities/01_ai_providers.md`
**Depends on:** Phase 1 (Config, Error Handling, Retry Engine)

---

## Sub-Phase 2.1 — Provider Abstractions
> Spec: `01_ai_providers.md` §1-3

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 2.1.1 | `BaseLLMProvider` ABC | Define `generate()`, `safe_generate()`, `stream_generate()` interfaces | 🔴 Critical |
| 2.1.2 | `LLMRequest` model | Pydantic model: messages, tools, temperature, max_tokens, model | 🔴 Critical |
| 2.1.3 | `LLMResponse` model | Pydantic model: text, tool_calls, usage, cost, provider metadata | 🔴 Critical |
| 2.1.4 | `BaseToolFormatter` ABC | Convert internal tool schemas to provider-specific format (OpenAI JSON / XML) | 🔴 Critical |
| 2.1.5 | Unit tests | Test models, serialization | 🔴 Critical |

**Deliverable:** `agent_cli/providers/base.py`, `agent_cli/providers/models.py`

---

## Sub-Phase 2.2 — Concrete Adapters
> Spec: `01_ai_providers.md` §4

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 2.2.1 | `OpenAIProvider` | GPT-4.5, GPT-5, o3 via `openai` SDK. Native function calling | 🔴 Critical |
| 2.2.2 | `AnthropicProvider` | Claude 4.6 (Sonnet/Opus) via `anthropic` SDK. Native tool_use blocks | 🔴 Critical |
| 2.2.3 | `GoogleProvider` | Gemini models via new `google-genai` SDK. Function declarations | 🟡 Medium |
| 2.2.4 | `OpenAICompatibleProvider` | Ollama, LM Studio via OpenAI-compatible API. XML tool fallback | 🟡 Medium |
| 2.2.5 | `XMLToolFormatter` | `<tool_call>` XML format for providers without native FC | 🟡 Medium |
| 2.2.6 | Integration tests | Test each adapter against live API (mocked for CI) | 🔴 Critical |

**Deliverable:** `agent_cli/providers/openai.py`, `agent_cli/providers/anthropic.py`, `agent_cli/providers/google.py`, `agent_cli/providers/openai_compat.py`

---

## Sub-Phase 2.3 — Streaming
> Spec: `01_ai_providers.md` §6

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 2.3.1 | Streaming interface | `stream_generate()` → async generator yielding `StreamChunk` | 🔴 Critical |
| 2.3.2 | Text streaming | Yield text chunks as they arrive, emit `AgentThinkingEvent` per chunk | 🔴 Critical |
| 2.3.3 | Tool call buffering | Buffer tool_use blocks until stream completes, then return as `LLMResponse` | 🔴 Critical |
| 2.3.4 | `<thinking>` detection | Parse `<thinking>` tags from stream, emit as `AgentThinkingEvent` | 🟡 Medium |
| 2.3.5 | Tests | Test streaming with mocked async generators | 🔴 Critical |

**Deliverable:** `agent_cli/providers/streaming.py`

---

## Sub-Phase 2.4 — Retry & Cost Tracking
> Spec: `01_ai_providers.md` §5, §7

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 2.4.1 | `safe_generate()` | Wrap `generate()` with retry engine from Phase 1 | 🔴 Critical |
| 2.4.2 | Error classification | Map HTTP status codes → RetryableError / FatalError / UserActionRequired | 🔴 Critical |
| 2.4.3 | Pricing table | Per-model input/output token costs (configurable via TOML) | 🟡 Medium |
| 2.4.4 | Per-call cost estimation | Calculate cost from token usage after each call | 🟡 Medium |
| 2.4.5 | Session cost aggregation | Running total. Emit `CostUpdateEvent` for TUI `/cost` display | 🟡 Medium |
| 2.4.6 | Tests | Test retry scenarios, cost calculation | 🟡 Medium |

**Deliverable:** Integrated into `base.py`, `agent_cli/providers/cost.py`

---

## Sub-Phase 2.5 — Provider Manager
> Spec: `01_ai_providers.md` §8

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 2.5.1 | `ProviderManager` factory | Instantiate and cache providers by model name | 🔴 Critical |
| 2.5.2 | TOML registration | Map model names → provider class + config in `[providers]` section | 🔴 Critical |
| 2.5.3 | Auto-inference | If model not in config, infer provider from model name prefix (gpt- → OpenAI) | 🟡 Medium |
| 2.5.4 | Hot-swap | `/model <name>` command switches provider at runtime | 🟡 Medium |
| 2.5.5 | Tests | Test factory lookup, caching, inference | 🔴 Critical |

**Deliverable:** `agent_cli/providers/manager.py`

---

## Completion Criteria

- [x] Can send a prompt to OpenAI/Anthropic/Google and receive a response
- [x] Streaming works: text chunks arrive progressively
- [x] Tool calls are properly formatted and parsed per provider
- [x] Retry engine handles rate limits and transient errors
- [x] Cost tracking accumulates per-call costs
- [x] ProviderManager resolves model → provider from config
- [x] All adapters have mocked integration tests
