# Workspace & Sandbox Architecture

## Overview
An autonomous agent with write-access to the host machine is inherently dangerous. If the agent misinterprets a prompt (e.g., "Clean up the config files"), it might accidentally run `rm -rf ~/.config` instead of limiting its actions to the current project.

This architecture defines the **Workspace Boundary** (where the agent can operate), the **Sandbox Mode** (maximum isolation for experimental tasks), the **two-level directory structure** (global vs. local), and how all of this integrates with the ToolExecutor for centralized path enforcement.

---

## 1. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Workspace Root** | CWD when user runs `agent start` | Simple, predictable, user-controlled. Works for monorepos. |
| **Path Enforcement** | Centralized in ToolExecutor (not per-tool) | Single enforcement point. Impossible to bypass. Tools receive pre-validated paths. |
| **`.gitignore` Management** | Auto-add `.agent_cli/` on first workspace init | Prevents accidental commit of agent data. |

---

## 2. The Two-Level Directory Structure

The agent CLI uses two directories: a **global** directory for cross-project data and a **local** directory per workspace for project-specific data.

### Global Directory (`~/.agent_cli/`)

Stores data shared across all workspaces. Created on first CLI launch.

```
~/.agent_cli/
├── config.toml         # Global user preferences (02_config_management.md)
├── sessions.db         # SQLite: all sessions across workspaces (04_session_persistence.md)
└── logs/               # Structured JSON log files (03_observability.md)
    ├── session_abc123.jsonl
    └── session_def456.jsonl
```

### Local Workspace Directory (`<project>/.agent_cli/`)

Stores project-specific overrides and workspace-scoped data. Created on first use in a workspace.

```
<project_root>/
├── .agent_cli/          # ← Auto-added to .gitignore
│   ├── settings.toml    # Project-specific config overrides (02_config_management.md)
│   └── sandbox/         # Isolation folder (only in sandbox mode)
│       ├── ...          # Agent-generated files (write-only boundary)
├── .gitignore           # ← .agent_cli/ auto-appended here
├── src/
├── tests/
└── ...
```

### Why This Split?

| Data | Location | Reason |
|---|---|---|
| Sessions | Global (`sessions.db`) | Sessions are scoped by `workspace_path` field, not by file location. One DB = one query surface. |
| Logs | Global (`logs/`) | Logs span sessions across workspaces. Centralized for `jq` querying. |
| Config (user prefs) | Global (`config.toml`) | User preferences persist across all projects. |
| Config (project overrides) | Local (`settings.toml`) | Project-specific overrides live with the project. |
| Sandbox files | Local (`sandbox/`) | Experimental code belongs near the project. |

---

## 3. Workspace Initialization

```python
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def initialize_global_directory() -> Path:
    """
    Ensure the global ~/.agent_cli/ directory exists.
    Called on every CLI startup.
    """
    global_dir = Path.home() / ".agent_cli"
    global_dir.mkdir(exist_ok=True)
    (global_dir / "logs").mkdir(exist_ok=True)
    
    # Create default config if first run
    config_path = global_dir / "config.toml"
    if not config_path.exists():
        config_path.write_text(
            '# Agent CLI Configuration\n'
            '# See documentation for all available options.\n\n'
            'default_model = "claude-3-5-sonnet"\n'
            'default_effort_level = "MEDIUM"\n'
            'show_agent_thinking = true\n',
            encoding="utf-8"
        )
        logger.info(f"Created default config at {config_path}")
    
    return global_dir


def initialize_workspace(workspace_root: Path) -> Path:
    """
    Ensure the local .agent_cli/ directory exists in the workspace.
    Auto-adds .agent_cli/ to .gitignore.
    
    Args:
        workspace_root: CWD where the user ran `agent start`.
    
    Returns:
        Path to the local .agent_cli/ directory.
    """
    local_dir = workspace_root / ".agent_cli"
    local_dir.mkdir(exist_ok=True)
    
    # Auto-manage .gitignore
    _ensure_gitignore(workspace_root)
    
    return local_dir


def _ensure_gitignore(workspace_root: Path) -> None:
    """
    Add .agent_cli/ to .gitignore if not already present.
    Creates .gitignore if it doesn't exist.
    Only modifies .gitignore if a .git directory exists (i.e., it's a git repo).
    """
    git_dir = workspace_root / ".git"
    if not git_dir.exists():
        return  # Not a git repo — skip
    
    gitignore_path = workspace_root / ".gitignore"
    entry = ".agent_cli/"
    
    if gitignore_path.exists():
        content = gitignore_path.read_text(encoding="utf-8")
        if entry in content:
            return  # Already present
        
        # Append to existing .gitignore
        separator = "" if content.endswith("\n") else "\n"
        gitignore_path.write_text(
            content + separator + "\n# Agent CLI workspace data\n" + entry + "\n",
            encoding="utf-8"
        )
    else:
        # Create new .gitignore
        gitignore_path.write_text(
            "# Agent CLI workspace data\n" + entry + "\n",
            encoding="utf-8"
        )
    
    logger.info(f"Added '{entry}' to {gitignore_path}")
```

