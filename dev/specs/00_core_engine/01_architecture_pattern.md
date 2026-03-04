# Agent CLI System Architecture

## Overview

The Agent CLI is an AI-powered terminal application that orchestrates LLM agents to perform coding tasks. It uses a **Hybrid Architecture: Hierarchical Control over an Event-Driven Backbone** — combining the predictability of a supervisor model with the loose coupling and async performance of an event-driven system.

This document is the **top-level architectural overview**. It maps every component, describes the data flow, and references the detailed spec for each subsystem.

---

## 1. Why This Architecture?

| Principle | Implementation |
|---|---|
| **TUI Responsiveness** | Event-driven — UI never blocks. It subscribes to events and updates reactively. |
| **Loose Coupling** | Agents don't know about UI. UI doesn't know about LLM prompts. They communicate via the Event Bus. |
| **Hierarchical Control** | The Orchestrator enforces rules, routes tasks, manages budgets, prevents infinite loops. |
| **Extensibility** | Adding a new agent type = register with AgentRegistry. Adding a UI widget = subscribe to events. |
| **Abstraction (DRY)** | Every major component is defined via abstract base classes. Concrete implementations are swappable. |

---

## 2. System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              TEXTUAL TUI                                    │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────┐ ┌──────────────┐ │
│  │ Chat Panel    │  │ Session Info     │  │ Changed Files│ │ Terminal     │ │
│  │ (messages,    │  │ (context, cost,  │  │ Panel        │ │ Viewer       │ │
│  │  thinking)    │  │  session ID)     │  │ (real-time)  │ │              │ │
│  └──────┬───────┘  └─────────────────┘  └──────────────┘ └──────────────┘ │
│         │  ↑ events                                                        │
│  ┌──────▼──┴────────────────────────────────────────────────────────────┐  │
│  │                         Interaction Handler                          │  │
│  │              (approval modals, clarification, plan review)           │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└─────────────┬───────────────────────────────────────────────────────────────┘
              │ UserRequestEvent / UserResponseEvent
              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            EVENT BUS (asyncio)                              │
│                   Central async pub/sub message broker                      │
│         All components communicate ONLY through the Event Bus               │
└──┬──────────┬──────────────┬──────────────┬──────────────┬──────────────┬───┘
   │          │              │              │              │              │
   ▼          ▼              ▼              ▼              ▼              ▼
┌──────┐ ┌────────┐ ┌──────────────┐ ┌──────────┐ ┌────────────┐ ┌──────────┐
│State │ │Orchest-│ │  Agents      │ │Tool      │ │  Memory    │ │Structured│
│Mgr   │ │rator  │ │  (Workers)   │ │Executor  │ │  Manager   │ │Logger    │
└──────┘ └────────┘ └──────────────┘ └──────────┘ └────────────┘ └──────────┘
                         │                │              │
                         ▼                ▼              ▼
                    ┌──────────┐   ┌──────────────┐ ┌──────────┐
                    │LLM       │   │ Workspace    │ │ Mem0     │
                    │Provider  │   │ Manager      │ │ (Vector) │
                    └──────────┘   └──────────────┘ └──────────┘
