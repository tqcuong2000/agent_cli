# Agent CLI — Phase 4 Implementation Plan
# TUI & Interactive Experience

> **Phase rules:**
> 1. 🛑 Stop after each component/widget for UX review before proceeding
> 2. 🚫 Sub-Phase 4.5 (Terminal Viewer) is deferred to a future phase

---

## Current State Audit

### Already Built (TUI Shell)
| File | What It Does |
|---|---|
| `ux/tui/app.py` | App shell, Header/Body/Footer layout, popup routing |
| `views/header/header.py` | HeaderContainer — title, terminal icon, agent badge |
| `views/header/status.py` | StatusContainer — mode / model / effort / shortcuts (static) |
| `views/footer/footer.py` | FooterContainer — input bar + submit button + status bar |
| `views/footer/user_input.py` | UserInputComponent — multi-line TextArea, popup triggers |
| `views/footer/submit_btn.py` | SubmitButtonComponent |
| `views/body/body.py` | BodyContainer — TextWindow + PanelWindow side-by-side |
| `views/body/text_window.py` | TextWindowContainer — stub with a hardcoded UserMessageContainer |
| `views/body/panel_window.py` | PanelWindowContainer — hosts ContextContainer |
| `views/body/messages/user_message.py` | UserMessageContainer — chat bubble widget |
| `views/body/panel/context_container.py` | ContextContainer — session / cost / context usage |
| `views/common/popup_list.py` | BasePopupListView — reusable fuzzy-filtered popup |
| `views/common/command_popup.py` | CommandPopup — `/` trigger, static command list |
| `views/common/file_popup.py` | FileDiscoveryPopup — `@` trigger, workspace file scan |
| `views/common/kv_line.py` | KVLine — key/separator/value row widget |
| `ux/tui/controllers/` | Empty — no controllers yet |

### Not Yet Built (Phase 4 Targets)
- Agent response widgets (ThinkingBlock, ToolStepWidget, AnswerBlock, AgentResponseContainer)
- Event Bus wiring in TextWindowContainer
- ErrorPopup
- Command system backend (`@command` decorator, `CommandParser`, handlers)
- Keyboard shortcut bindings in App
- Human-in-the-loop modals and `TUIInteractionHandler`
- Changed files tracker + panel widget
- Dynamic status bar (reactive mode/model/effort)

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

- [ ] Create `views/body/messages/agent_response.py`
- [ ] Extend `Widget` with `DEFAULT_CSS` (full width, auto height, left-aligned, padding)
- [ ] `compose()` yields empty `Vertical` container (`id="response_body"`)
- [ ] `append_thinking() -> ThinkingBlock` — mounts new `ThinkingBlock`, returns reference
- [ ] `append_tool_step(tool_name, args) -> ToolStepWidget` — mounts spinner widget
- [ ] `set_answer(content: str)` — mounts `AnswerBlock` at the end
- [ ] `get_active_thinking() -> ThinkingBlock | None` — returns last open thinking block
- [ ] All mount calls use `call_after_refresh` to ensure DOM is ready

🛑 **STOP — Review `AgentResponseContainer` layout and spacing with user**

---

### 4.1.2 — `ThinkingBlock`
**File:** `views/body/messages/thinking_block.py`

Collapsible, dimmed monologue section. Collapsed by default showing
"▸ Thinking…". Click to toggle. While the agent is actively
thinking, text chunks are appended live (streaming). When the
agent moves to a tool call or answer, `finish_streaming()` is
called to freeze the content.

- [ ] Create `views/body/messages/thinking_block.py`
- [ ] State: `is_expanded: reactive[bool] = reactive(False)`
- [ ] State: `is_streaming: bool = True` (set False by `finish_streaming()`)
- [ ] `compose()` — header row (`▸ Thinking…` label) + collapsible content `Static`
- [ ] `DEFAULT_CSS` — dimmed color (`$text-muted` / 60% opacity), left border accent, auto height
- [ ] `append_chunk(text: str)` — appends to internal buffer, updates `Static` content
- [ ] `finish_streaming()` — sets `is_streaming = False`, updates header to show char count hint
- [ ] `on_click()` — toggles `is_expanded`, shows/hides content area, rotates `▸`/`▾`
- [ ] Collapsed state shows only the header line (height: 1)
- [ ] Expanded state shows full content in a `ScrollableContainer`

🛑 **STOP — Review `ThinkingBlock` collapsed/expanded appearance with user**

---

### 4.1.3 — `ToolStepWidget`
**File:** `views/body/messages/tool_step.py`

