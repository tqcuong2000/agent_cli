# Tool Execution & Persistent Terminal Architecture

## Overview
Tools bridge the gap between the Agent's reasoning (text generation) and the host operating system. They are the only mechanism through which an Agent can read files, search code, execute commands, or interact with running processes.

This architecture defines how tools are structured (with Pydantic schemas for native FC compatibility), how they're registered and discovered, how their output is standardized, and how the Persistent Terminal Manager enables long-running background processes.

---

## 1. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Argument Validation** | Pydantic `args_schema` per tool | Type-safe, auto-documented, auto-converts to native FC JSON Schema |
| **Terminal Synchronization** | `wait_for_terminal()` + `sleep()` | Deterministic waiting for server readiness, not guessing delays |
| **Output Formatting** | Centralized `ToolOutputFormatter` | Consistent truncation, prevents context bloat, uniform structure |

---

## 2. The `BaseTool` Interface

Each tool is a self-contained unit with four responsibilities:
1. **Schema**: A Pydantic model declaring its arguments (used by LLM and by native FC APIs)
2. **Metadata**: Name, description, safety flag, category
3. **Executor**: The async function that performs the action
4. **Shield**: Error handling that catches OS exceptions and returns formatted strings

```python
from abc import ABC, abstractmethod
from pydantic import BaseModel, Field
from typing import Type, Optional
from enum import Enum, auto


class ToolCategory(Enum):
    """Categories for organizing and filtering tools."""
    FILE       = auto()   # read_file, write_file, edit_file
    SEARCH     = auto()   # grep_search, find_files
    EXECUTION  = auto()   # run_command, spawn_terminal
    TERMINAL   = auto()   # read_terminal, send_terminal_input, kill_terminal
    UTILITY    = auto()   # sleep, wait_for_terminal, ask_user


class BaseTool(ABC):
    """
    Abstract base class for all tools in the system.
    
    Every tool declares:
    - name:        Unique identifier used by the LLM to invoke the tool
    - description: Human-readable docstring (injected into LLM prompt for XML mode)
    - args_schema: Pydantic model defining expected arguments (auto-converted to JSON Schema for native FC)
    - is_safe:     Whether this tool requires user approval before execution
    - category:    Grouping for tool filtering (e.g., file-only agents get FILE + SEARCH tools)
    """
    
    name: str
    description: str
    is_safe: bool = False            # Requires user approval if False
    category: ToolCategory = ToolCategory.UTILITY
    
    @property
    @abstractmethod
    def args_schema(self) -> Type[BaseModel]:
        """
        Returns the Pydantic model class for this tool's arguments.
        Used for:
        - Argument validation before execution
        - Auto-generating JSON Schema for native FC providers
        - Auto-generating text descriptions for XML prompting
        """
        pass
    
    @abstractmethod
    async def execute(self, **kwargs) -> str:
        """
        Execute the tool with validated arguments.
        
        Returns:
            A formatted string result (passed to ToolOutputFormatter before
            reaching the Agent's Working Memory).
        
        Raises:
            ToolExecutionError: On any recoverable failure (file not found,
            permission denied, command failed). The error handler in the
            Agent loop returns this as an observation, not a crash.
        """
        pass
    
    def validate_args(self, **kwargs) -> BaseModel:
        """
        Validate arguments against the Pydantic schema.
        Raises ValidationError with a helpful message if invalid.
        """
        return self.args_schema(**kwargs)
    
    def get_json_schema(self) -> dict:
        """
        Generate JSON Schema for native FC providers.
        Used by BaseToolFormatter.format_for_native_fc().
        """
        return self.args_schema.model_json_schema()
```

---

## 3. Concrete Tool Examples

### A. File Operations