```

---

## 3. Component Registry

Every component in the system, its abstract interface, and its detailed spec:

### Core Engine (`00_core_engine/`)

| Component | Abstract Interface | Spec | Role |
|---|---|---|---|
| **Event Bus** | `AbstractEventBus` | [00_event_bus.md](file:///x:/agent_cli/architect-workspace/00_core_engine/00_event_bus.md) | Central async pub/sub. All inter-component communication. |
| **State Manager** | `AbstractStateManager` | [02_state_management.md](file:///x:/agent_cli/architect-workspace/00_core_engine/02_state_management.md) | Task lifecycle (`PENDING → ROUTING → WORKING → SUCCESS/FAILED`). Single source of truth. |
| **Task Planner** | `PlanParser` | [03_task_planning.md](file:///x:/agent_cli/architect-workspace/00_core_engine/03_task_planning.md) | Two-phase routing: classify FAST_PATH vs PLAN, then Planner Agent generates ExecutionPlan. |
| **Error Handler** | `BaseRetryPolicy` | [04_error_handling.md](file:///x:/agent_cli/architect-workspace/00_core_engine/04_error_handling.md) | 3-tier errors (Retryable → Recoverable → Fatal). Retry engine with exponential backoff. |

### Agent Logic (`01_agent_logic/`)

| Component | Abstract Interface | Spec | Role |
|---|---|---|---|
| **Reasoning Loop** | `BaseAgent` | [01_reasoning_loop.md](file:///x:/agent_cli/architect-workspace/01_agent_logic/01_reasoning_loop.md) | ReAct loop: Think → Act → Observe. `AgentConfig` for per-agent settings. Effort levels. |
| **Schema Validator** | `BaseSchemaValidator` | [02_schema_verification.md](file:///x:/agent_cli/architect-workspace/01_agent_logic/02_schema_verification.md) | Dual-mode parsing: Native Function Calling or XML fallback. Pydantic validation. |
| **Tool Architecture** | `BaseTool`, `ToolExecutor` | [03_tools_architecture.md](file:///x:/agent_cli/architect-workspace/01_agent_logic/03_tools_architecture.md) | Tool registry, Pydantic args, output formatting, workspace enforcement, safety checks. |
| **Orchestrator & Routing** | `AgentRegistry` | [04_multi_agent_definitions.md](file:///x:/agent_cli/architect-workspace/01_agent_logic/04_multi_agent_definitions.md) | Agent catalogue, LLM-based routing, capability tags, TOML-based user-defined agents. |

### Data Management (`02_data_management/`)

| Component | Abstract Interface | Spec | Role |
|---|---|---|---|
| **Memory Manager** | `BaseMemoryManager` | [01_memory_management.md](file:///x:/agent_cli/architect-workspace/02_data_management/01_memory_management.md) | 3-layer model: Working (token-budgeted), Episodic (Session DB), Semantic (Mem0). |
| **Config Manager** | `AgentSettings` | [02_config_management.md](file:///x:/agent_cli/architect-workspace/02_data_management/02_config_management.md) | Tri-layer TOML merge, Pydantic validation, secrets hierarchy, provider registration. |
| **Workspace Manager** | `BaseWorkspaceManager` | [03_workspace_sandbox.md](file:///x:/agent_cli/architect-workspace/02_data_management/03_workspace_sandbox.md) | Path jailing, sandbox mode, `.gitignore` management, workspace auto-detection. |
| **Session Persistence** | `AbstractSessionManager` | [04_session_persistence.md](file:///x:/agent_cli/architect-workspace/02_data_management/04_session_persistence.md) | SQLite-based session save/restore, `/session` command, workspace-scoped sessions. |

### User Interface (`03_user_interface/`)

| Component | Abstract Interface | Spec | Role |
|---|---|---|---|
| **Human-in-the-Loop** | `BaseInteractionHandler` | [01_human_in_loop.md](file:///x:/agent_cli/architect-workspace/03_user_interface/01_human_in_loop.md) | 4 interaction types: Approval, Clarification, Plan Approval, Fatal Error. asyncio.Event pausing. |
| **Terminal Viewer** | — | [02_terminal_viewer.md](file:///x:/agent_cli/architect-workspace/03_user_interface/02_terminal_viewer.md) | Persistent terminal management, process lifecycle, output viewing. |
| **Command System** | `BaseCommandRegistry` | [03_command_system.md](file:///x:/agent_cli/architect-workspace/03_user_interface/03_command_system.md) | `/` commands: `/mode`, `/agent`, `/model`, `/config`, `/clear`, `/session`, `/sandbox`. |
| **Changed Files** | `FileChangeTracker` | [04_changed_files.md](file:///x:/agent_cli/architect-workspace/03_user_interface/04_changed_files.md) | Real-time file change tracking in sidebar. Accept/Reject all on completion. |

### Utilities (`04_utilities/`)

| Component | Abstract Interface | Spec | Role |
|---|---|---|---|
| **LLM Providers** | `BaseLLMProvider` | [01_ai_providers.md](file:///x:/agent_cli/architect-workspace/04_utilities/01_ai_providers.md) | Adapter pattern: OpenAI, Anthropic, Google, OpenAI-compatible. Retry engine. |
| **File Discovery** | — | [02_file_discovery.md](file:///x:/agent_cli/architect-workspace/04_utilities/02_file_discovery.md) | Workspace file search, indexing, pattern matching. |
| **Observability** | `StructuredLogger` | [03_observability.md](file:///x:/agent_cli/architect-workspace/04_utilities/03_observability.md) | Structured JSON logging, OpenTelemetry spans, key sanitization. |

---

## 4. Abstract Interfaces Summary

Every major component is defined via an abstract base class. Concrete implementations are swappable for testing (mocks) and extensibility (new providers, new agents):

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Any, Type


# ── 1. Event System ─────────────────────────────────────────

@dataclass
class BaseEvent:
    source: str
    event_id: str = ""      # Auto-generated UUID
    timestamp: float = 0.0  # Auto-set on creation
    task_id: str = ""       # Links event to a task

class AbstractEventBus(ABC):
    @abstractmethod
    async def emit(self, event: BaseEvent) -> None: ...
    
    @abstractmethod
    def subscribe(self, event_type: Type[BaseEvent], callback) -> None: ...


# ── 2. State Management ─────────────────────────────────────

class TaskState(Enum):
    PENDING        = auto()
    ROUTING        = auto()
    PLANNING       = auto()
    WORKING        = auto()
    AWAITING_INPUT = auto()
    SUCCESS        = auto()
    FAILED         = auto()
    CANCELLED      = auto()

class AbstractStateManager(ABC):
    @abstractmethod
    async def transition(self, task_id: str, new_state: TaskState) -> None: ...
    
    @abstractmethod
    def get_state(self, task_id: str) -> TaskState: ...


# ── 3. Agent ─────────────────────────────────────────────────

class BaseAgent(ABC):
    @abstractmethod
    async def handle_task(self, task_description: str, task_id: str) -> str: ...
    
    @abstractmethod
    def build_system_prompt(self) -> str: ...


# ── 4. LLM Provider ─────────────────────────────────────────

class BaseLLMProvider(ABC):
    @abstractmethod
    async def safe_generate(self, context: List[dict], **kwargs) -> "LLMResponse": ...


# ── 5. Tool System ───────────────────────────────────────────

class BaseTool(ABC):
    name: str
    is_safe: bool
    
    @abstractmethod
    async def execute(self, **kwargs) -> str: ...


# ── 6. Memory ────────────────────────────────────────────────

class BaseMemoryManager(ABC):
    @abstractmethod
    def get_working_context(self) -> List[dict]: ...
    
    @abstractmethod
    async def summarize_and_compact(self) -> None: ...


# ── 7. Workspace ─────────────────────────────────────────────

class BaseWorkspaceManager(ABC):
    @abstractmethod
    def enforce_path(self, path: str, operation: "FileOperation") -> "Path": ...


# ── 8. Interaction ───────────────────────────────────────────

class BaseInteractionHandler(ABC):
    @abstractmethod
    async def request_human_input(self, request: "UserInteractionRequest") -> "UserInteractionResponse": ...


# ── 9. Session ───────────────────────────────────────────────

class AbstractSessionManager(ABC):
    @abstractmethod
    async def save_session(self, session: "Session") -> None: ...
    
    @abstractmethod
    async def load_session(self, session_id: str) -> "Session": ...
```

