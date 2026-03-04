# Phase 5 — Data Management & Persistence

## Goal
Add session persistence (save/restore conversations), memory management (context window optimization), workspace security (sandbox mode), and file discovery for the `@` popup.

**Specs:** `01_memory_management.md`, `04_session_persistence.md`, `03_workspace_sandbox.md`, `02_file_discovery.md`
**Depends on:** Phase 1 (State, Config), Phase 4 (TUI)

---

## Sub-Phase 5.1 — Memory Management
> Spec: `02_data_management/01_memory_management.md`

Manage the LLM's context window efficiently.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 5.1.1 | `BaseMemoryManager` ABC | Define `add_message()`, `get_messages()`, `compact()`, `get_token_count()` | 🔴 Critical |
| 5.1.2 | Sliding window | Keep last N messages, summarize older ones | 🔴 Critical |
| 5.1.3 | Token counting | Use `tiktoken` or provider-specific tokenizer for accurate counts | 🔴 Critical |
| 5.1.4 | Context compaction | When near limit: summarize old turns, keep system prompt + recent turns | 🟡 Medium |
| 5.1.5 | System prompt management | Template-based system prompts per agent type | 🟡 Medium |
| 5.1.6 | `TokenBudget` model | Max context, response reserve, compaction threshold | 🟡 Medium |
| 5.1.7 | Tests | Test token counting, compaction trigger, message ordering | 🔴 Critical |

**Deliverable:** `agent_cli/memory/base.py`, `agent_cli/memory/sliding_window.py`, `agent_cli/memory/token_counter.py`

---

## Sub-Phase 5.2 — Session Persistence
> Spec: `02_data_management/04_session_persistence.md`

Save and restore conversation sessions.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 5.2.1 | `AbstractSessionManager` ABC | Define `save()`, `load()`, `list()`, `delete()` interface | 🔴 Critical |
| 5.2.2 | JSON file storage | Save sessions as JSON in `~/.agent_cli/sessions/` | 🔴 Critical |
| 5.2.3 | Session model | `Session`: id, name, created_at, messages, state snapshot, cost | 🔴 Critical |
| 5.2.4 | Auto-save | Periodic save during active conversation (configurable interval) | 🟡 Medium |
| 5.2.5 | Session restore | Load session → restore messages, state, and resume agent | 🟡 Medium |
| 5.2.6 | Wire to `/session` commands | `list`, `save`, `restore`, `delete`, `info` subcommands | 🟡 Medium |
| 5.2.7 | Tests | Test save/load round-trip, listing, deletion | 🔴 Critical |

**Deliverable:** `agent_cli/session/base.py`, `agent_cli/session/file_store.py`

---

## Sub-Phase 5.3 — Workspace Security
> Spec: `02_data_management/03_workspace_sandbox.md`

Jail file operations to the workspace root. Sandbox mode for extra safety.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 5.3.1 | `BaseWorkspaceManager` ABC | Define `resolve_path()`, `is_allowed()`, `get_root()` interface | 🔴 Critical |
| 5.3.2 | `StrictWorkspaceManager` | Path resolution, symlink detection, traversal prevention | 🔴 Critical |
| 5.3.3 | Sandbox mode | Copy workspace to temp dir, operate there, diff on exit | 🟡 Medium |
| 5.3.4 | Path enforcement in ToolExecutor | All file tools pass through workspace manager before execution | 🔴 Critical |
| 5.3.5 | Allowed/denied patterns | Configurable include/exclude globs (e.g., `!.env`, `!.git/`) | 🟡 Medium |
| 5.3.6 | Wire to `/sandbox` command | `on`, `off`, `ls` (list sandboxed files) | 🟡 Medium |
| 5.3.7 | Tests | Test path traversal prevention, symlink blocking, sandbox isolation | 🔴 Critical |

**Deliverable:** `agent_cli/workspace/base.py`, `agent_cli/workspace/strict.py`, `agent_cli/workspace/sandbox.py`

---

## Sub-Phase 5.4 — File Discovery (Enhanced)
> Spec: `04_utilities/02_file_discovery.md`

Replace the simple `pathlib.iterdir()` in `FileDiscoveryPopup` with a proper indexed file discovery system.

| # | Task | Description | Priority |
|---|------|-------------|----------|
| 5.4.1 | `FileIndex` | Build and cache a file index for the workspace | 🟡 Medium |
| 5.4.2 | Gitignore support | Respect `.gitignore` patterns when scanning | 🟡 Medium |
| 5.4.3 | Fuzzy path matching | Improve fuzzy matching with scoring (path segment weighting) | 🟡 Medium |
| 5.4.4 | File watcher | Detect filesystem changes and update index (optional `watchdog`) | 🟢 Low |
| 5.4.5 | Wire to `@` popup | Replace `FileDiscoveryPopup._scan_workspace()` with `FileIndex` queries | 🟡 Medium |
| 5.4.6 | Tests | Test index building, gitignore filtering, fuzzy scoring | 🟡 Medium |

**Deliverable:** `agent_cli/workspace/file_index.py`

---

## Completion Criteria

- [ ] Memory: token-aware sliding window with compaction
- [ ] Sessions: save/load/list works from `/session` commands
- [ ] Workspace: path jailing blocks all escape attempts
- [ ] Sandbox: operate on copy, diff on exit
- [ ] File discovery: indexed, gitignore-aware, fuzzy scored
- [ ] All tests pass including security edge cases