```python
class ReadFileArgs(BaseModel):
    path: str = Field(description="Relative path to the file to read")
    start_line: Optional[int] = Field(default=None, description="Starting line number (1-indexed)")
    end_line: Optional[int] = Field(default=None, description="Ending line number (inclusive)")


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read the contents of a file. Supports optional line range slicing."
    is_safe = True
    category = ToolCategory.FILE
    
    def __init__(self, workspace: "BaseWorkspaceManager"):
        self.workspace = workspace
    
    @property
    def args_schema(self) -> Type[BaseModel]:
        return ReadFileArgs
    
    async def execute(self, path: str, start_line: int = None, end_line: int = None) -> str:
        # Enforce workspace jailing
        resolved = self.workspace.enforce_jail(path, is_write_operation=False)
        
        if not resolved.exists():
            raise ToolExecutionError(f"File not found: {path}")
        
        content = resolved.read_text(encoding="utf-8")
        
        # Optional line slicing
        if start_line is not None or end_line is not None:
            lines = content.splitlines()
            start = (start_line or 1) - 1
            end = end_line or len(lines)
            content = "\n".join(lines[start:end])
        
        return content
```

### B. Command Execution

```python
class RunCommandArgs(BaseModel):
    command: str = Field(description="The shell command to execute")
    timeout: int = Field(default=30, description="Timeout in seconds (max 120)")


class RunCommandTool(BaseTool):
    name = "run_command"
    description = "Execute a shell command and return its stdout/stderr. For short-lived commands only (max 120s timeout). For long-running processes, use spawn_terminal instead."
    is_safe = False  # Requires approval (dynamic regex may override)
    category = ToolCategory.EXECUTION
    
    @property
    def args_schema(self) -> Type[BaseModel]:
        return RunCommandArgs
    
    async def execute(self, command: str, timeout: int = 30) -> str:
        timeout = min(timeout, 120)  # Hard cap
        
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.workspace.root_workspace)
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise ToolExecutionError(
                f"Command timed out after {timeout}s: {command[:100]}"
            )
        
        output = stdout.decode() + stderr.decode()
        exit_code = proc.returncode
        
        return f"[Exit Code: {exit_code}]\n{output}"
```

---

## 4. Tool Registry & Discovery

The Tool Registry is a centralized catalog where all tools are registered at startup. Agents and the `BaseToolFormatter` consume tools from this registry.

```python
from typing import Dict, List, Optional


class ToolRegistry:
    """
    Central catalog of all available tools.
    Agents are initialized with a filtered subset based on their role.
    The BaseToolFormatter reads from this to generate LLM tool definitions.
    """
    
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
    
    def register(self, tool: BaseTool) -> None:
        """Register a tool. Raises if name already exists."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered.")
        self._tools[tool.name] = tool
    
    def get(self, name: str) -> Optional[BaseTool]:
        """Retrieve a tool by name."""
        return self._tools.get(name)
    
    def get_by_category(self, category: ToolCategory) -> List[BaseTool]:
        """Get all tools in a category."""
        return [t for t in self._tools.values() if t.category == category]
    
    def get_for_agent(self, tool_names: List[str]) -> List[BaseTool]:
        """
        Return a filtered list of tools for a specific agent.
        Used during agent initialization to assign its tool set.
        """
        tools = []
        for name in tool_names:
            tool = self._tools.get(name)
            if tool:
                tools.append(tool)
            else:
                raise ValueError(f"Tool '{name}' not found in registry.")
        return tools
    
    def get_all_names(self) -> List[str]:
        """Return all registered tool names (for the Schema Validator)."""
        return list(self._tools.keys())
    
    def get_definitions_for_llm(self, tool_names: List[str]) -> List[dict]:
        """
        Generate standardized tool definitions consumable by BaseToolFormatter.
        Each definition includes: name, description, json_schema.
        """
        definitions = []
        for name in tool_names:
            tool = self._tools[name]
            definitions.append({
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.get_json_schema(),
                "is_safe": tool.is_safe,
                "category": tool.category.name,
            })
        return definitions


# ── Startup Wiring ────────────────────────────────────────
def build_tool_registry(workspace: "BaseWorkspaceManager") -> ToolRegistry:
    """Create and populate the tool registry with all system tools."""
    registry = ToolRegistry()
    
    # File tools
    registry.register(ReadFileTool(workspace))
    registry.register(WriteFileTool(workspace))
    registry.register(EditFileTool(workspace))
    
    # Search tools
    registry.register(GrepSearchTool(workspace))
    registry.register(FindFilesTool(workspace))
    
    # Execution tools
    registry.register(RunCommandTool(workspace))
    registry.register(SleepTool())
    
    # Terminal tools (registered separately, connected to TerminalManager)
    # See Section 6
    
    return registry
```