---

## 4. The `BaseWorkspaceManager` Interface

```python
from abc import ABC, abstractmethod
from pathlib import Path
from enum import Enum, auto


class FileOperation(Enum):
    """Classifies file operations for path enforcement."""
    READ = auto()       # read_file, grep_search, find_files
    WRITE = auto()      # write_file, edit_file
    EXECUTE = auto()    # run_command, spawn_terminal


class SecurityViolationError(Exception):
    """
    Raised when an agent attempts to escape workspace boundaries.
    Caught by the ToolExecutor and returned as an observation to the agent.
    """
    def __init__(self, path: str, boundary: str):
        self.path = path
        self.boundary = boundary
        super().__init__(
            f"Security Violation: Path '{path}' escapes the workspace boundary "
            f"'{boundary}'. All file operations must stay within the workspace."
        )


class BaseWorkspaceManager(ABC):
    """
    Abstract interface for workspace boundary enforcement.
    
    The ToolExecutor calls enforce_path() BEFORE dispatching
    to any filesystem tool. Tools never need to validate paths themselves.
    """
    
    @abstractmethod
    def enforce_path(self, target_path: str, operation: FileOperation) -> Path:
        """
        Validate a path against the active boundary.
        
        Args:
            target_path: The path the agent wants to access (may be relative).
            operation: READ, WRITE, or EXECUTE.
        
        Returns:
            The resolved absolute Path if safe.
        
        Raises:
            SecurityViolationError if the path escapes the boundary.
        """
        pass
    
    @abstractmethod
    def get_workspace_root(self) -> Path:
        """Return the absolute path of the workspace root."""
        pass
    
    @abstractmethod
    def get_local_dir(self) -> Path:
        """Return the path to the local .agent_cli/ directory."""
        pass
    
    @abstractmethod
    def is_sandbox_mode(self) -> bool:
        """Return True if sandbox mode is active."""
        pass
    
    @abstractmethod
    def get_workspace_summary(self) -> str:
        """
        Return a brief summary of the workspace for system prompts.
        Detects project type, language, framework from files present.
        """
        pass
```

---

## 5. Concrete Implementation: `StrictWorkspaceManager`