Animated spinner row showing which tool is executing. Spinner
cycles through braille frames (`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`) at ~100ms
intervals using `set_interval`. On completion, replaced with
`✓` (green) or `✗` (red) and the interval is cancelled.

- [ ] Create `views/body/messages/tool_step.py`
- [ ] `SPINNER_FRAMES = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]`
- [ ] State: `_frame_index: int = 0`, `_timer` handle
- [ ] `compose()` — single `Static` widget for the full row
- [ ] `on_mount()` — starts `set_interval(0.1, self._spin)`
- [ ] `_spin()` — advances frame, calls `self._label.update(self._render_row())`
- [ ] `_render_row()` — returns Rich markup: `spinner tool_name(formatted_args)`
- [ ] `_format_args(args: dict) -> str` — truncates long values, max 60 chars total
- [ ] `mark_success(duration_ms: int)` — cancels timer, renders `✓` in green + tool name + duration
- [ ] `mark_failed(error: str)` — cancels timer, renders `✗` in red + truncated error
- [ ] `DEFAULT_CSS` — auto height, left padding matching ThinkingBlock indent

🛑 **STOP — Review `ToolStepWidget` spinner and completion states with user**

---

### 4.1.4 — `AnswerBlock`
**File:** `views/body/messages/answer_block.py`

The final response rendered as Markdown. Uses Textual's built-in
`Markdown` widget for full rendering (code blocks with syntax
highlighting, headers, bold, lists, tables). Supports both
immediate full-content display and progressive streaming
(chunk-by-chunk append rebuilds the Markdown widget).

- [ ] Create `views/body/messages/answer_block.py`
- [ ] `compose()` — yields `Markdown("")` widget
- [ ] `update_content(text: str)` — replaces Markdown widget content in full
- [ ] `append_chunk(chunk: str)` — appends to `_buffer`, calls `update_content(_buffer)`
- [ ] `DEFAULT_CSS` — full width, auto height, top margin (1) to separate from tool steps
- [ ] Padding: left 2, top 0, matches UserMessageContainer visual rhythm

🛑 **STOP — Review `AnswerBlock` Markdown rendering, padding, and code block style with user**

---

### 4.1.5 — `ErrorPopup`
**File:** `views/common/error_popup.py`

Non-blocking floating error notification at bottom-right corner.
Auto-dismisses after 5 seconds. Click to dismiss early. Styled
with a red border. Follows the same `layer: overlay` pattern as
`BasePopupListView`. Does NOT block the event loop.

- [ ] Create `views/common/error_popup.py`
- [ ] Extend `Widget`, `layer: overlay`, `dock: bottom`, `display: none`
- [ ] `DEFAULT_CSS` — width 50, background `$surface`, border `$error`, bottom-right position
- [ ] `show_error(title: str, message: str, error_type: str = "error")` — updates content, shows widget
- [ ] `_position_bottom_right()` — sets `margin` to sit above footer, right-aligned
- [ ] `dismiss()` — hides widget, cancels auto-dismiss timer
- [ ] `on_mount()` — registers `ErrorPopup` at App level (called in `app.py compose()`)
- [ ] `on_click()` — calls `dismiss()`
- [ ] Auto-dismiss: `set_timer(5.0, self.dismiss)` in `show_error()`
- [ ] `render()` — Rich panel: `[bold red]Warning: {title}[/]\n{message}`
- [ ] Yield `ErrorPopup` in `app.py compose()` alongside existing popups

🛑 **STOP — Review `ErrorPopup` position, style, and dismiss behaviour with user**

---

### 4.1.6 — `TextWindowContainer` Event Wiring
**File:** `views/body/text_window.py` (modify existing)

Replace the hardcoded stub with a live event-driven message list.
`TextWindowContainer` subscribes to Event Bus events and mounts
widgets dynamically. It tracks the "current" `AgentResponseContainer`
across the multi-step ReAct loop.