### Integration with BaseToolFormatter (from `01_ai_providers.md`)

```python
class ToolFormatter(BaseToolFormatter):
    """Converts tool definitions to provider-specific formats."""
    
    def format_for_native_fc(self, tools: List[dict]) -> list:
        """Convert to OpenAI/Anthropic native function calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                }
            }
            for t in tools
        ]
    
    def format_for_prompt_injection(self, tools: List[dict]) -> str:
        """Convert to text block for XML prompting system prompt."""
        lines = ["You have access to the following tools:\n"]
        for t in tools:
            params = t["parameters"].get("properties", {})
            param_str = ", ".join(
                f'{name}: {info.get("type", "any")}' 
                for name, info in params.items()
            )
            lines.append(f"- **{t['name']}**({param_str}): {t['description']}")
        
        lines.append("\nTo use a tool, respond with:")
        lines.append("<action><tool>tool_name</tool><args>{...}</args></action>")
        return "\n".join(lines)
```

---

## 5. Tool Output Formatter

All tool results pass through a centralized formatter before reaching the Agent's Working Memory. This prevents context bloat and ensures consistent formatting.

```python
class ToolOutputFormatter:
    """
    Standardizes tool output before it enters Working Memory.
    Enforces max length, adds tool name prefix, and handles truncation.
    """
    
    def __init__(self, max_output_length: int = 5000):
        self.max_output_length = max_output_length
    
    def format(self, tool_name: str, raw_output: str, success: bool = True) -> str:
        """
        Format a tool's raw output for the Agent's Working Memory.
        
        Rules:
        1. Prefix with tool name for LLM context
        2. Truncate if exceeds max length (keep head + tail)
        3. Mark errors clearly
        """
        if not success:
            return f"[Tool: {tool_name}] Error:\n{raw_output[:2000]}"
        
        if len(raw_output) <= self.max_output_length:
            return f"[Tool: {tool_name}] Result:\n{raw_output}"
        
        # Truncate: keep head and tail for context
        half = self.max_output_length // 2
        head = raw_output[:half]
        tail = raw_output[-half:]
        truncated_chars = len(raw_output) - self.max_output_length
        
        return (
            f"[Tool: {tool_name}] Result (truncated):\n"
            f"{head}\n"
            f"\n[...TRUNCATED {truncated_chars:,} characters. "
            f"Use read_file with line range for full content.]\n\n"
            f"{tail}"
        )
```

---

## 6. Tool Executor (The Safety + Observability Wrapper)

The Tool Executor sits between the Agent loop and the actual `BaseTool.execute()`. It handles safety checks, observability spans, output formatting, and error shielding.

