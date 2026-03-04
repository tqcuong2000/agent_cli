# Multi-Agent System — User-Driven Agent Management

## Overview
The Multi-Agent System gives users **explicit, direct control** over which agent handles their requests. There is no routing LLM and no middleman classification. The system starts with a single default agent. Users add specialized agents to their session via the `/agent` command and switch between them using `!mention` tags inline.

This design prioritizes **clarity, simplicity, and user control** over automatic routing. The user always knows exactly which agent is working.

---

## 1. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Agent Selection** | User-driven via `!mention` tags | Zero ambiguity. No routing LLM overhead. User always in control. |
| **Default Agent** | `DefaultAgent` (configurable via `config.toml`) | Works out of the box. Power users can change their default. |
| **Agent Addition** | Explicit via `/agent add <name>` command | Prevents typos from silently failing. User is always aware of active agents. |
| **Context Passing** | Summarized session context on agent switch | Prevents token budget blowout from raw `session.messages`. |
| **Agent State on Re-activation** | Stateless — fresh Working Memory from summary | Simple, consistent. No stale context. |
| **Effort Scope** | Global default + per-agent override in config | Flexible. Researcher can stay LOW while coder goes HIGH. |
| **PLAN Mode** | Removed (for now) | Simplifies the system. User manually breaks complex tasks into smaller requests. |

---

## 2. Agent Lifecycle & Status

Each agent in a session has one of three statuses:

```python
from enum import Enum, auto

class AgentStatus(Enum):
    ACTIVE   = auto()  # Currently handling user requests
    IDLE     = auto()  # Added to session but not the current handler
    INACTIVE = auto()  # Explicitly disabled by user, excluded from session
```

### Status Rules

| Status | Description | Receives Requests? |
|---|---|---|
| `ACTIVE` | The agent currently working with the user | ✅ Yes |
| `IDLE` | Previously active, now waiting | ❌ No |
| `INACTIVE` | User explicitly disabled this agent | ❌ No |

**Invariant:** At most one agent can be `ACTIVE` at any time.

---

## 3. Session Agent Registry

The `SessionAgentRegistry` tracks which agents are participating in the current session and their statuses. It is distinct from the global `AgentRegistry` which holds all *available* agent definitions.

```python
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class SessionAgent:
    """An agent's state within a session."""
    name: str
    status: AgentStatus = AgentStatus.IDLE
    agent_instance: "BaseAgent" = None  # The actual agent object
    
    @property
    def is_available(self) -> bool:
        return self.status != AgentStatus.INACTIVE


class SessionAgentRegistry:
    """
    Tracks agents participating in the current session.
    Manages status transitions and the active agent pointer.
    """
    
    def __init__(self):
        self._agents: Dict[str, SessionAgent] = {}
        self._active_name: Optional[str] = None
    
    def add(self, agent: "BaseAgent", activate: bool = False) -> None:
        """Add an agent to the session."""
        if agent.name in self._agents:
            raise ValueError(f"Agent '{agent.name}' is already in this session.")
        
        status = AgentStatus.ACTIVE if activate else AgentStatus.IDLE
        self._agents[agent.name] = SessionAgent(
            name=agent.name,
            status=status,
            agent_instance=agent
        )
        
        if activate:
            self._set_active(agent.name)
    
    def switch_to(self, name: str) -> "BaseAgent":
        """
        Switch the active agent. 
        The current active agent becomes IDLE.
        The target agent becomes ACTIVE.
        Returns the newly activated agent instance.
        """
        if name not in self._agents:
            raise KeyError(f"Agent '{name}' is not in this session. Use /agent add {name} first.")
        
        target = self._agents[name]
        if target.status == AgentStatus.INACTIVE:
            raise ValueError(f"Agent '{name}' is inactive. Use /agent enable {name} first.")
        
        self._set_active(name)
        return target.agent_instance
    
    def disable(self, name: str) -> None:
        """Set an agent to INACTIVE. Cannot disable the active agent."""
        if name == self._active_name:
            raise ValueError("Cannot disable the active agent. Switch to another agent first.")
        if name in self._agents:
            self._agents[name].status = AgentStatus.INACTIVE
    
    def enable(self, name: str) -> None:
        """Re-enable an INACTIVE agent (sets to IDLE)."""
        if name in self._agents:
            self._agents[name].status = AgentStatus.IDLE
    
    def remove(self, name: str) -> None:
        """Remove an agent from the session entirely."""
        if name == self._active_name:
            raise ValueError("Cannot remove the active agent. Switch to another agent first.")
        self._agents.pop(name, None)
    
    @property
    def active_agent(self) -> Optional["BaseAgent"]:
        if self._active_name and self._active_name in self._agents:
            return self._agents[self._active_name].agent_instance
        return None
    
    @property
    def active_name(self) -> Optional[str]:
        return self._active_name
    
    def list_agents(self) -> List[SessionAgent]:
        """Return all agents in the session with their statuses."""
        return list(self._agents.values())
    
    def has(self, name: str) -> bool:
        return name in self._agents
    
    # ── Private ──────────────────────────────────────────────
    
    def _set_active(self, name: str) -> None:
        """Set one agent as ACTIVE, demote the current active to IDLE."""
        if self._active_name and self._active_name in self._agents:
            current = self._agents[self._active_name]
            if current.status == AgentStatus.ACTIVE:
                current.status = AgentStatus.IDLE
        
        self._agents[name].status = AgentStatus.ACTIVE
        self._active_name = name
```

