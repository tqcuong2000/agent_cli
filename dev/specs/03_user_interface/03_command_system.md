# Command System & Keyboard Shortcuts Architecture

## Overview
While the Orchestrator handles natural language requests, users frequently need to perform rigid administrative actions — switching models, changing effort levels, managing sessions, toggling sandbox mode. Instead of forcing the LLM to interpret meta-requests, the architecture introduces the **Command System**: a `/`-prefixed syntax that executes native Python functions instantly, bypassing the Agent entirely.

This spec also defines **keyboard shortcuts** as alternative triggers for common commands.

---

## 1. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Registration** | Decorator-based (`@command`) | Self-documenting, scannable, easy to add new commands |
| **Parsing** | Intercept `/` prefix before Event Bus | Commands never reach the Orchestrator or Agent |
| **Autocomplete** | Floating popup widget with fuzzy matching | IDE-like discoverability |
| **Keyboard Shortcuts** | Global Textual key bindings | Power-user speed. Shortcuts defined alongside commands. |
| **Error Handling** | Every command returns `CommandResult(success, message)` | Consistent feedback to user |

---

## 2. Command Pipeline

```
User types in TUI input bar → hits Enter
         │
         ▼
┌──────────────────────────┐
│ Starts with '/' ?         │
│                           │
│  NO → UserRequestEvent    │──→ Event Bus → Orchestrator → Agent
│       (natural language)  │
│                           │
│  YES → CommandParser      │──→ Registry lookup → Execute handler
│        (bypass Agent)     │    → CommandResult → TUI feedback
└──────────────────────────┘
```

---

## 3. The `@command` Decorator & Registry

```python
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Any
from functools import wraps
import shlex


@dataclass
class CommandResult:
    """Result returned by every command handler."""
    success: bool
    message: str


@dataclass
class CommandDef:
    """Definition of a registered command."""
    name: str
    description: str
    usage: str                            # e.g., "/model <provider/name>"
    handler: Callable                     # The async function to execute
    subcommands: Dict[str, str] = field(default_factory=dict)  # {"show": "Show current value"}
    shortcut: Optional[str] = None        # Keyboard shortcut (e.g., "ctrl+e")
    category: str = "General"             # For /help grouping


# ── Global command registry ──────────────────────────────────
_COMMAND_REGISTRY: Dict[str, CommandDef] = {}


def command(
    name: str,
    description: str,
    usage: str = "",
    subcommands: Dict[str, str] = None,
    shortcut: str = None,
    category: str = "General"
):
    """
    Decorator to register a TUI command.
    
    Usage:
        @command(name="model", description="Switch LLM model", usage="/model <name>")
        async def cmd_model(args: List[str], ctx: CommandContext) -> CommandResult:
            ...
    """
    def decorator(func):
        _COMMAND_REGISTRY[name] = CommandDef(
            name=name,
            description=description,
            usage=usage or f"/{name}",
            handler=func,
            subcommands=subcommands or {},
            shortcut=shortcut,
            category=category,
        )
        
        @wraps(func)
        async def wrapper(*a, **kw):
            return await func(*a, **kw)
        return wrapper
    return decorator


@dataclass
class CommandContext:
    """
    Dependencies injected into every command handler.
    Provides access to system components without global imports.
    """
    settings: "AgentSettings"
    orchestrator: "Orchestrator"
    event_bus: "AbstractEventBus"
    state_manager: "AbstractStateManager"
    session_manager: "AbstractSessionManager"
    workspace: "BaseWorkspaceManager"
    memory_manager: "BaseMemoryManager"
    provider_manager: "ProviderManager"
    change_tracker: "FileChangeTracker"
```

---

## 4. Command Parser

