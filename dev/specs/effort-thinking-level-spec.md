# Effort (Thinking Level) Feature Specification

Status: Draft
Date: 2026-03-04
Owner: Agent CLI Core

## 1. Objective

Introduce an `effort` feature that lets users control model reasoning depth for providers that support it, while keeping behavior safe and backward-compatible for providers that do not.

Primary outcomes:
- Users can inspect and set effort at runtime.
- Effort is persisted per session and can be configured globally.
- Gemini (Google Gen SDK) applies effort to request config.
- Unsupported providers continue to work with no regressions.

## 2. Non-Goals

- No migration to provider-managed chat state (`client.chats.create`).
- No change to existing task-loop JSON schema contract.
- No multimodal pipeline changes.
- No cross-provider parity in this iteration (Google first).

## 3. Current Baseline (Code-Verified)

Current generation path:
1. `Orchestrator._route_to_agent()` calls `BaseAgent.handle_task()`.
2. `BaseAgent.handle_task()` calls `provider.safe_generate(...)`.
3. `BaseLLMProvider.safe_generate()` calls `generate(...)`.
4. Provider adapter sends vendor-specific request.

Current limitation:
- There is no reasoning-effort field in settings, session model, command handlers, or provider interfaces.
- Current reasoning depth is provider/model default.

Relevant files:
- `agent_cli/agent/base.py`
- `agent_cli/core/orchestrator.py`
- `agent_cli/core/config.py`
- `agent_cli/providers/base.py`
- `agent_cli/providers/provider/google_provider.py`
- `agent_cli/providers/provider/openai_provider.py`
- `agent_cli/providers/provider/anthropic_provider.py`
- `agent_cli/commands/handlers/core.py`
- `agent_cli/session/base.py`
- `agent_cli/session/file_store.py`

## 4. User Contract

### 4.1 Command Surface

Add command:
- `/effort`
- `/effort <auto|minimal|low|medium|high>`

Behavior:
- `/effort` shows current desired and effective effort for active model/provider.
- `/effort <value>` updates session desired effort (create active session if needed).

### 4.2 Desired vs Effective Semantics

Use two values to avoid confusion:
- `desired_effort`: user preference (`auto|minimal|low|medium|high`).
- `effective_effort`: what current provider/model actually applies.

Examples:
- `Desired effort: high`
- `Effective effort (google/gemini-2.5-flash-lite): high`
- `Effective effort (openai/gpt-5): auto (not supported by this adapter yet)`

## 5. Effort Model and Resolution Rules

### 5.1 Canonical Levels

Introduce canonical enum:
- `auto`
- `minimal`
- `low`
- `medium`
- `high`

### 5.2 Resolution Precedence

At request time:
1. `session.desired_effort` (if available)
2. `settings.default_effort`
3. `auto`

### 5.3 Fallback Rules

- If provider cannot apply effort, request proceeds unchanged.
- Effective effort is reported as `auto` for that call.
- No hard failure for unsupported effort.

## 6. Provider Capability Matrix (v1)

### 6.1 Google (Gemini via `google.genai`)

Supported in v1.

SDK-confirmed fields:
- `types.ThinkingConfig(thinking_level=..., include_thoughts=..., thinking_budget=...)`
- `types.ThinkingLevel`: `MINIMAL`, `LOW`, `MEDIUM`, `HIGH`

Mapping:
- `auto` -> omit `thinking_config.thinking_level`
- `minimal|low|medium|high` -> set corresponding `types.ThinkingLevel.*`

Out of scope for v1:
- `include_thoughts`
- `thinking_budget`

### 6.2 Other Providers

Deferred in v1:
- OpenAI adapter
- Anthropic adapter
- OpenAI-compatible / Ollama adapters

Behavior in v1:
- Ignore effort parameter.
- Keep default request behavior.

## 7. Architecture Changes

### 7.1 Config

Add to `AgentSettings`:
- `default_effort: str = "auto"`

Validation:
- Must be one of allowed canonical levels.

Default config template update:
- Add `default_effort = "auto"` in generated global config.

### 7.2 Session Persistence

Add to `Session`:
- `desired_effort: str = "auto"`

Update:
- `FileSessionManager._session_to_dict()`
- `FileSessionManager._session_from_dict()`
- Backward-compat: missing field defaults to `"auto"`.

### 7.3 Provider Interface

Extend method signatures with optional effort:
- `BaseLLMProvider.generate(..., effort: str | None = None)`
- `BaseLLMProvider.stream(..., effort: str | None = None)`
- `BaseLLMProvider.safe_generate(..., effort: str | None = None)`

Compatibility rule:
- Default `None` preserves existing behavior.

### 7.4 Agent Path

In `BaseAgent.handle_task()`:
- Resolve active desired effort using precedence rules.
- Pass effort into `provider.safe_generate(...)`.

### 7.5 Command Integration

In `commands/handlers/core.py`:
- Add `/effort` handler.
- Validate and persist desired effort on active session.
- Emit `SettingsChangedEvent(setting_name="effort", new_value=...)`.

Optional v1.1:
- show effort indicator in header status bar.

## 8. Rollout Plan

### Phase 0: Types + Spec
- Add canonical effort constants/enum.
- Add validation helper.

### Phase 1: Core Plumbing
- Thread optional `effort` through provider base interfaces and calls.
- Update all providers to accept the parameter (even if ignored).

### Phase 2: Google Support
- Implement effort -> Gemini `thinking_config.thinking_level` mapping for `generate` and `stream`.

### Phase 3: Session + Config
- Persist `desired_effort` in session JSON.
- Add `default_effort` to settings and config defaults.

### Phase 4: Command UX
- Implement `/effort`.
- Add clear user-facing messages for supported/unsupported providers.

### Phase 5: Observability + Docs
- Add `desired_effort` and `effective_effort` to LLM call metadata/logs.
- Update `/config` output and user docs.

## 9. Testing Strategy

Unit tests:
- Settings validation accepts only allowed values.
- Session roundtrip persists `desired_effort`.
- `/effort` command parse/validate/update behavior.
- Google mapping tests for each non-auto level.
- Unsupported providers ignore effort without errors.

Integration tests:
- Set `/effort high` on Gemini model, execute request, verify provider request contains expected `thinking_level`.
- Restart/reload session and verify effort remains active.

Regression tests:
- Requests without effort setting behave exactly as before.
- Existing slash commands and model switching remain unaffected.

## 10. Complexity Estimate

Estimated complexity: Medium.

Reasoning:
- Broad touchpoints (settings, session, command, provider interface).
- Low algorithmic risk.
- Main risk is cross-provider signature propagation and backward compatibility.

Expected implementation size:
- ~10-14 files changed.

## 11. Risks and Mitigations

1. Provider divergence:
- Mitigation: canonical `desired/effective` split and per-provider capability mapping.

2. Silent user confusion on unsupported adapters:
- Mitigation: explicit `/effort` output showing effective behavior.

3. Interface churn across all providers:
- Mitigation: optional parameter with default `None`, phased changes.

4. Increased schema errors with higher reasoning depth:
- Mitigation: keep existing schema recovery loop unchanged; monitor validation error rate.

## 12. Acceptance Criteria

- `/effort` and `/effort <value>` work end-to-end.
- `default_effort` can be configured and validated.
- Session persists and restores `desired_effort`.
- Gemini requests apply selected effort level.
- Unsupported providers do not fail and report effective fallback clearly.
