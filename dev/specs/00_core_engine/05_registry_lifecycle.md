# Registry Lifecycle Refactor Specification

## Overview

This specification defines the refactoring of the Agent CLI registry system to align with the [Registry & Data-Driven Design Guideline](file:///x:/agent_cli/dev/guideline/registry-and-data-driven-guideline.md). The refactor introduces a **shared lifecycle layer** (validate → freeze), eliminates **global singleton registries**, adds **duck-type registration validation**, and removes the **`@command` decorator pattern** in favour of explicit command construction.

**Scope:** `core/registry.py`, `agent/registry.py`, `agent/session_registry.py`, `tools/registry.py`, `commands/base.py`, `commands/handlers/`, `providers/manager.py`, `core/logging.py`, `core/bootstrap.py`

**Audit Reference:** [audit_report.md](file:///C:/Users/cuong/.gemini/antigravity/brain/99b6a24b-77a4-4191-af2f-a2f7518062dc/audit_report.md)

---

## 1. Design Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| Should `SessionAgentRegistry` be freezable? | **No** — skip freeze entirely | It is designed for runtime mutation (`/agent add`, `/agent switch`). A freeze phase would break its core purpose. |
| How to eliminate `_DEFAULT_REGISTRY`? | **Lazy discovery** — remove `@command` decorator, construct commands explicitly in bootstrap | Removes global state entirely. Commands become explicit, discoverable, testable objects. |
| How to handle `_OBSERVABILITY` global? | **Route through `AppContext`** — delete `get_observability()` | Single source of truth via DI. No hidden global access. |
| Validation strictness in `register()`? | **Duck-type friendly** — validate attribute presence (`hasattr`) not inheritance (`isinstance`) | Preserves testability with mocks/fakes that don't subclass the base. |

---

## 2. Shared Registry Lifecycle Mixin

### 2.1 `RegistryLifecycleMixin`

A mixin providing the freeze/validate lifecycle to any registry.

**Location:** New file `agent_cli/core/registry_base.py`

```python
"""Shared lifecycle mixin for registry classes."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RegistryLifecycleMixin:
    """Adds validate → freeze lifecycle to any registry.

    Subclasses call ``_assert_mutable()`` at the top of every
    mutating method.  Override ``validate()`` to add consistency
    checks that run before freeze.
    """

    _frozen: bool = False
    _registry_name: str = "unnamed"

    def freeze(self) -> None:
        """Validate internal consistency, then lock against further writes."""
        if self._frozen:
            return
        self.validate()
        self._frozen = True
        logger.info(
            "Registry '%s' frozen (%s).",
            self._registry_name,
            self._freeze_summary(),
        )

    def validate(self) -> None:
        """Check internal consistency. Override in subclasses.

        Called automatically before ``freeze()``.
        Raise ``RuntimeError`` on validation failure.
        """

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def _assert_mutable(self) -> None:
        """Guard — call at the top of every mutating method."""
        if self._frozen:
            raise RuntimeError(
                f"Registry '{self._registry_name}' is frozen; mutations rejected."
            )

    def _freeze_summary(self) -> str:
        """Override to return a human-readable summary for the freeze log."""
        return "ok"
```

### 2.2 Integration Pattern

Each registry class:

1. Inherits `RegistryLifecycleMixin` (in addition to any existing bases).
2. Sets `self._registry_name = "<name>"` in `__init__`.
3. Calls `self._assert_mutable()` as the **first line** of every mutating method.
4. Optionally overrides `validate()` for consistency checks.
5. Optionally overrides `_freeze_summary()` for useful freeze logs.

---

## 3. Per-Registry Changes

### 3.1 `ToolRegistry` (`tools/registry.py`)

**Changes:**
- Inherit `RegistryLifecycleMixin`.
- Add `self._assert_mutable()` in `register()`.
- Add duck-type validation in `register()`.
- Override `_freeze_summary()` to log tool count.

```python
class ToolRegistry(RegistryLifecycleMixin):

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}
        self._registry_name = "tools"

    def register(self, tool: BaseTool) -> None:
        self._assert_mutable()

        # Duck-type validation
        if not hasattr(tool, "name") or not str(getattr(tool, "name", "")).strip():
            raise ValueError("Tool must have a non-empty 'name' attribute.")
        if not hasattr(tool, "execute"):
            raise ValueError(
                f"Tool '{tool.name}' must have an 'execute' method."
            )
        if not hasattr(tool, "get_json_schema"):
            raise ValueError(
                f"Tool '{tool.name}' must have a 'get_json_schema' method."
            )

        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered.")
        self._tools[tool.name] = tool
        logger.debug("Registered tool: %s (category=%s)", tool.name, tool.category.name)

    def _freeze_summary(self) -> str:
        return f"{len(self._tools)} tools"
```

### 3.2 `AgentRegistry` (`agent/registry.py`)

**Changes:**
- Inherit `RegistryLifecycleMixin`.
- Add `self._assert_mutable()` in `register()`.
- Add duck-type validation for `name`, `handle_task` attributes.
- Override `_freeze_summary()`.

```python
class AgentRegistry(RegistryLifecycleMixin):

    def __init__(self) -> None:
        self._agents: Dict[str, BaseAgent] = {}
        self._registry_name = "agents"

    def register(self, agent: BaseAgent) -> None:
        self._assert_mutable()

        # Duck-type validation
        if not hasattr(agent, "name") or not str(getattr(agent, "name", "")).strip():
            raise ValueError("Agent must have a non-empty 'name' attribute.")
        if not hasattr(agent, "handle_task"):
            raise ValueError(
                f"Agent '{agent.name}' must have a 'handle_task' method."
            )

        name = agent.name
        if name in self._agents:
            raise ValueError(f"Agent '{name}' is already registered.")
        self._agents[name] = agent

    def _freeze_summary(self) -> str:
        return f"{len(self._agents)} agents: {', '.join(sorted(self._agents))}"
```

### 3.3 `CommandRegistry` (`commands/base.py`)

**Changes:**
- Inherit `RegistryLifecycleMixin`.
- Add `self._assert_mutable()` in `register()`.
- Error on duplicate keys by default; add `override` parameter.
- Remove the module-level `_DEFAULT_REGISTRY` global.
- Remove the `@command` decorator entirely.
- Remove the `absorb()` method.

```python
class CommandRegistry(RegistryLifecycleMixin):

    def __init__(self) -> None:
        self._registry: Dict[str, CommandDef] = {}
        self._registry_name = "commands"

    def register(self, cmd: CommandDef, *, override: bool = False) -> None:
        self._assert_mutable()
        key = cmd.name.lower()
        if key in self._registry and not override:
            raise ValueError(f"Command '/{cmd.name}' is already registered.")
        self._registry[key] = cmd
        logger.debug("Registered command: /%s", cmd.name)

    def _freeze_summary(self) -> str:
        return f"{len(self._registry)} commands"

# REMOVED: _DEFAULT_REGISTRY, @command decorator, absorb()
```

### 3.4 `SessionAgentRegistry` (`agent/session_registry.py`)

**Changes:**
- **No freeze/mixin** — this registry is designed for runtime mutation.
- Add duck-type validation in `add()` only.

```python
class SessionAgentRegistry:
    """Tracks which agents participate in a session and their state.

    NOT freezable — designed for runtime mutation during sessions.
    """

    def add(self, agent: BaseAgent, *, activate: bool = False) -> None:
        # Duck-type validation
        if not hasattr(agent, "name") or not str(getattr(agent, "name", "")).strip():
            raise ValueError("Agent must have a non-empty 'name' attribute.")

        if agent.name in self._agents:
            raise ValueError(f"Agent '{agent.name}' is already in this session.")
        # ... rest unchanged
```

### 3.5 `ADAPTER_TYPES` (`providers/manager.py`)

**Changes:**
- Replace mutable `Dict` with immutable `MappingProxyType`.
- Add startup validation in `ProviderManager.__init__()`.

```python
from types import MappingProxyType

_ADAPTER_TYPES_INTERNAL: Dict[str, Type[BaseLLMProvider]] = {
    "openai": OpenAIProvider,
    "azure": AzureProvider,
    "anthropic": AnthropicProvider,
    "google": GoogleProvider,
    "ollama": OllamaProvider,
    "openai_compatible": OpenAICompatibleProvider,
}

# Immutable at module scope — no runtime mutations
ADAPTER_TYPES: Mapping[str, Type[BaseLLMProvider]] = MappingProxyType(
    _ADAPTER_TYPES_INTERNAL
)


class ProviderManager:
    def __init__(self, settings, *, data_registry=None) -> None:
        # Validate adapter registry at startup
        for key, cls in ADAPTER_TYPES.items():
            if not key or not key.strip():
                raise RuntimeError(f"Empty adapter type key in ADAPTER_TYPES.")
            if not hasattr(cls, "safe_generate"):
                raise RuntimeError(
                    f"Adapter '{key}' ({cls.__name__}) missing 'safe_generate' method."
                )
        # ... rest unchanged
```

### 3.6 `DataRegistry` (`core/registry.py`)

**Changes:**
- Add structured logging via `logging.getLogger(__name__)`.
- Log offering registration count after `_load_offerings()`.
- Log resolution hits/misses in `resolve_model_spec()`.
- Replace `_declared_support()` if/elif with data-driven accessor map.

```python
logger = logging.getLogger(__name__)

class DataRegistry:
    _CAPABILITY_ACCESSORS = {
        "native_tools": lambda spec: spec.native_tools.supported,
        "effort": lambda spec: spec.effort.supported,
        "web_search": lambda spec: spec.web_search.supported,
    }

    def __init__(self) -> None:
        # ... existing loading ...
        logger.info(
            "DataRegistry loaded: %d offerings, %d providers",
            len(self._offerings),
            len(self._providers.get("providers", {})),
        )

    def resolve_model_spec(self, model_name: str) -> ModelSpec | None:
        # ... existing logic ...
        logger.debug("resolve_model_spec('%s') → %s", model_name, resolved_id)
        return deepcopy(spec)

    @staticmethod
    def _declared_support(spec: CapabilitySpec, capability_name: str) -> bool:
        accessor = DataRegistry._CAPABILITY_ACCESSORS.get(capability_name)
        return bool(accessor(spec)) if accessor else False
```

---

## 4. Command System Redesign

### 4.1 Remove `@command` Decorator

The `@command` decorator and `_DEFAULT_REGISTRY` global are deleted entirely. Commands are no longer registered at import time.

**Files removed/changed:**
- `commands/base.py`: Remove `_DEFAULT_REGISTRY`, `command()` decorator, `absorb()`.
- `commands/handlers/core.py`: Remove all `@command(...)` decorators.
- `commands/handlers/agent.py`: Same.
- `commands/handlers/sandbox.py`: Same.
- `commands/handlers/session.py`: Same.

### 4.2 Explicit Command Construction in Bootstrap

All commands are constructed explicitly in `_build_command_registry()`:

```python
def _build_command_registry() -> CommandRegistry:
    """Create a CommandRegistry and register all built-in commands explicitly."""
    from agent_cli.commands.handlers.core import (
        cmd_help, cmd_exit, cmd_clear, cmd_context,
        cmd_cost, cmd_model, cmd_effort, cmd_debug, cmd_config,
    )
    from agent_cli.commands.handlers.agent import cmd_agent
    from agent_cli.commands.handlers.sandbox import cmd_sandbox
    from agent_cli.commands.handlers.session import cmd_session

    registry = CommandRegistry()

    registry.register(CommandDef(
        name="help", description="Show all available commands",
        usage="/help [command]", shortcut="ctrl+?",
        category="System", handler=cmd_help,
    ))
    registry.register(CommandDef(
        name="exit", description="Exit the CLI",
        usage="/exit", shortcut="ctrl+q",
        category="System", handler=cmd_exit,
    ))
    registry.register(CommandDef(
        name="clear", description="Clear working memory",
        usage="/clear", shortcut="ctrl+l",
        category="Memory", handler=cmd_clear,
    ))
    registry.register(CommandDef(
        name="context", description="Show context window usage",
        usage="/context", category="Memory", handler=cmd_context,
    ))
    registry.register(CommandDef(
        name="cost", description="Show session cost breakdown",
        usage="/cost", category="Memory", handler=cmd_cost,
    ))
    registry.register(CommandDef(
        name="model", description="Switch LLM model",
        usage="/model <name>", category="Model", handler=cmd_model,
    ))
    registry.register(CommandDef(
        name="effort", description="Get or set reasoning effort",
        usage="/effort [auto|minimal|low|medium|high|max]",
        category="Model", handler=cmd_effort,
    ))
    registry.register(CommandDef(
        name="debug", description="Toggle debug logging",
        usage="/debug [on|off]", category="Model", handler=cmd_debug,
    ))
    registry.register(CommandDef(
        name="config", description="View current settings",
        usage="/config", category="Configuration", handler=cmd_config,
    ))
    registry.register(CommandDef(
        name="agent", description="Manage agents",
        usage="/agent <action> [name]",
        category="Agent", handler=cmd_agent,
    ))
    registry.register(CommandDef(
        name="sandbox", description="Sandbox file management",
        usage="/sandbox <action>",
        category="Workspace", handler=cmd_sandbox,
    ))
    registry.register(CommandDef(
        name="session", description="Session management",
        usage="/session <action>",
        category="Session", handler=cmd_session,
    ))

    logger.info(
        "Command registry built with %d commands: %s",
        len(registry.all()),
        ", ".join(c.name for c in registry.all()),
    )
    return registry
```

### 4.3 Fix `/help` Handler

The `cmd_help` handler currently imports `_DEFAULT_REGISTRY` directly. After the refactor, it must use the injected registry:

```python
async def cmd_help(args: List[str], ctx: CommandContext) -> CommandResult:
    registry = ctx.app_context.command_registry  # Use injected registry
    # ... rest unchanged
```

---

## 5. Observability Global Elimination

### 5.1 Delete `get_observability()`

Remove the module-level `_OBSERVABILITY` global and `get_observability()` function from `core/logging.py`.

**Before:**
```python
_OBSERVABILITY: Optional[ObservabilityManager] = None

def get_observability() -> Optional[ObservabilityManager]:
    return _OBSERVABILITY
```

**After:**
```python
# REMOVED: _OBSERVABILITY module-level global
# REMOVED: get_observability() function
# Access exclusively through AppContext.observability
```

### 5.2 Update `configure_observability()`

The function still creates and returns the `ObservabilityManager`, but no longer stores it globally:

```python
def configure_observability(settings, *, data_registry=None) -> ObservabilityManager:
    """Create a fresh observability manager for current app session."""
    log_dir = Path(str(getattr(settings, "log_directory", "~/.agent_cli/logs"))).expanduser()
    level = str(getattr(settings, "log_level", "INFO"))
    max_size_mb = int(getattr(settings, "log_max_file_size_mb", 50))
    return ObservabilityManager(
        log_dir=log_dir, level=level,
        max_size_mb=max_size_mb, data_registry=data_registry,
    )
```

### 5.3 Update `ObservabilityManager.shutdown()`

Remove the `global _OBSERVABILITY` reference:

```python
def shutdown(self) -> None:
    self._logger.info(
        "Observability shutdown",
        extra={"source": "observability", "data": self.metrics.to_summary()},
    )
    self.write_summary()
    _remove_managed_handlers()
```

### 5.4 Update All Call Sites

Every call to `get_observability()` must be replaced with `ctx.app_context.observability`:

| File | Current | Replacement |
|------|---------|-------------|
| `commands/handlers/core.py` (`cmd_cost`) | `get_observability()` | `ctx.app_context.observability` |
| `commands/handlers/core.py` (`cmd_debug`) | `get_observability()` | `ctx.app_context.observability` |

---

## 6. Bootstrap Freeze Sequence

At the end of `create_app()`, after all registries are populated, freeze them in order:

```python
# 12. Freeze all registries — no further mutations after this point.
tool_registry.freeze()
agent_registry.freeze()
cmd_registry.freeze()

logger.info(
    "All registries frozen — bootstrap complete.",
)
```

**Order matters:** Tools must freeze before agents (agents reference tools). Agents must freeze before orchestrator wiring. Commands freeze last since they reference all other components.

**`SessionAgentRegistry` is NOT frozen** — it is designed for runtime mutation.

---

## 7. File Impact Summary

| File | Change Type | Description |
|------|------------|-------------|
| `core/registry_base.py` | **NEW** | `RegistryLifecycleMixin` |
| `tools/registry.py` | MODIFY | Add mixin, validation, freeze |
| `agent/registry.py` | MODIFY | Add mixin, validation, freeze |
| `agent/session_registry.py` | MODIFY | Add duck-type validation only (no freeze) |
| `commands/base.py` | MODIFY | Add mixin, duplicate guard, remove `_DEFAULT_REGISTRY`, `@command`, `absorb()` |
| `commands/handlers/core.py` | MODIFY | Remove `@command` decorators, fix `cmd_help` to use injected registry, replace `get_observability()` |
| `commands/handlers/agent.py` | MODIFY | Remove `@command` decorators |
| `commands/handlers/sandbox.py` | MODIFY | Remove `@command` decorators |
| `commands/handlers/session.py` | MODIFY | Remove `@command` decorators |
| `providers/manager.py` | MODIFY | `MappingProxyType` for `ADAPTER_TYPES`, startup validation |
| `core/logging.py` | MODIFY | Remove `_OBSERVABILITY` global, `get_observability()` |
| `core/registry.py` | MODIFY | Add logging, data-driven `_declared_support` |
| `core/bootstrap.py` | MODIFY | Explicit command registration, freeze calls, import changes |

**Total:** 1 new file, 12 modified files.

---

## 8. Testing Requirements

### 8.1 Freeze Behaviour

```python
def test_tool_registry_freeze():
    """Mutations after freeze() should raise RuntimeError."""
    registry = ToolRegistry()
    registry.register(mock_tool("read_file"))
    registry.freeze()
    with pytest.raises(RuntimeError, match="frozen"):
        registry.register(mock_tool("another_tool"))


def test_tool_registry_freeze_idempotent():
    """Calling freeze() twice is safe."""
    registry = ToolRegistry()
    registry.freeze()
    registry.freeze()  # no error


def test_agent_registry_freeze():
    registry = AgentRegistry()
    registry.register(mock_agent("default"))
    registry.freeze()
    with pytest.raises(RuntimeError, match="frozen"):
        registry.register(mock_agent("custom"))


def test_command_registry_duplicate_error():
    registry = CommandRegistry()
    registry.register(CommandDef(name="help", description="...", handler=noop))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(CommandDef(name="help", description="...", handler=noop))


def test_command_registry_override():
    registry = CommandRegistry()
    registry.register(CommandDef(name="help", description="v1", handler=noop))
    registry.register(
        CommandDef(name="help", description="v2", handler=noop),
        override=True,
    )
    assert registry.get("help").description == "v2"
```

### 8.2 Duck-Type Validation

```python
def test_tool_registry_rejects_missing_name():
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="non-empty 'name'"):
        registry.register(object())  # no name attribute


def test_tool_registry_rejects_missing_execute():
    fake = SimpleNamespace(name="test", get_json_schema=lambda: {})
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="'execute' method"):
        registry.register(fake)
```

### 8.3 `SessionAgentRegistry` Not Frozen

```python
def test_session_registry_allows_runtime_mutation():
    """SessionAgentRegistry is NOT freezable."""
    reg = SessionAgentRegistry()
    reg.add(mock_agent("a"))
    reg.add(mock_agent("b"))  # Should always work
    assert not hasattr(reg, "freeze")  # No freeze method at all
```

### 8.4 Observability via DI

```python
def test_cmd_cost_uses_injected_observability():
    """cmd_cost reads from ctx.app_context.observability, not global."""
    obs = ObservabilityManager(log_dir=tmp_path, level="INFO", max_size_mb=1)
    ctx = make_test_context(observability=obs)
    result = await cmd_cost([], ctx)
    assert result.success
```

### 8.5 ADAPTER_TYPES Immutability

```python
def test_adapter_types_is_immutable():
    with pytest.raises(TypeError):
        ADAPTER_TYPES["custom"] = object  # MappingProxyType rejects writes
```

---

## 9. Migration Checklist

- [ ] Create `core/registry_base.py` with `RegistryLifecycleMixin`
- [ ] Update `ToolRegistry` — mixin, validation, freeze
- [ ] Update `AgentRegistry` — mixin, validation, freeze
- [ ] Update `SessionAgentRegistry` — duck-type validation only
- [ ] Update `CommandRegistry` — mixin, duplicate guard, remove globals
- [ ] Remove `@command` decorator and `_DEFAULT_REGISTRY` from `commands/base.py`
- [ ] Remove `@command` decorators from all handler files
- [ ] Rewrite `_build_command_registry()` for explicit registration
- [ ] Fix `cmd_help` to use `ctx.app_context.command_registry`
- [ ] Make `ADAPTER_TYPES` immutable via `MappingProxyType`
- [ ] Add adapter validation in `ProviderManager.__init__()`
- [ ] Remove `_OBSERVABILITY` global and `get_observability()` from `core/logging.py`
- [ ] Update `cmd_cost` and `cmd_debug` to use `ctx.app_context.observability`
- [ ] Add logging to `DataRegistry`
- [ ] Replace `_declared_support()` with data-driven accessor map
- [ ] Add freeze sequence at end of `create_app()`
- [ ] Write tests for all freeze/validation/immutability behaviour
- [ ] Run full test suite, verify no regressions