---

## 4. The Orchestrator (Extended)

> **Existing file:** `agent_cli/core/orchestrator.py`

The current `Orchestrator` already handles slash commands, task lifecycle, and single-agent delegation. Phase 6 **extends** it — no rewrite. The key additions are:
1. Accept `AgentRegistry` and `SessionAgentRegistry` as constructor arguments.
2. Add `_parse_mention()` to extract `!agent_name` tags.
3. Add `_switch_agent()` and `_generate_session_summary()` for agent handoff.
4. Update `_route_to_agent()` to delegate to `session_agents.active_agent` instead of `_default_agent`.

```python
# agent_cli/core/orchestrator.py — Phase 6 additions
#
# Existing fields kept:
#   self._event_bus, self._state_manager, self._command_parser,
#   self._session_manager, self._commands, self._subscription_id
#
# New fields added:

class Orchestrator:
    def __init__(
        self,
        event_bus: AbstractEventBus,
        state_manager: AbstractStateManager,
        default_agent: BaseAgent,                           # Kept for backward compat
        command_parser: Optional["CommandParser"] = None,
        session_manager: Optional[AbstractSessionManager] = None,
        # ── Phase 6 additions ──
        agent_registry: Optional["AgentRegistry"] = None,
        session_agents: Optional[SessionAgentRegistry] = None,
    ) -> None:
        # ... existing init ...
        self._agent_registry = agent_registry
        self._session_agents = session_agents
    
    # ── Updated from handle_request() ────────────────────────
    
    async def handle_request(self, text: str) -> Optional[str]:
        text = text.strip()
        
        # Slash-command interception (unchanged — uses existing CommandParser)
        if text.startswith("/"):
            if self._command_parser is not None:
                result = await self._command_parser.execute(text)
                if result.message:
                    await self._event_bus.publish(
                        AgentMessageEvent(source="command_system", content=result.message)
                    )
                return result.message
            return await self._handle_command(text)
        
        # ── NEW: Parse !mention tag ──────────────────────────
        target_name, clean_message = self._parse_mention(text)
        
        if target_name and self._session_agents:
            if not self._session_agents.has(target_name):
                await self._emit_error(
                    f"Agent '{target_name}' is not in this session. "
                    f"Use `/agent add {target_name}` first."
                )
                return None
            if target_name != self._session_agents.active_name:
                await self._switch_agent(target_name)
        
        return await self._route_to_agent(clean_message)
    
    # ── Updated _route_to_agent() ────────────────────────────
    
    async def _route_to_agent(self, text: str) -> str:
        # Resolve active agent (Phase 6 vs legacy)
        agent = (
            self._session_agents.active_agent
            if self._session_agents
            else self._default_agent
        )
        
        # Existing session persistence flow (unchanged)
        active_session = self._get_or_create_active_session()
        session_messages = list(active_session.messages) if active_session else None
        
        task = await self._state_manager.create_task(
            description=text[:100],
            assigned_agent=agent.name,
        )
        # ... rest of existing _route_to_agent logic stays the same ...
    
    # ── NEW: Mention Parsing ────────────────────────────────────
    
    def _parse_mention(self, message: str) -> tuple[Optional[str], str]:
        """
        Extract the first !mention tag from the message.
        
        Examples:
            "!coder fix the bug"      → ("coder", "fix the bug")
            "fix the bug"             → (None, "fix the bug")
            "!coder !researcher help" → ("coder", "!researcher help")
        """
        import re
        match = re.match(r'^!(\w+)\s*(.*)', message, re.DOTALL)
        if match:
            return match.group(1), match.group(2).strip()
        return None, message
    
    # ── NEW: Agent Switching ───────────────────────────────────
    
    async def _switch_agent(self, target_name: str) -> None:
        """Switch active agent, generate session summary, reset working memory."""
        old_name = self._session_agents.active_name
        
        # Reuse SummarizingMemoryManager's summarization infrastructure
        summary = await self._generate_session_summary()
        
        new_agent = self._session_agents.switch_to(target_name)
        
        # Stateless re-activation: fresh Working Memory
        new_agent.memory.reset_working()
        system_prompt = await new_agent.build_system_prompt("")
        new_agent.memory.add_working_event({"role": "system", "content": system_prompt})
        
        if summary:
            new_agent.memory.add_working_event({
                "role": "system",
                "content": f"[Session Context Summary]\n{summary}"
            })
        
        await self._event_bus.emit(AgentMessageEvent(
            source="orchestrator",
            agent_name="system",
            content=f"Switched from **{old_name}** → **{target_name}**",
            is_monologue=False
        ))
    
    # ── NEW: Session Summary (reuses existing summarizer) ────
    
    async def _generate_session_summary(self) -> str:
        """
        Generate a summary of the session conversation for agent handoff.
        
        Reuses the existing SummarizingMemoryManager infrastructure:
          - _summarize_with_model()  → cheap LLM call (gpt-4o-mini)
          - _heuristic_summary()     → zero-cost regex fallback
        
        Source: session.messages (already contains full history)
        """
        session = self._get_or_create_active_session()
        if not session or not session.messages:
            return ""
        
        # Take last N messages from the persistent session
        recent = session.messages[-30:]
        
        # Filter to user/assistant only (skip tool outputs)
        filtered = [
            msg for msg in recent
            if msg.get("role") in ("user", "assistant")
        ]
        
        if not filtered:
            return ""
        
        # Delegate to the active agent's memory manager (SummarizingMemoryManager)
        # which already has _summarize_middle_messages() with model + heuristic fallback
        active_agent = self._session_agents.active_agent
        if hasattr(active_agent.memory, '_summarize_middle_messages'):
            return await active_agent.memory._summarize_middle_messages(filtered)
        
        # Fallback: simple truncated dump
        lines = []
        for msg in filtered[-10:]:
            role = msg.get("role", "?")
            content = str(msg.get("content", ""))[:200]
            lines.append(f"[{role}]: {content}")
        return "\n".join(lines)
    
    async def _emit_error(self, message: str) -> None:
        await self._event_bus.emit(AgentMessageEvent(
            source="orchestrator",
            agent_name="system",
            content=f"⚠ {message}",
            is_monologue=False
        ))
```

