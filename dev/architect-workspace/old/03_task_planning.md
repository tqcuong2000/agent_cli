# Task Planning & Execution Architecture

## Overview
A naive ReAct loop struggles with "Long-Horizon Execution" — tasks requiring dozens of steps across a vast codebase. The Agent forgets its original goal, gets distracted by minor bugs, and burns through the API budget.

This architecture introduces **Two-Phase Task Execution**, controlled explicitly by the user via the command system (`/mode`). If the user selects PLAN mode, a dedicated **Planner Agent** explores the codebase with read-only tools to produce a structured `ExecutionPlan`. Worker Agents then execute each step sequentially with isolated Working Memory. If the user selects FAST_PATH mode, tasks are routed directly to a built-in or user-defined agent without planning overhead.

---

## 1. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Execution Mode** | User-controlled (`/mode`) | Eliminates ambiguity, puts user in explicit control over cost and overhead. |
| **Task Ordering** | Sequential only (no dependency DAG) | Simpler to implement and reason about. Most plans are sequential in practice. |
| **Task Model** | Unified — uses `TaskRecord` from State Management | One source of truth. No separate `TaskDef`. |

---

## 2. User-Controlled Operational Modes

When a user submits a prompt, the system operates in one of two modes, determined by the user's current execution mode (`/mode fast` or `/mode plan`):

### A. Fast-Path (No Plan Needed)
For simple requests that a single agent can handle directly.

```
User: "What does main() do?"
  → Configuration: FAST_PATH (Default)
  → Routing LLM selects Researcher agent
  → Researcher agent handles it directly
```

No plan is generated. The Orchestrator delegates to a single agent (see `04_multi_agent_definitions.md`).

### B. Plan Mode (Complex Multi-Step)
For requests requiring codebase exploration, multiple file changes, or sequential reasoning.

```
User: "Refactor the auth module to use JWT"
  → Configuration: PLAN (User ran `/mode plan`)
  → Planner Agent explores codebase → generates ExecutionPlan
  → User approves the plan
  → Worker Agents execute each step sequentially
```

```
┌──────────────────────────────────────────────────────────┐
│                      User Request                        │
└─────────────────────────┬────────────────────────────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │  AgentSettings.mode   │  User explicitly selects
              │   FAST_PATH or PLAN   │  execution mode
              └─────┬───────────┬─────┘
                    │           │
               FAST_PATH      PLAN Mode
                    │           │
                    ▼           ▼
              Single Agent  ┌───────────────────────┐
              executes      │  Planner Agent (MED)  │  Phase 1: Thorough planning
              directly      │  Read-only tools      │
                            │  Explores codebase    │
                            └───────────┬───────────┘
                                        │
                                        ▼
                            ┌───────────────────────┐
                            │  Human Approval (TUI) │
                            │  Review / Edit / Reject│
                            └───────────┬───────────┘
                                        │
                                        ▼
                            ┌───────────────────────┐
                            │  Sequential Execution  │
                            │  t1 → t2 → t3 → ...  │
                            │  Isolated Working Mem  │
                            └───────────────────────┘
```

---

## 3. The Planner Agent

The Planner is a **built-in system agent** with a special role: it explores the codebase using read-only tools, then generates a structured `ExecutionPlan`.

### Configuration

```python
PLANNER_CONFIG = AgentConfig(
    name="planner",
    description="Analyzes complex requests and creates structured execution plans",
    persona="",  # Set in build_system_prompt
    effort_level=EffortLevel.MEDIUM,
    capabilities=["planning", "research"],
    tools=["read_file", "grep_search", "find_files"],  # READ-ONLY ONLY
    show_thinking=True
)
```

### Key Constraints
1. **No write tools.** The Planner cannot modify files, run commands, or spawn terminals. It only reads and searches.
2. **The Planner produces an `ExecutionPlan`, not a final answer.** Its `<final_answer>` is the plan itself.
3. **The Planner uses a capable model** (MEDIUM effort by default) because plan quality directly determines execution success.

### Planner System Prompt

