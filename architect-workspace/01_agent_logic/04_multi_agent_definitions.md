# Orchestrator Routing & Agent Registry

## Overview
The Orchestrator is the **decision-maker** that sits between the user's request and the agents that execute it. It determines: *"Which agent(s) should handle this? Is it a simple task (Fast-Path) or a complex plan (ExecutionPlan)?"*

This architecture defines how agents are registered and validated, how the Orchestrator routes tasks via a lightweight LLM classification call, and how user-defined agents integrate seamlessly with built-in system agents.

---

## 1. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Routing Strategy** | LLM-based classification (LOW effort, fast model) | Handles ambiguous prompts. Can decide Fast-Path vs. ExecutionPlan in one call. |
| **Agent Instantiation** | Singleton per session | Reused across tasks. Working Memory `reset_working()` per task. No per-task overhead. |
| **Capability Declaration** | Structured tags + description | Tags enable deterministic pre-filtering; LLM handles final selection. |
| **Secrets Management** | Merged into Config Management spec | Secrets are fundamentally configuration values — covered in `02_config_management.md`. |

---

## 2. Agent Registry

The Agent Registry is the central catalogue of all available agents — both system-defined and user-defined. The Orchestrator consults it during routing.

```python
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class AgentRegistry:
    """
    Central catalogue of all registered agents.
    Loaded on startup from built-in defaults + user TOML config.
    The Orchestrator queries this registry to find suitable agents for routing.
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
        logger.info(f"Agent registered: {agent.name} ({agent.config.description})")
    
    def get(self, name: str) -> Optional["BaseAgent"]:
        """Retrieve an agent by name."""
        return self._agents.get(name)
    
    def get_all(self) -> List["BaseAgent"]:
        """Return all registered agents."""
        return list(self._agents.values())
    
    def get_catalogue(self) -> List[dict]:
        """
        Generate a catalogue for the routing LLM.
        Returns agent metadata (name, description, capabilities) 
        without exposing internal implementation.
        """
        catalogue = []
        for config in self._configs.values():
            catalogue.append({
                "name": config.name,
                "description": config.description,
                "capabilities": config.capabilities,
                "tools": config.tools,
            })
        return catalogue
    
    def find_by_capability(self, capability: str) -> List["BaseAgent"]:
        """Pre-filter agents that declare a specific capability tag."""
        return [
            self._agents[name]
            for name, config in self._configs.items()
            if capability in config.capabilities
        ]
```

---

## 3. Agent Capability Tags

Each agent declares structured capability tags alongside its description. Tags enable deterministic pre-filtering before the LLM makes the final routing decision.

### Updated `AgentConfig`

```python
@dataclass
class AgentConfig:
    """Agent configuration with capability tags for routing."""
    name: str = ""
    description: str = ""
    persona: str = ""
    model: str = ""
    effort_level: EffortLevel = EffortLevel.MEDIUM
    tools: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)  # Routing tags
    max_iterations_override: Optional[int] = None
    show_thinking: bool = True
```

### Standard Capability Tags

| Tag | Meaning | Built-in Agent |
|---|---|---|
| `code_writing` | Can create and modify source code | Coder |
| `code_review` | Can analyze code for quality/security | Reviewer |
| `debugging` | Can diagnose and fix bugs | Coder |
| `testing` | Can write and run tests | Coder |
| `research` | Can search, read, and summarize information | Researcher |
| `infrastructure` | Can manage servers, Docker, CI/CD | (user-defined) |
| `documentation` | Can write docs, READMEs, comments | (user-defined) |
| `data_analysis` | Can analyze data and generate reports | (user-defined) |
| `planning` | Can break complex tasks into sub-tasks | Orchestrator (internal) |

### User-Defined Capabilities in TOML

```toml
[agents.devops]
description = "Infrastructure and deployment specialist"
capabilities = ["infrastructure", "debugging", "code_writing"]
persona = "You are a DevOps engineer..."
tools = ["read_file", "write_file", "run_command", "spawn_terminal"]
effort_level = "HIGH"
```

---

## 4. The Routing Model (Two-Phase)

Routing is split into two phases (see `03_task_planning.md` for the full flow):

