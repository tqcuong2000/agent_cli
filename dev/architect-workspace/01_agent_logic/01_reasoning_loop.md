# The Agent Reasoning Loop (ReAct Framework)

## Overview
When the system state transitions to `WORKING`, the assigned Agent enters its core execution cycle. This architecture uses the **ReAct (Reasoning and Acting)** framework, which forces the LLM to "think" before it "acts," yielding more reliable and explainable results.

The Reasoning Loop is the **heart of the system**. It connects every major component: the LLM Provider for generation, the Schema Validator for parsing, the Tool Executor for actions, the State Manager for lifecycle transitions, the Event Bus for TUI updates, and the Memory Manager for context.

---

## 1. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Working Memory Scope** | Isolated per agent | Each agent in an ExecutionPlan starts clean. Previous results injected as brief summary. No context pollution. |
| **System Prompt** | Dynamic `PromptBuilder` | Composable from persona + tools + format rules + effort behavior. Adapts per agent and per task. |
| **Effort Level** | Per-task override + global default + per-agent config | Orchestrator assigns cheap effort for routing, expensive for coding. Users define effort in agent config files. |

---

## 2. The `BaseAgent` Interface

Every agent in the system — built-in or user-defined — implements this abstract contract:

```python
from abc import ABC, abstractmethod
from typing import List, Optional
from dataclasses import dataclass, field
from enum import Enum, auto


class EffortLevel(Enum):
    """Controls reasoning depth, model tier, and iteration limits."""
    LOW    = auto()   # Fast. Bias towards immediate action. 3-5 iterations.
    MEDIUM = auto()   # Balanced. Chain-of-thought reasoning. 10-15 iterations.
    HIGH   = auto()   # Deep. Multi-path planning + self-verification. 25-30 iterations.


@dataclass
class AgentConfig:
    """
    Configuration for an agent instance.
    Built-in agents have defaults. User-defined agents set these in config files.
    """
    name: str = ""                           # Unique identifier (e.g., "coder", "researcher")
    description: str = ""                    # Role description for the Orchestrator
    persona: str = ""                        # System prompt persona text
    model: str = ""                          # LLM model override (empty = use global default)
    effort_level: EffortLevel = EffortLevel.MEDIUM  # Default effort (overridable per-task)
    tools: List[str] = field(default_factory=list)   # Tool names from ToolRegistry
    max_iterations_override: Optional[int] = None    # Custom max iterations (overrides effort default)
    show_thinking: bool = True                       # Whether to stream <thinking> to TUI


# ── Effort → Constraints Mapping ────────────────────────────
EFFORT_CONSTRAINTS = {
    EffortLevel.LOW: {
        "max_iterations": 5,
        "model_tier": "fast",           # e.g., Haiku, GPT-4o-mini
        "reasoning_instruction": "Be concise. Act immediately when the path is clear.",
        "review_policy": "none",        # Accept first final answer
    },
    EffortLevel.MEDIUM: {
        "max_iterations": 15,
        "model_tier": "capable",        # e.g., Sonnet, GPT-4o
        "reasoning_instruction": "Think step-by-step. Explain your reasoning before acting.",
        "review_policy": "standard",    # Normal generation
    },
    EffortLevel.HIGH: {
        "max_iterations": 30,
        "model_tier": "premium",        # e.g., Opus, o1
        "reasoning_instruction": (
            "Think deeply. Consider multiple approaches before choosing one. "
            "After completing the task, review your work for correctness."
        ),
        "review_policy": "self_verify", # Must self-check before yielding
    },
}


class BaseAgent(ABC):
    """
    Abstract base class for all agents.
    Implements the ReAct reasoning loop with hooks for customization.
    """

    def __init__(
        self,
        config: AgentConfig,
        provider: "BaseLLMProvider",
        tool_executor: "ToolExecutor",
        schema_validator: "SchemaValidator",
        memory_manager: "BaseMemoryManager",
        event_bus: "AbstractEventBus",
        state_manager: "AbstractStateManager",
        session_manager: "AbstractSessionManager",
        prompt_builder: "PromptBuilder",
        logger: "StructuredLogger",
    ):
        self.config = config
        self.provider = provider
        self.tool_executor = tool_executor
        self.validator = schema_validator
        self.memory = memory_manager
        self.event_bus = event_bus
        self.state_manager = state_manager
        self.session_manager = session_manager
        self.prompt_builder = prompt_builder
        self.logger = logger
    
    @property
    def name(self) -> str:
        return self.config.name

    @abstractmethod
    async def build_system_prompt(self, task_context: str) -> str:
        """
        Construct the system prompt for this agent.
        Called once at the start of handle_task().
        Implementations typically delegate to PromptBuilder with agent-specific persona.
        """
        pass

    @abstractmethod
    async def on_tool_result(self, tool_name: str, result: str) -> None:
        """
        Hook called after every tool execution.
        Agents can override to add custom behavior (e.g., code agents
        auto-running tests after file edits).
        """
        pass

    @abstractmethod
    async def on_final_answer(self, answer: str) -> str:
        """
        Hook called before returning the final answer.
        Agents can override for self-verification (HIGH effort) or
        formatting adjustments.
        Returns the (potentially modified) final answer.
        """
        pass
```

