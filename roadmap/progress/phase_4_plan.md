# Agent CLI — Phase 4 Implementation Plan
# TUI & Interactive Experience

> **Phase rules:**
> 1. 🛑 Stop after each component/widget for UX review before proceeding
> 2. 🚫 Sub-Phase 4.5 (Terminal Viewer) is deferred to a future phase

---

## Current State Audit

### Already Built (TUI Shell & Core UI)
| File | What It Does |
|---|---|
| `ux/tui/app.py` | App shell, Header/Body/Footer layout, popup routing, keyboard shortcuts |
| `views/header/header.py` | HeaderContainer — title, terminal icon, agent badge |
| `views/header/status.py` | StatusContainer — reactive mode / model / effort display |
| `views/footer/footer.py` | FooterContainer — input bar + submit button + command interception |
| `views/footer/user_input.py` | UserInputComponent — multi-line TextArea, popup triggers |
| `views/footer/submit_btn.py` | SubmitButtonComponent |
| `views/body/body.py` | BodyContainer — TextWindow + PanelWindow side-by-side |
| `views/body/text_window.py` | TextWindowContainer — dynamic event-driven chat window |
| `views/body/panel_window.py` | PanelWindowContainer — hosts ContextContainer |
| `views/body/messages/user_message.py` | UserMessageContainer — user chat bubble |
| `views/body/messages/agent_response.py` | AgentResponseContainer — agent turn wrapper |
| `views/body/messages/thinking_block.py` | ThinkingBlock — collapsible streaming monologue |
| `views/body/messages/tool_step.py` | ToolStepWidget — animated tool spinner |
| `views/body/messages/answer_block.py` | AnswerBlock — streaming Markdown support |
| `views/common/error_popup.py` | ErrorPopup — floating warning notification |
| `commands/base.py` | Command Registry & @command decorator |
| `commands/parser.py` | Command Parser for slash commands |
| `commands/handlers/core.py` | Core handlers (/help, /model, /effort, etc.) |
| `ux/tui/controllers/` | Empty — no controllers yet |

### Not Yet Built (Phase 4 Targets)
- Remaining HITL types: `PLAN_APPROVAL` and `FATAL_ERROR` UI flows
- Changed files tracker + panel widget (Sub-Phase 4.4)
- Session persistence logic (future phases)
- Terminal processes integration (Phase 5/Deferred)

---

## Sub-Phase 4.1 — Response Visualization
> Spec: `architect-workspace/03_user_interface/05_response_visualization.md`

### Overview
Wire the agent's Event Bus output to visual widgets in the chat window.
Each agent turn produces an `AgentResponseContainer` that holds
`ThinkingBlock` → `ToolStepWidget` → `ThinkingBlock` → ... → `AnswerBlock`
in vertical order, matching the ReAct loop.

---

### 4.1.1 — `AgentResponseContainer`
**File:** `views/body/messages/agent_response.py`

The outer wrapper for one complete agent turn. Manages child widget
lifecycle: creates a new `ThinkingBlock` when thinking starts,
appends `ToolStepWidget` per tool call, and mounts the final
`AnswerBlock`. All children are mounted dynamically.

- [x] Create `views/body/messages/agent_response.py`
- [x] Extend `Widget` with `DEFAULT_CSS` (full width, auto height, left-aligned, padding)
- [x] `compose()` yields empty `Vertical` container (`id="response_body"`)
- [x] `append_thinking() -> ThinkingBlock` — mounts new `ThinkingBlock`, returns reference
- [x] `append_tool_step(tool_name, args) -> ToolStepWidget` — mounts spinner widget
- [x] `set_answer(content: str)` — mounts `AnswerBlock` at the end
- [x] `get_active_thinking() -> ThinkingBlock | None` — returns last open thinking block
- [x] All mount calls use `call_after_refresh` to ensure DOM is ready

🛑 **STOP — Review `AgentResponseContainer` layout and spacing with user**

---

### 4.1.2 — `ThinkingBlock`
**File:** `views/body/messages/thinking_block.py`

Collapsible, dimmed monologue section. Collapsed by default showing
"▸ Thinking…". Click to toggle. While the agent is actively
thinking, text chunks are appended live (streaming). When the
agent moves to a tool call or answer, `finish_streaming()` is
called to freeze the content.