```python
class ToolExecutor:
    """
    Executes tool calls with safety checks, observability, and output formatting.
    This is what the Agent loop calls — never BaseTool.execute() directly.
    """
    
    def __init__(
        self,
        registry: ToolRegistry,
        event_bus: AbstractEventBus,
        output_formatter: ToolOutputFormatter,
        logger: "StructuredLogger",
        session_metrics: "SessionMetrics",
        interaction_handler: "BaseInteractionHandler"
    ):
        self.registry = registry
        self.event_bus = event_bus
        self.output_formatter = output_formatter
        self.logger = logger
        self.session_metrics = session_metrics
        self.interaction_handler = interaction_handler
    
    async def execute(self, action: "ParsedAction", task_id: str) -> str:
        """
        Execute a validated tool call.
        
        Flow:
        1. Look up tool in registry
        2. Validate arguments via Pydantic schema
        3. Check safety (is_safe flag + dynamic regex for commands)
        4. If unsafe → request user approval (AWAITING_INPUT)
        5. Emit ToolExecutionStartEvent
        6. Execute with error shielding
        7. Format output via ToolOutputFormatter
        8. Emit ToolExecutionResultEvent
        9. Return formatted result string
        """
        tool = self.registry.get(action.tool_name)
        if not tool:
            return f"[Tool Error] Unknown tool: '{action.tool_name}'"
        
        # ── 1. Validate arguments ──
        try:
            validated = tool.validate_args(**action.arguments)
        except Exception as e:
            return f"[Tool Error] Invalid arguments for '{action.tool_name}': {e}"
        
        # ── 2. Safety check ──
        requires_approval = not tool.is_safe
        if requires_approval and tool.name == "run_command":
            requires_approval = self._is_dangerous_command(action.arguments.get("command", ""))
        
        if requires_approval:
            approved = await self.interaction_handler.request_human_input(
                UserInteractionRequest(
                    interaction_type=InteractionType.APPROVAL,
                    message=f"Agent wants to execute: {action.tool_name}",
                    tool_name=action.tool_name,
                    tool_args=action.arguments
                )
            )
            if approved.lower() not in ("y", "yes", "approve"):
                return f"[Tool: {action.tool_name}] User denied execution."
        
        # ── 3. Execute with observability ──
        span = SpanContext(task_id=task_id, span_type="tool_exec")
        
        await self.event_bus.emit(ToolExecutionStartEvent(
            source="tool_executor",
            tool_name=action.tool_name,
            task_id=task_id
        ))
        
        try:
            raw_result = await tool.execute(**validated.model_dump())
            timing = span.finish()
            success = True
            
        except ToolExecutionError as e:
            timing = span.finish()
            raw_result = str(e)
            success = False
            
        except Exception as e:
            # Shield: catch unexpected OS errors
            timing = span.finish()
            raw_result = f"{type(e).__name__}: {str(e)}"
            success = False
        
        # ── 4. Log ──
        self.logger.log(
            "INFO" if success else "WARNING",
            "tool_executor",
            f"Tool '{action.tool_name}' {'completed' if success else 'failed'}",
            task_id=task_id,
            span_id=timing["span_id"],
            span_type="tool_exec",
            data={
                "tool": action.tool_name,
                "duration_ms": timing["duration_ms"],
                "success": success,
                "result_length": len(raw_result),
            }
        )
        self.session_metrics.record_tool_call(success=success)
        
        # ── 5. Format output ──
        formatted = self.output_formatter.format(action.tool_name, raw_result, success)
        
        await self.event_bus.emit(ToolExecutionResultEvent(
            source="tool_executor",
            tool_name=action.tool_name,
            task_id=task_id,
            success=success
        ))
        
        return formatted
    
    def _is_dangerous_command(self, command: str) -> bool:
        """Dynamic regex check for command safety (from Human-in-the-Loop spec)."""
        import re
        safe_patterns = [
            r"^(ls|cat|echo|pwd|head|tail|wc|grep|find|which|whoami|date|env)\b",
            r"^python\s+-c\s+['\"]print\b",
        ]
        for pattern in safe_patterns:
            if re.match(pattern, command.strip()):
                return False  # Safe — no approval needed
        return True  # Dangerous — approval required
```

---

## 7. Persistent Terminal Management

### A. The Terminal Tool Suite (5 Tools)

| Tool | Safety | Description |
|---|---|---|
| `spawn_terminal(name, command)` | `is_safe=False` | Start a non-blocking subprocess. Returns instantly. |
| `read_terminal(name, lines)` | `is_safe=True` | Fetch the most recent N lines from stdout/stderr. |
| `send_terminal_input(name, input)` | `is_safe=False` | Pipe text into the subprocess stdin. |
| `kill_terminal(name)` | `is_safe=False` | Send SIGTERM/SIGKILL to the process. |
| `wait_for_terminal(name, pattern, timeout)` | `is_safe=True` | Watch output buffer for a regex pattern. Returns when matched or on timeout. |

### B. The `BaseTerminalManager` Interface