---

## 3. The Core Reasoning Loop

The `handle_task()` method implements the full ReAct cycle, integrating every component:

```python
class BaseAgent(ABC):
    # ... (continued from above) ...
    
    async def handle_task(
        self,
        task_id: str,
        task_description: str,
        prior_context: str = "",
        effort_override: Optional[EffortLevel] = None
    ) -> str:
        """
        The main ReAct reasoning loop.
        
        Args:
            task_id:          The task being executed (for State Manager / logging)
            task_description: What the agent should accomplish
            prior_context:    Summary from previous agents in an ExecutionPlan (if any)
            effort_override:  Orchestrator can override the agent's default effort level
        
        Returns:
            The agent's final answer string.
        
        Raises:
            MaxIterationsExceededError: If the loop exhausts all iterations.
            AgentCLIError: On fatal errors (propagated to Orchestrator).
        """
        # ── Resolve effort level (priority: task override > agent config > global default) ──
        effort = effort_override or self.config.effort_level
        constraints = EFFORT_CONSTRAINTS[effort]
        max_iterations = self.config.max_iterations_override or constraints["max_iterations"]
        
        # ── Build system prompt ──
        system_prompt = await self.build_system_prompt(task_description)
        
        # ── Initialize Working Memory ──
        self.memory.reset_working()
        self.memory.add_working_event({"role": "system", "content": system_prompt})
        
        # Inject prior context from previous agents (if in an ExecutionPlan)
        if prior_context:
            self.memory.add_working_event({
                "role": "user",
                "content": f"Context from previous steps:\n{prior_context}"
            })
        
        # Inject the task itself
        self.memory.add_working_event({
            "role": "user",
            "content": task_description
        })
        
        # ── Tracking ──
        schema_error_count = 0
        MAX_CONSECUTIVE_SCHEMA_ERRORS = 3
        stuck_detection = _StuckDetector()
        
        # ── The Loop ────────────────────────────────────────────
        for iteration in range(1, max_iterations + 1):
            self.logger.log("DEBUG", self.name, f"Iteration {iteration}/{max_iterations}",
                task_id=task_id, data={"effort": effort.name})
            
            try:
                # ── STEP 1: Generate (LLM Call) ──────────────────
                llm_response = await self.provider.safe_generate(
                    context=self.memory.get_working_context(),
                    tools=self._get_tool_definitions()
                )
                
                # ── STEP 2: Stream Thinking to TUI ───────────────
                thinking_text = self.validator.extract_thinking(llm_response.text_content)
                if thinking_text and self.config.show_thinking:
                    await self.event_bus.emit(AgentMessageEvent(
                        source=self.name,
                        agent_name=self.name,
                        content=thinking_text,
                        is_monologue=True
                    ))
                
                # ── STEP 3: Validate & Parse Response ────────────
                response = self.validator.parse_and_validate(llm_response)
                schema_error_count = 0  # Reset on success
                
                # ── STEP 4: Process explicit AgentDecision ───────
                match response.decision:
                    case AgentDecision.EXECUTE_ACTION:
                        # ── TOOL EXECUTION PATH ──
                        stuck_detection.reset_reflects()
                        result = await self._execute_tool(response.action, task_id)
                        
                        # Add LLM response + tool result to Working Memory
                        self.memory.add_working_event({
                            "role": "assistant",
                            "content": llm_response.text_content
                        })
                        self.memory.add_working_event({
                            "role": "tool",
                            "content": result
                        })
                        
                        # Agent-specific hook (e.g., auto-run tests)
                        await self.on_tool_result(response.action.tool_name, result)
                        
                        # Stuck detection
                        if stuck_detection.is_stuck(response.action.tool_name, result):
                            self.memory.add_working_event({
                                "role": "user",
                                "content": (
                                    "⚠ You appear to be repeating the same action with the same result. "
                                    "Try a completely different approach."
                                )
                            })
                        
                        continue  # Next iteration
                    
                    case AgentDecision.NOTIFY_USER:
                        # ── FINAL ANSWER PATH ──
                        final = await self.on_final_answer(response.final_answer)
                    
                    # Persist the final answer as a message
                    await self._persist_message("assistant", final)
                    
                    # Publish to TUI
                    await self.event_bus.emit(AgentMessageEvent(
                        source=self.name,
                        agent_name=self.name,
                        content=final,
                        is_monologue=False
                    ))
                    
                    self.logger.log("INFO", self.name,
                        f"Task completed in {iteration} iterations",
                        task_id=task_id,
                        data={"iterations": iteration, "effort": effort.name}
                    )
                    return final
                
                    case AgentDecision.YIELD:
                        # ── GRACEFUL ABORT PATH ──
                        final = f"Agent yielded: {response.yield_reason}"
                        await self._persist_message("assistant", final)
                        
                        await self.event_bus.emit(AgentMessageEvent(
                            source=self.name,
                            agent_name=self.name,
                            content=final,
                            is_monologue=False
                        ))
                        
                        self.logger.log("WARNING", self.name,
                            f"Task yielded after {iteration} iterations",
                            task_id=task_id,
                            data={"reason": response.yield_reason}
                        )
                        return final
                        
                    case AgentDecision.REFLECT:
                        # ── CONTINUED REASONING PATH ──
                        self.memory.add_working_event({
                            "role": "assistant",
                            "content": llm_response.text_content
                        })
                        
                        # Reflection budget enforcement
                        stuck_detection.increment_reflects()
                        if stuck_detection.reflect_count >= self.config.max_consecutive_reflects:
                            self.memory.add_working_event({
                                "role": "user",
                                "content": "You have reflected multiple times. Please take action or provide a final answer."
                            })
                            
                        continue
            
            # ── ERROR HANDLING (from 04_error_handling.md) ──────────
            
            except ContextLengthExceededError:
                # TIER 2: Summarize and retry
                await self.event_bus.emit(AgentMessageEvent(
                    source=self.name, agent_name=self.name,
                    content="⚠ Context too long, summarizing older steps...",
                    is_monologue=True
                ))
                await self.memory.summarize_and_compact()
                continue
            
            except SchemaValidationError as e:
                # TIER 2: Feedback loop
                schema_error_count += 1
                if schema_error_count >= MAX_CONSECUTIVE_SCHEMA_ERRORS:
                    raise MaxRetriesExhaustedError(
                        f"Agent produced {MAX_CONSECUTIVE_SCHEMA_ERRORS} consecutive malformed responses",
                        original_error=e
                    )
                self.memory.add_working_event({
                    "role": "user",
                    "content": f"Schema Error: {e}. Fix your formatting and try again."
                })
                continue
            
            except ToolExecutionError as e:
                # TIER 2: Return error to agent as observation
                self.memory.add_working_event({
                    "role": "tool",
                    "content": f"Tool Error: {e}. Try a different approach."
                })
                continue
            
            except AgentCLIError as e:
                if e.tier == ErrorTier.FATAL:
                    raise  # Caught by Orchestrator
                raise
        
        # ── LOOP EXHAUSTED ──
        raise MaxIterationsExceededError(
            f"Agent '{self.name}' reached {max_iterations} iterations "
            f"(effort={effort.name}) without completing the task."
        )
    
    # ── Private Helpers ─────────────────────────────────────────
    
    async def _execute_tool(self, action: "ParsedAction", task_id: str) -> str:
        """Execute a tool via the ToolExecutor (handles safety, logging, formatting)."""
        
        # Check if this tool requires user approval → AWAITING_INPUT
        tool = self.tool_executor.registry.get(action.tool_name)
        if tool and not tool.is_safe:
            await self.state_manager.transition(task_id, TaskState.AWAITING_INPUT)
        
        result = await self.tool_executor.execute(action, task_id)
        
        # Return to WORKING after approval
        task = self.state_manager.get_task(task_id)
        if task and task.state == TaskState.AWAITING_INPUT:
            await self.state_manager.transition(task_id, TaskState.WORKING)
        
        return result
    
    def _get_tool_definitions(self) -> List[dict]:
        """Retrieve tool definitions for the LLM from the ToolRegistry."""
        return self.tool_executor.registry.get_definitions_for_llm(self.config.tools)
    
    async def _persist_message(self, role: str, content: str) -> None:
        """Save a message to the session database."""
        await self.session_manager.save_message(MessageRecord(
            session_id=self._current_session_id,
            sequence=self._message_counter,
            role=role,
            content=content
        ))
        self._message_counter += 1
```