```python
class StrictWorkspaceManager(BaseWorkspaceManager):
    """
    Production workspace manager with strict path jailing.
    
    Normal mode: Read/Write/Execute all constrained to workspace root.
    Sandbox mode: Read from workspace root, Write/Execute only in .agent_cli/sandbox/.
    """
    
    def __init__(self, terminal_cwd: str, sandbox_mode: bool = False):
        self._root = Path(terminal_cwd).resolve()
        self._local_dir = self._root / ".agent_cli"
        self._sandbox_mode = sandbox_mode
        
        # Determine boundaries
        self._read_boundary = self._root
        self._write_boundary = (
            (self._local_dir / "sandbox").resolve()
            if sandbox_mode
            else self._root
        )
        
        # Ensure directories exist
        self._local_dir.mkdir(exist_ok=True)
        if sandbox_mode:
            self._write_boundary.mkdir(parents=True, exist_ok=True)
    
    def enforce_path(self, target_path: str, operation: FileOperation) -> Path:
        """
        Resolve and validate a path against the active boundaries.
        
        - Relative paths are resolved from workspace root
        - Symlinks are resolved to prevent escapes
        - '..' traversal is caught after resolution
        """
        # Resolve relative to workspace root
        if not Path(target_path).is_absolute():
            resolved = (self._root / target_path).resolve()
        else:
            resolved = Path(target_path).resolve()
        
        if operation == FileOperation.READ:
            # Read: must be within workspace root
            if not resolved.is_relative_to(self._read_boundary):
                raise SecurityViolationError(str(target_path), str(self._read_boundary))
        
        elif operation in (FileOperation.WRITE, FileOperation.EXECUTE):
            # Write/Execute: must be within write boundary
            if not resolved.is_relative_to(self._write_boundary):
                raise SecurityViolationError(str(target_path), str(self._write_boundary))
        
        return resolved
    
    def get_workspace_root(self) -> Path:
        return self._root
    
    def get_local_dir(self) -> Path:
        return self._local_dir
    
    def is_sandbox_mode(self) -> bool:
        return self._sandbox_mode
    
    def get_workspace_summary(self) -> str:
        """
        Auto-detect project type from workspace files.
        Injected into system prompts for context.
        """
        indicators = {
            "pyproject.toml": "Python project (pyproject.toml)",
            "setup.py": "Python project (setup.py)",
            "package.json": "Node.js project",
            "Cargo.toml": "Rust project",
            "go.mod": "Go project",
            "pom.xml": "Java/Maven project",
            "Makefile": "Uses Make build system",
            "Dockerfile": "Dockerized",
            "docker-compose.yml": "Docker Compose",
            ".github/workflows": "GitHub Actions CI/CD",
            "pytest.ini": "Uses pytest",
            "tsconfig.json": "TypeScript project",
        }
        
        detected = []
        for file, description in indicators.items():
            if (self._root / file).exists():
                detected.append(description)
        
        if not detected:
            return f"Workspace: {self._root.name} (no specific project type detected)"
        
        return f"Workspace: {self._root.name}\nProject type: {', '.join(detected)}"
```

---

## 6. Integration with ToolExecutor

The `ToolExecutor` is the **single enforcement point** for path validation. Individual tools never validate paths themselves — they receive pre-validated `Path` objects.