- [x] Create `views/body/messages/thinking_block.py`
- [x] State: `is_expanded: reactive[bool] = reactive(False)`
- [x] State: `is_streaming: bool = True` (set False by `finish_streaming()`)
- [x] `compose()` — header row (`▸ Thinking…` label) + collapsible content `Static`
- [x] `DEFAULT_CSS` — dimmed color (`$text-muted` / 60% opacity), left border accent, auto height
- [x] `append_chunk(text: str)` — appends to internal buffer, updates `Static` content
- [x] `finish_streaming()` — sets `is_streaming = False`, updates header to show char count hint
- [x] `on_click()` — toggles `is_expanded`, shows/hides content area, rotates `▸`/`▾`
- [x] Collapsed state shows only the header line (height: 1)
- [x] Expanded state shows full content in a `ScrollableContainer`

🛑 **STOP — Review `ThinkingBlock` collapsed/expanded appearance with user**

---

### 4.1.3 — `ToolStepWidget`
**File:** `views/body/messages/tool_step.py`

Animated spinner row showing which tool is executing. Spinner
cycles through braille frames (`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`) at ~100ms
intervals using `set_interval`. On completion, replaced with
`✓` (green) or `✗` (red) and the interval is cancelled.

- [x] Create `views/body/messages/tool_step.py`
- [x] `SPINNER_FRAMES = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]`
- [x] State: `_frame_index: int = 0`, `_timer` handle
- [x] `compose()` — single `Static` widget for the full row
- [x] `on_mount()` — starts `set_interval(0.1, self._spin)`
- [x] `_spin()` — advances frame, calls `self._label.update(self._render_row())`
- [x] `_render_row()` — returns Rich markup: `spinner tool_name(formatted_args)`
- [x] `_format_args(args: dict) -> str` — truncates long values, max 60 chars total
- [x] `mark_success(duration_ms: int)` — cancels timer, renders `✓` in green + tool name + duration
- [x] `mark_failed(error: str)` — cancels timer, renders `✗` in red + truncated error
- [x] `DEFAULT_CSS` — auto height, left padding matching ThinkingBlock indent

🛑 **STOP — Review `ToolStepWidget` spinner and completion states with user**

---

### 4.1.4 — `AnswerBlock`
**File:** `views/body/messages/answer_block.py`

The final response rendered as Markdown. Uses Textual's built-in
`Markdown` widget for full rendering (code blocks with syntax
highlighting, headers, bold, lists, tables). Supports both
immediate full-content display and progressive streaming
(chunk-by-chunk append rebuilds the Markdown widget).

- [x] Create `views/body/messages/answer_block.py`
- [x] `compose()` — yields `Markdown("")` widget
- [x] `update_content(text: str)` — replaces Markdown widget content in full
- [x] `append_chunk(chunk: str)` — appends to `_buffer`, calls `update_content(_buffer)`
- [x] `DEFAULT_CSS` — full width, auto height, top margin (1) to separate from tool steps
- [x] Padding: left 2, top 0, matches UserMessageContainer visual rhythm

🛑 **STOP — Review `AnswerBlock` Markdown rendering, padding, and code block style with user**

---

### 4.1.5 — `ErrorPopup`
**File:** `views/common/error_popup.py`

Non-blocking floating error notification at bottom-right corner.
Auto-dismisses after 5 seconds. Click to dismiss early. Styled
with a red border. Follows the same `layer: overlay` pattern as
`BasePopupListView`. Does NOT block the event loop.

- [x] Create `views/common/error_popup.py`
- [x] Extend `Widget`, `layer: overlay`, `dock: bottom`, `display: none`
- [x] `DEFAULT_CSS` — width 50, background `$surface`, border `$error`, bottom-right position
- [x] `show_error(title: str, message: str, error_type: str = "error")` — updates content, shows widget
- [x] `_position_bottom_right()` — sets `margin` to sit above footer, right-aligned
- [x] `dismiss()` — hides widget, cancels auto-dismiss timer
- [x] `on_mount()` — registers `ErrorPopup` at App level (called in `app.py compose()`)
- [x] `on_click()` — calls `dismiss()`
- [x] Auto-dismiss: `set_timer(5.0, self.dismiss)` in `show_error()`
- [x] `render()` — Rich panel: `[bold red]Warning: {title}[/]\n{message}`
- [x] Yield `ErrorPopup` in `app.py compose()` alongside existing popups