---

## 4. Stuck Detection

Prevents the agent from repeating the same failing action in a loop:

```python
class _StuckDetector:
    """
    Tracks recent actions to detect repetitive loops.
    If the agent calls the same tool with the same result 3 times
    consecutively, it's considered stuck.
    """
    
    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self._recent: list[tuple[str, str]] = []
    
    def is_stuck(self, tool_name: str, result_hash: str) -> bool:
        """Check if the agent is repeating the same action."""
        key = (tool_name, hash(result_hash))
        self._recent.append(key)
        
        if len(self._recent) < self.threshold:
            return False
        
        # Check if the last N actions are identical
        last_n = self._recent[-self.threshold:]
        if all(k == last_n[0] for k in last_n):
            self._recent.clear()  # Reset after detection
            return True
        
        # Keep only the last 10 entries
        if len(self._recent) > 10:
            self._recent = self._recent[-10:]
        
        return False
```

---

## 5. Dynamic Prompt Builder

The system prompt is assembled dynamically from modular sections, adapting to the agent's role, tools, and effort level:

```python
class PromptBuilder:
    """
    Assembles the system prompt from composable sections.
    Each agent customizes its prompt via build_system_prompt().
    """
    
    def __init__(self, tool_registry: "ToolRegistry"):
        self.tool_registry = tool_registry
    
    def build(
        self,
        persona: str,
        tool_names: List[str],
        effort: EffortLevel,
        workspace_context: str = "",
        extra_instructions: str = ""
    ) -> str:
        """
        Assemble a complete system prompt.
        
        Sections:
        1. Agent persona/role
        2. Output format instructions
        3. Tool descriptions (auto-generated from registry)
        4. Effort-level behavioral modifiers
        5. Workspace context (project type, language)
        6. Agent-specific extra instructions
        """
        sections = []
        
        # 1. Persona
        sections.append(f"# Role\n{persona}")
        
        # 2. Output format
        sections.append(self._output_format_section())
        
        # 3. Tool descriptions
        tool_defs = self.tool_registry.get_definitions_for_llm(tool_names)
        sections.append(self._tools_section(tool_defs))
        
        # 4. Effort-level behavior
        constraints = EFFORT_CONSTRAINTS[effort]
        sections.append(f"# Reasoning Policy\n{constraints['reasoning_instruction']}")
        
        # 5. Workspace context
        if workspace_context:
            sections.append(f"# Workspace Context\n{workspace_context}")
        
        # 6. Extra instructions
        if extra_instructions:
            sections.append(f"# Additional Instructions\n{extra_instructions}")
        
        return "\n\n".join(sections)
    
    def _output_format_section(self) -> str:
        return """# Output Format
You MUST structure every response using ONE of four decisions:

1. **reflect**: <thinking>Your internal monologue.</thinking>
2. **execute_action**: <thinking>Why</thinking>\n<action><tool>name</tool><args>{"key":"val"}</args></action>
3. **notify_user**: <final_answer>Final answer text.</final_answer>
4. **yield**: <yield>Reason task failed.</yield>

You must ALWAYS include <thinking> before any action or final answer."""
    
    def _tools_section(self, tool_defs: List[dict]) -> str:
        lines = ["# Available Tools\n"]
        for t in tool_defs:
            params = t["parameters"].get("properties", {})
            required = t["parameters"].get("required", [])
            
            lines.append(f"## {t['name']}")
            lines.append(f"{t['description']}")
            if params:
                lines.append("**Parameters:**")
                for pname, pinfo in params.items():
                    req = " (required)" if pname in required else " (optional)"
                    lines.append(f"  - `{pname}` ({pinfo.get('type', 'any')}){req}: {pinfo.get('description', '')}")
            lines.append("")
        
        return "\n".join(lines)
```