```python
class ToolExecutor:
    """
    From 03_tools_architecture.md — extended with workspace enforcement.
    """
    
    def __init__(
        self,
        registry: "ToolRegistry",
        workspace: BaseWorkspaceManager,
        interaction_handler: "BaseInteractionHandler",
        event_bus: "AbstractEventBus",
        logger: "StructuredLogger",
    ):
        self.registry = registry
        self.workspace = workspace
        self.interaction_handler = interaction_handler
        self.event_bus = event_bus
        self.logger = logger
    
    async def execute(self, action: "ParsedAction", task_id: str) -> str:
        tool = self.registry.get(action.tool_name)
        if not tool:
            return f"[Tool Error] Unknown tool: {action.tool_name}"
        
        # ── Step 1: Workspace Path Enforcement ──────────────
        try:
            validated_args = self._enforce_workspace_paths(
                tool, action.arguments
            )
        except SecurityViolationError as e:
            self.logger.log("WARNING", "tool_executor",
                f"Workspace violation: {e}", task_id=task_id)
            return f"[Security] {str(e)}"
        
        # ── Step 2: Pydantic Argument Validation ────────────
        # ... (from 03_tools_architecture.md)
        
        # ── Step 3: Safety Check (Human-in-the-Loop) ────────
        # ... (from 01_human_in_loop.md)
        
        # ── Step 4: Execute ─────────────────────────────────
        result = await tool.execute(**validated_args)
        
        # ── Step 5: Format Output ───────────────────────────
        return self.formatter.format(tool.name, result)
    
    def _enforce_workspace_paths(
        self, tool: "BaseTool", args: dict
    ) -> dict:
        """
        Scan tool arguments for file paths and validate them
        against the workspace boundary.
        
        Uses the tool's declared path_args to know which arguments
        contain file paths.
        """
        validated = dict(args)
        
        # Determine operation type from tool category
        operation = self._classify_operation(tool)
        
        # Validate all path arguments
        for arg_name in tool.path_args:
            if arg_name in validated and validated[arg_name]:
                validated[arg_name] = str(
                    self.workspace.enforce_path(
                        str(validated[arg_name]),
                        operation
                    )
                )
        
        return validated
    
    def _classify_operation(self, tool: "BaseTool") -> FileOperation:
        """Map a tool to its file operation type."""
        read_tools = {"read_file", "grep_search", "find_files"}
        write_tools = {"write_file", "edit_file"}
        execute_tools = {"run_command", "spawn_terminal", "send_terminal_input"}
        
        if tool.name in read_tools:
            return FileOperation.READ
        elif tool.name in write_tools:
            return FileOperation.WRITE
        elif tool.name in execute_tools:
            return FileOperation.EXECUTE
        else:
            return FileOperation.WRITE  # Default to most restrictive
```

### Updated `BaseTool` with Path Args

```python
class BaseTool(ABC):
    """Extended with path_args declaration for workspace enforcement."""
    
    name: str
    description: str
    is_safe: bool
    category: ToolCategory
    
    # Declare which arguments contain file paths
    # The ToolExecutor validates these against the workspace boundary
    path_args: List[str] = []  # e.g., ["path"], ["file_path", "destination"]
    
    # ... rest of BaseTool from 03_tools_architecture.md
```

### Example: ReadFileTool

```python
class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read the contents of a file"
    is_safe = True
    category = ToolCategory.FILE
    path_args = ["path"]  # ← ToolExecutor validates this
    
    @property
    def args_schema(self) -> Type[BaseModel]:
        return ReadFileArgs
    
    async def execute(self, path: str, start_line: int = None, end_line: int = None) -> str:
        """
        Path is already validated by ToolExecutor before reaching here.
        The tool can trust that 'path' is within the workspace boundary.
        """
        content = Path(path).read_text(encoding="utf-8")
        # ... line range filtering ...
        return content
```

---

## 7. Shell Command Jailing

Shell commands (`run_command`, `spawn_terminal`) are harder to jail because bash can `cd` anywhere. Our strategy uses **layers of defense**:

### Layer 1: CWD Enforcement
All shell commands execute with `cwd=workspace_root`:

```python
async def execute_command(self, command: str) -> str:
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(self.workspace.get_workspace_root()),  # Enforced CWD
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # ...
```

### Layer 2: Human-in-the-Loop (from `01_human_in_loop.md`)
Dangerous commands are caught by the regex patterns and require user approval.

### Layer 3: Path Argument Extraction (Best Effort)
For commands with obvious file paths, extract and validate them:

```python
def extract_paths_from_command(command: str) -> List[str]:
    """
    Best-effort extraction of file paths from shell commands.
    Not foolproof — this is a complement to human approval, not a replacement.
    """
    import shlex
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []
    
    paths = []
    for token in tokens:
        # Skip flags
        if token.startswith("-"):
            continue
        # Check if it looks like a path
        if "/" in token or "\\" in token or token.startswith("."):
            paths.append(token)
    
    return paths
```