```python
class CommandParser:
    """
    Parses and executes '/' commands.
    Sits between the TUI input and the Event Bus.
    """
    
    def __init__(self, ctx: CommandContext):
        self.ctx = ctx
        self.registry = _COMMAND_REGISTRY
    
    def is_command(self, raw_input: str) -> bool:
        """Check if input starts with '/'."""
        return raw_input.strip().startswith("/")
    
    async def execute(self, raw_input: str) -> CommandResult:
        """
        Parse and execute a command.
        Returns CommandResult with success/failure and message.
        """
        raw_input = raw_input.strip()
        
        # Remove leading '/'
        without_slash = raw_input[1:]
        
        # Split into command name and arguments
        try:
            parts = shlex.split(without_slash)
        except ValueError:
            parts = without_slash.split()
        
        if not parts:
            return CommandResult(False, "Empty command. Type /help for a list.")
        
        cmd_name = parts[0].lower()
        args = parts[1:]
        
        # Lookup in registry
        cmd_def = self.registry.get(cmd_name)
        if not cmd_def:
            # Try fuzzy match for suggestions
            suggestions = self.get_suggestions(cmd_name)
            if suggestions:
                names = ", ".join(f"/{s['name']}" for s in suggestions[:3])
                return CommandResult(False, f"Unknown command: /{cmd_name}. Did you mean: {names}?")
            return CommandResult(False, f"Unknown command: /{cmd_name}. Type /help for a list.")
        
        # Execute the handler
        try:
            return await cmd_def.handler(args, self.ctx)
        except Exception as e:
            return CommandResult(False, f"Command error: {e}")
    
    def get_suggestions(self, partial: str) -> List[Dict[str, str]]:
        """
        Fuzzy autocomplete suggestions for the command palette.
        Returns list of {name, description, usage, shortcut} dicts.
        """
        partial = partial.lower()
        matches = []
        
        for name, cmd in self.registry.items():
            # Match by prefix or substring
            if name.startswith(partial) or partial in name:
                matches.append({
                    "name": cmd.name,
                    "description": cmd.description,
                    "usage": cmd.usage,
                    "shortcut": cmd.shortcut or "",
                })
        
        # Sort: prefix matches first, then substring matches
        matches.sort(key=lambda m: (not m["name"].startswith(partial), m["name"]))
        return matches
    
    def get_all_commands(self) -> List[CommandDef]:
        """Return all registered commands (for /help)."""
        return sorted(self.registry.values(), key=lambda c: (c.category, c.name))
```

---

## 5. Complete Command Inventory

### Navigation & Mode

```python
@command(
    name="mode",
    description="Set execution mode for the next request",
    usage="/mode <plan|fast>",
    subcommands={"plan": "Force plan mode", "fast": "Force fast-path mode"},
    shortcut="ctrl+m",
    category="Navigation"
)
async def cmd_mode(args: List[str], ctx: CommandContext) -> CommandResult:
    if not args or args[0] not in ("plan", "fast"):
        return CommandResult(False, "Usage: /mode <plan|fast>")
    
    mode = args[0]
    ctx.settings.force_mode = mode
    return CommandResult(True, f"Next request will use {mode.upper()} mode.")


@command(
    name="agent",
    description="Force a specific agent for the next request",
    usage="/agent <name>",
    category="Navigation"
)
async def cmd_agent(args: List[str], ctx: CommandContext) -> CommandResult:
    if not args:
        # List available agents
        agents = ctx.orchestrator.agent_registry.get_catalogue()
        names = ", ".join(a["name"] for a in agents)
        return CommandResult(True, f"Available agents: {names}")
    
    agent_name = args[0]
    if not ctx.orchestrator.agent_registry.has(agent_name):
        return CommandResult(False, f"Unknown agent: '{agent_name}'. Use /agent to see available agents.")
    
    ctx.orchestrator.force_agent = agent_name
    return CommandResult(True, f"Next request will be handled by '{agent_name}' agent.")
```

### Model & Provider

```python
@command(
    name="model",
    description="Switch LLM model mid-session",
    usage="/model <name>",
    shortcut="ctrl+shift+m",
    category="Model"
)
async def cmd_model(args: List[str], ctx: CommandContext) -> CommandResult:
    if not args:
        current = ctx.settings.default_model
        return CommandResult(True, f"Current model: {current}")
    
    model_name = args[0]
    try:
        # Test that we can create a provider for this model
        ctx.provider_manager.get_provider(model_name)
        ctx.settings.default_model = model_name
        return CommandResult(True, f"Switched model to: {model_name}")
    except ValueError as e:
        return CommandResult(False, str(e))


@command(
    name="effort",
    description="Set default effort level",
    usage="/effort <LOW|MEDIUM|HIGH>",
    shortcut="ctrl+e",
    category="Model"
)
async def cmd_effort(args: List[str], ctx: CommandContext) -> CommandResult:
    if not args:
        return CommandResult(True, f"Current effort: {ctx.settings.default_effort_level}")
    
    level = args[0].upper()
    if level not in ("LOW", "MEDIUM", "HIGH"):
        return CommandResult(False, "Usage: /effort <LOW|MEDIUM|HIGH>")
    
    ctx.settings.default_effort_level = level
    return CommandResult(True, f"Effort level set to: {level}")
```