> **Key:** The Orchestrator is extended, not rewritten. All existing `_route_to_agent()`, `_handle_command()`, session persistence, and task state management stay intact.

---

## 5. The `/agent` Command

> **Existing pattern:** `agent_cli/commands/handlers/core.py` uses `@command` decorator → auto-registers into `CommandRegistry` → `CommandParser` dispatches.

The `/agent` command follows the same `@command` decorator pattern used by `/help`, `/model`, `/effort`, etc. It accesses registries via `CommandContext.app_context`.

```python
# agent_cli/commands/handlers/agent.py — NEW FILE
#
# Uses the existing @command decorator (agent_cli/commands/base.py)
# which auto-registers into _DEFAULT_REGISTRY at import time.
# Bootstrap absorbs it into the live CommandRegistry.

from agent_cli.commands.base import CommandContext, CommandResult, command

@command(
    name="agent",
    description="Manage agents in the session",
    usage="/agent [list|add|remove|enable|disable|default] [name]",
    category="Agent",
)
async def cmd_agent(args: List[str], ctx: CommandContext) -> CommandResult:
    """
    /agent                    — List agents in this session (with status)
    /agent list               — List all AVAILABLE agents (global registry)
    /agent add <name>         — Add an agent to this session
    /agent remove <name>      — Remove an agent from this session
    /agent enable <name>      — Re-enable an inactive agent
    /agent disable <name>     — Disable an agent
    /agent default <name>     — Set the default agent (persisted to config)
    """
    # Access registries through existing CommandContext.app_context
    app = ctx.app_context
    session_agents = app.orchestrator._session_agents   # SessionAgentRegistry
    agent_registry = app.agent_registry                 # Global AgentRegistry (new AppContext field)
    
    if not args:
        return _format_session_agents(session_agents)
    
    subcommand = args[0].lower()
    
    if subcommand == "list":
        return _format_available_agents(agent_registry, session_agents)
    
    elif subcommand == "add" and len(args) > 1:
        name = args[1]
        agent = agent_registry.get(name)
        if not agent:
            available = [a.name for a in agent_registry.get_all()]
            return CommandResult(
                success=False,
                message=f"Unknown agent '{name}'.\nAvailable: {', '.join(available)}"
            )
        try:
            session_agents.add(agent)
        except ValueError as e:
            return CommandResult(success=False, message=str(e))
        return CommandResult(
            success=True,
            message=f"✓ Agent **{name}** added to session (IDLE).\nUse `!{name}` to switch."
        )
    
    elif subcommand == "remove" and len(args) > 1:
        try:
            session_agents.remove(args[1])
            return CommandResult(success=True, message=f"✓ Agent **{args[1]}** removed.")
        except ValueError as e:
            return CommandResult(success=False, message=str(e))
    
    elif subcommand == "enable" and len(args) > 1:
        session_agents.enable(args[1])
        return CommandResult(success=True, message=f"✓ Agent **{args[1]}** re-enabled (IDLE).")
    
    elif subcommand == "disable" and len(args) > 1:
        try:
            session_agents.disable(args[1])
            return CommandResult(success=True, message=f"✓ Agent **{args[1]}** disabled.")
        except ValueError as e:
            return CommandResult(success=False, message=str(e))
    
    elif subcommand == "default" and len(args) > 1:
        name = args[1]
        if not agent_registry.get(name):
            return CommandResult(success=False, message=f"Unknown agent '{name}'.")
        ctx.settings.default_agent = name
        return CommandResult(success=True, message=f"✓ Default agent set to **{name}**.")
    
    return CommandResult(
        success=False,
        message="Usage: /agent [list|add|remove|enable|disable|default] [name]"
    )
```