🛑 **STOP — Review `ErrorPopup` position, style, and dismiss behaviour with user**

---

### 4.1.6 — `TextWindowContainer` Event Wiring
**File:** `views/body/text_window.py` (modify existing)

Replace the hardcoded stub with a live event-driven message list.
`TextWindowContainer` subscribes to Event Bus events and mounts
widgets dynamically. It tracks the "current" `AgentResponseContainer`
across the multi-step ReAct loop.

- [x] Add `app_context: AppContext` parameter to `__init__` (passed from `app.py`)
- [x] Replace hardcoded `UserMessageContainer` yield with empty `VerticalScroll(id="messages")`
- [x] `on_mount()` — subscribe to Event Bus: `AgentThinkingEvent`, `ToolExecutionStartEvent`, `ToolExecutionResultEvent`, `AgentMessageEvent`, `UserRequestEvent`, `AgentErrorEvent`
- [x] `_on_user_request(event)` — mount `UserMessageContainer(event.message)` into scroll
- [x] `_on_thinking(event)` — if no active `AgentResponseContainer`, create one and mount it; call `arc.append_thinking()` and stream chunk
- [x] `_on_tool_start(event)` — close active thinking block (`finish_streaming()`), call `arc.append_tool_step()`
- [x] `_on_tool_result(event)` — call `tool_step.mark_success()` or `mark_failed()`
- [x] `_on_agent_message(event)` — if `is_monologue=False`, call `arc.set_answer()`; close active thinking block
- [x] `_on_agent_error(event)` — call `error_popup.show_error()`
- [x] `_current_arc: AgentResponseContainer | None` — track across events; reset to `None` on task completion (`TaskResultEvent`)
- [x] All `mount()` calls followed by `call_after_refresh(self._scroll_to_end)`
- [x] `_scroll_to_end()` — calls `scroll.scroll_end(animate=False)` on the `VerticalScroll`

- [x] Update `app.py` to pass `AppContext` to `TextWindowContainer` (requires DI hookup)
- [x] Update `app.py compose()` to yield `ErrorPopup(id="error_popup")`

🛑 **STOP — Review full response flow (think → tool → answer) end-to-end with user**

---

### 4.1.7 — Auto-Scroll
**Covered in 4.1.6** (`_scroll_to_end` helper).

- [x] Verify scroll triggers on: new `UserMessageContainer`, new `AgentResponseContainer`, each `ThinkingBlock` chunk, each `ToolStepWidget` mount, `AnswerBlock` mount

---

### 4.1.8 — Tests
**File:** `tests/tui/test_response_visualization.py`

- [x] Create `tests/tui/__init__.py`
- [x] Create `tests/tui/test_response_visualization.py`
- [x] Test: `AgentResponseContainer` mounts `ThinkingBlock` via `append_thinking()`
- [x] Test: `ThinkingBlock.append_chunk()` updates content; `finish_streaming()` freezes it
- [x] Test: `ThinkingBlock` collapse toggle changes `is_expanded` reactive
- [x] Test: `ToolStepWidget.mark_success()` stops timer, updates label to `✓`
- [x] Test: `ToolStepWidget.mark_failed()` stops timer, updates label to `✗`
- [x] Test: `AnswerBlock.append_chunk()` accumulates and updates Markdown widget
- [x] Test: `ErrorPopup.show_error()` makes widget visible; `dismiss()` hides it

---

## Sub-Phase 4.2 — Command System Execution
> Spec: `architect-workspace/03_user_interface/03_command_system.md`

### Overview
Wire the existing `CommandPopup` (static display only) to a real backend:
`@command` decorator registers handlers into a `CommandRegistry`,
`CommandParser` routes `/cmd args` strings, and concrete handlers
for the core commands are implemented. The `Orchestrator` delegates
`/`-prefixed inputs to `CommandParser` before touching the agent.

---

### 4.2.1 — `@command` Decorator + `CommandRegistry`
**File:** `agent_cli/commands/base.py`

- [x] Create `agent_cli/commands/__init__.py`
- [x] Create `agent_cli/commands/base.py`
- [x] `CommandResult` dataclass: `success: bool`, `message: str`, `data: Any = None`
- [x] `CommandDef` dataclass: `name`, `description`, `usage`, `shortcut`, `category`, `handler`
- [x] `CommandRegistry` singleton — `_registry: dict[str, CommandDef]`
- [x] `@command(name, description, usage="", shortcut="", category="General")` decorator
  - Wraps async handler functions
  - Registers `CommandDef` into `CommandRegistry`
  - Handler signature: `async def handler(ctx: CommandContext, *args) -> CommandResult`
