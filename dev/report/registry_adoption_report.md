# Registry System Adoption Report

> **Date:** 2026-03-05  
> **Scope:** Identify features in the `agent_cli` codebase that currently handle their own lookup/initialization logic and could instead leverage the centralized [DataRegistry](file:///x:/agent_cli/agent_cli/core/infra/registry/registry.py#30-798) system via dependency injection.

---

## Executive Summary

The [DataRegistry](file:///x:/agent_cli/agent_cli/core/infra/registry/registry.py#30-798) (read-only data-driven defaults) and [RegistryLifecycleMixin](file:///x:/agent_cli/agent_cli/core/infra/registry/registry_base.py#10-55) (validate→freeze lifecycle) are production-ready. Three mutable registries — [AgentRegistry](file:///x:/agent_cli/agent_cli/core/runtime/agents/registry.py#11-63), [ToolRegistry](file:///x:/agent_cli/agent_cli/core/runtime/tools/registry.py#26-131), and [CommandRegistry](file:///x:/agent_cli/agent_cli/core/ux/commands/base.py#83-155) — correctly inherit from [RegistryLifecycleMixin](file:///x:/agent_cli/agent_cli/core/infra/registry/registry_base.py#10-55) and are frozen at bootstrap.

However, **many modules still bypass the DI-wired [DataRegistry](file:///x:/agent_cli/agent_cli/core/infra/registry/registry.py#30-798) instance** by constructing their own [DataRegistry()](file:///x:/agent_cli/agent_cli/core/infra/registry/registry.py#30-798) singletons at the module level or inside `@lru_cache` helpers. This violates the "no global singletons" principle established in the registry refactor.

Additionally, two subsystems maintain their own hard-coded adapter/counter lookup tables that could be folded into the registry pattern.

---

## Findings

### 🔴 Finding 1: Module-Level [DataRegistry()](file:///x:/agent_cli/agent_cli/core/infra/registry/registry.py#30-798) Singletons (Critical)

**Problem:** Several modules instantiate a fresh [DataRegistry()](file:///x:/agent_cli/agent_cli/core/infra/registry/registry.py#30-798) at **module import time** — before [create_app()](file:///x:/agent_cli/agent_cli/core/infra/registry/bootstrap.py#320-659) has a chance to create the canonical instance and pass it via DI. This means:
- Multiple [DataRegistry](file:///x:/agent_cli/agent_cli/core/infra/registry/registry.py#30-798) instances exist simultaneously
- Data file I/O is duplicated on every import
- If [DataRegistry](file:///x:/agent_cli/agent_cli/core/infra/registry/registry.py#30-798) ever gains mutable state beyond capability observations, these shadows will silently diverge

| File | Line | Pattern |
|------|------|---------|
| [shell_tool.py](file:///x:/agent_cli/agent_cli/core/runtime/tools/shell_tool.py#L30) | 30 | `_SHELL_DEFAULTS = DataRegistry().get_tool_defaults()…` |
| [shell_tool.py](file:///x:/agent_cli/agent_cli/core/runtime/tools/shell_tool.py#L137) | 137 | `defaults = DataRegistry().get_safe_command_patterns()` |
| [file_tools.py](file:///x:/agent_cli/agent_cli/core/runtime/tools/file_tools.py#L24) | 24 | `_FILE_TOOL_DEFAULTS = DataRegistry().get_tool_defaults()…` |

**Recommendation:** Accept the [DataRegistry](file:///x:/agent_cli/agent_cli/core/infra/registry/registry.py#30-798) instance via constructor and read defaults from it, or defer reading defaults to a class method that receives the registry. For module-level constants that must be available before DI wiring, use lazy initialization with the DI-wired instance.

---

### 🔴 Finding 2: `@lru_cache` Global Registry Factories (Critical)

**Problem:** Four modules define `@lru_cache(maxsize=1)` functions that construct a **process-global** [DataRegistry](file:///x:/agent_cli/agent_cli/core/infra/registry/registry.py#30-798) singleton to avoid import-time construction. While slightly better than module-level globals, these still create shadow registries outside the DI container:

| File | Function | Line |
|------|----------|------|
| [token_counter.py](file:///x:/agent_cli/agent_cli/core/providers/cost/token_counter.py#L290-L292) | [_default_data_registry()](file:///x:/agent_cli/agent_cli/core/providers/cost/token_counter.py#290-293) | 290 |
| [cost.py](file:///x:/agent_cli/agent_cli/core/providers/cost/cost.py#L25-L27) | [_default_data_registry()](file:///x:/agent_cli/agent_cli/core/providers/cost/token_counter.py#290-293) | 25 |
| [budget.py](file:///x:/agent_cli/agent_cli/core/providers/cost/budget.py#L55-L57) | [_default_data_registry()](file:///x:/agent_cli/agent_cli/core/providers/cost/token_counter.py#290-293) | 55 |

**Why it matters:** Each [_default_data_registry()](file:///x:/agent_cli/agent_cli/core/providers/cost/token_counter.py#290-293) creates a singleton that is **not** the same instance used by the rest of the application. The `or DataRegistry()` fallback pattern in many constructors means every code path that gets called without the DI instance will silently fork to its own [DataRegistry](file:///x:/agent_cli/agent_cli/core/infra/registry/registry.py#30-798).

**Recommendation:** Remove all [_default_data_registry()](file:///x:/agent_cli/agent_cli/core/providers/cost/token_counter.py#290-293) functions. Make `data_registry: DataRegistry` a **required** parameter (not optional with default) in the functions/constructors that use it. The bootstrap already wires the instance everywhere — the `Optional` fallback is only needed for tests, which should create their own [DataRegistry()](file:///x:/agent_cli/agent_cli/core/infra/registry/registry.py#30-798) explicitly.

---

### 🟡 Finding 3: `or DataRegistry()` Fallback Pattern (Warning)

**Problem:** Many constructors accept `data_registry: DataRegistry | None = None` and then do `data_registry or DataRegistry()`. While this "just works" since [DataRegistry](file:///x:/agent_cli/agent_cli/core/infra/registry/registry.py#30-798) is read-only, it creates unnecessary extra instances and hides wiring bugs:

| File | Line | Context |
|------|------|---------|
| [output_formatter.py](file:///x:/agent_cli/agent_cli/core/runtime/tools/output_formatter.py#L38) | 38 | [(data_registry or DataRegistry())](file:///x:/agent_cli/agent_cli/core/ux/commands/base.py#121-124) |
| [executor.py](file:///x:/agent_cli/agent_cli/core/runtime/tools/executor.py#L92) | 92 | [(data_registry or DataRegistry()).get_tool_defaults()…](file:///x:/agent_cli/agent_cli/core/ux/commands/base.py#121-124) |
| [schema.py](file:///x:/agent_cli/agent_cli/core/runtime/agents/schema.py#L51) | 51 | [(data_registry or DataRegistry()).get_schema_defaults()](file:///x:/agent_cli/agent_cli/core/ux/commands/base.py#121-124) |
| [react_loop.py](file:///x:/agent_cli/agent_cli/core/runtime/agents/react_loop.py#L128) | 128 | `self._data_registry = data_registry or DataRegistry()` |
| [base.py (agent)](file:///x:/agent_cli/agent_cli/core/runtime/agents/base.py#L136) | 136 | `self._data_registry = data_registry or DataRegistry()` |
| [manager.py](file:///x:/agent_cli/agent_cli/core/providers/manager.py#L73) | 73 | `self._data_registry = data_registry or DataRegistry()` |
| [summarizer.py](file:///x:/agent_cli/agent_cli/core/providers/cost/summarizer.py#L39) | 39 | `registry = data_registry or DataRegistry()` |
| [config.py](file:///x:/agent_cli/agent_cli/core/infra/config/config.py#L380) | 380 | `registry = data_registry or DataRegistry()` |
| [openai_provider.py](file:///x:/agent_cli/agent_cli/core/providers/adapters/openai_provider.py#L421) | 421 | `self._data_registry or DataRegistry()` |
| [google_provider.py](file:///x:/agent_cli/agent_cli/core/providers/adapters/google_provider.py#L333) | 333 | `self._data_registry or DataRegistry()` |
| [anthropic_provider.py](file:///x:/agent_cli/agent_cli/core/providers/adapters/anthropic_provider.py#L319) | 319 | `self._data_registry or DataRegistry()` |

**Recommendation:** Make [data_registry](file:///x:/agent_cli/agent_cli/core/providers/cost/token_counter.py#290-293) required (not optional) in the main application code paths. Keep the default only for standalone utility usage (if needed). This turns a silent data-fork into a loud error.

---

### 🟡 Finding 4: Hard-Coded Adapter Type Registry (Warning)

**Problem:** The [ProviderManager](file:///x:/agent_cli/agent_cli/core/providers/manager.py#54-337) module has a hard-coded module-level dictionary mapping adapter-type strings to provider classes:

```python
# manager.py L41-48
_ADAPTER_TYPES_INTERNAL: Dict[str, Type[BaseLLMProvider]] = {
    "openai": OpenAIProvider,
    "azure": AzureProvider,
    "anthropic": AnthropicProvider,
    "google": GoogleProvider,
    "ollama": OllamaProvider,
    "openai_compatible": OpenAICompatibleProvider,
}
```

**Why this should use the registry system:**
- Adding a new adapter requires editing [manager.py](file:///x:/agent_cli/agent_cli/core/providers/manager.py) source code
- The mapping is frozen at import time with no lifecycle management
- No validation other than the startup [_validate_adapter_types()](file:///x:/agent_cli/agent_cli/core/providers/manager.py#88-112) call
- Cannot dynamically register custom adapters from plugins or config

**Recommendation:** Create an `AdapterRegistry` (extending [RegistryLifecycleMixin](file:///x:/agent_cli/agent_cli/core/infra/registry/registry_base.py#10-55)) that:
1. Is populated during bootstrap alongside the other registries
2. Validates adapter contracts at registration time
3. Is frozen before first use
4. Is discoverable via [AppContext](file:///x:/agent_cli/agent_cli/core/infra/registry/bootstrap.py#69-313)

---

### 🟡 Finding 5: Hard-Coded Token Counter Registry (Warning)

**Problem:** `ProviderManager._build_token_counters()` returns a hard-coded dictionary mapping adapter types to token counter instances:

```python
# manager.py L300-321
return {
    "openai": TiktokenCounter(…),
    "azure": TiktokenCounter(…),
    "anthropic": AnthropicTokenCounter(…),
    "google": GeminiTokenCounter(…),
    "ollama": heuristic,
    "openai_compatible": heuristic,
}
```

**Why this should use the registry system:** Same issues as Finding 4 — adding a new counter requires editing source, no lifecycle validation, and cannot be extended via config.

**Recommendation:** Either:
- (a) Fold token counter registration into the proposed `AdapterRegistry` (each adapter binds its preferred counter), or
- (b) Create a lightweight `TokenCounterRegistry` with the same lifecycle pattern.

---

### 🟡 Finding 6: [SessionAgentRegistry](file:///x:/agent_cli/agent_cli/core/runtime/agents/session_registry.py#29-107) Lacks Lifecycle Mixin (Warning)

**Problem:** The [SessionAgentRegistry](file:///x:/agent_cli/agent_cli/core/runtime/agents/session_registry.py#29-107) class ([session_registry.py](file:///x:/agent_cli/agent_cli/core/runtime/agents/session_registry.py)) manages per-session agent state but does **not** extend [RegistryLifecycleMixin](file:///x:/agent_cli/agent_cli/core/infra/registry/registry_base.py#10-55). Unlike [AgentRegistry](file:///x:/agent_cli/agent_cli/core/runtime/agents/registry.py#11-63), [ToolRegistry](file:///x:/agent_cli/agent_cli/core/runtime/tools/registry.py#26-131), and [CommandRegistry](file:///x:/agent_cli/agent_cli/core/ux/commands/base.py#83-155), it:
- Has no [freeze()](file:///x:/agent_cli/agent_cli/core/infra/registry/registry_base.py#21-33) / [_assert_mutable()](file:///x:/agent_cli/agent_cli/core/infra/registry/registry_base.py#45-51) guards
- Has no [validate()](file:///x:/agent_cli/agent_cli/core/runtime/agents/registry.py#40-45) hook
- Is not frozen during bootstrap

**Assessment:** This is partially by design — the session agent registry is mutable at runtime (agents can be added/removed per session). However, it could still benefit from:
- A [validate()](file:///x:/agent_cli/agent_cli/core/runtime/agents/registry.py#40-45) hook to enforce invariants (e.g., always has an active agent)
- An optional partial-freeze for the initial session setup

**Recommendation:** Consider adding [RegistryLifecycleMixin](file:///x:/agent_cli/agent_cli/core/infra/registry/registry_base.py#10-55) with a relaxed freeze policy, or at minimum add validation guards for invariants.

---

### 🟢 Finding 7: [infer_model_max_context()](file:///x:/agent_cli/agent_cli/core/providers/cost/budget.py#29-32) Bypass (Minor)

**Problem:** The standalone function [infer_model_max_context()](file:///x:/agent_cli/agent_cli/core/providers/cost/budget.py#29-32) in [budget.py](file:///x:/agent_cli/agent_cli/core/providers/cost/budget.py#L29-L31) directly calls [_default_data_registry()](file:///x:/agent_cli/agent_cli/core/providers/cost/token_counter.py#290-293) without accepting a [data_registry](file:///x:/agent_cli/agent_cli/core/providers/cost/token_counter.py#290-293) parameter:

```python
def infer_model_max_context(model_name: str) -> int:
    return _default_data_registry().get_context_window(model_name)
```

**Recommendation:** Add an optional [data_registry](file:///x:/agent_cli/agent_cli/core/providers/cost/token_counter.py#290-293) parameter to match the convention used by [budget_for_model()](file:///x:/agent_cli/agent_cli/core/providers/cost/budget.py#34-53) alongside it.

---

## Summary Table

| # | Finding | Severity | Files Affected | Effort |
|---|---------|----------|----------------|--------|
| 1 | Module-level [DataRegistry()](file:///x:/agent_cli/agent_cli/core/infra/registry/registry.py#30-798) singletons | 🔴 Critical | 3 | Medium |
| 2 | `@lru_cache` global registry factories | 🔴 Critical | 3 | Low |
| 3 | `or DataRegistry()` fallback pattern | 🟡 Warning | 11 | Low |
| 4 | Hard-coded adapter type registry | 🟡 Warning | 1 | Medium |
| 5 | Hard-coded token counter registry | 🟡 Warning | 1 | Medium |
| 6 | [SessionAgentRegistry](file:///x:/agent_cli/agent_cli/core/runtime/agents/session_registry.py#29-107) lacks lifecycle mixin | 🟡 Warning | 1 | Low |
| 7 | [infer_model_max_context()](file:///x:/agent_cli/agent_cli/core/providers/cost/budget.py#29-32) bypass | 🟢 Minor | 1 | Trivial |

---

## Recommended Migration Priority

### Phase A — Eliminate Shadow Registries (Findings 1, 2, 3)
> **Goal:** Ensure exactly one [DataRegistry](file:///x:/agent_cli/agent_cli/core/infra/registry/registry.py#30-798) instance per application lifecycle.

1. Remove all `@lru_cache` [_default_data_registry()](file:///x:/agent_cli/agent_cli/core/providers/cost/token_counter.py#290-293) functions
2. Make [data_registry](file:///x:/agent_cli/agent_cli/core/providers/cost/token_counter.py#290-293) a required parameter where the DI container wires it
3. Refactor module-level constants in [shell_tool.py](file:///x:/agent_cli/agent_cli/core/runtime/tools/shell_tool.py) and [file_tools.py](file:///x:/agent_cli/agent_cli/core/runtime/tools/file_tools.py) to use lazy initialization or constructor injection

### Phase B — Formalize Adapter & Counter Registries (Findings 4, 5)
> **Goal:** Apply the [RegistryLifecycleMixin](file:///x:/agent_cli/agent_cli/core/infra/registry/registry_base.py#10-55) pattern to adapter type mappings.

1. Create `AdapterRegistry(RegistryLifecycleMixin)` in `core/providers/`
2. Move adapter-type → class mapping to bootstrap-time registration
3. Bind token counter selection to adapter registration
4. Freeze after bootstrap

### Phase C — Lifecycle Hardening (Finding 6, 7)
> **Goal:** Apply consistent lifecycle guards across all registry-like structures.

1. Add validation guards to [SessionAgentRegistry](file:///x:/agent_cli/agent_cli/core/runtime/agents/session_registry.py#29-107)
2. Fix standalone function signatures to accept the DI-wired registry