> **Integration:** Add `import agent_cli.commands.handlers.agent` to `_build_command_registry()` in `bootstrap.py` alongside the existing `core`, `sandbox`, and `session` imports.

---

## 6. The `!mention` Tag Syntax

Users switch between agents using inline mention tags at the **start** of their message:

```
!coder fix the bug in auth.py          → routes to coder
!researcher what does main() do?       → routes to researcher
fix the bug in auth.py                 → routes to current active agent (no tag)
!coder !researcher help                → routes to coder (first tag wins)
```

### Rules
1. Only the **first** `!mention` tag in a message is used.
2. The tag must reference an agent **already added** to the session via `/agent add`.
3. If the mentioned agent is `INACTIVE`, the request is rejected with an error.
4. If no tag is present, the request goes to the currently `ACTIVE` agent.
5. The `!` prefix was chosen to avoid conflicts with the `/` command prefix.

---

## 7. Agent Switching & Context Flow

When the user switches agents, the following happens:

```
┌──────────────────────────────────────────────────────────┐
│  User types: "!researcher what tests exist?"             │
└─────────────────────────┬────────────────────────────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │  Orchestrator parses  │
              │  mention: "researcher"│
              └─────┬─────────────────┘
                    │
                    ▼ (researcher ≠ current active "coder")
              ┌───────────────────────────────────────────┐
              │  1. Generate session summary              │
              │     (LLM summarizes last 50 messages)     │
              │  2. Set coder → IDLE                      │
              │  3. Set researcher → ACTIVE               │
              │  4. Reset researcher's Working Memory      │
              │  5. Inject: system_prompt + summary        │
              │  6. Delegate request to researcher         │
              └───────────────────────────────────────────┘
```