```python
class PlannerAgent(BaseAgent):
    
    async def build_system_prompt(self, task_context: str) -> str:
        return self.prompt_builder.build(
            persona=(
                "You are a senior software architect and project planner. "
                "Your job is to analyze the user's request and the codebase, then produce "
                "a structured execution plan that other specialized agents will follow.\n\n"
                "Rules:\n"
                "1. You MUST explore the codebase first using read_file and grep_search.\n"
                "2. You CANNOT modify any files. You are read-only.\n"
                "3. Your final answer MUST be a structured execution plan in XML format.\n"
                "4. Each task must be specific enough that a coder agent can execute it "
                "without needing to re-explore the codebase.\n"
                "5. Tasks are executed SEQUENTIALLY (t1 completes before t2 starts).\n"
                "6. Assign each task to the most appropriate agent."
            ),
            tool_names=self.config.tools,
            effort=self.config.effort_level,
            extra_instructions=self._plan_format_instructions()
        )
    
    def _plan_format_instructions(self) -> str:
        return """When you have explored enough, produce your plan in this format:

<final_answer>
<execution_plan>
    <goal>High-level description of what the plan achieves</goal>
    <tasks>
        <task agent="coder" effort="MEDIUM">
            Specific description of what to do, including file paths and logic.
        </task>
        <task agent="coder" effort="LOW">
            Next step description.
        </task>
    </tasks>
</execution_plan>
</final_answer>

Available agents for assignment:
{agent_catalogue}"""
    
    async def on_final_answer(self, answer: str) -> str:
        """Validate the plan XML before returning."""
        plan = self._parse_plan(answer)
        if not plan.assignments:
            raise SchemaValidationError(
                "Your plan has no tasks. Please explore the codebase and create "
                "a plan with at least one task."
            )
        return answer
```

---

## 4. The ExecutionPlan Model

The Planner's output is parsed into the existing `RoutingDecision` model from `04_multi_agent_definitions.md`. There is **no separate plan model** — the routing decision IS the plan.

```python
# Reuses the existing model from 04_multi_agent_definitions.md:
@dataclass
class TaskAssignment:
    """A single step in the execution plan."""
    agent_name: str
    task_description: str
    effort: EffortLevel = EffortLevel.MEDIUM

@dataclass
class RoutingDecision:
    """The plan. Mode is always "PLAN" for planner-generated plans."""
    mode: str = "PLAN"
    reasoning: str = ""
    assignments: List[TaskAssignment] = field(default_factory=list)
```

### Plan Parsing

```python
class PlanParser:
    """Parses the Planner Agent's XML output into a RoutingDecision."""
    
    def __init__(self, agent_registry: "AgentRegistry"):
        self.registry = agent_registry
    
    def parse(self, plan_xml: str) -> RoutingDecision:
        """
        Parse <execution_plan> XML from the Planner's final_answer.
        Validates agent names exist in the registry.
        """
        import re
        
        # Extract goal
        goal_match = re.search(r"<goal>(.*?)</goal>", plan_xml, re.DOTALL)
        goal = goal_match.group(1).strip() if goal_match else ""
        
        # Extract tasks
        assignments = []
        task_matches = re.finditer(
            r'<task\s+agent="(\w+)"\s+effort="(\w+)">(.*?)</task>',
            plan_xml, re.DOTALL
        )
        
        for match in task_matches:
            agent_name = match.group(1)
            effort_str = match.group(2).upper()
            description = match.group(3).strip()
            
            # Validate agent exists
            if not self.registry.get(agent_name):
                available = [a["name"] for a in self.registry.get_catalogue()]
                raise PlanValidationError(
                    f"Plan references unknown agent '{agent_name}'. "
                    f"Available agents: {', '.join(available)}"
                )
            
            assignments.append(TaskAssignment(
                agent_name=agent_name,
                task_description=description,
                effort=EffortLevel[effort_str] if effort_str in EffortLevel.__members__ else EffortLevel.MEDIUM
            ))
        
        if not assignments:
            raise PlanValidationError("Plan contains no tasks.")
        
        return RoutingDecision(
            mode="PLAN",
            reasoning=f"Plan goal: {goal}",
            assignments=assignments
        )


class PlanValidationError(AgentCLIError):
    """Raised when the Planner's output doesn't parse into a valid plan."""
    tier = ErrorTier.RECOVERABLE
    user_message = "The execution plan is invalid. Requesting correction..."
```

---

## 5. Human-in-the-Loop Plan Approval

Between planning and execution, the system pauses for user approval. This uses the `AWAITING_INPUT` state from `02_state_management.md`.

### Approval Flow