- [x] `CommandContext` dataclass: `settings`, `event_bus`, `state_manager`, `orchestrator`, `app` (Textual App ref)
- [x] `CommandRegistry.get(name) -> CommandDef | None`
- [x] `CommandRegistry.all() -> list[CommandDef]`
- [x] `CommandRegistry.get_suggestions(partial: str) -> list[CommandDef]` — prefix + fuzzy match

🛑 **STOP — Review `@command` decorator API and `CommandContext` fields with user**

---

### 4.2.2 — `CommandParser`
**File:** `agent_cli/commands/parser.py`

- [x] Create `agent_cli/commands/parser.py`
- [x] `CommandParser.__init__(registry: CommandRegistry, context: CommandContext)`
- [x] `is_command(text: str) -> bool` — returns `text.strip().startswith("/")`
- [x] `async execute(raw: str) -> CommandResult` — parse name + args, lookup, call handler
  - Strip `/`, split on whitespace, `args[0]` = command name, rest = positional args
  - Unknown command → `CommandResult(success=False, message=f"Unknown command: /{name}. Try /help")`
  - Handler exception → `CommandResult(success=False, message=str(e))`
- [x] `get_suggestions(partial: str) -> list[CommandDef]` — delegates to registry
- [x] `get_all_commands() -> list[CommandDef]` — delegates to registry

---

### 4.2.3 — Core Command Handlers
**File:** `agent_cli/commands/handlers/core.py`

- [x] Create `agent_cli/commands/handlers/__init__.py`
- [x] Create `agent_cli/commands/handlers/core.py`
- [x] `/help [cmd]` — list all commands with name/description/shortcut, or detail one command
- [x] `/clear` — emit event to clear `WorkingMemoryManager` + post message to TUI to clear chat
- [x] `/exit` — call `app.exit()`
- [x] `/mode [plan|fast]` — update `settings.execution_mode`, update status bar `#mode` widget
- [x] `/model <name>` — update `settings.default_model`, update status bar `#model` widget
- [x] `/effort [low|medium|high]` — update `settings.effort_level`, update status bar `#effort` widget
- [x] `/config [key] [value]` — show current settings or update a key (read-only display for now)
- [x] `/cost` — read cost from `AppContext`, format and return as `CommandResult.message`
- [x] `/context` — show token usage and context window from memory manager

🛑 **STOP — Review command output formatting (help layout, cost display, etc.) with user**

---

### 4.2.4 — Wire CommandParser to Orchestrator + TUI
**Files:** `core/orchestrator.py` (modify), `app.py` (modify)

- [x] `Orchestrator` — inject `CommandParser` in constructor
- [x] `Orchestrator._on_user_request()` — if `is_command(text)`, call `await parser.execute(text)` instead of routing to agent
  - On `CommandResult.success=False` — emit `AgentErrorEvent` with message
  - On `CommandResult.success=True` — if message non-empty, emit `AgentMessageEvent` with message
- [x] Remove old `register_command()` dict approach from `Orchestrator` (replaced by `CommandParser`)
- [x] `app.py` — build `CommandParser` after `AppContext` created; pass to `Orchestrator`
- [x] `CommandPopup` — replace static `_COMMANDS` list with live pull from `CommandRegistry.all()` in `get_all_items()`

---

### 4.2.5 — Keyboard Shortcuts
**File:** `ux/tui/app.py` (modify)

- [x] Add `BINDINGS` to `AgentCLIApp`:
  - `ctrl+p` → `action_open_command_palette` (focus input, insert `/`)
  - `ctrl+e` → `action_cycle_effort` (LOW → MEDIUM → HIGH → LOW)
  - `ctrl+m` → `action_toggle_mode` (plan ↔ fast)
  - `ctrl+l` → `action_clear_context` (calls `/clear`)
  - `ctrl+q` → `action_quit_app`
- [x] `action_open_command_palette()` — focuses `UserInputComponent`, sets text to `/`
- [x] `action_cycle_effort()` — calls `/effort` command with next level
- [x] `action_toggle_mode()` — calls `/mode` command with toggled value
- [x] `action_clear_context()` — calls `/clear` command
- [x] `action_quit_app()` — calls `app.exit()`