```python
from abc import ABC, abstractmethod
from typing import Optional, List, Dict


@dataclass
class TerminalInfo:
    """Metadata about a managed terminal."""
    name: str
    command: str
    pid: int
    is_alive: bool
    line_count: int


class BaseTerminalManager(ABC):
    """
    Manages persistent background processes.
    Subscribes to SystemShutdownEvent for zombie cleanup.
    """
    
    @abstractmethod
    async def spawn(self, name: str, command: str) -> TerminalInfo:
        """Start a non-blocking subprocess. Raises if name already exists."""
        pass
    
    @abstractmethod
    async def read(self, name: str, lines: int = 100) -> str:
        """Fetch the last N lines from the output buffer."""
        pass
    
    @abstractmethod
    async def send_input(self, name: str, text: str) -> None:
        """Pipe text into the subprocess stdin."""
        pass
    
    @abstractmethod
    async def kill(self, name: str) -> None:
        """Terminate a specific process."""
        pass
    
    @abstractmethod
    async def wait_for_output(
        self, name: str, pattern: str, timeout: float = 30.0
    ) -> Optional[str]:
        """
        Watch the output buffer for a regex pattern match.
        Returns the matching line if found, None if timeout.
        Polls every 0.5s to avoid busy-waiting.
        """
        pass
    
    @abstractmethod
    def list_terminals(self) -> List[TerminalInfo]:
        """List all active terminals."""
        pass
    
    @abstractmethod
    async def cleanup_all(self) -> None:
        """Kill all managed processes. Called on SystemShutdownEvent."""
        pass
```

### C. Solving Persistent Terminal Challenges

#### 1. The Zombie Problem (Orphan Processes)
**Problem:** If the main CLI crashes or the user hits `Ctrl+C`, background servers keep running forever.

**Solution:** 
- The Terminal Manager subscribes to `SystemShutdownEvent` to iterate and kill all registered PIDs.
- An `atexit` handler provides a fallback if the Event Bus is already down.
- `asyncio.create_subprocess_exec` with `wait()` callbacks detect unexpected child crashes.

```python
class ProcessTerminalManager(BaseTerminalManager):
    def __init__(self, event_bus: AbstractEventBus, workspace: "BaseWorkspaceManager"):
        self._processes: Dict[str, asyncio.subprocess.Process] = {}
        self._buffers: Dict[str, collections.deque] = {}
        self.workspace = workspace
        
        event_bus.subscribe("SystemShutdownEvent", self._on_shutdown, priority=5)
        atexit.register(self._sync_cleanup)
    
    async def _on_shutdown(self, event):
        await self.cleanup_all()
    
    def _sync_cleanup(self):
        """Fallback: synchronous kill for atexit (no event loop)."""
        for name, proc in self._processes.items():
            try:
                proc.kill()
            except ProcessLookupError:
                pass
```

#### 2. Buffer Bloat
**Problem:** A web server outputting 500 lines/second blows up RAM.

**Solution:** Each spawned terminal's `stdout/stderr` streams are piped into a bounded `collections.deque(maxlen=N)` where N is configurable via `AgentSettings.terminal_log_max_lines` (default 2000).

```python
async def _read_stream(self, name: str, stream: asyncio.StreamReader):
    """Background task: continuously read stdout/stderr into the bounded buffer."""
    while True:
        line = await stream.readline()
        if not line:
            break
        decoded = line.decode("utf-8", errors="replace").rstrip()
        self._buffers[name].append(decoded)
        
        # Emit to Event Bus for Terminal Viewer TUI
        await self.event_bus.emit(TerminalLogEvent(
            source="terminal_manager",
            terminal_name=name,
            line=decoded
        ))
```

#### 3. Execution Synchronization (Deterministic Waiting)
**Problem:** Agent spawns a server and immediately tries to curl it — server isn't ready yet.

**Solution:** Two tools for different needs:

**`sleep(seconds)`** — General-purpose delay. Simple, but the agent guesses duration.

**`wait_for_terminal(name, pattern, timeout)`** — Deterministic. Watches the output buffer for a regex pattern match.

```python
async def wait_for_output(self, name: str, pattern: str, timeout: float = 30.0) -> Optional[str]:
    import re
    compiled = re.compile(pattern)
    deadline = time.time() + timeout
    
    while time.time() < deadline:
        # Check existing buffer lines
        for line in self._buffers.get(name, []):
            if compiled.search(line):
                return line
        await asyncio.sleep(0.5)  # Poll interval
    
    return None  # Timeout
```