---

## 6. Working Memory on Agent Switch

When the user switches agents via `!mention` tags (see `04_multi_agent_definitions.md`), the new agent starts with **fresh Working Memory**. The Orchestrator generates a brief LLM summary of the session conversation and injects it as context — not the raw message history.

```python
class Orchestrator:
    
    async def _switch_agent(self, target_name: str) -> None:
        """
        Switch to a different agent.
        Generates a session summary and injects it into the new agent's context.
        """
        # 1. Summarize conversation so far (fast LLM call)
        summary = await self._generate_session_summary()
        
        # 2. Perform status switch (old → IDLE, new → ACTIVE)
        new_agent = self.session_agents.switch_to(target_name)
        
        # 3. Reset new agent's Working Memory (stateless)
        new_agent.memory.reset_working()
        system_prompt = await new_agent.build_system_prompt("")
        new_agent.memory.add_working_event({"role": "system", "content": system_prompt})
        
        # 4. Inject summary as context
        if summary:
            new_agent.memory.add_working_event({
                "role": "system",
                "content": f"[Session Context Summary]\n{summary}"
            })
```

### Why Summarize Instead of Passing Raw Messages?

| Approach | Tokens | Quality |
|---|---|---|
| Pass all `session.messages` | 50K+ (blows budget) | Tool outputs pollute context |
| Pass only user messages | ~5K | Missing agent reasoning and decisions |
| **Summarize (chosen)** | **~500-1000** | **Compact, focused, decision-aware** |