- [ ] Add `app_context: AppContext` parameter to `__init__` (passed from `app.py`)
- [ ] Replace hardcoded `UserMessageContainer` yield with empty `VerticalScroll(id="messages")`
- [ ] `on_mount()` — subscribe to Event Bus: `AgentThinkingEvent`, `ToolExecutionStartEvent`, `ToolExecutionResultEvent`, `AgentMessageEvent`, `UserRequestEvent`, `AgentErrorEvent`
- [ ] `_on_user_request(event)` — mount `UserMessageContainer(event.message)` into scroll
- [ ] `_on_thinking(event)` — if no active `AgentResponseContainer`, create one and mount it; call `arc.append_thinking()` and stream chunk
- [ ] `_on_tool_start(event)` — close active thinking block (`finish_streaming()`), call `arc.append_tool_step()`
- [ ] `_on_tool_result(event)` — call `tool_step.mark_success()` or `mark_failed()`
- [ ] `_on_agent_message(event)` — if `is_monologue=False`, call `arc.set_answer()`; close active thinking block
- [ ] `_on_agent_error(event)` — call `error_popup.show_error()`
- [ ] `_current_arc: AgentResponseContainer | None` — track across events; reset to `None` on task completion (`TaskResultEvent`)
- [ ] All `mount()` calls followed by `call_after_refresh(self._scroll_to_end)`
- [ ] `_scroll_to_end()` — calls `scroll.scroll_end(animate=False)` on the `VerticalScroll`

- [ ] Update `app.py` to pass `AppContext` to `TextWindowContainer` (requires DI hookup)
- [ ] Update `app.py compose()` to yield `ErrorPopup(id="error_popup")`

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

- [ ] Create `agent_cli/commands/__init__.py`
- [ ] Create `agent_cli/commands/base.py`
- [ ] `CommandResult` dataclass: `success: bool`, `message: str`, `data: Any = None`
- [ ] `CommandDef` dataclass: `name`, `description`, `usage`, `shortcut`, `category`, `handler`
- [ ] `CommandRegistry` singleton — `_registry: dict[str, CommandDef]`
- [ ] `@command(name, description, usage="", shortcut="", category="General")` decorator
  - Wraps async handler functions
  - Registers `CommandDef` into `CommandRegistry`
  - Handler signature: `async def handler(ctx: CommandContext, *args) -> CommandResult`
- [ ] `CommandContext` dataclass: `settings`, `event_bus`, `state_manager`, `orchestrator`, `app` (Textual App ref)
- [ ] `CommandRegistry.get(name) -> CommandDef | None`
- [ ] `CommandRegistry.all() -> list[CommandDef]`
- [ ] `CommandRegistry.get_suggestions(partial: str) -> list[CommandDef]` — prefix + fuzzy match

🛑 **STOP — Review `@command` decorator API and `CommandContext` fields with user**

---

### 4.2.2 — `CommandParser`
**File:** `agent_cli/commands/parser.py`

- [ ] Create `agent_cli/commands/parser.py`
- [ ] `CommandParser.__init__(registry: CommandRegistry, context: CommandContext)`
- [ ] `is_command(text: str) -> bool` — returns `text.strip().startswith("/")`
- [ ] `async execute(raw: str) -> CommandResult` — parse name + args, lookup, call handler
  - Strip `/`, split on whitespace, `args[0]` = command name, rest = positional args
  - Unknown command → `CommandResult(success=False, message=f"Unknown command: /{name}. Try /help")`
  - Handler exception → `CommandResult(success=False, message=str(e))`
- [ ] `get_suggestions(partial: str) -> list[CommandDef]` — delegates to registry
- [ ] `get_all_commands() -> list[CommandDef]` — delegates to registry

---

### 4.2.3 — Core Command Handlers
**File:** `agent_cli/commands/handlers/core.py`

- [ ] Create `agent_cli/commands/handlers/__init__.py`
- [ ] Create `agent_cli/commands/handlers/core.py`
- [ ] `/help [cmd]` — list all commands with name/description/shortcut, or detail one command
- [ ] `/clear` — emit event to clear `WorkingMemoryManager` + post message to TUI to clear chat
- [ ] `/exit` — call `app.exit()`
- [ ] `/mode [plan|fast]` — update `settings.execution_mode`, update status bar `#mode` widget
- [ ] `/model <name>` — update `settings.default_model`, update status bar `#model` widget
- [ ] `/effort [low|medium|high]` — update `settings.effort_level`, update status bar `#effort` widget
- [ ] `/config [key] [value]` — show current settings or update a key (read-only display for now)
- [ ] `/cost` — read cost from `AppContext`, format and return as `CommandResult.message`
- [ ] `/context` — show token usage and context window from memory manager

🛑 **STOP — Review command output formatting (help layout, cost display, etc.) with user**

---

### 4.2.4 — Wire CommandParser to Orchestrator + TUI
**Files:** `core/orchestrator.py` (modify), `app.py` (modify)

- [ ] `Orchestrator` — inject `CommandParser` in constructor
- [ ] `Orchestrator._on_user_request()` — if `is_command(text)`, call `await parser.execute(text)` instead of routing to agent
  - On `CommandResult.success=False` — emit `AgentErrorEvent` with message
  - On `CommandResult.success=True` — if message non-empty, emit `AgentMessageEvent` with message
