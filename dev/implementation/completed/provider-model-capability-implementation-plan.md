# Provider/Model Capability Architecture Implementation Plan

Status: Draft
Date: 2026-03-04
Owner: Agent CLI Core
Depends on: `dev/specs/provider-model-capability-architecture-spec.md`

## 1. Scope

Implement the new provider/model architecture with:

- `providers.toml` for provider transport + operational tuning.
- entry-based `models.toml` for declared model capabilities.
- runtime capability registry (`declared + observed -> effective`) keyed by provider + model + deployment.
- effective-capability-driven prompt/tool routing.

Out of scope:

- `/capabilities` command and UI capability status surfaces.

## 2. Delivery Principles

1. Single source of truth:
- capability support booleans are declared only in model specs.
- provider specs tune behavior only (mode/tool type/limits).

2. Safe migration:
- keep legacy read path behind a feature flag until validation passes.

3. Deterministic behavior:
- unknown model -> explicit error.
- unknown capability state -> explicit reason + safe routing behavior.

## 3. Phase Plan

### Phase 0: Baseline and Guardrails

Goal:
- Prepare migration controls and observability.

Tasks:
1. Add feature flag `core.model_registry_v2` in settings.
2. Add startup diagnostic logs:
- active provider/model/deployment identity
- current capability source (`declared|observed|effective` summary)
3. Add migration telemetry counters:
- v2 resolver usage
- probe successes/failures
- unknown capability fallbacks

Files:
- `agent_cli/core/config.py`
- `agent_cli/core/models/config_models.py` (if needed for typed flag enum)
- `agent_cli/core/bootstrap.py`
- `agent_cli/core/logging/*` (if instrumentation hook required)

Exit criteria:
- Flag can enable/disable v2 at runtime startup.
- Diagnostics visible in logs with no functional changes when flag is off.

### Phase 1: Data Schema and Files

Goal:
- Introduce canonical data files and schema shape.

Tasks:
1. Create `agent_cli/data/providers.toml`.
2. Restructure `agent_cli/data/models.toml` with entry-based models.
3. Migrate provider-native web search tuning from `tools.toml` to provider specs.
4. Add/refresh schema docs and examples for:
- model capabilities typed payloads
- provider operational tuning sections

Files:
- `agent_cli/data/providers.toml` (new)
- `agent_cli/data/models.toml`
- `agent_cli/data/tools.toml` (remove migrated web-search tuning sections)
- `dev/specs/*` (reference docs updates if needed)

Exit criteria:
- Data files load cleanly.
- No capability support booleans exist in provider config.

### Phase 2: Typed Registry and Validation Layer

Goal:
- Add strongly typed registry access for models/providers/capabilities.

Tasks:
1. Add typed structures:
- `ProviderSpec`
- `ModelSpec`
- `CapabilitySpec`
- `CapabilityObservation`
- `CapabilitySnapshot`
- `ModelResolution`
2. Extend `DataRegistry` with:
- `get_provider_specs()`
- `get_model_specs()`
- `resolve_model_spec(model_name)`
- `get_model_capabilities(model_name)`
3. Add strict validation:
- duplicate alias detection
- missing required capability keys
- invalid typed payload values
4. Keep legacy accessors operational by mapping through new typed data when flag is on.

Files:
- `agent_cli/data/registry.py`
- `agent_cli/core/models/config_models.py` (or new module for registry specs)

Exit criteria:
- Typed registry APIs pass unit tests.
- Bad data fails fast with deterministic error messages.

### Phase 3: Provider/Model Resolution Engine

Goal:
- Route provider creation through model-first resolution only.

Tasks:
1. Refactor `ProviderManager` resolution flow:
- parse model input
- resolve preset model spec
- resolve provider spec
- compute deployment identity
2. Remove unknown-model heuristic inference in v2 path.
3. Return explicit `model_not_supported` for non-preset models.
4. Wire context window/pricing/tokenizer lookup to resolved model spec.

Files:
- `agent_cli/providers/manager.py`
- `agent_cli/providers/cost.py`
- `agent_cli/memory/budget.py`
- `agent_cli/commands/handlers/core.py` (`/model` error behavior consistency)

Exit criteria:
- `/model` accepts registered models only in v2 mode.
- provider/model resolution path is deterministic.

### Phase 4: Capability Registry (Observed Cache)

Goal:
- Introduce observed capability storage and effective snapshot computation.

Tasks:
1. Add capability cache store (in-memory first, file-backed optional):
- key: provider + model + deployment_id
- value: capability observations with timestamp/reason/source
2. Implement merge logic:
- observed (fresh) + declared -> effective
3. Add freshness and invalidation rules:
- model switch
- provider config/base_url/deployment change
- cache version bump
4. Expose API:
- `get_capability_snapshot(...)`
- `save_capability_observation(...)`