1. **Phase 1 — Classification (this doc):** A lightweight LLM call decides FAST_PATH or PLAN, and which agent handles FAST_PATH.
2. **Phase 2 — Planning (if PLAN):** The dedicated Planner Agent explores the codebase with read-only tools and generates a detailed ExecutionPlan. This is specified in `03_task_planning.md`.

### Routing Prompt Template (Phase 1 Only)

```python
ROUTING_PROMPT = """You are a task router for a CLI agent system.
Given the user's request and the available agents, decide:

1. Is this a FAST_PATH (simple, single agent) or PLAN (complex, multi-step)?
2. If FAST_PATH, which agent should handle it?

Guidelines:
- FAST_PATH: Simple questions, single file reads, direct code edits, quick commands
- PLAN: Refactoring across files, multi-step features, tasks needing exploration first

## Available Agents:
{agent_catalogue}

## User Request:
{user_request}

## Respond in this exact format:
<routing>
    <mode>FAST_PATH or PLAN</mode>
    <reasoning>Why you chose this routing.</reasoning>
    <agent>agent_name</agent>  <!-- Only for FAST_PATH. Ignored for PLAN. -->
    <effort>LOW|MEDIUM|HIGH</effort>  <!-- Only for FAST_PATH. -->
</routing>"""
```

### The Routing Response Schema

```python
from dataclasses import dataclass, field
from typing import List


@dataclass
class TaskAssignment:
    """A single task routed to an agent."""
    agent_name: str
    task_description: str
    effort: EffortLevel = EffortLevel.MEDIUM


@dataclass
class RoutingDecision:
    """The Orchestrator's routing decision for a user request."""
    mode: str                            # "FAST_PATH" or "PLAN"
    reasoning: str                       # Why this routing was chosen
    assignments: List[TaskAssignment]    # Ordered list of task assignments
```

---

## 5. The Orchestrator

The Orchestrator is the top-level coordinator. It manages the full lifecycle: routing → delegation → monitoring → result aggregation.