---

## 7. Concrete Agent Examples

### A. Coder Agent

```python
class CoderAgent(BaseAgent):
    """
    Specialized for code-related tasks: writing, editing, debugging, refactoring.
    Auto-runs tests after file modifications.
    """
    
    async def build_system_prompt(self, task_context: str) -> str:
        return self.prompt_builder.build(
            persona=(
                "You are an expert software engineer. You write clean, efficient, "
                "well-documented code. You always read existing code before modifying it. "
                "After making changes, you verify correctness by running relevant tests."
            ),
            tool_names=self.config.tools,
            effort=self.config.effort_level,
            workspace_context=self._detect_workspace_context(),
            extra_instructions=(
                "After editing a file, always run the project's test suite to verify "
                "your changes didn't break anything."
            )
        )
    
    async def on_tool_result(self, tool_name: str, result: str) -> None:
        """After file edits, remind the agent to verify."""
        if tool_name in ("write_file", "edit_file"):
            self.memory.add_working_event({
                "role": "user",
                "content": "File modified. Remember to run tests to verify your changes."
            })
    
    async def on_final_answer(self, answer: str) -> str:
        """No special processing for coder agent."""
        return answer


# Agent config (can be defined in TOML by the user)
CODER_CONFIG = AgentConfig(
    name="coder",
    description="Expert software engineer for coding tasks",
    persona="",  # Set in build_system_prompt
    effort_level=EffortLevel.MEDIUM,
    tools=["read_file", "write_file", "edit_file", "grep_search",
           "find_files", "run_command", "spawn_terminal",
           "read_terminal", "kill_terminal", "wait_for_terminal"],
    show_thinking=True
)
```

