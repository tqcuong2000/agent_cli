# Phase 4 — TUI & Interactive Experience

## Goal
Build the interactive user experience: visualize agent responses in real-time, implement the command system, add human-in-the-loop approval flows, display changed files, and embed the terminal viewer.

**Specs:** `05_response_visualization.md`, `03_command_system.md`, `01_human_in_loop.md`, `04_changed_files.md`, `02_terminal_viewer.md`
**Depends on:** Phase 1 (Event Bus, State)

---

## Sub-Phase 4.1 — Response Visualization
> Spec: `03_user_interface/05_response_visualization.md`

The core chat experience — how the agent's response appears in the TUI.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 4.1.1 | `AgentResponseContainer` | Outer container for an agent turn. Holds thinking, tool steps, and answer | 🔴 Critical |
| 4.1.2 | `ThinkingBlock` | Collapsible dimmed text (click to expand). Streaming: chunks appended live | 🔴 Critical |
| 4.1.3 | `ToolStepWidget` | Animated spinner (`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`) → ✓ or ✗ on completion | 🔴 Critical |
| 4.1.4 | `AnswerBlock` | Textual `Markdown` widget rendering the final answer (code blocks, headers, lists) | 🔴 Critical |
| 4.1.5 | `ErrorPopup` | Floating overlay at bottom-right corner, auto-dismiss after 5s | 🟡 Medium |
| 4.1.6 | Event Bus subscriptions | Wire `TextWindowContainer` to listen for agent events and mount widgets | 🔴 Critical |
| 4.1.7 | Auto-scroll | `VerticalScroll.scroll_end()` as new content appears | 🟡 Medium |
| 4.1.8 | Tests | Test widget mounting, streaming append, collapse toggle | 🟡 Medium |

**Deliverable:** `views/body/messages/agent_response.py`, `views/body/messages/thinking_block.py`, `views/body/messages/tool_step.py`, `views/body/messages/answer_block.py`, `views/common/error_popup.py`

---

## Sub-Phase 4.2 — Command System Execution
> Spec: `03_user_interface/03_command_system.md`

Wire the popup-based command system to actual handlers.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 4.2.1 | `@command` decorator | Register handler functions with name, description, usage, shortcut | 🔴 Critical |
| 4.2.2 | `CommandParser` | Parse `/cmd args`, route to handler, fuzzy-match for suggestions | 🔴 Critical |
| 4.2.3 | `CommandContext` dataclass | Inject dependencies (settings, orchestrator, state, event_bus) into handlers | 🔴 Critical |
| 4.2.4 | Core command handlers | `/mode`, `/model`, `/effort`, `/config`, `/clear`, `/help`, `/exit` | 🔴 Critical |
| 4.2.5 | Session commands | `/session list\|save\|restore\|delete` | 🟡 Medium |
| 4.2.6 | Info commands | `/cost`, `/context`, `/changes` | 🟡 Medium |
| 4.2.7 | Sandbox command | `/sandbox on\|off\|ls` | 🟡 Medium |
| 4.2.8 | Wire to Orchestrator | Orchestrator checks for `/` prefix before routing to agent | 🔴 Critical |
| 4.2.9 | Keyboard shortcuts | Textual key bindings: `ctrl+p` palette, `ctrl+e` effort, `ctrl+m` mode, etc. | 🟡 Medium |
| 4.2.10 | Tests | Test parser, decorator registration, handler execution | 🔴 Critical |

**Deliverable:** `agent_cli/commands/base.py`, `agent_cli/commands/parser.py`, `agent_cli/commands/handlers/`

---

## Sub-Phase 4.3 — Human-in-the-Loop (HITL)
> Spec: `03_user_interface/01_human_in_loop.md`

Approval flows for dangerous operations.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 4.3.1 | `ApprovalModal` widget | Textual modal: show operation summary, Accept / Reject buttons | 🔴 Critical |
| 4.3.2 | File change approval | Show diff preview, approve/reject all changes | 🔴 Critical |
| 4.3.3 | Shell command approval | Show command string, working directory, risk level | 🔴 Critical |
| 4.3.4 | Plan review modal | Show multi-step plan, approve/edit/reject | 🟡 Medium |
| 4.3.5 | HITL event flow | Agent emits `ApprovalRequiredEvent` → TUI shows modal → user responds → agent continues | 🔴 Critical |
| 4.3.6 | Tests | Test event flow, modal rendering | 🟡 Medium |

**Deliverable:** `views/body/modals/approval_modal.py`, `views/body/modals/plan_review.py`

---

## Sub-Phase 4.4 — Changed Files Panel
> Spec: `03_user_interface/04_changed_files.md`

Real-time tracking of files modified by the agent.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 4.4.1 | `FileChangeTracker` | Track file modifications via tool detection (write_file, etc.) | 🔴 Critical |
| 4.4.2 | `ChangedFilesWidget` | Side panel list showing modified files with status icons | 🔴 Critical |
| 4.4.3 | Batch approve/reject | Accept All / Reject All buttons for all pending changes | 🟡 Medium |
| 4.4.4 | Event integration | `FileChangedEvent` → update panel | 🟡 Medium |
| 4.4.5 | Tests | Test tracking, widget updates | 🟡 Medium |

**Deliverable:** `views/body/panel/changed_files.py`, `agent_cli/core/file_tracker.py`

---

## Sub-Phase 4.5 — Terminal Viewer
> Spec: `03_user_interface/02_terminal_viewer.md`

Persistent terminal for running and viewing processes.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 4.5.1 | Terminal output widget | Display process stdout/stderr in a scrollable area | 🟡 Medium |
| 4.5.2 | Process lifecycle | Start, monitor, kill processes from the TUI | 🟡 Medium |
| 4.5.3 | Integration with shell tool | Shell tool output routes to terminal viewer | 🟡 Medium |
| 4.5.4 | Tests | Test process start/stop, output rendering | 🟢 Low |

**Deliverable:** `views/body/terminal/terminal_viewer.py`

---

## Completion Criteria

- [ ] ThinkingBlock: collapsible, dimmed, streaming
- [ ] ToolStepWidget: spinner animates, completes with ✓/✗
- [ ] AnswerBlock: Markdown rendering works (code blocks, headers)
- [ ] ErrorPopup: floating at bottom-right, auto-dismiss
- [ ] Commands: `/mode`, `/model`, `/help`, `/exit` work via decorator
- [ ] HITL: approval modal blocks agent until user responds
- [ ] Changed files: panel updates in real-time
- [ ] Full response flow visible: think → tool → think → answer