---

## 5. The Request Lifecycle (End-to-End Flow)

### A. Simple Request (Fast-Path)

```
User types: "What does the main() function do in app.py?"

1. TUI publishes UserRequestEvent
2. Orchestrator receives event
3. StateManager: create TaskRecord (PENDING → ROUTING)
4. Orchestrator calls routing LLM → "FAST_PATH, agent=researcher, effort=LOW"
5. StateManager: ROUTING → WORKING
6. Orchestrator delegates to Researcher agent
7. Agent reasoning loop (ReAct):
   a. Think: "I need to read app.py"
   b. Act: read_file(path="app.py") → ToolExecutor validates path → executes
   c. Observe: file contents
   d. Think: "Now I can explain main()"
   e. Final Answer: "The main() function initializes..."
8. Agent publishes TaskResultEvent
9. StateManager: WORKING → SUCCESS
10. TUI renders the final answer
```

### B. Complex Request (Plan Mode)

```
User types: "Refactor auth module to use JWT"

1.  TUI publishes UserRequestEvent
2.  Orchestrator receives event
3.  StateManager: PENDING → ROUTING
4.  Orchestrator calls routing LLM → "PLAN" (complex task)
5.  StateManager: ROUTING → PLANNING
6.  Orchestrator launches Planner Agent (read-only tools, MEDIUM effort)
7.  Planner explores codebase → generates ExecutionPlan (3 tasks)
8.  Orchestrator shows plan in TUI → AWAITING_INPUT (plan approval)
9.  User approves plan
10. StateManager: PLANNING → WORKING
11. Sequential execution:
    Task 1 (coder, MEDIUM): "Remove cookie-based auth logic"
      → Agent reasoning loop → SUCCESS
    Task 2 (coder, HIGH): "Implement JWT middleware"
      → Agent reasoning loop → file changes appear in sidebar → SUCCESS
    Task 3 (coder, MEDIUM): "Write unit tests"
      → Agent reasoning loop → SUCCESS
12. StateManager: WORKING → SUCCESS
13. Changed Files panel shows accept/reject
14. TUI renders completion summary
```