🛑 **STOP — Review keyboard shortcut bindings and command output display with user**

---

### 4.2.6 — Dynamic Status Bar
**File:** `views/header/status.py` (modify)

The `StatusContainer` currently has hardcoded strings. Make it
reactive to `CommandResult` side-effects.

- [x] Add `reactive` attributes: `mode`, `model`, `effort`
- [x] `watch_mode()` — updates `#mode` Static widget
- [x] `watch_model()` — updates `#model` Static widget
- [x] `watch_effort()` — updates `#effort` Static widget
- [x] Expose `update_mode(v)`, `update_model(v)`, `update_effort(v)` public methods
- [x] Command handlers call `app.query_one(StatusContainer).update_*()` after changing settings

🛑 **STOP — Review status bar live updates with user**

---

### 4.2.7 — Tests
**File:** `tests/tui/test_command_system.py`

- [x] Create `tests/tui/test_command_system.py`
- [x] Test: `@command` decorator registers into `CommandRegistry`
- [x] Test: `CommandParser.is_command("/help")` → True; `"hello"` → False
- [x] Test: `CommandParser.execute("/help")` → `CommandResult(success=True)`
- [x] Test: `CommandParser.execute("/unknown")` → `CommandResult(success=False)`
- [x] Test: `CommandParser.execute("/effort high")` → updates settings
- [x] Test: `get_suggestions("mo")` → returns `/mode`, `/model`
- [x] Test: `/clear` resets memory manager

---

## Sub-Phase 4.3 — Human-in-the-Loop (HITL)
> Spec: `architect-workspace/03_user_interface/01_human_in_loop.md`

### Overview
When the agent calls a dangerous tool or needs clarification, the
reasoning loop must pause and wait for the user. The pause is
implemented with `asyncio.Event` (non-blocking). The TUI shows the
inline footer interaction area. The user responds. The event is set.
The agent resumes.

---

### 4.3.1 — Data Models + `BaseInteractionHandler`
**File:** `agent_cli/core/interaction.py`

- [x] Create `agent_cli/core/interaction.py`
- [x] `InteractionType` enum: `APPROVAL`, `CLARIFICATION`, `PLAN_APPROVAL`, `FATAL_ERROR`
- [x] `UserInteractionRequest` dataclass (fields: `interaction_type`, `message`, `task_id`, `source`, `tool_name`, `tool_args`, `plan_assignments`, `error_details`, `options`)
- [x] `UserInteractionResponse` dataclass (fields: `action`, `feedback`, `edited_args`)
- [x] `BaseInteractionHandler` ABC with `async request_human_input(req) -> UserInteractionResponse` and `async notify(msg)`

---

### 4.3.2 — `UserInteraction` Inline Panel (Footer)
**File:** `views/footer/user_interaction.py`

Inline interaction area rendered above the user input. Hidden by
default. Shown when approval/clarification is needed. This replaces
the full-screen modal approach for better flow and lower visual
disruption.

- [x] Add/keep `views/footer/user_interaction.py`
- [x] Hidden by default (`display: none`)
- [x] Add `show_approval(task_id, tool_name, tool_args, message)` API
- [x] Add action buttons: `[Approve]` `[Deny]`
- [x] Emit `UserInteraction.ActionSelected` message with `{task_id, action}`
- [x] Add `hide_panel()` API
- [x] Wire panel into `FooterContainer` above input area

🛑 **STOP — Review inline interaction area layout and actions with user**

---

### 4.3.3 — `TUIInteractionHandler`
**File:** `agent_cli/core/tui_interaction_handler.py`

Bridges the agent backend to the inline TUI interaction flow.
When the agent calls `request_human_input()`, this handler emits
approval/question events and suspends via `asyncio.Event.wait()`.
When a response event arrives, it resolves and returns.

- [x] Create `agent_cli/core/tui_interaction_handler.py`
- [x] `TUIInteractionHandler(BaseInteractionHandler)`
- [x] `__init__(app: AgentCLIApp)` — stores app reference
- [x] `async request_human_input(req) -> UserInteractionResponse`:
  - Create `asyncio.Event`
  - Emit `UserApprovalRequestEvent` (inline footer panel)
  - Wait for `UserApprovalResponseEvent`
  - Return structured response
