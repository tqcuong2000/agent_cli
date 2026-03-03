# Response Visualization Architecture

## Overview
This spec defines how the agent's response is rendered in the TUI. The visualization handles streaming text from the LLM, displays thinking blocks as collapsible dimmed sections, shows tool execution with spinners, and renders the final answer as Markdown.

The TUI subscribes to events on the Event Bus and renders them reactively. The agent never directly manipulates the UI — it emits events, and the TUI widgets consume them.

---

## 1. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Thinking display** | Collapsible, dimmed text | Keeps reasoning visible but non-intrusive. Click to expand/collapse. |
| **Tool call display** | Spinner + tool name | Shows progress. User knows which tool is running. |
| **Tool result display** | Hidden (not shown to user) | Reduces clutter. Results are internal to the agent's reasoning loop. |
| **Final answer** | Markdown rendered as rich text | Code blocks, headers, bold, lists rendered natively in terminal. |
| **Error display** | Floating popup at bottom-right corner | Non-blocking. Uses the BasePopupListView overlay pattern. |
| **Streaming** | Progressive character rendering | Text appears as it arrives from the LLM provider stream. |

---

## 2. Message Flow (Event → Widget)

```
LLM Provider stream
        │
        ▼
┌──────────────────────┐
│  Agent Reasoning Loop │
│  (ReAct cycle)        │
└───────┬──────────────┘
        │ emits events via Event Bus
        ▼
┌──────────────────────────────────────────────────────┐
│                      Event Bus                        │
├───────────────────┬──────────────────┬────────────────┤
│ AgentThinkingEvent│ ToolStartEvent   │ AgentMessageEvent
│ (streaming chunks)│ (tool_name, args)│ (final answer)
└───────┬───────────┴──────┬───────────┴───────┬────────┘
        ▼                  ▼                   ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────────┐
│ ThinkingBlock│  │ ToolStepWidget│  │ AnswerBlock      │
│ (collapsible)│  │ (spinner)    │  │ (Markdown render) │
└──────────────┘  └──────────────┘  └──────────────────┘
```

---

## 3. Chat Layout Structure

Each agent turn in the chat produces a vertical stack inside the `VerticalScroll`:

```
┌─ VerticalScroll ──────────────────────────────────────────────┐
│                                                                │
│  ┌─ UserMessageContainer ───────────────────────────────────┐  │
│  │ ▌ Hello, help me refactor the auth module                │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ AgentResponseContainer ─────────────────────────────────┐  │
│  │                                                           │  │
│  │  ┌─ ThinkingBlock (collapsed) ─────────────────────────┐ │  │
│  │  │ ▸ Thinking... (click to expand)                      │ │  │
│  │  └─────────────────────────────────────────────────────┘ │  │
│  │                                                           │  │
│  │  ┌─ ToolStepWidget ───────────────────────────────────┐  │  │
│  │  │ ⠋ read_file(src/auth.py)                            │  │  │
│  │  └────────────────────────────────────────────────────┘  │  │
│  │                                                           │  │
│  │  ┌─ ThinkingBlock (collapsed) ─────────────────────────┐ │  │
│  │  │ ▸ Thinking... (click to expand)                      │ │  │
│  │  └─────────────────────────────────────────────────────┘ │  │
│  │                                                           │  │
│  │  ┌─ ToolStepWidget ───────────────────────────────────┐  │  │
│  │  │ ✓ write_file(src/auth.py)                           │  │  │
│  │  └────────────────────────────────────────────────────┘  │  │
│  │                                                           │  │
│  │  ┌─ AnswerBlock ──────────────────────────────────────┐  │  │
│  │  │ I've refactored the auth module to use JWT.         │  │  │
│  │  │                                                     │  │  │
│  │  │ ## Changes made:                                    │  │  │
│  │  │ - Removed cookie-based auth                         │  │  │
│  │  │ - Added `jwt_middleware.py`                          │  │  │
│  │  │ ```python                                           │  │  │
│  │  │ def verify_token(token: str):                       │  │  │
│  │  │     ...                                             │  │  │
│  │  │ ```                                                 │  │  │
│  │  └────────────────────────────────────────────────────┘  │  │
│  │                                                           │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─ UserMessageContainer ───────────────────────────────────┐  │
│  │ ▌ Can you add tests too?                                 │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

---

## 4. Widget Specifications

### A. AgentResponseContainer

The outer container for an entire agent turn. Holds thinking blocks, tool steps, and the final answer in order.

```python
class AgentResponseContainer(Container):
    """
    Container for a single agent response turn.
    Children are appended dynamically as events arrive:
    ThinkingBlock → ToolStepWidget → ThinkingBlock → ... → AnswerBlock
    """

    DEFAULT_CSS = """
    AgentResponseContainer {
        width: 100%;
        height: auto;
        padding: 0 2;
        margin: 1 0;
    }
    """

    def __init__(self, task_id: str, **kwargs):
        super().__init__(**kwargs)
        self.task_id = task_id

    def append_thinking(self, text: str) -> "ThinkingBlock":
        """Append or update the current thinking block."""
        ...

    def append_tool_step(self, tool_name: str, args: dict) -> "ToolStepWidget":
        """Append a new tool execution step with spinner."""
        ...

    def set_answer(self, markdown_text: str) -> "AnswerBlock":
        """Set the final answer (Markdown rendered)."""
        ...