### C. Dangerous Tool Request (Human-in-the-Loop)

```
Agent wants to run: rm -rf node_modules && npm install

1. ToolExecutor checks is_safe → False
2. ToolExecutor checks regex → "rm" matches dangerous pattern
3. StateManager: WORKING → AWAITING_INPUT
4. InteractionHandler shows approval modal in TUI
5. User presses [E] to edit → removes -rf → approves "rm -r node_modules && npm install"
6. ToolExecutor executes modified command
7. StateManager: AWAITING_INPUT → WORKING
8. Agent receives tool result and continues
```

---

## 6. Dependency Injection Graph

All components are wired together at startup via constructor injection. No global singletons, no hidden imports.

```python
def bootstrap(workspace_root: Path) -> "AgentCLIApp":
    """Wire all components together at startup."""
    
    # ── Configuration ────────────────────────────────────
    settings = AgentSettings()  # Loads TOML + env vars + defaults
    
    # ── Core Infrastructure ──────────────────────────────
    event_bus = AsyncEventBus()
    state_manager = StateManager(event_bus=event_bus)
    logger = StructuredLogger(log_dir=settings.log_directory)
    
    # ── Workspace & Security ─────────────────────────────
    workspace = StrictWorkspaceManager(
        terminal_cwd=str(workspace_root),
        sandbox_mode=False
    )
    
    # ── LLM Providers ────────────────────────────────────
    provider_registry = load_providers(settings)
    default_provider = provider_registry.get_provider(settings.default_model)
    routing_provider = provider_registry.get_provider(settings.routing_model)
    
    # ── Memory ───────────────────────────────────────────
    token_counter = get_token_counter(
        provider=default_provider.provider_name,
        model=settings.default_model
    )
    memory_manager = ContextMemoryManager(
        token_counter=token_counter,
        token_budget=TOKEN_BUDGETS.get(settings.default_model, TokenBudget(128000)),
        session_manager=session_manager,
        summarization_provider=routing_provider,  # Reuse cheap model
    )
    
    # ── Tools ────────────────────────────────────────────
    tool_registry = ToolRegistry()
    tool_registry.register_defaults()  # read_file, write_file, grep_search, etc.
    
    change_tracker = FileChangeTracker(
        workspace_root=workspace_root,
        event_bus=event_bus
    )
    
    tool_executor = ToolExecutor(
        registry=tool_registry,
        workspace=workspace,
        change_tracker=change_tracker,
        interaction_handler=interaction_handler,
        event_bus=event_bus,
        logger=logger,
    )
    
    # ── Interaction Handler ──────────────────────────────
    interaction_handler = TUIInteractionHandler(app=tui_app)
    
    # ── Session ──────────────────────────────────────────
    session_manager = SQLiteSessionManager(
        db_path=Path.home() / ".agent_cli" / "sessions.db"
    )
    
    # ── Agent Registry ───────────────────────────────────
    agent_registry = AgentRegistry()
    agent_registry.register_system_agents(
        provider=default_provider,
        tool_executor=tool_executor,
        memory_manager=memory_manager,
        event_bus=event_bus,
        state_manager=state_manager,
        interaction_handler=interaction_handler,
    )
    agent_registry.register_user_agents(settings)  # From TOML [agents.*]
    
    # ── Orchestrator ─────────────────────────────────────
    orchestrator = Orchestrator(
        agent_registry=agent_registry,
        state_manager=state_manager,
        event_bus=event_bus,
        routing_provider=routing_provider,
        change_tracker=change_tracker,
        interaction_handler=interaction_handler,
        memory_manager=memory_manager,
        settings=settings,
    )
    
    # ── TUI Application ─────────────────────────────────
    tui_app = AgentCLIApp(
        orchestrator=orchestrator,
        event_bus=event_bus,
        settings=settings,
        session_manager=session_manager,
        workspace=workspace,
        change_tracker=change_tracker,
    )
    
    return tui_app
```