- [x] `async notify(message: str)` — emits system notification into message stream
- [x] Wire into `AppContext` and `ToolExecutor`

---

### 4.3.4 — Shell Command Approval Flow
- [x] `ToolExecutor` — when `tool.requires_approval` and not auto-approve, call `interaction_handler.request_human_input(APPROVAL, tool_name, tool_args)`
- [x] If `response.action == "deny"` → deny execution
- [x] `edited_args` handling intentionally omitted (approve/deny only by project decision)
- [x] `RunCommandTool` — `requires_approval = not is_safe_command(cmd)`

---

### 4.3.5 — Clarification Flow (inline)
**Feature:** `AgentQuestion` (inline via footer `user_interaction`)

- [x] Add clarification request/response events (`AgentQuestionRequestEvent`, `AgentQuestionResponseEvent`)
- [x] Render one question at a time in `UserInteraction` panel (multi-line)
- [x] Show 2-5 suggested answers as clickable options
- [x] Keep "Type your answer" path using `UserInputComponent` submit
- [x] `TUIInteractionHandler` handles `CLARIFICATION` requests and waits for answer
- [x] No hard limit on number of `AgentQuestion`s per task
- [x] Do not append `User answers` transcript into conversation window

🛑 **STOP — Review clarification inline flow with user**

---

### 4.3.6 — Tests
**File:** `tests/tui/test_hitl.py`

- [x] Create `tests/tui/test_hitl.py`
- [x] Test: approval roundtrip returns `response.action == "approve"`
- [x] Test: `ToolExecutor` uses `interaction_handler` for approval path
- [x] Test: clarification roundtrip returns `response.action == "answered"`
- [x] Test: multiple clarifications emit no conversation transcript
- [x] Test: clarification has no hard limit (6 sequential questions)
- [x] Test: clarification rejects >5 options (requires 2-5)

---

## Sub-Phase 4.4 — Changed Files Panel
> Spec: `architect-workspace/03_user_interface/04_changed_files.md`

### Overview
Track every file the agent writes, creates, or deletes. Display them
in the right side panel in real time. At task completion, offer
"Accept All" / "Reject All". Rejection restores original file
content from a snapshot taken at task start.

---

### 4.4.1 — `FileChangeTracker`
**File:** `agent_cli/core/file_tracker.py`

- [ ] Create `agent_cli/core/file_tracker.py`
- [ ] `ChangeType` enum: `CREATED`, `MODIFIED`, `DELETED`
- [ ] `FileChange` dataclass: `path: Path`, `change_type: ChangeType`, `original_content: str | None`, `timestamp: datetime`
- [ ] `FileChangeTracker`:
  - `start_tracking(workspace_root: Path)` — sets root, clears state
  - `async record_change(path, change_type)` — snapshot original before first write; emit `FileChangedEvent`
  - `get_changes() -> list[FileChange]`
  - `total_files() -> int`
  - `is_empty() -> bool`
  - `reset()` — clears all tracked changes and snapshots
- [ ] Add `FileChangeTracker` to `AppContext`
- [ ] `FileChangedEvent` dataclass: `path: str`, `change_type: ChangeType`

---

### 4.4.2 — Emit `FileChangedEvent` from `ToolExecutor`
**File:** `agent_cli/tools/executor.py` (modify)

- [ ] Inject `FileChangeTracker` into `ToolExecutor.__init__`
- [ ] After successful `write_file` / `delete_file` tool execution, call `tracker.record_change()`
- [ ] Emit `FileChangedEvent` on the event bus

---

### 4.4.3 — `ChangedFilesPanel` Widget
**File:** `views/body/panel/changed_files.py`

Live-updating list in the right sidebar. Each row shows a status
icon (`+` created / `~` modified / `-` deleted), coloured by
change type, and the relative file path. Scrollable if many files.
When the panel is empty it shows a dim "No changes yet" placeholder.

- [ ] Create `views/body/panel/changed_files.py`
- [ ] Extend `Widget`, subscribe to `FileChangedEvent` from event bus on mount
- [ ] `DEFAULT_CSS` — full width, auto height, padding 1, `$panel 50%` background
- [ ] Header row: `Static("Changed Files")` with count badge `(N)`
- [ ] `VerticalScroll` for the file list
- [ ] `async _on_file_changed(event: FileChangedEvent)` — append a row to the scroll
- [ ] `_render_row(change: FileChange) -> str` — `[green]+[/]` / `[yellow]~[/]` / `[red]-[/]` + relative path
- [ ] `clear()` — removes all rows, resets count
- [ ] Empty state: dim italic "No changes yet" placeholder