### Configuration

```python
@command(
    name="config",
    description="View or modify settings",
    usage="/config <show|get|set|reset|providers>",
    subcommands={
        "show": "Show all settings",
        "get": "Get a specific setting: /config get <key>",
        "set": "Set a setting: /config set <key>=<value>",
        "reset": "Reset to defaults: /config reset [key]",
        "providers": "List configured LLM providers",
    },
    category="Configuration"
)
async def cmd_config(args: List[str], ctx: CommandContext) -> CommandResult:
    # Delegates to ConfigCommand (see 02_config_management.md Section 7)
    from commands.config_command import ConfigCommand
    handler = ConfigCommand(ctx.settings, ctx.settings._global_config_path)
    message = handler.execute(args)
    return CommandResult(True, message)
```

### Session Management

```python
@command(
    name="session",
    description="Manage sessions",
    usage="/session <list|restore|save|delete|info>",
    subcommands={
        "list": "List saved sessions for this workspace",
        "restore": "Restore a session: /session restore <id>",
        "save": "Force-save current session",
        "delete": "Delete a session: /session delete <id>",
        "info": "Show current session info",
    },
    shortcut="ctrl+s",
    category="Session"
)
async def cmd_session(args: List[str], ctx: CommandContext) -> CommandResult:
    # Delegates to SessionCommand (see 04_session_persistence.md)
    if not args:
        return CommandResult(False, "Usage: /session <list|restore|save|delete|info>")
    
    subcmd = args[0]
    
    if subcmd == "list":
        sessions = await ctx.session_manager.list_sessions(
            workspace=str(ctx.workspace.get_workspace_root())
        )
        if not sessions:
            return CommandResult(True, "No saved sessions for this workspace.")
        lines = ["Saved sessions:"]
        for s in sessions:
            lines.append(f"  {s.session_id}  {s.created_at}  msgs:{s.message_count}")
        return CommandResult(True, "\n".join(lines))
    
    elif subcmd == "restore" and len(args) >= 2:
        session_id = args[1]
        try:
            session = await ctx.session_manager.load_session(session_id)
            # Restore messages into memory
            ctx.memory_manager.reset_working()
            for msg in session.messages:
                ctx.memory_manager.add_working_event(msg)
            return CommandResult(True, f"Restored session {session_id} ({len(session.messages)} messages)")
        except Exception as e:
            return CommandResult(False, f"Failed to restore: {e}")
    
    elif subcmd == "save":
        await ctx.session_manager.save_session(ctx.orchestrator.current_session)
        return CommandResult(True, "Session saved.")
    
    elif subcmd == "info":
        s = ctx.orchestrator.current_session
        return CommandResult(True, (
            f"Session: {s.session_id}\n"
            f"Messages: {len(s.messages)}\n"
            f"Cost: ${s.metrics.total_cost_usd:.4f}\n"
            f"Tokens: {s.metrics.total_input_tokens + s.metrics.total_output_tokens}"
        ))
    
    return CommandResult(False, f"Unknown subcommand: {subcmd}")
```

### Workspace & Sandbox

```python
@command(
    name="sandbox",
    description="Toggle sandbox mode",
    usage="/sandbox <on|off|ls>",
    subcommands={"on": "Enable sandbox", "off": "Disable sandbox", "ls": "List sandbox files"},
    category="Workspace"
)
async def cmd_sandbox(args: List[str], ctx: CommandContext) -> CommandResult:
    # Delegates to SandboxCommand (see 03_workspace_sandbox.md Section 8)
    if not args:
        status = "ON" if ctx.workspace.is_sandbox_mode() else "OFF"
        return CommandResult(True, f"Sandbox mode: {status}")
    # ...implementation from workspace spec...
```

### Memory & Context