### Why Summarize Instead of Passing Raw Messages?

| Approach | Tokens | Quality |
|---|---|---|
| Pass all `session.messages` | 50K+ (blows budget) | Tool outputs pollute context |
| Pass only user messages | ~5K | Missing agent reasoning and decisions |
| **Summarize (chosen)** | **~500-1000** | **Compact, focused, decision-aware** |

> **Existing infrastructure:** `SummarizingMemoryManager` in `agent_cli/memory/summarizer.py` already has:
> - `_summarize_with_model()` → Uses cheap model (`gpt-4o-mini`) with structured prompt (Goals, Decisions, Actions, Tools, Files, Open Items)
> - `_heuristic_summary()` → Zero-cost regex-based fallback (no API call)
> - `_build_summary_prompt()` → Token-budget-aware prompt construction
> - `_normalize_summary()` → Strips XML artifacts from LLM output
>
> Phase 6 reuses this via `active_agent.memory._summarize_middle_messages(filtered)`. No new summarization code needed.

---

## 8. Default Agent Configuration

> **Existing file:** `agent_cli/core/config.py` — `AgentSettings` class

### Built-in Default

The system ships with a `DefaultAgent` (existing `agent_cli/agent/default.py`) — a general-purpose agent with access to all tools. Currently registered as `"Generalist"` in `bootstrap.py:402`. Phase 6 renames this to `"default"` for consistency.

### Config Change

```python
# In AgentSettings (agent_cli/core/config.py)
# REPLACE:
#   execution_mode: str = "plan"    ← Remove (PLAN mode deleted)
# ADD:
    default_agent: str = Field(
        default="default",
        description="Name of the agent to activate on session start.",
    )
    agents: Dict[str, Any] = Field(
        default_factory=dict,
        description="User-defined agent configs from [agents.*] TOML sections.",
    )
```

### User Override via `config.toml`

```toml
# .agent_cli/config.toml

[agent]
default = "coder"   # Set the default active agent on session start
```

### Resolution Order

```
1. config.toml [agent].default     ← User preference (if agent exists)
2. Built-in "default" agent         ← Fallback
```

If the user configures a `default` that doesn't exist in the global registry, the system falls back to the built-in `DefaultAgent` and logs a warning.

---

## 9. Effort Level Management

> **Already implemented:** `BaseAgent.effort` property in `agent_cli/agent/base.py:176-179` and `/effort` command in `agent_cli/commands/handlers/core.py:261-304`

Effort is managed at **global scope** with optional **per-agent overrides** in config. **No changes needed** — the existing resolution already works:

### Existing Code (works as-is)

```python
# agent_cli/agent/base.py
@property
def effort(self) -> EffortLevel:
    """Resolve: agent config override → global default."""
    return self.config.effort_level or self.settings.default_effort_level
```

### Global Effort (existing `/effort` command)

```bash
/effort high      → All agents use HIGH effort
/effort low       → All agents use LOW effort
```

### Per-Agent Override in Config