### B. Researcher Agent

```python
class ResearcherAgent(BaseAgent):
    """
    Specialized for information gathering: reading files, searching code,
    summarizing findings. Does NOT modify files.
    """
    
    async def build_system_prompt(self, task_context: str) -> str:
        return self.prompt_builder.build(
            persona=(
                "You are a meticulous research assistant. Your job is to find, read, "
                "and summarize information from the codebase. You NEVER modify files. "
                "Provide structured, comprehensive summaries."
            ),
            tool_names=self.config.tools,
            effort=self.config.effort_level,
        )
    
    async def on_tool_result(self, tool_name: str, result: str) -> None:
        pass  # No special behavior
    
    async def on_final_answer(self, answer: str) -> str:
        return answer


RESEARCHER_CONFIG = AgentConfig(
    name="researcher",
    description="Information gatherer and code analyst",
    effort_level=EffortLevel.LOW,  # Research is usually fast
    tools=["read_file", "grep_search", "find_files", "run_command"],
    show_thinking=True
)
```

---

## 8. User-Defined Agents (Config File)

Users can define custom agents in their project's config file (`.agent_cli/config.toml`):

```toml
# .agent_cli/config.toml

[agents.devops]
description = "Infrastructure and deployment specialist"
persona = """You are a DevOps engineer specializing in Docker, Kubernetes, 
and CI/CD pipelines. You prefer Terraform for infrastructure-as-code."""
model = "claude-3-5-sonnet"
effort_level = "HIGH"           # User sets effort level per agent
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

### Loading User-Defined Agents

```python
def load_agents_from_config(config: AgentSettings) -> Dict[str, AgentConfig]:
    """
    Parse agent definitions from the TOML config file.
    Merge with built-in agent defaults.
    """
    agents = {}
    
    # Built-in agents (always available)
    agents["coder"] = CODER_CONFIG
    agents["researcher"] = RESEARCHER_CONFIG
    
    # User-defined agents (from config file)
    for name, agent_conf in config.agents.items():
        agents[name] = AgentConfig(
            name=name,
            description=agent_conf.get("description", ""),
            persona=agent_conf.get("persona", ""),
            model=agent_conf.get("model", ""),
            effort_level=EffortLevel[agent_conf.get("effort_level", "MEDIUM").upper()],
            tools=agent_conf.get("tools", []),
            max_iterations_override=agent_conf.get("max_iterations", None),
            show_thinking=agent_conf.get("show_thinking", True),
        )
    
    return agents
```

---

## 9. Effort Level Resolution Order

When a task is executed, the effort level is resolved with this priority:

```
1. Task-level override (Orchestrator assigns per subtask)     ← Highest
2. Agent config effort_level (from TOML or built-in default)
3. Global setting: AgentSettings.default_effort_level          ← Lowest
```

```python
def resolve_effort(
    task_override: Optional[EffortLevel],
    agent_config: AgentConfig,
    global_default: EffortLevel
) -> EffortLevel:
    """Resolve effort level with priority: task > agent > global."""
    if task_override:
        return task_override
    if agent_config.effort_level:
        return agent_config.effort_level
    return global_default