---

## 7. Directory Structure (Two Levels)

### Global (`~/.agent_cli/`)
```
~/.agent_cli/
├── config.toml          # Global user preferences
├── sessions.db          # SQLite: all sessions across workspaces
└── logs/                # Structured JSON logs
```

### Local Workspace (`<project>/.agent_cli/`)
```
<project>/
├── .agent_cli/
│   ├── settings.toml    # Project-specific config overrides
│   └── sandbox/         # Sandbox mode isolation folder
├── .gitignore           # .agent_cli/ auto-added
└── ...                  # User's project files
```

See [03_workspace_sandbox.md](file:///x:/agent_cli/architect-workspace/02_data_management/03_workspace_sandbox.md) for details.

---

## 8. Event Catalogue

All events emitted in the system and who produces/consumes them:

| Event | Producer | Consumer(s) |
|---|---|---|
| `UserRequestEvent` | TUI (input bar) | Orchestrator |
| `UserResponseEvent` | TUI (modals, input) | InteractionHandler → Agent/ToolExecutor |
| `StateChangeEvent` | StateManager | TUI (status), Logger |
| `TaskDelegatedEvent` | Orchestrator | Agent |
| `AgentMessageEvent` | Agent (thinking) | TUI (chat panel) |
| `TaskResultEvent` | Agent (final answer) | Orchestrator |
| `ToolStartEvent` | ToolExecutor | TUI (step display), Logger |
| `ToolResultEvent` | ToolExecutor | Agent (observation), Logger |
| `FileChangedEvent` | FileChangeTracker | TUI (Changed Files panel) |
| `ChangesResetEvent` | Orchestrator | TUI (clear Changed Files panel) |
| `ErrorEvent` | Any component | Logger, Error Handler |
| `SystemShutdownEvent` | TUI (`/exit`) | All components (cleanup) |
| `SessionSavedEvent` | SessionManager | TUI (session info panel) |
| `PlanReadyEvent` | Planner Agent | Orchestrator → TUI (plan review) |

---

## 9. Technology Stack

| Layer | Technology | Rationale |
|---|---|---|
| **Language** | Python 3.12+ | LLM ecosystem, async/await, type hints |
| **TUI Framework** | Textual | Modern terminal UI, CSS styling, reactive widgets |
| **Async Runtime** | asyncio | Native Python async. Event Bus + I/O without threads. |
| **Config** | Pydantic Settings + TOML | Type-safe, env var merging, human-readable files |
| **Database** | SQLite (aiosqlite) | Zero-dependency, file-based, async-capable |
| **Vector Memory** | Mem0 | Cross-session semantic memory via vector search |
| **LLM SDKs** | openai, anthropic, google-genai | Official provider SDKs |
| **Token Counting** | tiktoken + provider-specific | Accurate context budget management |
| **Logging** | structlog (JSON) | Structured, queryable, sanitized |
| **CLI Entry** | Click | Command parsing, help generation |
| **Security** | keyring | OS-level encrypted credential storage |

---

## 10. Design Principles

1. **Everything Through the Event Bus.** No direct method calls between UI and backend. The Event Bus is the only communication channel.

2. **Abstract Base Classes Everywhere.** Every major component has an ABC. Concrete implementations are swappable. Testing uses mocks.

3. **Dependency Injection, Not Global State.** All components receive their dependencies via constructor injection in `bootstrap()`. No singleton imports.

4. **Fail Safe, Not Fail Silent.** Errors are captured, classified (Retryable/Recoverable/Fatal), and escalated to the user with clear options (Retry/Abort/Report).

5. **Human-in-the-Loop by Default.** Dangerous operations always require user approval. Auto-approve is opt-in.

6. **Configuration is Externalized.** Every magic number is a field in `AgentSettings` with a sensible default, overridable via TOML, env vars, or CLI flags.

7. **Token Budget Awareness.** The Memory Manager actively manages context window usage with provider-specific token counting and automatic compaction.

8. **Workspace Jailing.** All file operations are constrained to the workspace root. Path validation is centralized in the ToolExecutor, not per-tool.
