# Provider/Model Capability Architecture Specification

Status: Draft
Date: 2026-03-04
Owner: Agent CLI Core

## 1. Objective

Redesign provider/model configuration so capabilities are data-driven and model-aware, with clear separation of concerns:

- `providers.toml` defines provider connection and adapter metadata.
- `models.toml` defines model presets as first-class entries (pricing, context, tokenizer, capabilities).
- Capability support (tool calling, effort, web search, etc.) is resolved via a capability registry keyed by provider + model + deployment, with declared and observed states.

Primary outcome: remove scattered/hardcoded capability assumptions and make model behavior predictable, inspectable, and configurable.

## 2. Current Baseline (Code-Verified)

Current state in the repository:

- `agent_cli/data/models.toml` mixes:
  - internal model defaults
  - context window map + prefix heuristics
  - pricing map
  - tokenizer prefixes
  - built-in provider definitions
- `DataRegistry` (`agent_cli/data/registry.py`) provides separate lookups:
  - `get_context_window(model)`
  - `get_pricing(model)`
  - `get_builtin_providers()`
- Provider capabilities are largely code-driven and provider-wide:
  - `BaseLLMProvider.supports_native_tools`
  - `BaseLLMProvider.supports_effort`
  - `BaseLLMProvider.supports_web_search`
- Provider selection is model-string driven in `ProviderManager` (`agent_cli/providers/manager.py`) with:
  - exact configured match
  - provider prefix parsing (`provider/model` or `provider:model`)
  - fallback heuristics by model name patterns
- Agent-level provider-managed capability token (`web_search`) exists, but effective availability still depends on provider implementation/runtime behavior.

## 3. Problems

1. Data ownership is mixed:
- model metadata and provider metadata live in one file (`models.toml`), increasing coupling.

2. Capability truth is fragmented:
- some capability behavior is in TOML, some in provider code, some implicit by runtime errors.

3. Granularity mismatch:
- capability switches are mostly provider-level while reality is often model/deployment-level.

4. Fallback behavior is hard to reason about:
- prefix heuristics for context/pricing and provider inference can produce ambiguous outcomes.

5. High operational friction:
- adding/changing a model often requires touching multiple code paths and assumptions.

## 4. Design Goals

1. Single source of truth per concern:
- provider transport config in `providers.toml`
- model behavior config in `models.toml`

2. Per-model capability registry:
- each preset model explicitly declares supported capabilities.

3. Deterministic resolution:
- explicit precedence and fallback chain for provider/model/capability lookup.

4. Backward-compatible rollout:
- phased migration with legacy-reader fallback until cutoff.

5. Developer ergonomics:
- adding a new model should be a data-only change in common cases.

6. Effective capabilities:
- runtime prompt/tool routing must use effective capabilities (declared + observed), not provider defaults alone.

7. Single source of truth:
- capability support ownership is split by role only:
  - declared support: `models.toml`
  - observed support: runtime probe cache
  - effective support: resolver output
- provider specs may tune capability behavior (mode/tool type/limits) but do not declare support booleans.

### 4.1 Adapter Strategy (Explicit)

This redesign keeps your two adapter modes as first-class architecture:

- Provider-native adapter:
  - model is served through its own SDK/adapter (for example `google`, `anthropic`).
- OpenAI-compatible adapter:
  - model is served through OpenAI-compatible transport (`openai` SDK style), including Azure/OpenRouter/HuggingFace-compatible endpoints.

Provider entries declare `adapter_type` and transport details; model entries declare which provider they bind to.

## 5. Proposed Data Architecture

### 5.1 `providers.toml` (new)

Defines provider connection/adapter metadata only.

Example:

```toml
[providers.openai]
adapter_type = "openai"
base_url = ""
api_key_env = "OPENAI_API_KEY"
default_model = "gpt-4o"
[providers.openai.web_search]
tool_type = "web_search_preview"

[providers.azure]
adapter_type = "azure"
base_url = "https://YOUR-RESOURCE.openai.azure.com/openai/v1"
api_key_env = "AZURE_OPENAI_API_KEY"
default_model = "default-deployment"
[providers.azure.web_search]
tool_type = "web_search_preview"

[providers.google]
adapter_type = "google"
api_key_env = "GOOGLE_API_KEY"
default_model = "gemini-2.5-flash-lite"
[providers.google.web_search]
mode = "provider_native"
```

Notes:
- No per-model pricing/context/capability fields here.
- Provider specs hold connection/deployment settings and operational tuning only (no support booleans).

### 5.2 `models.toml` (restructured)

Defines one entry per preset model.

Example:

```toml
[models."gpt-4o"]
provider = "openai"
api_model = "gpt-4o"
aliases = ["openai/gpt-4o", "openai:gpt-4o"]
context_window = 128000
tokenizer = "o200k_base"
pricing_input = 2.50
pricing_output = 10.00

[models."gpt-4o".capabilities]
native_tools = { supported = true }
effort = { supported = false, levels = ["auto"] }
web_search = { supported = true, mode = "responses_api", tool_type = "web_search_preview" }

[models."gemini-2.5-flash-lite"]
provider = "google"
api_model = "gemini-2.5-flash-lite"
context_window = 1000000
tokenizer = "cl100k_base"
pricing_input = 0.15
pricing_output = 0.60

[models."gemini-2.5-flash-lite".capabilities]
native_tools = { supported = true }
effort = { supported = true, levels = ["auto", "minimal", "low", "medium", "high"] }
web_search = { supported = true, mode = "provider_native" }
```

Notes:
- Preset model entries are authoritative.
- Use raw API model names as canonical model IDs (for example `gpt-4o`, `gemini-2.5-flash-lite`).
- Existing `internal_models` can stay in `models.toml` or be moved to `runtime.toml` later.

### 5.3 Capability Schema (data-driven)

Required typed payload keys:

- `effort = { supported = bool, levels = ["auto", ...] }`
- `web_search = { supported = bool, mode = "provider_native|responses_api|none", tool_type = "..." }`
- `native_tools = { supported = bool }`

Rule: provider code can still validate at runtime, but data config is the first decision layer.

### 5.4 Capability Registry State (Declared + Observed)

Capability registry key:

- `provider_name`
- `api_model`
- `deployment_id`

State model:

- `declared`: from data files (`models.toml` capability support)
- `observed`: from runtime probes
- `effective`: resolved decision used by prompt/tool routing

Required observed payload shape:

- `status = "supported|unsupported|unknown"`
- `reason = "..."` (machine-readable short code + optional detail)
- `checked_at = "<ISO-8601>"`
- `source = "probe|cache|declared"`

`deployment_id` resolution:

- use explicit provider/deployment identifier when configured
- otherwise derive stable key from provider + base_url + model

## 6. Runtime Resolution Rules

### 6.1 Model Resolution

Given input model string from `/model` or config:

1. Exact `models.<id>.api_model` match.
2. Exact alias match.
3. Qualified name (`provider:model` or `provider/model`) parse.
4. If unresolved, return deterministic validation error (`model_not_supported`) and do not infer capabilities.

### 6.2 Provider Resolution

Resolved model returns `provider` key, then provider settings come from `providers.toml` + user overrides (`settings.providers`).

### 6.3 Capability Resolution Precedence

1. Observed capability for (`provider`, `model`, `deployment`) when fresh.
2. Declared model capability (`models.<id>.capabilities.*`).
3. If neither observed nor declared is available, resolve as `unknown` and surface deterministic reason.

### 6.4 Web Search/Effort Behavior

- Agent token `web_search` remains a capability request.
- Effective enablement requires effective capability `web_search.supported=true`.
- Effort command remains user intent; effective effort requires effective capability `effort.supported=true`.

### 6.5 Runtime Capability Probe and Cache

Probe triggers:

- on `/model` switch
- on session start (active model)

Probe policy:

- run lightweight provider-specific checks for capability features (`native_tools`, `effort`, `web_search`)
- if probe is unavailable or fails, keep capability as `unknown` with reason and fall back to declared behavior

Cache policy:

- cache observed capability results by (`provider`, `model`, `deployment`)
- reuse cached result when fresh
- invalidate cache on provider config change, model change, or explicit cache version bump

Routing rule:

- prompt capability hints and provider-managed tool wiring must use effective capability state only

## 7. Code Architecture Changes

### 7.1 Data Registry

`DataRegistry` should gain typed accessors:

- `get_provider_specs()`
- `get_model_specs()`
- `resolve_model_spec(model_name)`
- `get_model_capabilities(model_name)`
- `get_capability_snapshot(provider, model, deployment_id)`
- `save_capability_observation(provider, model, deployment_id, observation)`

Legacy methods (`get_context_window`, `get_pricing`, `get_builtin_providers`) should be reimplemented via new typed specs during transition.

### 7.2 Config Models

Add typed dataclasses for config payloads (examples):

- `ProviderSpec`
- `ModelSpec`
- `CapabilitySpec`
- `ModelResolution`
- `CapabilityObservation`
- `CapabilitySnapshot`

`ProviderConfig` remains for runtime merged settings, but should be built from `ProviderSpec` + user override.

### 7.3 Provider Manager

`ProviderManager` should resolve model first, then provider:

- avoids duplicated inference logic
- exposes resolved model metadata (context/pricing/capabilities) to downstream code
- resolves deployment identity used by capability registry keying

### 7.4 Provider Adapters

Replace hardcoded capability checks where possible with effective capability snapshots:

- `supports_web_search`
- `supports_effort`
- native tools gating

Provider code still keeps defensive runtime fallback for deployment/API mismatch, but that fallback updates observed state and does not become a second declared source.

### 7.5 Capability Probe Service

Add a dedicated runtime service (for example `CapabilityProbeService`) that:

- executes provider-specific probe checks
- normalizes observations into shared schema
- writes/reads cache for capability observations
- returns effective capability snapshot for routing decisions