```python
@command(
    name="clear",
    description="Clear working memory (start fresh context)",
    usage="/clear",
    shortcut="ctrl+l",
    category="Memory"
)
async def cmd_clear(args: List[str], ctx: CommandContext) -> CommandResult:
    ctx.memory_manager.reset_working()
    return CommandResult(True, "Working memory cleared. Starting fresh context.")


@command(
    name="context",
    description="Show context window usage",
    usage="/context",
    category="Memory"
)
async def cmd_context(args: List[str], ctx: CommandContext) -> CommandResult:
    tokens = ctx.memory_manager.get_token_count()
    budget = ctx.memory_manager.budget
    pct = (tokens / budget.usable_context) * 100
    return CommandResult(True, (
        f"Context usage: {tokens:,} / {budget.usable_context:,} tokens ({pct:.1f}%)\n"
        f"System prompt: {budget.system_prompt_budget:,}\n"
        f"Summary block: {budget.summary_budget:,}\n"
        f"Recent turns: {budget.recent_turns_budget:,}\n"
        f"Response reserve: {budget.response_reserve:,}"
    ))


@command(
    name="cost",
    description="Show session cost breakdown",
    usage="/cost",
    category="Memory"
)
async def cmd_cost(args: List[str], ctx: CommandContext) -> CommandResult:
    m = ctx.orchestrator.current_session.metrics
    return CommandResult(True, (
        f"Session cost: ${m.total_cost_usd:.4f}\n"
        f"LLM calls: {m.llm_calls}\n"
        f"Input tokens: {m.total_input_tokens:,}\n"
        f"Output tokens: {m.total_output_tokens:,}"
    ))
```

### UI & Display

```python
@command(
    name="theme",
    description="Switch TUI theme",
    usage="/theme <dark|light|neon>",
    category="UI"
)
async def cmd_theme(args: List[str], ctx: CommandContext) -> CommandResult:
    if not args or args[0] not in ("dark", "light", "neon"):
        return CommandResult(False, "Usage: /theme <dark|light|neon>")
    # Apply Textual CSS theme
    return CommandResult(True, f"Theme switched to: {args[0]}")


@command(
    name="changes",
    description="Show/manage changed files",
    usage="/changes",
    category="UI"
)
async def cmd_changes(args: List[str], ctx: CommandContext) -> CommandResult:
    changes = ctx.change_tracker.get_changes()
    if not changes:
        return CommandResult(True, "No files changed in current request.")
    
    icons = {"CREATED": "✚", "MODIFIED": "✎", "DELETED": "✖"}
    lines = [f"Changed files ({len(changes)}):"]
    for c in changes:
        icon = icons.get(c.change_type.name, "?")
        lines.append(f"  {icon} {c.path}")
    return CommandResult(True, "\n".join(lines))
```

### System

```python
@command(
    name="help",
    description="Show all available commands",
    usage="/help [command]",
    shortcut="ctrl+?",
    category="System"
)
async def cmd_help(args: List[str], ctx: CommandContext) -> CommandResult:
    if args:
        # Show help for a specific command
        cmd_def = _COMMAND_REGISTRY.get(args[0])
        if not cmd_def:
            return CommandResult(False, f"Unknown command: /{args[0]}")
        
        lines = [f"/{cmd_def.name} — {cmd_def.description}"]
        lines.append(f"Usage: {cmd_def.usage}")
        if cmd_def.shortcut:
            lines.append(f"Shortcut: {cmd_def.shortcut}")
        if cmd_def.subcommands:
            lines.append("Subcommands:")
            for sub, desc in cmd_def.subcommands.items():
                lines.append(f"  {sub}: {desc}")
        return CommandResult(True, "\n".join(lines))
    
    # Show all commands grouped by category
    commands = sorted(_COMMAND_REGISTRY.values(), key=lambda c: (c.category, c.name))
    lines = ["Available commands:\n"]
    current_category = ""
    
    for cmd in commands:
        if cmd.category != current_category:
            current_category = cmd.category
            lines.append(f"  {current_category}:")
        
        shortcut = f"  ({cmd.shortcut})" if cmd.shortcut else ""
        lines.append(f"    /{cmd.name:<12} {cmd.description}{shortcut}")
    
    lines.append("\nType /help <command> for details.")
    return CommandResult(True, "\n".join(lines))


@command(
    name="exit",
    description="Exit the CLI",
    usage="/exit",
    shortcut="ctrl+q",
    category="System"
)
async def cmd_exit(args: List[str], ctx: CommandContext) -> CommandResult:
    # Save session before exiting
    await ctx.session_manager.save_session(ctx.orchestrator.current_session)
    await ctx.event_bus.emit(SystemShutdownEvent(source="command_system"))
    return CommandResult(True, "Shutting down...")
```

