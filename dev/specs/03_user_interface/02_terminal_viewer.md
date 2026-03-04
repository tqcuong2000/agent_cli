# TUI Feature: Terminal Viewer Architecture

## Overview
Because the architecture supports **Persistent Terminal Management** (designed in `06_tools_architecture.md`), the Agent can spawn multiple background servers, testing suites, or REPLs. 

A high-quality TUI must expose these hidden processes to the user. The **Terminal Viewer** is a dedicated UI component that allows the user to inspect, monitor, and directly interact with any background terminal spawned by the agent.

## 1. The UX Design

The user should not have to dig through complex logs to see what the agent's background Node server is doing.

### A. The Layout Component
The Textual TUI will feature a collapsible pane or a dedicated tab called the **Terminal Viewer**.
*   **Sidebar List:** Shows all currently active virtual terminals (e.g., `Terminal 1: npm start`, `Terminal 2: python app.py`).
*   **Main Display:** A dynamic text log displaying the `stdout`/`stderr` of the currently selected terminal in real-time.
*   **Input Bar:** A command line input at the bottom of the Terminal Viewer allowing the *user* (not just the agent) to send data directly into the active terminal's `stdin`.

## 2. The Architectural Flow (Decoupled from Agent)

Crucially, the Terminal Viewer UI does *not* talk to the Agent. It talks directly to the `TerminalManager` via the **Event Bus**. This ensures the TUI remains responsive even if the Agent is currently waiting for an LLM response.

### Phase 1: Registration (How the TUI finds the terminals)
1.  **Agent Action:** The LLM outputs `<tool>spawn_terminal</tool><args>{"name": "api_backend"}</args>`.
2.  **Execution:** The `TerminalManager` starts the process.
3.  **Broadcasting:** The `TerminalManager` publishes a `TerminalSpawnedEvent(name="api_backend", pid=1024)` to the Event Bus.
4.  **TUI Update:** The TUI catches this event and adds a new clickable row to the Terminal Viewer sidebar.

### Phase 2: Live Monitoring (How the log updates)
The TUI should not constantly query the `TerminalManager` for logs (which causes CPU lag).
1.  **Streaming:** When the background process prints a line to its stdout, the `TerminalManager` intercepts it, adds it to its bounded deque buffer (to prevent memory bloat), and *also* publishes a `TerminalLogEvent(name="api_backend", line="Server listening on port 8000")`.
2.  **TUI Update:** If the TUI currently has the "api_backend" tab focused, it instantly appends that line to its `RichLog` or `TextArea` widget, ensuring a live, tailing log experience.

### Phase 3: User Interaction (Injecting Commands)
The user can intervene and type commands directly into the terminal, bypassing the Agent.
1.  **User Action:** The user focuses the TUI Terminal Viewer, types `npm run build`, and hits Enter.
2.  **Broadcasting:** The TUI parses the input and publishes a `UserTerminalInputEvent(name="api_backend", command="npm run build\n")` to the Event Bus.
3.  **Execution:** The `TerminalManager` hears this event, finds the active subprocess, and pipes the string into its `stdin.write()`.
4.  *(Bonus):* The TUI also logs this user input into the display pane (usually color-coded slightly differently so you know the user typed it, not the agent).

## 3. Abstract Python Integration

To adhere to `python-abstraction.md`, the UI components must simply be Event Bus listeners:

```python
# Pseudo-Textual implementation
from textual.app import App, ComposeResult
from textual.widgets import Input, ListView, RichLog, ListItem, Label
from src.core.events import EventBus, TerminalSpawnedEvent, TerminalLogEvent

class TerminalViewerApp(App):
    def __init__(self, event_bus: EventBus):
        super().__init__()
        self.event_bus = event_bus
        self.active_terminal_name = None
        
        # Subscribe TUI updates to background events
        self.event_bus.subscribe("TERMINAL_SPAWNED", self.on_terminal_spawned)
        self.event_bus.subscribe("TERMINAL_LOG", self.on_terminal_log)

    def compose(self) -> ComposeResult:
        # Sidebar for listing active terminals
        yield ListView(id="terminal_list")
        # Main log viewer
        yield RichLog(id="terminal_log_view", markup=True)
        # User input bar
        yield Input(id="terminal_input", placeholder="Send command to active terminal...")

    async def on_terminal_spawned(self, event: TerminalSpawnedEvent):
        """Update sidebar when a new background process starts."""
        list_view = self.query_one("#terminal_list", ListView)
        await list_view.append(ListItem(Label(event.name), id=f"list_item_{event.name}"))

    async def on_terminal_log(self, event: TerminalLogEvent):
        """Live stream stdout from the active terminal."""
        if self.active_terminal_name == event.name:
            log_view = self.query_one("#terminal_log_view", RichLog)
            log_view.write(event.line)

    async def on_input_submitted(self, message: Input.Submitted):
        """Send user input directly to the background subprocess."""
        if message.input.id == "terminal_input" and self.active_terminal_name:
            # Publish event rather than direct execution
            await self.event_bus.publish(
                UserTerminalInputEvent(name=self.active_terminal_name, command=message.value + "\n")
            )
            message.input.value = "" # Clear input
```

## 4. The Jailing Constraint
Even though the *user* is typing into the terminal, the `TerminalManager` still enforces the Workspace Rules defined in `12_sandbox_constraints.md`. The subprocess was spawned inside the root workspace (or `sandbox/` directory), meaning the user's manual commands are also safely contained.