🛑 **STOP — Review `ChangedFilesPanel` layout, icons, and colours with user**

---

### 4.4.4 — Accept / Reject Buttons
**File:** `views/body/panel/changed_files.py` (extend)

- [ ] Add `Accept All` and `Reject All` buttons below the file list
- [ ] Buttons only appear (display) when `tracker.total_files() > 0`
- [ ] `on_accept_all()` — calls `tracker.reset()`, clears the panel, hides buttons
- [ ] `on_reject_all()` — calls `tracker.revert_all()` (restores snapshots), clears panel
- [ ] `tracker.revert_all()` — iterates `FileChange` list: restore `original_content` for MODIFIED, delete for CREATED, restore for DELETED
- [ ] On `TaskResultEvent` from event bus — automatically show/hide buttons

🛑 **STOP — Review accept/reject button placement and revert behaviour with user**

---

### 4.4.5 — Wire Panel into `PanelWindowContainer`
**File:** `views/body/panel_window.py` (modify)

- [ ] Add `ChangedFilesPanel` below `ContextContainer` in `PanelWindowContainer.compose()`
- [ ] Pass `AppContext` (specifically `file_tracker` + `event_bus`) into `ChangedFilesPanel`
- [ ] `ChangedFilesPanel` hidden when `tracker.is_empty()` to avoid empty visual noise

---

### 4.4.6 — Tests
**File:** `tests/tui/test_changed_files.py`

- [ ] Create `tests/tui/test_changed_files.py`
- [ ] Test: `FileChangeTracker.record_change()` snapshots original content on first write
- [ ] Test: second write to same file does NOT overwrite snapshot
- [ ] Test: `FileChangedEvent` is emitted after `record_change()`
- [ ] Test: `reset()` clears all changes and snapshots
- [ ] Test: `revert_all()` restores MODIFIED files from snapshot
- [ ] Test: `revert_all()` deletes CREATED files
- [ ] Test: `ChangedFilesPanel` renders a row per `FileChangedEvent`
- [ ] Test: empty panel shows "No changes yet" placeholder

---

## ~~Sub-Phase 4.5 — Terminal Viewer~~ (Deferred)
> **Moved to a future phase** per project directive.
> Spec: `architect-workspace/03_user_interface/02_terminal_viewer.md`

---

## Master Checklist

### Sub-Phase 4.1 — Response Visualization
- [x] `AgentResponseContainer` — outer wrapper, dynamic child mounting
- [x] `ThinkingBlock` — collapsible, streaming, dimmed
- [x] `ToolStepWidget` — animated spinner → ✓/✗
- [x] `AnswerBlock` — Markdown rendering, streaming append
- [x] `ErrorPopup` — floating bottom-right, auto-dismiss 5s
- [x] `TextWindowContainer` — event bus wiring, auto-scroll
- [x] Tests — 8 widget-level tests passing

### Sub-Phase 4.2 — Command System
- [x] `@command` decorator + `CommandRegistry`
- [x] `CommandContext` dataclass
- [x] `CommandParser` — parse, route, suggest
- [x] Core handlers — `/help`, `/clear`, `/exit`, `/mode`, `/model`, `/effort`, `/config`, `/cost`, `/context`
- [x] `Orchestrator` — delegates `/` prefix to `CommandParser`
- [x] `CommandPopup` — pulls live from `CommandRegistry`
- [x] `app.py` — keyboard bindings wired
- [x] `StatusContainer` — reactive mode/model/effort
- [x] Tests — 7 command-level tests passing

### Sub-Phase 4.3 — Human-in-the-Loop
- [x] `InteractionType`, `UserInteractionRequest`, `UserInteractionResponse`, `BaseInteractionHandler`
- [x] `UserInteraction` inline panel — approve / deny above input
- [x] `TUIInteractionHandler` — `asyncio.Event` pause/resume
- [x] Shell command approval flow in `ToolExecutor`
- [x] Inline clarification widget (`AgentQuestion`)
- [x] Tests — 6 HITL tests passing