```python
class Orchestrator:
    """
    The central coordinator that routes user requests to agents
    and manages task execution.
    """
    
    def __init__(
        self,
        agent_registry: AgentRegistry,
        state_manager: "AbstractStateManager",
        event_bus: "AbstractEventBus",
        session_manager: "AbstractSessionManager",
        routing_provider: "BaseLLMProvider",  # Fast model for routing
        config: "AgentSettings",
        logger: "StructuredLogger",
    ):
        self.agents = agent_registry
        self.state_manager = state_manager
        self.event_bus = event_bus
        self.session_manager = session_manager
        self.routing_provider = routing_provider
        self.config = config
        self.logger = logger
        
        # Subscribe to user requests
        self.event_bus.subscribe("UserRequestEvent", self.on_user_request, priority=10)
    
    # ── Main Entry Point ────────────────────────────────────────
    
    async def on_user_request(self, event: "UserRequestEvent") -> None:
        """Handle a new user request from the TUI."""
        user_message = event.content
        
        # Persist the user message
        await self.session_manager.save_message(MessageRecord(
            session_id=self._current_session_id,
            sequence=self._message_counter,
            role="user",
            content=user_message
        ))
        self._message_counter += 1
        
        # Create a top-level task
        parent_task = await self.state_manager.create_task(
            description=user_message
        )
        
        # Route
        await self.state_manager.transition(parent_task.task_id, TaskState.ROUTING)
        
        try:
            decision = await self._route(user_message)
            
            if decision.mode == "FAST_PATH":
                await self._execute_fast_path(parent_task, decision)
            else:
                await self._execute_plan(parent_task, decision)
                
        except AgentCLIError as e:
            await self.state_manager.transition(
                parent_task.task_id, TaskState.FAILED, error=e.user_message
            )
    
    # ── Routing ─────────────────────────────────────────────────
    
    async def _route(self, user_request: str) -> RoutingDecision:
        """
        Make a lightweight LLM call to classify the request.
        Uses a fast model (LOW effort) for minimal cost.
        """
        catalogue = self.agents.get_catalogue()
        catalogue_text = "\n".join(
            f"- **{a['name']}**: {a['description']} "
            f"(capabilities: {', '.join(a['capabilities'])})"
            for a in catalogue
        )
        
        prompt = ROUTING_PROMPT.format(
            agent_catalogue=catalogue_text,
            user_request=user_request
        )
        
        span = SpanContext(task_id="routing", span_type="llm_call")
        
        response = await self.routing_provider.safe_generate(
            context=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_request}
            ]
        )
        
        timing = span.finish()
        self.logger.log("INFO", "orchestrator", "Routing decision made",
            span_id=timing["span_id"], span_type="llm_call",
            data={"mode": "routing", "duration_ms": timing["duration_ms"]}
        )
        
        return self._parse_routing_response(response.text_content)
    
    def _parse_routing_response(self, text: str) -> RoutingDecision:
        """Parse the routing LLM's XML response into a RoutingDecision."""
        import re
        
        mode_match = re.search(r"<mode>(.*?)</mode>", text, re.DOTALL)
        mode = mode_match.group(1).strip() if mode_match else "FAST_PATH"
        
        reasoning_match = re.search(r"<reasoning>(.*?)</reasoning>", text, re.DOTALL)
        reasoning = reasoning_match.group(1).strip() if reasoning_match else ""
        
        assignments = []
        task_matches = re.finditer(
            r'<task\s+agent="(\w+)"\s+effort="(\w+)">(.*?)</task>',
            text, re.DOTALL
        )
        for match in task_matches:
            assignments.append(TaskAssignment(
                agent_name=match.group(1),
                task_description=match.group(3).strip(),
                effort=EffortLevel[match.group(2).upper()]
            ))
        
        # Fallback: if parsing fails, default to coder with the raw request
        if not assignments:
            assignments = [TaskAssignment(
                agent_name="coder",
                task_description=text,
                effort=EffortLevel.MEDIUM
            )]
        
        return RoutingDecision(mode=mode, reasoning=reasoning, assignments=assignments)
    
    # ── Fast-Path Execution ─────────────────────────────────────
    
    async def _execute_fast_path(
        self, parent_task: "TaskRecord", decision: RoutingDecision
    ) -> None:
        """Single agent, single task. No ExecutionPlan overhead."""
        assignment = decision.assignments[0]
        agent = self.agents.get(assignment.agent_name)
        
        if not agent:
            await self.state_manager.transition(
                parent_task.task_id, TaskState.FAILED,
                error=f"Agent '{assignment.agent_name}' not found."
            )
            return
        
        await self.state_manager.transition(parent_task.task_id, TaskState.WORKING)
        
        try:
            result = await agent.handle_task(
                task_id=parent_task.task_id,
                task_description=assignment.task_description,
                effort_override=assignment.effort
            )
            await self.state_manager.transition(
                parent_task.task_id, TaskState.SUCCESS, result=result
            )
        except AgentCLIError as e:
            await self.state_manager.transition(
                parent_task.task_id, TaskState.FAILED, error=e.user_message
            )
            await self._handle_retry_if_applicable(parent_task, e)
    
    # ── Plan Execution ──────────────────────────────────────────
    
    async def _execute_plan(
        self, parent_task: "TaskRecord", decision: RoutingDecision
    ) -> None:
        """
        Multi-step execution with isolated Working Memory per agent.
        See 01_reasoning_loop.md Section 6 for handoff details.
        """
        await self.state_manager.transition(parent_task.task_id, TaskState.WORKING)
        
        prior_context = ""
        
        for assignment in decision.assignments:
            agent = self.agents.get(assignment.agent_name)
            if not agent:
                await self.state_manager.transition(
                    parent_task.task_id, TaskState.FAILED,
                    error=f"Agent '{assignment.agent_name}' not found in registry."
                )
                return
            
            # Create child task
            child_task = await self.state_manager.create_task(
                description=assignment.task_description,
                parent_id=parent_task.task_id,
                assigned_agent=assignment.agent_name
            )
            
            await self.state_manager.transition(child_task.task_id, TaskState.ROUTING)
            await self.state_manager.transition(child_task.task_id, TaskState.WORKING)
            
            try:
                result = await agent.handle_task(
                    task_id=child_task.task_id,
                    task_description=assignment.task_description,
                    prior_context=prior_context,
                    effort_override=assignment.effort
                )
                await self.state_manager.transition(
                    child_task.task_id, TaskState.SUCCESS, result=result
                )
                
                # Build context for the next agent
                prior_context = self._summarize_for_handoff(
                    agent.name, assignment.task_description, result
                )
                
            except AgentCLIError as e:
                await self.state_manager.transition(
                    child_task.task_id, TaskState.FAILED, error=e.user_message
                )
                # Plan fails if any step fails
                await self.state_manager.transition(
                    parent_task.task_id, TaskState.FAILED,
                    error=f"Step '{assignment.task_description[:50]}' failed: {e.user_message}"
                )
                return
        
        # All steps completed
        await self.state_manager.transition(parent_task.task_id, TaskState.SUCCESS)
    
    # ── Retry ───────────────────────────────────────────────────
    
    async def _handle_retry_if_applicable(
        self, failed_task: "TaskRecord", error: AgentCLIError
    ) -> None:
        """
        If retries are configured and not exhausted, create a new task.
        See 02_state_management.md Section 8 for retry strategy.
        """
        retry_count = self._count_retries(failed_task)
        if retry_count < self.config.max_task_retries:
            new_task = await self.state_manager.create_task(
                description=f"[Retry #{retry_count + 1}] {failed_task.description}",
                parent_id=failed_task.parent_id,
                assigned_agent=failed_task.assigned_agent
            )
            # Re-route the new task
            await self.on_user_request(UserRequestEvent(
                source="orchestrator",
                content=failed_task.description
            ))
    
    def _summarize_for_handoff(self, agent_name, task_desc, result) -> str:
        """Brief summary for the next agent's Working Memory."""
        preview = result[:2000] if len(result) > 2000 else result
        return (
            f"--- Previous Step ---\n"
            f"Agent: {agent_name}\n"
            f"Task: {task_desc}\n"
            f"Result:\n{preview}\n"
            f"--- End Previous Step ---"
        )
```