```python
class Orchestrator:
    
    async def _request_plan_approval(
        self, parent_task: "TaskRecord", plan: RoutingDecision
    ) -> RoutingDecision:
        """
        Present the plan to the user and wait for approval.
        The user can approve, reject (with feedback), or cancel.
        """
        # Transition to AWAITING_INPUT
        await self.state_manager.transition(
            parent_task.task_id, TaskState.AWAITING_INPUT
        )
        
        # Render the plan in the TUI
        plan_display = self._format_plan_for_display(plan)
        
        response = await self.interaction_handler.request_human_input(
            UserInteractionRequest(
                interaction_type=InteractionType.PLAN_APPROVAL,
                message=plan_display,
                options=["approve", "reject", "cancel"]
            )
        )
        
        if response.action == "cancel":
            await self.state_manager.transition(
                parent_task.task_id, TaskState.CANCELLED
            )
            raise TaskCancelledError("User cancelled the plan.")
        
        elif response.action == "reject":
            # User provided feedback — send back to Planner for revision
            await self.state_manager.transition(
                parent_task.task_id, TaskState.WORKING
            )
            return await self._revise_plan(parent_task, plan, response.feedback)
        
        else:  # approve
            await self.state_manager.transition(
                parent_task.task_id, TaskState.WORKING
            )
            return plan
    
    def _format_plan_for_display(self, plan: RoutingDecision) -> str:
        """Format the plan as a TUI-renderable checklist."""
        lines = [f"📋 Execution Plan: {plan.reasoning}\n"]
        for i, task in enumerate(plan.assignments, 1):
            lines.append(
                f"  {i}. [{task.agent_name}] ({task.effort.name}) "
                f"{task.description}"
            )
        lines.append("\n[Approve] [Reject with feedback] [Cancel]")
        return "\n".join(lines)
```

### TUI Plan Approval Widget

```
┌──────────────────────────────────────────────────────────┐
│  📋 Execution Plan: Refactor auth module to use JWT      │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  1. [coder]      (MEDIUM) Remove cookie-based logic      │
│                           in src/auth.py                 │
│                                                          │
│  2. [coder]      (HIGH)   Implement JWT verify           │
│                           middleware in src/middleware.py │
│                                                          │
│  3. [coder]      (MEDIUM) Write unit tests for the       │
│                           new JWT middleware              │
│                                                          │
├──────────────────────────────────────────────────────────┤
│  [Enter] Approve  │  [E] Edit/Reject  │  [Esc] Cancel   │
└──────────────────────────────────────────────────────────┘
```

### Plan Revision

If the user rejects with feedback, the Planner Agent receives the feedback and revises:

```python
async def _revise_plan(
    self, parent_task, original_plan, user_feedback
) -> RoutingDecision:
    """Send the plan back to the Planner Agent with user corrections."""
    planner = self.agents.get("planner")
    
    revision_context = (
        f"Your previous plan was rejected by the user.\n\n"
        f"Previous plan:\n{self._format_plan_for_display(original_plan)}\n\n"
        f"User feedback: {user_feedback}\n\n"
        f"Please revise the plan based on this feedback."
    )
    
    revised_xml = await planner.handle_task(
        task_id=parent_task.task_id,
        task_description=revision_context,
    )
    
    revised_plan = self.plan_parser.parse(revised_xml)
    
    # Ask for approval again (recursive until approved or cancelled)
    return await self._request_plan_approval(parent_task, revised_plan)
```

---

## 6. Updated Orchestrator Flow (Two-Phase)

The Orchestrator from `04_multi_agent_definitions.md` is extended with the planning phase:

```python
class Orchestrator:
    
    async def on_user_request(self, event: "UserRequestEvent") -> None:
        """Updated flow with explicit user execution mode."""
        user_message = event.content
        
        # Persist user message
        await self._persist_message("user", user_message)
        
        # Create parent task
        parent_task = await self.state_manager.create_task(description=user_message)
        await self.state_manager.transition(parent_task.task_id, TaskState.ROUTING)
        
        try:
            mode = getattr(self.config, 'force_mode', 'fast').upper()
            
            if mode == "FAST":
                # User config is fast mode → Single agent routing & execution
                decision = await self._route_fast_path(user_message)
                await self._execute_fast_path(parent_task, decision)
            elif mode == "PLAN":
                # User config is plan mode → Engage Planner
                # ── Phase 1: Detailed Planning (Planner Agent) ──
                plan = await self._generate_plan(parent_task, user_message)
                
                # ── Human Approval ──
                approved_plan = await self._request_plan_approval(parent_task, plan)
                
                # ── Phase 2: Execute the approved plan ──
                await self._execute_plan(parent_task, approved_plan)
                
        except TaskCancelledError:
            pass  # Already transitioned to CANCELLED
        except AgentCLIError as e:
            await self.state_manager.transition(
                parent_task.task_id, TaskState.FAILED, error=e.user_message
            )
            
    # Note: _route_fast_path is defined in 04_multi_agent_definitions.md
    
    async def _generate_plan(
        self, parent_task: "TaskRecord", user_message: str
    ) -> RoutingDecision:
        """
        Phase 2: Run the Planner Agent to explore the codebase 
        and generate a detailed ExecutionPlan.
        """
        planner = self.agents.get("planner")
        if not planner:
            raise AgentCLIError("Planner agent not found in registry.")
        
        await self.state_manager.transition(parent_task.task_id, TaskState.WORKING)
        
        plan_xml = await planner.handle_task(
            task_id=parent_task.task_id,
            task_description=user_message,
            effort_override=EffortLevel.MEDIUM
        )
        
        return self.plan_parser.parse(plan_xml)
```

---

## 7. Task-Isolating Memory (Cross-Reference)

When executing a plan, each Worker Agent starts with **fresh Working Memory** containing:
1. The system prompt
2. A brief summary of what previous steps accomplished (max 2,000 chars)
3. The specific step description

Full details in `01_reasoning_loop.md` Section 6.

This ensures:
- ✅ By Task 8, the Agent isn't polluted with 150 tool calls from Tasks 1-7
- ✅ Every task starts with a clean, focused context window
- ✅ Massive token savings on long-horizon plans

---

## 8. The `/mode plan` Command

Users can explicitly request plan mode for any request:

```bash
# Normal fast-path mode (Default)
> Refactor the auth module  # (Will just be handled by a single agent)

# Explicit plan mode — enforces planner agent
> /mode plan
> Add a hello world endpoint # (Will generate a plan first)
```

```python
class ModeCommand:
    """Handles /mode plan and /mode auto commands."""
    
    def execute(self, args: List[str]):
        if args[0] == "plan":
            self.orchestrator.force_mode = "plan"
            return "Plan mode enabled. All requests will generate an execution plan."
        elif args[0] == "fast":
            self.orchestrator.force_mode = "fast"
            return "Fast-path mode restored. All requests will use single-agent execution without plans."
```

---

## 9. Testing Strategy

```python
import pytest

@pytest.mark.asyncio
async def test_planner_generates_valid_plan():
    planner = PlannerAgent(config=PLANNER_CONFIG, ...)
    
    result = await planner.handle_task(
        task_id="t1",
        task_description="Refactor auth to use JWT"
    )
    
    parser = PlanParser(agent_registry)
    plan = parser.parse(result)
    
    assert len(plan.assignments) >= 1
    assert plan.mode == "PLAN"

@pytest.mark.asyncio
async def test_plan_parser_validates_agent_names():
    parser = PlanParser(agent_registry)
    
    bad_xml = '<execution_plan><tasks><task agent="nonexistent" effort="LOW">Do something</task></tasks></execution_plan>'
    
    with pytest.raises(PlanValidationError):
        parser.parse(bad_xml)

@pytest.mark.asyncio
async def test_plan_approval_approve():
    """User approves → plan is returned as-is."""
    # Mock interaction_handler to return "approve"
    approved = await orchestrator._request_plan_approval(task, plan)
    assert approved == plan

@pytest.mark.asyncio
async def test_plan_approval_reject_triggers_revision():
    """User rejects → Planner revises with feedback."""
    # Mock interaction_handler to return "reject" with feedback
    # Assert: planner.handle_task() is called with feedback context

@pytest.mark.asyncio
async def test_plan_approval_cancel():
    """User cancels → task transitions to CANCELLED."""
    # Mock interaction_handler to return "cancel"
    with pytest.raises(TaskCancelledError):
        await orchestrator._request_plan_approval(task, plan)

@pytest.mark.asyncio
async def test_fast_path_mode():
    orchestrator.config.force_mode = "fast"
    # Execute should use single-agent path directly

@pytest.mark.asyncio
async def test_plan_mode_triggers_planner():
    orchestrator.config.force_mode = "plan"
    # Even simple requests generate a plan
```