- [ ] Remove old `register_command()` dict approach from `Orchestrator` (replaced by `CommandParser`)
- [ ] `app.py` — build `CommandParser` after `AppContext` created; pass to `Orchestrator`
- [ ] `CommandPopup` — replace static `_COMMANDS` list with live pull from `CommandRegistry.all()` in `get_all_items()`

---

### 4.2.5 — Keyboard Shortcuts
**File:** `ux/tui/app.py` (modify)

- [ ] Add `BINDINGS` to `AgentCLIApp`:
  - `ctrl+p` → `action_open_command_palette` (focus input, insert `/`)
  - `ctrl+e` → `action_cycle_effort` (LOW → MEDIUM → HIGH → LOW)
  - `ctrl+m` → `action_toggle_mode` (plan ↔ fast)
  - `ctrl+l` → `action_clear_context` (calls `/clear`)
  - `ctrl+q` → `action_quit_app`
- [ ] `action_open_command_palette()` — focuses `UserInputComponent`, sets text to `/`
- [ ] `action_cycle_effort()` — calls `/effort` command with next level
- [ ] `action_toggle_mode()` — calls `/mode` command with toggled value
- [ ] `action_clear_context()` — calls `/clear` command
- [ ] `action_quit_app()` — calls `app.exit()`

🛑 **STOP — Review keyboard shortcut bindings and command output display with user**

---

### 4.2.6 — Dynamic Status Bar
**File:** `views/header/status.py` (modify)

The `StatusContainer` currently has hardcoded strings. Make it
reactive to `CommandResult` side-effects.

- [ ] Add `reactive` attributes: `mode`, `model`, `effort`
- [ ] `watch_mode()` — updates `#mode` Static widget
- [ ] `watch_model()` — updates `#model` Static widget
- [ ] `watch_effort()` — updates `#effort` Static widget
- [ ] Expose `update_mode(v)`, `update_model(v)`, `update_effort(v)` public methods
- [ ] Command handlers call `app.query_one(StatusContainer).update_*()` after changing settings

🛑 **STOP — Review status bar live updates with user**

---

### 4.2.7 — Tests
**File:** `tests/tui/test_command_system.py`

- [ ] Create `tests/tui/test_command_system.py`
- [ ] Test: `@command` decorator registers into `CommandRegistry`
- [ ] Test: `CommandParser.is_command("/help")` → True; `"hello"` → False
- [ ] Test: `CommandParser.execute("/help")` → `CommandResult(success=True)`
- [ ] Test: `CommandParser.execute("/unknown")` → `CommandResult(success=False)`
- [ ] Test: `CommandParser.execute("/effort high")` → updates settings
- [ ] Test: `get_suggestions("mo")` → returns `/mode`, `/model`
- [ ] Test: `/clear` resets memory manager

---

## Sub-Phase 4.3 — Human-in-the-Loop (HITL)
> Spec: `architect-workspace/03_user_interface/01_human_in_loop.md`

### Overview
When the agent calls a dangerous tool or needs clarification, the
reasoning loop must pause and wait for the user. The pause is
implemented with `asyncio.Event` (non-blocking). The TUI shows a
modal. The user responds. The event is set. The agent resumes.

---

### 4.3.1 — Data Models + `BaseInteractionHandler`
**File:** `agent_cli/core/interaction.py`

- [ ] Create `agent_cli/core/interaction.py`
- [ ] `InteractionType` enum: `APPROVAL`, `CLARIFICATION`, `PLAN_APPROVAL`, `FATAL_ERROR`
- [ ] `UserInteractionRequest` dataclass (fields: `interaction_type`, `message`, `task_id`, `source`, `tool_name`, `tool_args`, `plan_assignments`, `error_details`, `options`)
- [ ] `UserInteractionResponse` dataclass (fields: `action`, `feedback`, `edited_args`)
- [ ] `BaseInteractionHandler` ABC with `async request_human_input(req) -> UserInteractionResponse` and `async notify(msg)`

---

### 4.3.2 — `ApprovalModal` Widget
**File:** `views/body/modals/approval_modal.py`

Full-screen dimmed overlay modal. Shows the tool name, formatted
arguments, and risk level. Three buttons: **Approve**, **Edit**,
**Deny**. Pressing Deny or Escape resolves the pending
`asyncio.Event` with `action="deny"`.