### Layer 4: Sandbox Mode Docker (Optional, Advanced)
In sandbox mode, `run_command` can optionally execute inside a Docker container:

```python
async def execute_sandboxed_command(self, command: str) -> str:
    """
    Run a command inside an ephemeral Docker container.
    Only the sandbox directory is mounted.
    """
    sandbox_path = self.workspace.get_local_dir() / "sandbox"
    
    docker_cmd = (
        f"docker run --rm "
        f"-v {sandbox_path}:/workspace "
        f"-w /workspace "
        f"--network none "  # No network access
        f"--memory 512m "   # Memory limit
        f"python:3.12-slim "
        f"bash -c '{command}'"
    )
    
    process = await asyncio.create_subprocess_shell(
        docker_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return stdout.decode() + stderr.decode()
```

---

## 8. Sandbox Mode

### Activation

```bash
# Start in sandbox mode via CLI flag
agent start --sandbox

# Or toggle within a session
/sandbox on
/sandbox off
```

### Behavior Comparison

| Aspect | Normal Mode | Sandbox Mode |
|---|---|---|
| Read boundary | Workspace root | Workspace root (same) |
| Write boundary | Workspace root | `.agent_cli/sandbox/` only |
| Execute CWD | Workspace root | `.agent_cli/sandbox/` |
| Docker isolation | No | Optional (via config) |
| Use case | Normal development | Experimental code, untrusted tasks |

### Sandbox Command

```python
class SandboxCommand:
    """Handles /sandbox TUI command."""
    
    def execute(self, args: List[str]) -> str:
        if not args:
            status = "ON" if self.workspace.is_sandbox_mode() else "OFF"
            return f"Sandbox mode: {status}"
        
        if args[0] == "on":
            self.workspace = StrictWorkspaceManager(
                str(self.workspace.get_workspace_root()),
                sandbox_mode=True
            )
            return "🔒 Sandbox mode enabled. Agent can only write to .agent_cli/sandbox/"
        
        elif args[0] == "off":
            self.workspace = StrictWorkspaceManager(
                str(self.workspace.get_workspace_root()),
                sandbox_mode=False
            )
            return "🔓 Sandbox mode disabled. Agent can write to the full workspace."
        
        elif args[0] == "ls":
            sandbox_dir = self.workspace.get_local_dir() / "sandbox"
            if not sandbox_dir.exists():
                return "Sandbox is empty."
            files = list(sandbox_dir.rglob("*"))
            return "\n".join(str(f.relative_to(sandbox_dir)) for f in files[:50])
```

---

## 9. Workspace Context for System Prompts

The `WorkspaceManager` auto-detects the project type and provides context for agent system prompts:

```python
# In PromptBuilder.build() (from 01_reasoning_loop.md):
workspace_context = workspace_manager.get_workspace_summary()

# Example output:
# Workspace: agent_cli
# Project type: Python project (pyproject.toml), Uses pytest, Dockerized
```

This helps agents make better decisions (e.g., knowing to use `pytest` instead of `unittest`, or `npm` instead of `pip`).

---

## 10. Cross-Reference Map

| Spec | What It References From This Doc |
|---|---|
| `03_tools_architecture.md` | `BaseTool.path_args`, ToolExecutor calls `enforce_path()` |
| `02_config_management.md` | Local `settings.toml` path, global `config.toml` path |
| `04_session_persistence.md` | Global `sessions.db` path |
| `03_observability.md` | Global `logs/` directory path |
| `01_reasoning_loop.md` | `workspace_context` in system prompts via `get_workspace_summary()` |
| `01_human_in_loop.md` | Shell command safety (complementary to path jailing) |

---

## 11. Testing Strategy