```toml
[agents.researcher]
effort_level = "LOW"    # Researcher always uses LOW regardless of global

[agents.coder]
# No effort_level specified → uses global default
```

### Resolution Order (already implemented)

```
1. AgentConfig.effort_level (if set)    ← Per-agent override
2. AgentSettings.default_effort_level   ← Global (set via /effort)
3. Built-in default: MEDIUM             ← Fallback
```

---

## 10. User Settings & Agent Model Override

When the user changes model settings, those changes apply to the **active agent** and are saved to its definition:

```bash
/model claude-3-5-sonnet     → Sets the active agent's model to claude-3-5-sonnet
```

This is persisted in the agent's config so it survives session restarts:

```toml
# Auto-saved to config.toml
[agents.coder]
model = "claude-3-5-sonnet"
```

---

## 11. Bootstrap Flow

> **Existing file:** `agent_cli/core/bootstrap.py` — `create_app()` factory
>
> **Current:** Steps 1-10 create core components, step 11 creates a single `DefaultAgent` and calls `register_default_agent()`. Phase 6 replaces step 11 with multi-agent setup.

```
┌──────────────────────────────────────────────────────┐
│  create_app() — existing steps 1-10 UNCHANGED        │
│                                                       │
│  Steps 1-10: DataRegistry, Settings, EventBus,       │
│  StateManager, Providers, Tools, SchemaValidator,     │
│  PromptBuilder, SessionManager, CommandSystem         │
└───────────────────┬──────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────────────┐
│  Step 11 — REPLACED: Multi-Agent Setup               │
│                                                       │
│  11a. Create AgentRegistry (global)                  │
│  11b. Create built-in agents (default, coder,        │
│       researcher) — each with OWN memory_manager     │
│  11c. Load user-defined agents from [agents.*] TOML  │
│  11d. Register all in AgentRegistry                  │
│  11e. Create SessionAgentRegistry                    │
│  11f. Resolve default agent (config → fallback)      │
│  11g. Add default agent (activate=True)              │
│  11h. Create Orchestrator with both registries       │
│  11i. Import agent command handler                   │
└──────────────────────────────────────────────────────┘
```

### Per-Agent Memory Isolation

> **⚠ Critical change:** Currently `memory_manager` is a single shared instance in `AppContext`. Each agent must get its **own** `SummarizingMemoryManager` instance. The shared `AppContext.memory_manager` remains as the default (for backward compat and commands like `/context`).

```python
# In create_app(), for each agent:
def _create_agent_memory(providers, settings, data_registry, model_name):
    context_budget = data_registry.get_context_budget()
    return SummarizingMemoryManager(
        token_counter=providers.get_token_counter(model_name),
        token_budget=providers.get_token_budget(
            model_name,
            response_reserve=4096,
            compaction_threshold=float(context_budget.get("compaction_threshold", 0.80)),
        ),
        model_name=model_name,
        summarizer_provider_factory=providers.get_provider,
        data_registry=data_registry,
    )
```

---

## 12. Agent Registry (Global — All Available Agents)

The global `AgentRegistry` holds all agent definitions. Unchanged from the original design:

```python
class AgentRegistry:
    """
    Global catalogue of all available agent definitions.
    Loaded on startup from built-in defaults + user TOML config.
    """
    
    def __init__(self):
        self._agents: Dict[str, "BaseAgent"] = {}
        self._configs: Dict[str, "AgentConfig"] = {}
    
    def register(self, agent: "BaseAgent") -> None:
        """Register a fully instantiated agent."""
        if agent.name in self._agents:
            raise ValueError(f"Agent '{agent.name}' is already registered.")
        self._agents[agent.name] = agent
        self._configs[agent.name] = agent.config
    
    def get(self, name: str) -> Optional["BaseAgent"]:
        return self._agents.get(name)
    
    def get_all(self) -> List["BaseAgent"]:
        return list(self._agents.values())
```

---

## 13. User-Defined Agents

Users define custom agents in their project's config file:

```toml
# .agent_cli/config.toml

[agents.devops]
description = "Infrastructure and deployment specialist"
persona = """You are a DevOps engineer specializing in Docker, Kubernetes,
and CI/CD pipelines. You prefer Terraform for infrastructure-as-code."""
model = "claude-3-5-sonnet"
effort_level = "HIGH"
tools = [
    "read_file", "write_file", "edit_file",
    "run_command", "spawn_terminal", "read_terminal",
    "kill_terminal", "wait_for_terminal"
]
show_thinking = true

[agents.reviewer]
description = "Code review specialist"
persona = "You are a senior code reviewer focused on security, performance, and maintainability."
model = "gpt-4o"
effort_level = "MEDIUM"
tools = ["read_file", "grep_search", "find_files"]
show_thinking = false
```

User-defined agents are loaded into the global `AgentRegistry` on startup and become available for `/agent add`.

---

## 14. Complete Interaction Example

```
Session Start:
  🟢 default (ACTIVE)
  
User: "What is a unit test?"
  → default agent handles it, explains unit tests.

User: "/agent add coder"
  ✓ Agent coder added to session (status: IDLE).

User: "/agent add researcher"
  ✓ Agent researcher added to session (status: IDLE).

User: "/agent"
  Session Agents:
    🟢 default — ACTIVE
    ⚪ coder — IDLE
    ⚪ researcher — IDLE

User: "!coder write me a pytest example"
  → Orchestrator:
      1. Summarizes session ("User asked about unit tests...")
      2. default → IDLE, coder → ACTIVE
      3. Coder receives summary + request
      4. Coder writes the pytest example (has context about unit tests)

User: "now add a fixture for database connection"
  → No mention tag → goes to current active (coder)
  → Coder handles it directly

User: "!researcher find all test files in this project"  
  → Orchestrator:
      1. Summarizes session (includes unit test discussion + pytest example)
      2. coder → IDLE, researcher → ACTIVE
      3. Researcher receives summary + request
      4. Researcher searches and reports

User: "/agent disable default"
  ✓ Agent default disabled.

User: "/agent"
  Session Agents:
    🔴 default — INACTIVE
    ⚪ coder — IDLE
    🟢 researcher — ACTIVE
```

---

## 15. Testing Strategy

```python
import pytest

def test_session_agent_registry_add_and_switch():
    registry = SessionAgentRegistry()
    coder = MockAgent("coder")
    researcher = MockAgent("researcher")
    
    registry.add(coder, activate=True)
    registry.add(researcher)
    
    assert registry.active_name == "coder"
    assert registry._agents["researcher"].status == AgentStatus.IDLE
    
    registry.switch_to("researcher")
    assert registry.active_name == "researcher"
    assert registry._agents["coder"].status == AgentStatus.IDLE

def test_cannot_remove_active_agent():
    registry = SessionAgentRegistry()
    coder = MockAgent("coder")
    registry.add(coder, activate=True)
    
    with pytest.raises(ValueError, match="Cannot remove the active agent"):
        registry.remove("coder")

def test_cannot_disable_active_agent():
    registry = SessionAgentRegistry()
    coder = MockAgent("coder")
    registry.add(coder, activate=True)
    
    with pytest.raises(ValueError, match="Cannot disable the active agent"):
        registry.disable("coder")

def test_mention_must_reference_session_agent():
    """!mention for an agent not in session should error."""
    registry = SessionAgentRegistry()
    
    with pytest.raises(KeyError, match="not in this session"):
        registry.switch_to("unknown")

def test_parse_mention_tag():
    orchestrator = Orchestrator(...)
    
    name, msg = orchestrator._parse_mention("!coder fix the bug")
    assert name == "coder"
    assert msg == "fix the bug"
    
    name, msg = orchestrator._parse_mention("fix the bug")
    assert name is None
    assert msg == "fix the bug"
    
    # First tag wins
    name, msg = orchestrator._parse_mention("!coder !researcher help")
    assert name == "coder"

@pytest.mark.asyncio
async def test_switch_generates_summary():
    """Switching agents should produce a context summary."""
    orchestrator = Orchestrator(...)
    # ... setup coder as active, researcher as idle ...
    
    await orchestrator._switch_agent("researcher")
    
    # Verify researcher's memory contains the summary
    context = orchestrator.session_agents.active_agent.memory.get_working_context()
    assert any("Session Context Summary" in msg["content"] for msg in context)

@pytest.mark.asyncio
async def test_switch_resets_working_memory():
    """New agent should start with fresh Working Memory, not inherited state."""
    orchestrator = Orchestrator(...)
    # ... coder works on a task, accumulates tool results ...
    
    await orchestrator._switch_agent("researcher")
    
    # Researcher should only have system_prompt + summary, not coder's tool results
    context = orchestrator.session_agents.active_agent.memory.get_working_context()
    assert len(context) <= 3  # system_prompt + summary + maybe prior context

def test_effort_resolution_order():
    """Per-agent override > global > built-in default."""
    config = AgentConfig(effort_level=EffortLevel.LOW)  # Agent override
    global_effort = EffortLevel.HIGH
    
    resolved = resolve_effort(
        task_override=None,
        agent_config=config,
        global_default=global_effort
    )
    assert resolved == EffortLevel.LOW  # Agent override wins
```