```

The user can also change effort mid-session via the `/effort` command:

```
/effort high     → Sets global default to HIGH (affects next tasks)
/effort low      → Sets global default to LOW
```

---

## 10. The Full ReAct Sequence Diagram

```
User ──▶ Orchestrator ──▶ StateManager.transition(WORKING)
                │
                ▼
         Agent.handle_task()
                │
                ▼
    ┌───────────────────────────────────┐
    │  1. PromptBuilder.build()        │ ← Build system prompt
    │  2. memory.reset_working()       │ ← Fresh memory
    │  3. Inject system + task + prior │
    └───────────────┬───────────────────┘
                    │
                    ▼
    ┌──── ITERATION LOOP ──────────────┐
    │                                   │
    │  4. provider.safe_generate()     │ ← LLM call (with retry engine)
    │          │                        │
    │          ▼                        │
    │  5. Extract <thinking>           │ ← Stream to TUI via EventBus
    │          │                        │
    │          ▼                        │
    │  6. validator.parse_and_validate │ ← Dual-mode (native FC / XML)
    │          │                        │
    │     ┌────┴────┬─────────┬──────┐   │
    │     │         │         │      │   │
    │  REFLECT   ACTION   FINAL_  YIELD  │
    │     │         │      ANSWER    │   │
    │     ▼         ▼         ▼      ▼   │
    │  7a. Loop  7b. Exec  7c. End 7d.End│
    │     │         │         │      │   │
    │     ▼         ▼         ▼      ▼   │
    │  8a. Warn  8b. tool  8c. Ret 8d.Ret│
    │      budget   result                   │
    │     │         │                    │
    │     └────── continue ──────────────┘
    │                                   │
    └──── (max_iterations guard) ──────┘
                    │
                    ▼ (exhausted)
         MaxIterationsExceededError
                    │
                    ▼
         Orchestrator → Task FAILED
```

---

## 11. Testing Strategy

```python
import pytest

@pytest.mark.asyncio
async def test_agent_completes_simple_task():
    """Agent should reach final_answer in a few iterations."""
    agent = CoderAgent(config=CODER_CONFIG, ...)
    
    result = await agent.handle_task(
        task_id="t1",
        task_description="What is 2 + 2?"
    )
    assert result  # Some non-empty answer

@pytest.mark.asyncio
async def test_max_iterations_raises():
    """Agent should raise when loop is exhausted."""
    config = AgentConfig(name="test", effort_level=EffortLevel.LOW)
    agent = TestAgent(config=config, ...)
    
    # Mock provider to never return a final answer
    with pytest.raises(MaxIterationsExceededError):
        await agent.handle_task("t1", "Impossible task")

@pytest.mark.asyncio
async def test_stuck_detection_injects_hint():
    """Agent repeating the same action should receive a hint."""
    detector = _StuckDetector(threshold=3)
    
    assert detector.is_stuck("read_file", "same content") == False
    assert detector.is_stuck("read_file", "same content") == False
    assert detector.is_stuck("read_file", "same content") == True  # 3rd time

@pytest.mark.asyncio
async def test_schema_error_feedback_loop():
    """Agent should self-correct after schema errors."""
    # Mock provider: returns bad XML twice, then good XML
    # Assert: 2 feedback injections, then success

@pytest.mark.asyncio
async def test_context_compaction_on_overflow():
    """When context exceeds limit, memory should be compacted, not task failed."""
    # Mock provider: raises ContextLengthExceededError on first call
    # Assert: memory.summarize_and_compact() called, then retry succeeds

@pytest.mark.asyncio  
async def test_isolated_working_memory_per_agent():
    """Each agent in an ExecutionPlan starts with clean memory."""
    orchestrator = Orchestrator(...)
    
    # Agent 1 runs and fills memory with 50 tool results
    # Agent 2 should start fresh, with only a summary from Agent 1
    # Assert: Agent 2's working memory has < 5 messages at start

@pytest.mark.asyncio
async def test_effort_level_from_agent_config():
    """User-defined agents should respect their configured effort level."""
    config = AgentConfig(name="custom", effort_level=EffortLevel.HIGH)
    # Assert: max_iterations = 30 (from HIGH constraints)

@pytest.mark.asyncio
async def test_effort_override_takes_priority():
    """Task-level effort override should beat agent config."""
    config = AgentConfig(name="custom", effort_level=EffortLevel.LOW)
    agent = TestAgent(config=config, ...)
    
    # Run with HIGH override
    # Assert: max_iterations = 30 despite agent config being LOW
```