## 8. Migration Plan

### Phase 0: Baseline + Compatibility Guard
- Add feature flag: `core.model_registry_v2` (default off).
- Add observability logs comparing legacy vs v2 resolution for canary sessions.

### Phase 1: Data Files
- Introduce `agent_cli/data/providers.toml`.
- Add new `models` section schema in `models.toml` (or `models_v2.toml` temporarily).

### Phase 2: Typed Registry + Read Path
- Implement typed parsing and validation.
- Keep legacy readers functional.

### Phase 3: Runtime Wiring
- Update `ProviderManager`, cost estimator, token budget resolver, command surfaces to use v2 resolution when flag is on.
- Add capability registry key resolution (`provider + model + deployment`) and effective-capability lookup.

### Phase 4: Capability Adoption
- Shift capability decisions to data-driven registry across providers.
- Add runtime probe+cache flow on `/model` switch and session start.
- Keep provider runtime fallbacks for unsupported deployments.

### Phase 5: Cleanup
- Remove old prefix-based tables after migration window.
- Remove legacy fallback path and feature flag.
- Removal trigger: complete migration rollout and pass migration validation tests successfully.

## 9. Testing Strategy

Unit tests:
- Data parse/validation for `providers.toml` and new `models.toml` schema.
- Model resolution precedence (exact, alias, qualified).
- Capability precedence behavior.
- Backward-compat methods return same values for existing known models.
- Capability observation merge rules (`declared + observed -> effective`).
- Probe cache freshness/invalidation behavior.

Integration tests:
- `/model` switching resolves correct provider and capabilities.
- Effort/web_search behavior changes per selected model.
- Provider runtime fallback messages remain clear when deployment disagrees.
- Session startup triggers capability probe/cached hydration for active model.
- Prompt/tool routing excludes capabilities marked `unsupported` by effective snapshot.

Regression tests:
- Existing sessions and old configs still load.
- Unsupported models produce deterministic error messages from `/model` and task routing.

## 10. Complexity Estimate

Overall complexity: Medium-High.

Why:
- Cross-cutting change across data layer, provider routing, capability gating, and command/runtime UX.
- Requires parallel support of legacy + v2 during migration.

Estimated change size: ~14-24 files.

## 11. Risks and Mitigations

1. Risk: capability metadata drift from real provider behavior.
- Mitigation: keep runtime probe/fallback and structured warnings.

2. Risk: stale cached observations cause incorrect routing.
- Mitigation: TTL/freshness checks + deterministic invalidation triggers.

3. Risk: migration regressions for user custom providers.
- Mitigation: preserve `settings.providers` override contract and add compatibility tests.

4. Risk: duplicate config source confusion during transition.
- Mitigation: explicit precedence docs and startup diagnostics showing effective source.

5. Risk: model alias collisions.
- Mitigation: fail-fast validation on duplicate alias/provider-qualified keys.

6. Risk: users currently relying on ad-hoc unknown model names will fail after preset-only policy.
- Mitigation: clear error message with remediation ("register model in models.toml/providers.toml").

## 12. Assumptions

1. Preset models are curated by maintainers and can be updated with releases.
2. Users can select only registered preset models; ad-hoc unknown models are not allowed.
3. `settings.providers` user overrides must remain supported.
4. Existing session persistence format should remain backward-compatible.
5. Capability evaluation should prefer data-driven settings but cannot fully eliminate runtime provider variance.
6. Provider-specific web search tuning migrates to provider specs (not `tools.toml`).
7. Capability metadata ownership and updates are manual (maintainer-managed).
8. Runtime probing is best-effort and must never block task execution indefinitely.

## 13. Resolved Decisions

1. Canonical model key strategy:
- Use raw API names.

2. Ad-hoc model policy:
- Preset-only. Unknown model names are rejected with explicit error.

3. Capability shape depth:
- Use typed payloads now.

4. Source of truth ownership:
- Manual maintainer updates.

5. User-facing diagnostics:
- Keep `/model` command as-is for now; unsupported model yields clear error.

6. Web search split-of-responsibility:
- Migrate provider-specific web-search tuning into provider specs.

7. Deprecation timing:
- Remove legacy behavior immediately after migration is complete and validation tests pass.

## 14. Acceptance Criteria

1. Provider metadata is loaded from `providers.toml`, not from mixed sections in `models.toml`.
2. Preset model metadata is entry-based (`models.<id>`) with explicit capability fields.
3. Runtime provider/model/capability resolution follows documented precedence.
4. Existing user config and session behavior remain backward-compatible during migration.
5. Capability-dependent features (web search, effort, native tools) are determined by data-driven model registry with provider runtime fallback only as safety net.
6. Unknown model names are rejected with deterministic error messages (preset-only policy).
7. Runtime probes execute on model switch and session start, and cached observations are reused when fresh.
8. Prompt/tool routing uses effective capability snapshot (declared + observed), not provider defaults alone.
9. No duplicate support ownership: providers do not declare capability support booleans.