**Agent usage:**
```xml
<thinking>I'll start the server and wait for it to be ready.</thinking>
<action>
    <tool>spawn_terminal</tool>
    <args>{"name": "server", "command": "npm start"}</args>
</action>

<!-- After spawn returns: -->
<action>
    <tool>wait_for_terminal</tool>
    <args>{"name": "server", "pattern": "listening on port", "timeout": 10}</args>
</action>

<!-- Now safe to interact: -->
<action>
    <tool>run_command</tool>
    <args>{"command": "curl http://localhost:3000/health"}</args>
</action>
```

---

## 8. Essential Tool Inventory

| Tool | Category | `is_safe` | Description |
|---|---|---|---|
| `read_file` | FILE | ✅ True | Read file contents (optional line range) |
| `write_file` | FILE | ❌ False | Create or overwrite a file |
| `edit_file` | FILE | ❌ False | Apply targeted edits to an existing file |
| `grep_search` | SEARCH | ✅ True | Search for patterns across files |
| `find_files` | SEARCH | ✅ True | Find files by name/glob pattern |
| `run_command` | EXECUTION | ❌ False* | Execute a blocking shell command (dynamic regex override) |
| `spawn_terminal` | TERMINAL | ❌ False | Start a background process |
| `read_terminal` | TERMINAL | ✅ True | Read output from background process |
| `send_terminal_input` | TERMINAL | ❌ False | Send stdin to background process |
| `kill_terminal` | TERMINAL | ❌ False | Terminate a background process |
| `wait_for_terminal` | TERMINAL | ✅ True | Wait for output pattern match |
| `sleep` | UTILITY | ✅ True | Pause the agent for N seconds |
| `ask_user` | UTILITY | ✅ True | Request clarification from the user |

---

## 9. Configuration

```python
class AgentSettings(BaseSettings):
    # ... existing fields ...
    
    # Tool settings
    tool_output_max_length: int = Field(
        default=5000,
        description="Maximum characters in a tool result before truncation."
    )
    terminal_log_max_lines: int = Field(
        default=2000,
        description="Maximum lines kept in RAM per persistent terminal."
    )
    command_timeout_default: int = Field(
        default=30,
        description="Default timeout for run_command in seconds."
    )
    command_timeout_max: int = Field(
        default=120,
        description="Hard cap on run_command timeout."
    )
```

---

## 10. Testing Strategy

```python
import pytest

@pytest.mark.asyncio
async def test_tool_registry_discovery():
    registry = ToolRegistry()
    registry.register(ReadFileTool(mock_workspace))
    registry.register(GrepSearchTool(mock_workspace))
    
    file_tools = registry.get_by_category(ToolCategory.FILE)
    assert len(file_tools) == 1
    assert file_tools[0].name == "read_file"

def test_tool_json_schema_generation():
    tool = ReadFileTool(mock_workspace)
    schema = tool.get_json_schema()
    
    assert "properties" in schema
    assert "path" in schema["properties"]
    assert schema["properties"]["path"]["type"] == "string"

def test_output_formatter_truncation():
    formatter = ToolOutputFormatter(max_output_length=100)
    long_output = "x" * 500
    
    result = formatter.format("test_tool", long_output)
    assert len(result) < 500
    assert "TRUNCATED" in result
    assert result.startswith("[Tool: test_tool]")

@pytest.mark.asyncio
async def test_tool_executor_safety_check():
    """Dangerous commands require approval, safe commands skip it."""
    executor = ToolExecutor(...)
    
    assert executor._is_dangerous_command("rm -rf /") == True
    assert executor._is_dangerous_command("ls -la") == False
    assert executor._is_dangerous_command("echo hello") == False
    assert executor._is_dangerous_command("sudo apt install foo") == True

@pytest.mark.asyncio
async def test_wait_for_terminal_finds_pattern():
    manager = ProcessTerminalManager(...)
    await manager.spawn("test", "echo 'Server ready on port 8000'")
    
    result = await manager.wait_for_output("test", r"Server ready", timeout=5.0)
    assert result is not None
    assert "Server ready" in result

@pytest.mark.asyncio
async def test_terminal_cleanup_on_shutdown():
    manager = ProcessTerminalManager(...)
    await manager.spawn("bg_server", "python -m http.server 9999")
    
    assert len(manager.list_terminals()) == 1
    await manager.cleanup_all()
    assert len(manager.list_terminals()) == 0
```