### Sub-Phase 4.4 — Changed Files Panel
- [ ] `FileChangeTracker` — snapshot, record, revert
- [ ] `FileChangedEvent` emitted from `ToolExecutor`
- [ ] `ChangedFilesPanel` — live list, count badge, empty state
- [ ] Accept All / Reject All buttons + revert logic
- [ ] `PanelWindowContainer` wired with `ChangedFilesPanel`
- [ ] Tests — 8 tracker/panel tests passing

---

## File Tree — New Files This Phase

```
agent_cli/
├── commands/                                   # NEW package
│   ├── __init__.py
│   ├── base.py                                 # @command decorator, CommandRegistry, CommandContext
│   ├── parser.py                               # CommandParser
│   └── handlers/
│       ├── __init__.py
│       └── core.py                             # /help /clear /exit /mode /model /effort /config /cost /context
├── core/
│   ├── interaction.py                          # NEW — InteractionType, request/response models, BaseInteractionHandler
│   ├── tui_interaction_handler.py              # NEW — TUIInteractionHandler (asyncio.Event bridge)
│   ├── file_tracker.py                         # NEW — FileChangeTracker, FileChange, ChangeType
│   ├── bootstrap.py                            # MODIFIED — add FileChangeTracker + TUIInteractionHandler to AppContext
│   └── orchestrator.py                         # MODIFIED — inject CommandParser, remove old register_command dict
├── tools/
│   └── executor.py                             # MODIFIED — inject FileChangeTracker, call record_change()
ux/tui/
├── app.py                                      # MODIFIED — BINDINGS, ErrorPopup, AppContext wiring
└── views/
    ├── body/
    │   ├── text_window.py                      # MODIFIED — event bus subscriptions, dynamic mounting
    │   ├── panel_window.py                     # MODIFIED — add ChangedFilesPanel
    │   ├── messages/
    │   │   ├── agent_response.py               # NEW — AgentResponseContainer
    │   │   ├── thinking_block.py               # NEW — ThinkingBlock
    │   │   ├── tool_step.py                    # NEW — ToolStepWidget
    │   │   └── answer_block.py                 # NEW — AnswerBlock
    │   ├── panel/
    │   │   └── changed_files.py                # NEW — ChangedFilesPanel
    │   └── modals/
    │       ├── __init__.py                     # NEW
    │       ├── approval_modal.py               # NEW — ApprovalModal (ModalScreen)
    │       └── clarification_inline.py         # NEW — inline clarification widget
    ├── common/
    │   └── error_popup.py                      # NEW — ErrorPopup (floating overlay)
    └── header/
        └── status.py                           # MODIFIED — reactive mode/model/effort

tests/
└── tui/
    ├── __init__.py                             # NEW
    ├── test_response_visualization.py          # NEW — 7 tests
    ├── test_command_system.py                  # NEW — 7 tests
    ├── test_hitl.py                            # NEW — 6 tests
    └── test_changed_files.py                   # NEW — 8 tests
```

---

## Dependency Order (Build Sequence)

```
4.1.1 AgentResponseContainer
  └─ 4.1.2 ThinkingBlock
  └─ 4.1.3 ToolStepWidget
  └─ 4.1.4 AnswerBlock
       └─ 4.1.5 ErrorPopup
            └─ 4.1.6 TextWindowContainer wiring  ← first live end-to-end flow
                 └─ 4.1.8 Tests

4.2.1 CommandRegistry + @command decorator
  └─ 4.2.2 CommandParser
       └─ 4.2.3 Core handlers
            └─ 4.2.4 Wire to Orchestrator + App
                 └─ 4.2.5 Keyboard shortcuts
                      └─ 4.2.6 Reactive StatusBar
                           └─ 4.2.7 Tests

4.3.1 Interaction models + BaseInteractionHandler
  └─ 4.3.2 ApprovalModal
       └─ 4.3.3 TUIInteractionHandler
            └─ 4.3.4 Shell command approval (ToolExecutor)
                 └─ 4.3.5 Clarification inline
                      └─ 4.3.6 Tests

4.4.1 FileChangeTracker
  └─ 4.4.2 Emit FileChangedEvent from ToolExecutor
       └─ 4.4.3 ChangedFilesPanel widget
            └─ 4.4.4 Accept/Reject buttons
                 └─ 4.4.5 Wire into PanelWindowContainer
                      └─ 4.4.6 Tests
```