```python
import pytest
from pathlib import Path

def test_read_within_workspace(tmp_path):
    ws = StrictWorkspaceManager(str(tmp_path))
    test_file = tmp_path / "src" / "main.py"
    test_file.parent.mkdir()
    test_file.touch()
    
    resolved = ws.enforce_path("src/main.py", FileOperation.READ)
    assert resolved == test_file.resolve()

def test_read_outside_workspace_blocked(tmp_path):
    ws = StrictWorkspaceManager(str(tmp_path))
    
    with pytest.raises(SecurityViolationError):
        ws.enforce_path("../../etc/passwd", FileOperation.READ)

def test_write_outside_workspace_blocked(tmp_path):
    ws = StrictWorkspaceManager(str(tmp_path))
    
    with pytest.raises(SecurityViolationError):
        ws.enforce_path("/tmp/evil.sh", FileOperation.WRITE)

def test_dotdot_traversal_blocked(tmp_path):
    ws = StrictWorkspaceManager(str(tmp_path))
    
    with pytest.raises(SecurityViolationError):
        ws.enforce_path("src/../../../etc/passwd", FileOperation.READ)

def test_symlink_escape_blocked(tmp_path):
    """Symlinks pointing outside workspace must be caught."""
    ws = StrictWorkspaceManager(str(tmp_path))
    
    link = tmp_path / "evil_link"
    link.symlink_to("/etc/passwd")
    
    with pytest.raises(SecurityViolationError):
        ws.enforce_path("evil_link", FileOperation.READ)

def test_sandbox_mode_write_restricted(tmp_path):
    ws = StrictWorkspaceManager(str(tmp_path), sandbox_mode=True)
    
    # Write to sandbox → allowed
    sandbox_file = tmp_path / ".agent_cli" / "sandbox" / "test.py"
    resolved = ws.enforce_path(".agent_cli/sandbox/test.py", FileOperation.WRITE)
    assert resolved.is_relative_to(tmp_path / ".agent_cli" / "sandbox")
    
    # Write to workspace root → blocked
    with pytest.raises(SecurityViolationError):
        ws.enforce_path("src/main.py", FileOperation.WRITE)

def test_sandbox_mode_read_allowed(tmp_path):
    """Sandbox mode should still allow reading from workspace root."""
    ws = StrictWorkspaceManager(str(tmp_path), sandbox_mode=True)
    test_file = tmp_path / "README.md"
    test_file.touch()
    
    resolved = ws.enforce_path("README.md", FileOperation.READ)
    assert resolved == test_file.resolve()

def test_gitignore_auto_created(tmp_path):
    (tmp_path / ".git").mkdir()  # Make it a git repo
    initialize_workspace(tmp_path)
    
    gitignore = tmp_path / ".gitignore"
    assert gitignore.exists()
    assert ".agent_cli/" in gitignore.read_text()

def test_gitignore_not_duplicated(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text(".agent_cli/\nnode_modules/\n")
    
    initialize_workspace(tmp_path)
    
    content = (tmp_path / ".gitignore").read_text()
    assert content.count(".agent_cli/") == 1  # Not duplicated

def test_gitignore_skipped_for_non_git(tmp_path):
    """Should not create .gitignore if not a git repo."""
    initialize_workspace(tmp_path)
    
    assert not (tmp_path / ".gitignore").exists()

def test_workspace_summary_detection(tmp_path):
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "Dockerfile").touch()
    
    ws = StrictWorkspaceManager(str(tmp_path))
    summary = ws.get_workspace_summary()
    
    assert "Python project" in summary
    assert "Dockerized" in summary

def test_absolute_path_within_workspace(tmp_path):
    ws = StrictWorkspaceManager(str(tmp_path))
    test_file = tmp_path / "src" / "app.py"
    test_file.parent.mkdir()
    test_file.touch()
    
    resolved = ws.enforce_path(str(test_file), FileOperation.READ)
    assert resolved == test_file.resolve()

def test_absolute_path_outside_workspace_blocked(tmp_path):
    ws = StrictWorkspaceManager(str(tmp_path))
    
    with pytest.raises(SecurityViolationError):
        ws.enforce_path("/usr/bin/python", FileOperation.READ)
```