Files:
- `agent_cli/data/registry.py` (if registry-owned cache contract)
- `agent_cli/providers/*` or `agent_cli/core/*` new capability service module

Exit criteria:
- Effective snapshot is available for active provider/model/deployment.
- Stale observations are not reused.

### Phase 5: Capability Probe Service

Goal:
- Observe actual provider/deployment capability at runtime.

Tasks:
1. Add `CapabilityProbeService`.
2. Implement probe hooks:
- trigger on `/model` switch
- trigger on session start
3. Add provider-specific probes:
- OpenAI/Azure path checks (responses/web search/tooling)
- Google checks (search + tools compatibility)
- Anthropic checks (web search/tool-use support)
- openai-compatible minimal probe (or unknown if no reliable check)
4. Persist probe results to observation cache.
5. Ensure probe failures never block task execution.

Files:
- `agent_cli/providers/provider/*` (probe helpers)
- new `agent_cli/providers/capability_probe.py` (or similar)
- `agent_cli/core/bootstrap.py`
- `agent_cli/commands/handlers/core.py`

Exit criteria:
- Probe runs at both required triggers.
- Snapshot status can be `supported|unsupported|unknown` with reason.

### Phase 6: Effective-Capability Routing Integration

Goal:
- Ensure runtime behavior uses effective capability snapshot only.

Tasks:
1. Agent prompt integration:
- provider-managed capability hints rendered only when effective status is supported.
2. Tool routing integration:
- `web_search` token activation depends on effective capability snapshot.
3. Effort integration:
- `/effort` effective resolution uses snapshot (`effort.supported`).
4. Provider adapter runtime fallback path updates observed state on mismatch.

Files:
- `agent_cli/agent/base.py`
- `agent_cli/agent/default.py`
- `agent_cli/agent/react_loop.py`
- `agent_cli/providers/models.py`
- `agent_cli/providers/base.py`
- provider adapters under `agent_cli/providers/provider/*.py`
- `agent_cli/commands/handlers/core.py`

Exit criteria:
- Prompt/tool behavior reflects effective state, not static provider defaults.
- Deployment mismatch errors feed observation updates and deterministic fallback.

### Phase 7: Legacy Removal

Precondition:
- Migration complete and validation tests pass.

Goal:
- Remove duplicate/legacy ownership paths.

Tasks:
1. Remove legacy provider/model inference code in v2 path.
2. Remove legacy context/pricing prefix tables only used by old resolver.
3. Remove feature flag and dead branches.
4. Remove old capability config from deprecated locations.

Files:
- `agent_cli/data/registry.py`
- `agent_cli/providers/manager.py`
- `agent_cli/data/models.toml` (cleanup remaining legacy sections)
- `agent_cli/core/config.py`
- any transitional compatibility glue modules

Exit criteria:
- Single production path remains.
- No duplicate capability ownership locations remain.

### Phase 8: Validation, Rollout, and Documentation

Goal:
- Ship with confidence and clear maintenance guidance.

Tasks:
1. Add/expand test suites:
- unit: resolver, schema validation, cache merge/invalidation
- integration: model switch, session start probe, routing behavior
2. Run full regression pass for existing agents and providers.
3. Update developer docs:
- how to add a provider
- how to add a model preset
- how to define capability support vs provider tuning
4. Add troubleshooting section for `unknown` capability status reasons.

Files:
- `tests/**/*`
- `README.md` / docs files
- `dev/specs/*` cross-links

Exit criteria:
- CI green with migration scenarios.
- Documentation reflects final architecture and ownership rules.

## 4. Task Breakdown by Workstream

### Workstream A: Data and Schema
- Phase 1, Phase 2

### Workstream B: Resolver and Runtime
- Phase 3, Phase 6

### Workstream C: Probing and Observability
- Phase 0, Phase 4, Phase 5

### Workstream D: Migration and Release
- Phase 7, Phase 8

## 5. Suggested Execution Order

1. Phase 0
2. Phase 1
3. Phase 2
4. Phase 3
5. Phase 4
6. Phase 5
7. Phase 6
8. Phase 8 (validation pass)
9. Phase 7 (legacy removal after successful validation)

## 6. Definition of Done

1. Capability support has one declared owner (`models.toml`) and one observed owner (probe cache).
2. Provider specs contain only transport/deployment/tuning data.
3. Runtime uses effective capability snapshot for prompt/tool/effort routing.
4. Unknown models are rejected with clear errors.
5. Legacy paths are removed only after successful migration validation.
