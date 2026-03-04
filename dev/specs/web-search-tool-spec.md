# Web Search Tool Specification

Status: Draft (Revised)
Date: 2026-03-04
Owner: Agent CLI Core

## 1. Objective

Add `web_search` as a provider-managed tool that can be assigned per agent via agent tool lists, while preserving the existing ReAct loop and session recording.

Primary outcomes:
- `web_search` is enabled by agent tool assignment, not manual per-session toggles.
- Provider-specific web search settings are data-driven defaults.
- Existing local tool execution (`ToolExecutor`) behavior remains unchanged.

## 2. Required Product Decisions

1. `web_search` is treated as a tool capability in agent configuration.
2. No `/web` command surface is introduced.
3. No session-level on/off toggle is required.
4. All defaults come from data files loaded through `DataRegistry` (not `AgentSettings` defaults).
5. Anthropic defaults must be:
- `max_uses = 10`
- `allowed_domains = []` (empty = allow all domains)

## 3. Provider Capability Research (Official Docs)

As of 2026-03-04:

### 3.1 Google Gen SDK
- Gemini supports Google Search grounding via `types.Tool(google_search=types.GoogleSearch())`.
- Works through request config tools.

### 3.2 Anthropic (Claude)
- Claude supports a built-in web search tool with `max_uses` and `allowed_domains` controls.

### 3.3 OpenAI
- OpenAI web search is documented as a built-in tool in the Responses API (`{"type": "web_search"}`).

### 3.4 OpenAI-Compatible Providers
- No universal standard exists.
- Support is provider-specific (for example, OpenRouter plugin-style web search).

## 4. Current Architecture Baseline (Code-Verified)

- Agent loop: `BaseAgent.handle_task()` -> `provider.safe_generate(...)`.
- Local executable tools are defined in `ToolRegistry` and run only by `ToolExecutor`.
- Providers currently receive standard function-call tool definitions.
- Session persistence already records full message history and task metadata.

Key files:
- `agent_cli/agent/base.py`
- `agent_cli/tools/registry.py`
- `agent_cli/tools/executor.py`
- `agent_cli/providers/base.py`
- `agent_cli/providers/provider/google_provider.py`
- `agent_cli/providers/provider/anthropic_provider.py`
- `agent_cli/providers/provider/openai_provider.py`
- `agent_cli/providers/provider/openai_compat.py`
- `agent_cli/data/registry.py`

## 5. Revised Design

### 5.1 `web_search` Is a Provider-Managed Tool Token

`web_search` is assignable in agent tool lists (for example `[agents.researcher].tools`).

Important distinction:
- It is **not** a locally executable tool (not run via `ToolExecutor`).
- It is a capability token used to instruct provider adapters to attach native web-search configuration.

### 5.2 Tool Set Split at Runtime

When building a request, split agent tools into:
- `executable_tools`: local tools (read/write/search/shell/etc.)
- `provider_managed_tools`: includes `web_search`

Behavior:
- `executable_tools` continue through existing function-calling/tool-execution flow.
- `provider_managed_tools` become provider request options.

### 5.3 No Session Toggle, No Command

Web search availability is determined by agent tool assignment only.
- No `/web` command.
- No `desired_web_search_*` session fields.
- No `default_web_search_*` `AgentSettings` fields.

## 6. Data-Driven Defaults (Mandatory)

All defaults must come from package data files and `DataRegistry` accessors.

### 6.1 Proposed Data Schema

Add data-driven section (new file or extension of existing TOML, implementation choice):

```toml
[web_search.defaults]
allowed_domains = []

[web_search.providers.anthropic]
max_uses = 10
allowed_domains = []

[web_search.providers.google]
enabled = true

[web_search.providers.openai]
enabled = true
```

Notes:
- `allowed_domains = []` means unrestricted (allow all domains).
- Anthropic `max_uses` default is fixed at `10` unless data override changes.

### 6.2 DataRegistry Contract Additions

Add read APIs, e.g.:
- `get_web_search_defaults()`
- `get_web_search_provider_defaults(provider_name: str)`

## 7. Provider Mapping Strategy

### 7.1 Anthropic

If `web_search` is present in the active agent tool list:
- Include Anthropic built-in web search tool in request.
- Source `max_uses` and `allowed_domains` from data registry.
- Default to `max_uses=10`, `allowed_domains=[]`.

### 7.2 Google

If `web_search` is present:
- Add Google Search grounding tool to request config.
- Use data-driven defaults for provider-specific options if applicable.

### 7.3 OpenAI

If `web_search` is present:
- Use Responses-API-compatible built-in web search tool path.
- Keep existing non-web path backward compatible when not present.

### 7.4 OpenAI-Compatible

If `web_search` is present:
- Only enable when specific provider adapter/config declares support.
- Source any extension payload from data-driven provider settings.
- Otherwise, capability is ignored with clear observability log.

## 8. Prompt and Schema Handling

To avoid invalid local tool execution attempts:
- `web_search` must not be exposed as an executable `execute_action` local tool.
- It should be treated as provider capability, not a callable local function.

Expected result:
- Schema validator and `ToolExecutor` logic remain stable.
- No `execute_action` with `tool=web_search` should be required for supported native paths.

## 9. Rollout Plan

### Phase 0: Data Layer
- Add data-driven web-search defaults and `DataRegistry` accessors.
- Include Anthropic defaults (`max_uses=10`, `allowed_domains=[]`).

### Phase 1: Agent Tool Capability Split
- Add runtime split between executable vs provider-managed tools.
- Detect `web_search` from agent tool assignment.

### Phase 2: Provider Implementations
- Implement Anthropic/Google/OpenAI mappings using data-driven defaults.
- Add guarded OpenAI-compatible extension path.

### Phase 3: Observability and Hardening
- Log desired/effective web-search capability by provider/model.
- Add regressions to guarantee unchanged local tool flow.

## 10. Testing Strategy

Unit tests:
- DataRegistry web-search default loading.
- Agent tool split logic (`web_search` not sent to local executor schema).
- Anthropic request includes data-driven `max_uses`/`allowed_domains` defaults.
- Google/OpenAI request mapping when `web_search` assigned.

Integration tests:
- Agent with `web_search` in tool list triggers provider-native web search path.
- Agent without `web_search` behaves exactly as current baseline.

Regression tests:
- Existing prompt JSON and native function-call local tools remain unchanged.
- Session persistence behavior remains unchanged (no new web-search session fields).

## 11. Complexity Estimate

Overall complexity: Medium.

Reasoning:
- No session-command surface.
- Main work is provider plumbing + tool capability split + data registry integration.
- OpenAI web-search path may still require adapter branching depending on API path choice.

Estimated change size:
- ~10-16 files.

## 12. Acceptance Criteria

- `web_search` can be assigned in agent tool lists.
- No manual session toggle is required.
- Anthropic uses data-driven defaults: `max_uses=10`, `allowed_domains=[]`.
- All default web-search settings are sourced from data files (`DataRegistry`).
- Existing local tool execution and schema handling do not regress.

## 13. References

- Google Gemini API docs: Grounding with Google Search  
  https://ai.google.dev/gemini-api/docs/google-search
- Anthropic docs: Web search tool  
  https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/web-search-tool
- OpenAI docs: Built-in tools guide (web search)  
  https://platform.openai.com/docs/guides/tools-web-search
- OpenAI docs: Search guide  
  https://platform.openai.com/docs/guides/tools?api-mode=responses#search
- OpenRouter docs: Plugins (web plugin)  
  https://openrouter.ai/docs/features/web-search