---

## 6. Agent Lifecycle (Startup → Routing → Execution)

```
CLI Startup
    │
    ▼
┌─────────────────────────────────────────┐
│ 1. Load AgentSettings from TOML + env   │
│ 2. Load built-in AgentConfigs            │
│ 3. Load user-defined agents from TOML   │
│ 4. Validate: tools exist in ToolRegistry│
│ 5. Instantiate all agents (singletons)  │
│ 6. Register in AgentRegistry            │
└───────────────────┬─────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│           AgentRegistry Ready            │
│  ┌──────────┐ ┌────────────┐ ┌────────┐│
│  │  coder   │ │ researcher │ │ devops ││
│  │ (system) │ │  (system)  │ │ (user) ││
│  └──────────┘ └────────────┘ └────────┘│
└───────────────────┬─────────────────────┘
                    │
          User types a request
                    │
                    ▼
┌─────────────────────────────────────────┐
│  Orchestrator._route()                   │
│                                          │
│  1. Build agent catalogue (names +       │
│     descriptions + capability tags)      │
│  2. LLM call (fast model, LOW effort)   │
│  3. Parse RoutingDecision               │
│     → FAST_PATH or PLAN                 │
│     → agent assignments + effort levels │
└───────────────────┬─────────────────────┘
                    │
        ┌───────────┴───────────┐
        │                       │
   FAST_PATH                  PLAN
        │                       │
        ▼                       ▼
  Single agent            Create child tasks
  handle_task()           Execute sequentially
                          Isolated memory per agent
                          Prior context as summary
```

### Startup Validation