---

## 6. Keyboard Shortcuts

### Shortcut Map

| Shortcut | Command | Action |
|---|---|---|
| `ctrl+p` | — | Open command palette (same as typing `/`) |
| `ctrl+e` | `/effort` | Cycle effort level (LOW → MEDIUM → HIGH) |
| `ctrl+m` | `/mode` | Toggle mode (fast ↔ plan) |
| `ctrl+l` | `/clear` | Clear working memory |
| `ctrl+s` | `/session save` | Force-save session |
| `ctrl+q` | `/exit` | Exit the CLI |
| `ctrl+shift+m` | `/model` | Open model picker |
| `ctrl+?` | `/help` | Show help |
| `tab` | — | Autocomplete command in input bar |
| `Esc` | — | Cancel current input / dismiss popup |

### TUI Key Binding Implementation

```python
from textual.app import App
from textual.binding import Binding


class AgentCLIApp(App):
    
    BINDINGS = [
        Binding("ctrl+p", "open_command_palette", "Commands", show=True),
        Binding("ctrl+e", "cycle_effort", "Effort", show=True),
        Binding("ctrl+m", "toggle_mode", "Mode", show=False),
        Binding("ctrl+l", "clear_context", "Clear", show=False),
        Binding("ctrl+s", "save_session", "Save", show=False),
        Binding("ctrl+q", "quit_app", "Quit", show=False),
    ]
    
    async def action_open_command_palette(self) -> None:
        """Show the command palette popup."""
        self.query_one(CommandPalette).show()
    
    async def action_cycle_effort(self) -> None:
        """Cycle through effort levels."""
        levels = ["LOW", "MEDIUM", "HIGH"]
        current = self.settings.default_effort_level
        idx = levels.index(current)
        next_level = levels[(idx + 1) % 3]
        self.settings.default_effort_level = next_level
        self.notify(f"Effort: {next_level}")
    
    async def action_toggle_mode(self) -> None:
        """Toggle between fast and plan mode."""
        current = getattr(self.settings, 'force_mode', 'fast')
        new_mode = 'plan' if current == 'fast' else 'fast'
        self.settings.force_mode = new_mode
        self.notify(f"Mode: {new_mode.upper()}")
    
    async def action_clear_context(self) -> None:
        result = await cmd_clear([], self.command_ctx)
        self.notify(result.message)
    
    async def action_save_session(self) -> None:
        result = await cmd_session(["save"], self.command_ctx)
        self.notify(result.message)
    
    async def action_quit_app(self) -> None:
        await cmd_exit([], self.command_ctx)
```

### Footer Display

The keyboard shortcuts are displayed in the TUI footer bar:

```
Plan ● gemini-3.1-pro ● xHigh    tab mode │ ctrl+p commands │ ctrl+e efforts
```

---

## 7. Command Palette (Autocomplete Widget)

### Trigger
When the user types `/` in the input bar, a floating `CommandPalette` widget appears above the input:

```
┌────────────────────────────────────────┐
│  /mo                                   │
├────────────────────────────────────────┤
│  /model     Switch LLM model    ctrl+m │
│  /mode      Set execution mode         │
└────────────────────────────────────────┘
```

### Behavior

1. **Trigger:** User types `/` → palette opens
2. **Filter:** As user continues typing (`/mo...`), the list narrows
3. **Navigate:** `↑/↓` arrows to highlight
4. **Select:** `Tab` or `Enter` to autocomplete the command name
5. **Arguments:** After command name + space, palette shows subcommands/usage
6. **Dismiss:** `Esc` or backspace past `/`

### Implementation