```

### B. ThinkingBlock (Collapsible, Dimmed)

Uses the same click-to-toggle pattern from `ContextContainer`:

```python
class ThinkingBlock(Container):
    """
    Collapsible block showing the agent's internal reasoning.
    Starts collapsed. Click header to expand/collapse.
    Uses dimmed text to distinguish from the final answer.

    Streaming: text is appended character-by-character as chunks arrive.
    """

    DEFAULT_CSS = """
    ThinkingBlock {
        width: 100%;
        height: auto;
        margin: 0 0 1 0;
    }

    ThinkingBlock .thinking_header {
        width: 100%;
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }

    ThinkingBlock .thinking_header:hover {
        background: $surface;
    }

    ThinkingBlock .thinking_content {
        width: 100%;
        height: auto;
        color: $text-disabled;
        padding: 0 2;
        display: none;
    }

    ThinkingBlock .thinking_content.expanded {
        display: block;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._text = ""
        self._is_streaming = True

    def compose(self):
        yield Static("▸ Thinking...", id="thinking_header", classes="thinking_header")
        yield Static("", id="thinking_content", classes="thinking_content")

    def append_chunk(self, text_chunk: str) -> None:
        """Append streaming text chunk to the thinking block."""
        self._text += text_chunk
        content = self.query_one("#thinking_content")
        content.update(self._text)
        # Update header with preview
        preview = self._text[:60].replace("\n", " ")
        header = self.query_one("#thinking_header")
        arrow = "▾" if content.has_class("expanded") else "▸"
        header.update(f"{arrow} Thinking: {preview}...")

    def finish_streaming(self) -> None:
        """Mark streaming as complete."""
        self._is_streaming = False
        header = self.query_one("#thinking_header")
        arrow = "▾" if self.query_one("#thinking_content").has_class("expanded") else "▸"
        preview = self._text[:60].replace("\n", " ")
        header.update(f"{arrow} Thought: {preview}...")

    def on_click(self, event) -> None:
        """Toggle expand/collapse on header click."""
        header = self.query_one("#thinking_header")
        content = self.query_one("#thinking_content")
        if event.control is header or header in event.control.ancestors:
            content.toggle_class("expanded")
            arrow = "▾" if content.has_class("expanded") else "▸"
            preview = self._text[:60].replace("\n", " ")
            label = "Thinking" if self._is_streaming else "Thought"
            header.update(f"{arrow} {label}: {preview}...")
            event.stop()
```

### C. ToolStepWidget (Spinner)

Shows which tool is executing with an animated spinner. Completes to a checkmark or error icon.

```python
class ToolStepWidget(Container):
    """
    Displays a tool execution step with an animated spinner.

    States:
    - RUNNING:  ⠋ read_file(src/auth.py)       (spinner animates)
    - SUCCESS:  ✓ read_file(src/auth.py)        (green checkmark)
    - FAILED:   ✗ read_file(src/auth.py) — Error (red cross)
    """

    SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    SPINNER_INTERVAL = 0.1  # seconds

    DEFAULT_CSS = """
    ToolStepWidget {
        width: 100%;
        height: 1;
        padding: 0 1;
        margin: 0;
    }

    ToolStepWidget .tool_label {
        width: auto;
        color: $text-muted;
    }

    ToolStepWidget .tool_label.success {
        color: $success;
    }

    ToolStepWidget .tool_label.error {
        color: $error;
    }
    """

    def __init__(self, tool_name: str, args: dict, **kwargs):
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self.args = args
        self._frame_index = 0
        self._timer = None

    def compose(self):
        args_preview = self._format_args()
        yield Static(
            f"⠋ {self.tool_name}({args_preview})",
            id="tool_label",
            classes="tool_label"
        )

    def on_mount(self) -> None:
        """Start the spinner animation."""
        self._timer = self.set_interval(self.SPINNER_INTERVAL, self._spin)

    def _spin(self) -> None:
        """Advance the spinner frame."""
        self._frame_index = (self._frame_index + 1) % len(self.SPINNER_FRAMES)
        frame = self.SPINNER_FRAMES[self._frame_index]
        args_preview = self._format_args()
        label = self.query_one("#tool_label")
        label.update(f"{frame} {self.tool_name}({args_preview})")

    def mark_success(self) -> None:
        """Stop spinner and show green checkmark."""
        if self._timer:
            self._timer.stop()
        label = self.query_one("#tool_label")
        args_preview = self._format_args()
        label.update(f"✓ {self.tool_name}({args_preview})")
        label.add_class("success")

    def mark_failed(self, error_msg: str = "") -> None:
        """Stop spinner and show red cross."""
        if self._timer:
            self._timer.stop()
        label = self.query_one("#tool_label")
        args_preview = self._format_args()
        suffix = f" — {error_msg}" if error_msg else ""
        label.update(f"✗ {self.tool_name}({args_preview}){suffix}")
        label.add_class("error")

    def _format_args(self) -> str:
        """Format args for display: show key values, truncated."""
        if not self.args:
            return ""
        parts = []
        for key, val in self.args.items():
            val_str = str(val)
            if len(val_str) > 40:
                val_str = val_str[:37] + "..."
            parts.append(f'{key}="{val_str}"')
        return ", ".join(parts[:2])  # Max 2 args for readability
```

### D. AnswerBlock (Markdown Rendered)

Renders the agent's final answer as rich Markdown in the terminal.

```python
from textual.widgets import Markdown


class AnswerBlock(Container):
    """
    Displays the agent's final answer with full Markdown rendering.

    Textual's Markdown widget supports:
    - Headers (# ## ###)
    - Bold, italic
    - Code blocks (```python ... ```)
    - Lists (- and 1.)
    - Links
    - Tables
    """

    DEFAULT_CSS = """
    AnswerBlock {
        width: 100%;
        height: auto;
        padding: 0;
        margin: 1 0 0 0;
    }

    AnswerBlock Markdown {
        width: 100%;
        background: transparent;
        padding: 0 1;
    }
    """

    def __init__(self, markdown_text: str = "", **kwargs):
        super().__init__(**kwargs)
        self._markdown_text = markdown_text

    def compose(self):
        yield Markdown(self._markdown_text)

    def update_content(self, markdown_text: str) -> None:
        """Update the displayed Markdown content."""
        self._markdown_text = markdown_text
        md_widget = self.query_one(Markdown)
        md_widget.update(markdown_text)

    def append_chunk(self, chunk: str) -> None:
        """Append streaming text and re-render Markdown."""
        self._markdown_text += chunk
        md_widget = self.query_one(Markdown)
        md_widget.update(self._markdown_text)
```

---

## 5. Event-to-Widget Mapping

The `TextWindowContainer` subscribes to events and routes them to the appropriate widget:

```python
class TextWindowContainer(Container):
    """Manages the chat message list. Subscribes to agent events."""

    def __init__(self, event_bus: AbstractEventBus, **kwargs):
        super().__init__(**kwargs)
        self._current_response: AgentResponseContainer | None = None

    def on_mount(self) -> None:
        """Subscribe to agent events."""
        # Subscriptions registered via Event Bus
        # AgentThinkingEvent → _on_thinking
        # ToolStartEvent → _on_tool_start
        # ToolResultEvent → _on_tool_result
        # AgentMessageEvent → _on_agent_message
        # ErrorEvent → _on_error

    # ── Event Handlers ───────────────────────────────────────

    async def _on_user_request(self, event: "UserRequestEvent") -> None:
        """User submitted a message. Add user bubble and create response container."""
        scroll = self.query_one(VerticalScroll)
        # Add user message
        await scroll.mount(UserMessageContainer(event.message))
        # Create agent response container
        self._current_response = AgentResponseContainer(task_id=event.task_id)
        await scroll.mount(self._current_response)
        scroll.scroll_end(animate=True)

    async def _on_thinking(self, event: "AgentThinkingEvent") -> None:
        """Streaming thinking chunk arrived. Append to current ThinkingBlock."""
        if self._current_response is None:
            return

        # Get or create the current thinking block
        thinking = self._current_response._current_thinking
        if thinking is None:
            thinking = ThinkingBlock()
            await self._current_response.mount(thinking)
            self._current_response._current_thinking = thinking

        thinking.append_chunk(event.text_chunk)
        self.query_one(VerticalScroll).scroll_end(animate=False)

    async def _on_tool_start(self, event: "ToolStartEvent") -> None:
        """A tool execution started. Close thinking block, show spinner."""
        if self._current_response is None:
            return

        # Finish current thinking block
        if self._current_response._current_thinking:
            self._current_response._current_thinking.finish_streaming()
            self._current_response._current_thinking = None

        # Add tool step with spinner
        step = ToolStepWidget(tool_name=event.tool_name, args=event.arguments)
        await self._current_response.mount(step)
        self._current_response._current_tool_step = step
        self.query_one(VerticalScroll).scroll_end(animate=False)

    async def _on_tool_result(self, event: "ToolResultEvent") -> None:
        """Tool finished. Mark spinner as success/failed. Result NOT shown to user."""
        if self._current_response is None:
            return

        step = self._current_response._current_tool_step
        if step is None:
            return

        if event.success:
            step.mark_success()
        else:
            step.mark_failed(event.error_message)

        self._current_response._current_tool_step = None

    async def _on_agent_message(self, event: "AgentMessageEvent") -> None:
        """Final answer arrived. Render as Markdown."""
        if self._current_response is None:
            return

        # Finish any active thinking block
        if self._current_response._current_thinking:
            self._current_response._current_thinking.finish_streaming()
            self._current_response._current_thinking = None

        answer = AnswerBlock(event.markdown_text)
        await self._current_response.mount(answer)
        self._current_response = None
        self.query_one(VerticalScroll).scroll_end(animate=True)
```

---

## 6. Error Display (Floating Popup)

Errors appear as a floating popup at the **bottom-right** corner using the overlay pattern from `BasePopupListView`. Auto-dismisses after a timeout or on click.

```python
class ErrorPopup(Widget):
    """
    Floating error notification at the bottom-right corner.
    Appears on ErrorEvent. Auto-dismisses after 5 seconds.
    """

    DEFAULT_CSS = """
    ErrorPopup {
        layer: overlay;
        display: none;
        dock: bottom;
        width: 50;
        height: auto;
        max-height: 6;
        background: $error 15%;
        border: solid $error;
        padding: 1 2;
        margin-bottom: 4;
        margin-left: auto;
        margin-right: 1;
    }

    ErrorPopup.visible {
        display: block;
    }

    ErrorPopup .error_title {
        color: $error;
        text-style: bold;
    }

    ErrorPopup .error_message {
        color: $text;
    }
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("id", "error_popup")
        super().__init__(**kwargs)
        self._dismiss_timer = None

    def show_error(self, title: str, message: str, auto_dismiss: float = 5.0) -> None:
        """Show an error notification."""
        self._update_content(title, message)
        # Dynamic positioning above footer
        self._position_above_footer()
        self.add_class("visible")
        # Auto-dismiss
        if self._dismiss_timer:
            self._dismiss_timer.stop()
        self._dismiss_timer = self.set_timer(auto_dismiss, self.dismiss)

    def _position_above_footer(self) -> None:
        """Position dynamically above the footer."""
        try:
            from agent_cli.ux.tui.views.footer.footer import FooterContainer
            footer = self.app.query_one(FooterContainer)
            footer_height = footer.outer_size.height
            self.styles.margin = (0, 1, footer_height, 0)
            # Align right: margin-left auto
            self.styles.margin = (0, 1, footer_height, 0)
        except Exception:
            self.styles.margin = (0, 1, 4, 0)

    def dismiss(self) -> None:
        """Hide the error popup."""
        self.remove_class("visible")

    def on_click(self) -> None:
        """Dismiss on click."""
        self.dismiss()

    def _update_content(self, title: str, message: str) -> None:
        self.update(f"[bold $error]{title}[/]\n{message}")
```

### Error Types and Display

| Error Type | Title | Example |
|---|---|---|
| Rate Limit (429) | ⚠ Rate Limited | Retrying in 30s... (attempt 2/3) |
| Server Error (500) | ⚠ Server Error | Provider returned 500. Retrying... |
| Auth Error (401) | ✗ Authentication Failed | Invalid API key for anthropic. Run /config set ... |
| Tool Error | ✗ Tool Failed | write_file: Permission denied on /etc/passwd |
| Context Overflow | ⚠ Context Full | Compacting memory... Summarizing older turns. |

---

## 7. Streaming Lifecycle (End-to-End)

```
User types: "Refactor auth.py to use JWT"
│
├─ 1. UserRequestEvent ──────────────────────────────────────────
│      TUI: UserMessageContainer("Refactor auth.py to use JWT")
│      TUI: AgentResponseContainer(task_id="abc123") created
│
├─ 2. AgentThinkingEvent (streaming chunks) ─────────────────────
│      "I need to "             → ThinkingBlock: ▸ Thinking: I need to...
│      "read the current "      → ThinkingBlock: ▸ Thinking: I need to read the current...
│      "auth implementation"    → ThinkingBlock: ▸ Thinking: I need to read the current auth impl...
│
├─ 3. ToolStartEvent(read_file, {path: "src/auth.py"}) ─────────
│      ThinkingBlock → finish_streaming() → "▸ Thought: I need to..."
│      TUI: ToolStepWidget: ⠋ read_file(path="src/auth.py")     (spinner)
│
├─ 4. ToolResultEvent(success=True) ─────────────────────────────
│      TUI: ToolStepWidget: ✓ read_file(path="src/auth.py")     (green check)
│      Note: Result content NOT displayed to user
│
├─ 5. AgentThinkingEvent (streaming chunks) ─────────────────────
│      "Now I can see the "     → NEW ThinkingBlock: ▸ Thinking: Now I can...
│      "cookie-based auth..."   → ThinkingBlock: ▸ Thinking: Now I can see the cookie-based...
│
├─ 6. ToolStartEvent(write_file, {path: "src/auth.py"}) ────────
│      ThinkingBlock → finish_streaming()
│      TUI: ToolStepWidget: ⠋ write_file(path="src/auth.py")    (spinner)
│
├─ 7. ToolResultEvent(success=True) ─────────────────────────────
│      TUI: ToolStepWidget: ✓ write_file(path="src/auth.py")    (green check)
│      + FileChangedEvent emitted → Changed Files panel updates
│
├─ 8. AgentMessageEvent (final answer, Markdown) ───────────────
│      TUI: AnswerBlock rendered with Markdown:
│      "I've refactored auth.py to use JWT..."
│      ## Changes made:
│      - Removed cookie-based auth
│      ```python
│      def verify_token(token): ...
│      ```
│
└─ 9. StateChangeEvent(WORKING → SUCCESS) ──────────────────────
       Changed Files panel: Accept/Reject buttons appear
```

---

## 8. Cross-References

| Component | Spec |
|---|---|
| Event definitions | `00_event_bus.md` |
| AgentThinkingEvent, ToolStartEvent | `01_reasoning_loop.md` |
| Streaming chunks from LLM | `01_ai_providers.md` (Section 6) |
| Tool execution and result events | `03_tools_architecture.md` |
| Error classification | `04_error_handling.md` |
| Changed Files panel (FileChangedEvent) | `04_changed_files.md` |
| `<thinking>` parsing from text | `02_schema_verification.md` |
| Collapsible pattern reference | `context_container.py` |