```python
def validate_agent_configs(
    configs: Dict[str, AgentConfig],
    tool_registry: ToolRegistry
) -> List[str]:
    """
    Validate all agent configs on startup.
    Returns a list of warning messages for invalid configs.
    """
    warnings = []
    
    for name, config in configs.items():
        # Check tools exist
        for tool_name in config.tools:
            if not tool_registry.get(tool_name):
                warnings.append(
                    f"Agent '{name}' references unknown tool '{tool_name}'. "
                    f"This tool will not be available."
                )
        
        # Check model is valid (optional — may be resolved at runtime)
        if config.model and not _is_valid_model(config.model):
            warnings.append(
                f"Agent '{name}' uses model '{config.model}' which is not configured. "
                f"It will fall back to the default model."
            )
        
        # Check capabilities are from the standard set (warn on unknown)
        for cap in config.capabilities:
            if cap not in STANDARD_CAPABILITIES:
                warnings.append(
                    f"Agent '{name}' has non-standard capability '{cap}'. "
                    f"The routing LLM may not understand it."
                )
    
    return warnings
```

---

## 7. Routing Examples

### Fast-Path: Simple Code Question
```
User: "What does the main() function do in app.py?"

Phase 1 (Routing LLM):
  mode: FAST_PATH
  agent: researcher
  effort: LOW
  reasoning: "Simple code reading task. Researcher agent is best suited."

→ Researcher agent handles it directly. No plan needed.
```

### Plan: Complex Multi-Step Task
```
User: "Refactor the authentication module to use JWT instead of cookies"

Phase 1 (Routing LLM):
  mode: PLAN
  reasoning: "Complex refactoring requires codebase exploration and multiple steps."

Phase 2 (Planner Agent explores codebase, generates plan):
  → See 03_task_planning.md for the full planning flow.
```

### Fallback: Ambiguous Request
```
User: "help"

Phase 1 (Routing LLM):
  mode: FAST_PATH
  agent: coder
  effort: LOW
  reasoning: "Ambiguous request. Default to coder for general assistance."
```

---

## 8. Built-In Agent Roster

| Agent | Type | Capabilities | Tools | Default Effort |
|---|---|---|---|---|
| `coder` | System | `code_writing`, `debugging`, `testing` | read, write, edit, grep, find, run_command, terminal suite | MEDIUM |
| `researcher` | System | `research`, `code_review` | read, grep, find, run_command | LOW |

All other agents are user-defined via TOML (see `01_reasoning_loop.md` Section 8 for the config format).

---

## 9. Testing Strategy

```python
import pytest

@pytest.mark.asyncio
async def test_agent_registry_catalogue():
    registry = AgentRegistry()
    registry.register(mock_coder_agent)
    registry.register(mock_researcher_agent)
    
    catalogue = registry.get_catalogue()
    assert len(catalogue) == 2
    assert catalogue[0]["name"] == "coder"
    assert "code_writing" in catalogue[0]["capabilities"]

@pytest.mark.asyncio
async def test_routing_fast_path():
    """Simple requests should route to a single agent."""
    orchestrator = Orchestrator(...)
    decision = await orchestrator._route("Read the README file")
    
    assert decision.mode == "FAST_PATH"
    assert len(decision.assignments) == 1

@pytest.mark.asyncio
async def test_routing_plan():
    """Complex requests should generate multi-step plans."""
    orchestrator = Orchestrator(...)
    decision = await orchestrator._route("Rewrite the auth module and add tests")
    
    assert decision.mode == "PLAN"
    assert len(decision.assignments) >= 2

@pytest.mark.asyncio
async def test_routing_fallback_on_parse_failure():
    """If routing XML is unparseable, default to coder agent."""
    decision = orchestrator._parse_routing_response("garbled nonsense")
    
    assert len(decision.assignments) == 1
    assert decision.assignments[0].agent_name == "coder"

def test_startup_validation_warns_on_missing_tool():
    """User-defined agent with unknown tool should produce a warning."""
    config = AgentConfig(name="bad", tools=["nonexistent_tool"])
    warnings = validate_agent_configs({"bad": config}, tool_registry)
    
    assert len(warnings) == 1
    assert "nonexistent_tool" in warnings[0]

@pytest.mark.asyncio
async def test_find_agent_by_capability():
    registry = AgentRegistry()
    registry.register(mock_coder_agent)     # capabilities: [code_writing, debugging]
    registry.register(mock_researcher_agent) # capabilities: [research]
    
    code_agents = registry.find_by_capability("code_writing")
    assert len(code_agents) == 1
    assert code_agents[0].name == "coder"
```