- [ ] Create `views/body/modals/__init__.py`
- [ ] Create `views/body/modals/approval_modal.py`
- [ ] Extend `ModalScreen` (Textual built-in)
- [ ] `compose()` — centered panel with:
  - Title: `⚠ Approval Required`
  - Tool name + formatted args display (read-only `TextArea` or `Static`)
  - Risk level badge (color-coded: `$warning` / `$error`)
  - Row of 3 buttons: `[Approve]` `[Edit Command]` `[Deny]`
- [ ] `DEFAULT_CSS` — modal overlay, centered, max-width 80, border `$warning`
- [ ] `on_approve()` — `dismiss(UserInteractionResponse(action="approve"))`
- [ ] `on_deny()` — `dismiss(UserInteractionResponse(action="deny"))`
- [ ] `on_edit()` — make args editable (`TextArea` becomes active), button changes to `[Confirm Edit]`
- [ ] `on_confirm_edit()` — `dismiss(UserInteractionResponse(action="approve", edited_args=...))`
- [ ] `key_escape()` → `on_deny()`

🛑 **STOP — Review `ApprovalModal` layout, risk badge colours, and button arrangement with user**

---

### 4.3.3 — `TUIInteractionHandler`
**File:** `agent_cli/core/tui_interaction_handler.py`

Bridges the agent backend to the TUI modal system. When the
agent calls `request_human_input()`, this handler pushes
`ApprovalModal` onto the Textual screen stack and suspends
via `asyncio.Event.wait()`. When the modal is dismissed, it
resolves the event.

- [ ] Create `agent_cli/core/tui_interaction_handler.py`
- [ ] `TUIInteractionHandler(BaseInteractionHandler)`
- [ ] `__init__(app: AgentCLIApp)` — stores app reference
- [ ] `async request_human_input(req) -> UserInteractionResponse`:
  - Create `asyncio.Event`
  - Schedule `app.push_screen(ApprovalModal(req), callback)` via `app.call_from_thread`
  - `callback` sets event + stores response
  - `await event.wait()`
  - Return stored response
- [ ] `async notify(message: str)` — mounts a `Static` notification into messages scroll
- [ ] Wire into `AppContext` and `ToolExecutor` — replace auto-approve stub

---

### 4.3.4 — Shell Command Approval Flow
- [ ] `ToolExecutor` — when `tool.requires_approval` and not auto-approve, call `interaction_handler.request_human_input(APPROVAL, tool_name, tool_args)`
- [ ] If `response.action == "deny"` → raise `ToolExecutionError("User denied execution")`
- [ ] If `response.edited_args` is set → re-validate and run with edited args
- [ ] `RunCommandTool` — `requires_approval = not is_safe_command(cmd)`

---

### 4.3.5 — Clarification Flow (inline)
**File:** `views/body/modals/clarification_inline.py`

- [ ] Create `views/body/modals/clarification_inline.py`
- [ ] Mount an inline `Static` in the chat: "Agent needs clarification: {question}"
- [ ] Re-focus the `UserInputComponent`
- [ ] User's next submission is captured as the clarification response (not a new agent task)
- [ ] `TUIInteractionHandler` detects `CLARIFICATION` type and uses this path instead of modal

🛑 **STOP — Review clarification inline flow with user**

---

### 4.3.6 — Tests
**File:** `tests/tui/test_hitl.py`

- [ ] Create `tests/tui/test_hitl.py`
- [ ] Test: `ApprovalModal` renders tool name and args
- [ ] Test: Approve button → `response.action == "approve"`
- [ ] Test: Deny button → `response.action == "deny"`
- [ ] Test: Edit flow → `response.edited_args` is populated
- [ ] Test: `asyncio.Event` is set after modal dismiss (non-blocking check)
- [ ] Test: auto-approve mode skips modal entirely

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
- [ ] `AgentResponseContainer` — outer wrapper, dynamic child mounting
- [ ] `ThinkingBlock` — collapsible, streaming, dimmed
- [ ] `ToolStepWidget` — animated spinner → ✓/✗
- [ ] `AnswerBlock` — Markdown rendering, streaming append
- [ ] `ErrorPopup` — floating bottom-right, auto-dismiss 5s
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
- [ ] `InteractionType`, `UserInteractionRequest`, `UserInteractionResponse`, `BaseInteractionHandler`
- [ ] `ApprovalModal` — approve / edit / deny, Escape = deny
- [ ] `TUIInteractionHandler` — `asyncio.Event` pause/resume
- [ ] Shell command approval flow in `ToolExecutor`
- [ ] Inline clarification widget
- [ ] Tests — 6 HITL tests passing

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