---

## 16. Architecture Integration Summary

### Files Reused As-Is

| File | What's Reused |
|---|---|
| `agent/base.py` | `BaseAgent` ABC, `AgentConfig` dataclass, `effort` property, `handle_task()` |
| `agent/default.py` | `DefaultAgent` — becomes the built-in default agent |
| `agent/memory.py` | `BaseMemoryManager`, `WorkingMemoryManager` — per-agent instances |
| `memory/summarizer.py` | `SummarizingMemoryManager` — reused for agent-switch summaries |
| `commands/base.py` | `@command` decorator, `CommandRegistry`, `CommandParser` |
| `commands/handlers/core.py` | `/help`, `/effort`, `/model`, `/clear`, `/context`, `/config` |
| `session/base.py` | `Session` model with `messages` list |
| `data/prompts/` | Prompt template loading via `DataRegistry` |

### Files Modified

| File | Change |
|---|---|
| `core/orchestrator.py` | Add `_session_agents`, `_agent_registry`, `_parse_mention()`, `_switch_agent()` |
| `core/bootstrap.py` | Replace single-agent step 11 with multi-agent setup |
| `core/config.py` | Replace `execution_mode` with `default_agent` + `agents` dict |
| `commands/base.py` | No change needed — `CommandContext.app_context` already provides access |
| `commands/handlers/core.py` | Remove `/mode` command |

### New Files

| File | Purpose |
|---|---|
| `agent/registry.py` | `AgentRegistry` (global catalogue) |
| `agent/session_registry.py` | `SessionAgentRegistry` + `AgentStatus` + `SessionAgent` |
| `agent/agents/coder.py` | `CoderAgent` with coding persona |
| `agent/agents/researcher.py` | `ResearcherAgent` with research persona |
| `commands/handlers/agent.py` | `/agent` command handler |
| `data/prompts/coder_persona.txt` | Coder persona template |
| `data/prompts/researcher_persona.txt` | Researcher persona template |

---

## 17. Comparison: Old vs. New Architecture

| Aspect | Old Architecture | New Architecture |
|---|---|---|
| Agent selection | Routing LLM decides (automatic) | User decides via `!mention` (explicit) |
| FAST/PLAN mode | LLM classifies, then user overrides | Removed. User handles complexity manually. |
| Context on switch | Working Memory isolation + brief handoff summary | Session summary via existing `SummarizingMemoryManager` |
| Overhead per request | 1 extra LLM call (routing) | 0 extra LLM calls (unless switching) |
| User control | Indirect (override via `/agent`, `/mode`) | Direct (`!tag`, `/agent add`) |
| Complexity | High (routing LLM, plan parser, mode FSM) | Low (mention parser, status enum) |
| Ambiguity | Router may pick wrong agent/mode | None — user always decides |
| Code reuse | N/A (greenfield) | Reuses summarizer, command system, memory, session |