```python
from textual.widget import Widget
from textual.reactive import reactive


class CommandPalette(Widget):
    """Floating autocomplete widget for / commands."""
    
    DEFAULT_CSS = """
    CommandPalette {
        layer: overlay;
        dock: bottom;
        width: 50;
        max-height: 10;
        border: solid $primary;
        background: $surface;
        padding: 0 1;
        display: none;
    }
    
    CommandPalette.visible {
        display: block;
    }
    
    CommandPalette .selected {
        background: $accent;
        color: $text;
    }
    """
    
    filter_text: reactive[str] = reactive("")
    selected_index: reactive[int] = reactive(0)
    
    def __init__(self, parser: CommandParser):
        super().__init__()
        self.parser = parser
        self._suggestions: List[Dict] = []
    
    def on_input_changed(self, value: str) -> None:
        """Called when the input bar text changes."""
        if value.startswith("/"):
            partial = value[1:]  # Remove /
            self._suggestions = self.parser.get_suggestions(partial)
            self.selected_index = 0
            self.add_class("visible")
        else:
            self.remove_class("visible")
    
    def on_key_down(self) -> None:
        self.selected_index = min(
            self.selected_index + 1, len(self._suggestions) - 1
        )
    
    def on_key_up(self) -> None:
        self.selected_index = max(self.selected_index - 1, 0)
    
    def on_key_tab(self) -> str:
        """Return the selected command name for autocomplete."""
        if self._suggestions:
            return f"/{self._suggestions[self.selected_index]['name']} "
        return ""
```

---

## 8. Cross-Reference Map

| Spec | Commands Defined There |
|---|---|
| `02_config_management.md` | `/config show\|get\|set\|reset\|providers` |
| `04_session_persistence.md` | `/session list\|restore\|save\|delete\|info` |
| `03_workspace_sandbox.md` | `/sandbox on\|off\|ls` |
| `04_changed_files.md` | `/changes` |
| `01_memory_management.md` | `/clear`, `/context` |
| `01_ai_providers.md` | `/cost` |
| `03_task_planning.md` | `/mode plan` |
| `04_multi_agent_definitions.md` | `/agent <name>` |

---

## 9. Testing Strategy

```python
import pytest

@pytest.mark.asyncio
async def test_command_intercept():
    parser = CommandParser(ctx=mock_ctx)
    assert parser.is_command("/help") == True
    assert parser.is_command("Fix the bug") == False
    assert parser.is_command("  /model gpt-4o") == True

@pytest.mark.asyncio
async def test_unknown_command():
    parser = CommandParser(ctx=mock_ctx)
    result = await parser.execute("/nonexistent")
    assert result.success == False
    assert "Unknown command" in result.message

@pytest.mark.asyncio
async def test_fuzzy_suggestion():
    parser = CommandParser(ctx=mock_ctx)
    result = await parser.execute("/modle")  # Typo
    assert "Did you mean" in result.message
    assert "/model" in result.message

@pytest.mark.asyncio
async def test_effort_command():
    result = await cmd_effort(["HIGH"], mock_ctx)
    assert result.success == True
    assert mock_ctx.settings.default_effort_level == "HIGH"

@pytest.mark.asyncio
async def test_effort_invalid():
    result = await cmd_effort(["EXTREME"], mock_ctx)
    assert result.success == False

@pytest.mark.asyncio
async def test_model_command():
    result = await cmd_model(["gpt-4o"], mock_ctx)
    assert result.success == True
    assert mock_ctx.settings.default_model == "gpt-4o"

@pytest.mark.asyncio
async def test_clear_resets_memory():
    result = await cmd_clear([], mock_ctx)
    assert result.success == True
    assert mock_ctx.memory_manager.reset_working_called

@pytest.mark.asyncio
async def test_help_lists_all_commands():
    result = await cmd_help([], mock_ctx)
    assert result.success == True
    assert "/model" in result.message
    assert "/help" in result.message

@pytest.mark.asyncio
async def test_help_specific_command():
    result = await cmd_help(["config"], mock_ctx)
    assert result.success == True
    assert "show" in result.message  # Shows subcommands

def test_suggestions_prefix_match():
    parser = CommandParser(ctx=mock_ctx)
    suggestions = parser.get_suggestions("mo")
    names = [s["name"] for s in suggestions]
    assert "model" in names
    assert "mode" in names

def test_suggestions_show_shortcuts():
    parser = CommandParser(ctx=mock_ctx)
    suggestions = parser.get_suggestions("effort")
    assert suggestions[0]["shortcut"] == "ctrl+e"

@pytest.mark.asyncio
async def test_exit_saves_session():
    result = await cmd_exit([], mock_ctx)
    assert mock_ctx.session_manager.save_called == True
```
