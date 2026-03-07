# Deprecation & Removal Plan: Legacy Compatibility Cleanup (Dev Status)

---

## 1. Overview

| Field              | Value                                              |
|:-------------------|:---------------------------------------------------|
| **What**           | Remove legacy compatibility shims identified in the legacy spike: deprecated workspace wrapper, orchestrator legacy command fallback, static command popup fallback, schema legacy `tool/args` repair, compatibility aliases, and temporary debug UI action. |
| **Source**         | `dev/reports/legacy_feature_leftovers_spike_2026-03-06.md` (Findings 1-6) |
| **Why**            | Project is in active dev mode; modern replacements are default runtime paths, and legacy shims add complexity/noise. |
| **Replacement**    | `StrictWorkspaceManager` + `SandboxWorkspaceManager`, `CommandRegistry` + `CommandParser`, canonical `decision.actions`, explicit `get_root()` API, and removal of debug-only binding. |
| **Scope**          | Remove legacy code and migrate in-repo consumers/tests. Out of scope: external repos and historical saved sessions outside this workspace. |
| **Estimated Effort** | M - multi-file changes across runtime, UI, parser, and tests; behavior is understood and covered by tests. |
| **Risk Level**     | Medium - parser/schema/session compatibility regressions possible if any hidden consumer still depends on legacy formats. |

### 1.1 Items to Deprecate

| # | Item | Type | File Path | Status |
|:-:|:-----|:-----|:----------|:------:|
| D1 | `WorkspaceContext` | Class/module wrapper | `agent_cli/core/runtime/tools/workspace.py` | Already deprecated |
| D2 | Orchestrator legacy command fallback (`_commands`, `register_command`, `_handle_command` fallback branch) | Runtime path/API | `agent_cli/core/runtime/orchestrator/orchestrator.py` | Active fallback |
| D3 | Static popup fallback (`_COMMANDS`, constructor fallback, `set_commands`) | UI fallback | `agent_cli/core/ux/tui/views/common/command_popup.py` | Active fallback |
| D4 | `execute_actions` legacy repair from `decision.tool` + `decision.args` | Parser compatibility shim | `agent_cli/core/runtime/agents/schema.py` | Active fallback |
| D5 | `StrictWorkspaceManager.root_path` alias | Property alias | `agent_cli/core/ux/interaction/strict.py` | Active compatibility alias |
| D6 | `SandboxWorkspaceManager.root_path` alias | Property alias | `agent_cli/core/ux/interaction/sandbox.py` | Active compatibility alias |
| D7 | Temporary debug popup action (`shift+up`, `action_show_error_popup`) | UI debug feature | `agent_cli/core/ux/tui/app.py` | Active temporary code |
| D8 | Session effort fallback coercion to `auto` for invalid/missing values | Persistence compatibility shim | `agent_cli/core/runtime/session/file_store.py` | Active fallback |

### 1.2 Success Criteria

- [ ] All consumers migrated to replacement (section 3 migration paths followed)
- [ ] All deprecated items removed from codebase
- [ ] Zero references to removed code: `Select-String` verification commands in section 5/7 return no hits in source/tests
- [ ] Full test suite passes after removal (`pytest -q dev/tests`)
- [ ] Legacy-focused regression pack passes (`pytest -q dev/tests/core/test_orchestrator.py dev/tests/agent/test_schema.py dev/tests/session/test_session_manager.py dev/tests/tools/test_file_tools.py dev/tests/tools/test_shell_tool.py`)

### 1.3 Out of Scope

- Migration or repair of historical session files outside repository-controlled test fixtures.
- External consumer compatibility guarantees outside `X:\agent_cli`.

---

## 2. Consumer Analysis

### 2.1 Direct Consumers

| # | Consumer | File Path | Usage | Migration Action |
|:-:|:---------|:----------|:------|:-----------------|
| C1 | File tool tests | `dev/tests/tools/test_file_tools.py:14` | Imports `WorkspaceContext` | Instantiate `StrictWorkspaceManager` directly (or helper fixture using modern stack). |
| C2 | Shell tool tests | `dev/tests/tools/test_shell_tool.py:15` | Imports `WorkspaceContext` | Same as C1. |
| C3 | Orchestrator test path | `dev/tests/core/test_orchestrator.py:295` | Calls `orchestrator.register_command(...)` | Rewrite test to exercise `CommandParser` behavior instead of legacy API. |
| C4 | Orchestrator runtime fallback branch | `agent_cli/core/runtime/orchestrator/orchestrator.py:207` | Falls back to `_handle_command` if parser missing | Remove branch; require parser at orchestrator construction/runtime. |
| C5 | CommandPopup internal static list | `agent_cli/core/ux/tui/views/common/command_popup.py:30` | Static `_COMMANDS` backing list | Use `CommandRegistry` exclusively; remove static list fallback. |
| C6 | Schema legacy field repair | `agent_cli/core/runtime/agents/schema.py:316` | Converts legacy `tool/args` for `execute_actions` | Remove repair; require explicit `decision.actions` contract. |
| C7 | Session effort fallback | `agent_cli/core/runtime/session/file_store.py:240` | Coerces invalid/missing effort to `auto` | Tighten parsing policy (either strict error or explicit migration gate). |
| C8 | Debug popup action | `agent_cli/core/ux/tui/app.py:34` | Keybinding invokes temporary popup | Remove binding and method. |

### 2.2 Indirect Consumers

| # | Consumer | File Path | How It Consumes | Migration Action |
|:-:|:---------|:----------|:----------------|:-----------------|
| I1 | Bootstrap orchestration path | `agent_cli/core/infra/registry/bootstrap.py:723` | Constructs `Orchestrator` with parser already supplied | Keep as canonical path; enforce parser required contract. |
| I2 | TUI popup construction | `agent_cli/core/ux/tui/app.py:52` | Passes `command_registry` to `CommandPopup` | Keep as canonical path; disallow no-registry construction in production code. |

### 2.3 Test Consumers

| # | Test | File Path | Action |
|:-:|:-----|:----------|:-------|
| T1 | `test_orchestrator_command_interception` | `dev/tests/core/test_orchestrator.py:288` | Update: remove `register_command` usage; assert parser-based command execution. |
| T2 | File tools fixture tests | `dev/tests/tools/test_file_tools.py:14` | Update: replace `WorkspaceContext` with `StrictWorkspaceManager`. |
| T3 | Shell tool fixture tests | `dev/tests/tools/test_shell_tool.py:15` | Update: replace `WorkspaceContext` with `StrictWorkspaceManager`. |
| T4 | `test_parse_prompt_json_execute_actions_repairs_object_shape` | `dev/tests/agent/test_schema.py:178` | Update/remove: no repair expected after schema contract hardening. |
| T5 | `test_file_session_manager_backfills_missing_desired_effort` | `dev/tests/session/test_session_manager.py:62` | Update expectation per new strictness policy for missing effort. |

### 2.4 Other References

| # | Reference | Location | Action |
|:-:|:----------|:---------|:-------|
| O1 | Spec mentions orchestrator `_handle_command` preserved | `dev/specs/01_agent_logic/04_multi_agent_definitions.md:345` | Update spec text to reflect removal of legacy fallback. |
| O2 | Internal spike report references legacy shims | `dev/reports/legacy_feature_leftovers_spike_2026-03-06.md` | Keep as historical artifact; no change required. |

### 2.5 Consumer Count Summary

| Category | Count | All Identified? |
|:---------|:-----:|:---------------:|
| Direct consumers | 8 | ? Yes |
| Indirect consumers | 2 | ? Yes |
| Test consumers | 5 | ? Yes |
| Other references | 2 | ? Yes |

**Search commands used**:
```powershell
Get-ChildItem -Path . -Recurse -File | Where-Object {$_.Extension -eq '.py'} |
  Select-String -Pattern '\bWorkspaceContext\b|\bregister_command\(|\b_handle_command\(|_COMMANDS|legacy tool/args|root_path\(|show_error_popup'

Get-ChildItem -Path . -Recurse -File -Force |
  Where-Object {$_.FullName -notmatch '\\.git\\|\\__pycache__\\|\\.pytest_cache\\|\\temp\\'} |
  Select-String -Pattern @('WorkspaceContext','register_command(','_handle_command(','_COMMANDS','show_error_popup','root_path(self)','legacy tool/args') -SimpleMatch
```

---

## 3. Migration Paths

### D1: `WorkspaceContext` -> `StrictWorkspaceManager` (or `SandboxWorkspaceManager` in runtime)

**Before** (deprecated):
```python
from agent_cli.core.runtime.tools.workspace import WorkspaceContext
workspace = WorkspaceContext(root_path=tmp_path)
```

**After** (replacement):
```python
from agent_cli.core.ux.interaction.strict import StrictWorkspaceManager
workspace = StrictWorkspaceManager(root_path=tmp_path)
```

**Migration notes**: Runtime bootstrap already uses strict+sandbox composition; test fixtures should match modern interfaces.

---

### D2: Orchestrator `register_command` and fallback path -> `CommandParser`

**Before** (deprecated):
```python
orchestrator.register_command("help", handler)
result = await orchestrator.handle_request("/help")
```

**After** (replacement):
```python
result = await command_parser.execute("/help")
# Orchestrator always delegates slash commands to parser.
```

**Migration notes**: Make parser mandatory for orchestrator construction or fail fast if missing.

---

### D3: Static popup `_COMMANDS` -> live `CommandRegistry`

**Before** (deprecated):
```python
popup = CommandPopup()  # falls back to static _COMMANDS
```

**After** (replacement):
```python
popup = CommandPopup(registry=command_registry)
```

**Migration notes**: Remove static fallback and `set_commands`; preserve rendering behavior from registry entries.

---

### D4: Schema legacy `tool/args` repair -> strict `decision.actions` contract

**Before** (deprecated):
```json
{"decision": {"type": "execute_actions", "tool": "foo", "args": {"x": 1}}}
```

**After** (replacement):
```json
{"decision": {"type": "execute_actions", "actions": [{"tool": "foo", "args": {"x": 1}}]}}
```

**Migration notes**: Update tests/fixtures to canonical shape; keep error messages actionable for invalid payloads.

---

### D5/D6: `root_path` alias -> `get_root()`

**Before** (deprecated):
```python
root = manager.root_path
```

**After** (replacement):
```python
root = manager.get_root()
```

**Migration notes**: Repo scan currently shows no real consumers for these aliases.

---

### D7: Temporary debug popup action -> removed

**Before** (deprecated):
```python
("shift+up", "show_error_popup", "Show Error Popup (Temp)")
```

**After** (replacement):
```python
# No debug-only keybinding in production bindings.
```

**Migration notes**: If debug preview is still desired, gate behind explicit debug mode only.

---

### D8: Session effort coercion fallback -> strict parsing policy

**Before** (deprecated):
```python
def _coerce_effort(value: Any) -> str:
    try:
        return normalize_effort(value).value
    except Exception:
        return EffortLevel.AUTO.value
```

**After** (replacement):
```python
# Option A (strict): raise on invalid values
# Option B (dev-safe): accept missing only, reject invalid strings
```

**Migration notes**: Choose strictness level in implementation PR; update `test_session_manager.py` accordingly.

---

## 4. Risk Assessment

| # | Risk | Likelihood | Impact | Detection | Mitigation |
|:-:|:-----|:----------:|:------:|:----------|:-----------|
| 1 | Missed consumer after shim removal | M | M | Failing tests/import errors | Exhaustive search + full `pytest -q dev/tests` |
| 2 | Hidden workflow depended on orchestrator fallback without parser | L | H | Runtime slash commands fail | Enforce parser presence at bootstrap and add explicit constructor guard |
| 3 | Schema strictness breaks old fixtures/sessions | M | M | SchemaValidationError in tests/replay | Migrate fixtures first; clear error messaging; rollback by revert |
| 4 | Session effort strictness causes session load failures | M | M | Session manager tests fail | Use staged strictness (missing->auto allowed first) |
| 5 | UI command popup misses commands after static fallback removal | L | M | TUI command popup regression tests/manual check | Ensure registry supplied in all app constructions |

### 4.1 Rollback Strategy

| Phase | Rollback Method | Estimated Rollback Time |
|:------|:----------------|:------------------------|
| Phase 1 | Revert warning/deprecation annotations only | < 5 min |
| Phase 2 | Revert migration commits (tests/consumers) | < 5 min |
| Phase 3 | Revert removal commit restoring deleted code | < 5 min |

---

## 5. Deprecation Phases

---

### Phase 1: Mark as Deprecated

**Goal**: All removal targets have explicit deprecation markers and replacement guidance; no behavior change yet.

**Prerequisites**: Consumer analysis complete (section 2).

#### Steps

1. **Add deprecation markers to all items in section 1.1**
   - Files: `workspace.py`, `orchestrator.py`, `command_popup.py`, `schema.py`, `strict.py`, `sandbox.py`, `app.py`, `file_store.py`
   - Details: docstring/inline comments and warning logs for targeted legacy paths.

2. **Verify no immediate breakage**
   - Run: `pytest -q dev/tests/core/test_orchestrator.py dev/tests/agent/test_schema.py dev/tests/session/test_session_manager.py dev/tests/tools/test_file_tools.py dev/tests/tools/test_shell_tool.py`
   - Expected: All pass.

#### Checkpoint

- [ ] All items from section 1.1 marked clearly deprecated
- [ ] Targeted regression pack passes
- [ ] Legacy-path warnings visible where expected

---

### Phase 2: Migrate Consumers

**Goal**: All code/tests switched to replacements; deprecated code still present but unused.

**Prerequisites**: Phase 1 checkpoint passed.

#### Steps

1. **Migrate direct consumers**
   - C1/C2: update test fixtures to `StrictWorkspaceManager`
   - C3: rewrite orchestrator test to parser flow
   - C5: ensure all popup construction paths pass `CommandRegistry`
   - C6/T4: update schema payload tests to canonical `actions`
   - C7/T5: align session tests with chosen strictness policy

2. **Migrate indirect consumers**
   - I1/I2: enforce parser/registry non-optional in construction paths

3. **Update tests**
   - Remove/update legacy-specific tests listed in section 2.3

4. **Update other references**
   - O1: update spec text about `_handle_command` legacy retention

5. **Run full test suite**
   - Run: `pytest -q dev/tests`

#### Checkpoint

- [ ] All consumers from section 2 migrated
- [ ] Full test suite passes
- [ ] Deprecated code remains but is unreferenced by source/tests

---

### Phase 3: Remove Deprecated Code

**Goal**: Delete all deprecated items and keep system green.

**Prerequisites**: Phase 2 checkpoint passed and zero-reference verification passes.

#### Steps

1. **Verify zero references**
   ```powershell
   Get-ChildItem -Path . -Recurse -File | Where-Object {$_.Extension -eq '.py'} |
     Select-String -Pattern '\bWorkspaceContext\b|\bregister_command\(|\b_handle_command\(|_COMMANDS|legacy tool/args|root_path\(|show_error_popup'
   ```
   - Expected: zero source hits for removed items.

2. **Delete deprecated files/code**
   - D1: delete `agent_cli/core/runtime/tools/workspace.py` (or remove class/export and keep file only if needed)
   - D2: remove legacy command dict API and fallback branch in orchestrator
   - D3: remove static list and fallback-only APIs from `command_popup.py`
   - D4: remove legacy `tool/args` repair in schema
   - D5/D6: remove `root_path` alias properties
   - D7: remove debug keybinding and `action_show_error_popup`
   - D8: remove broad fallback coercion per selected strict policy

3. **Remove re-exports and stale imports**
   - Confirm no import paths reference removed wrapper module/class.

4. **Remove deprecation-only tests**
   - Remove/replace tests that only validated legacy shim behavior.

5. **Run full test suite**
   - Run: `pytest -q dev/tests`

#### Checkpoint

- [ ] All deprecated items removed
- [ ] Zero references remain (verification command clean)
- [ ] All tests pass
- [ ] No dead imports or unused variables: `python -m pytest -q dev/tests` plus local lint command if configured

---

## 6. File Change Summary

| # | Action | File Path | Phase | Description |
|:-:|:------:|:----------|:-----:|:------------|
| 1 | MODIFY | `agent_cli/core/runtime/tools/workspace.py` | 1/3 | Mark then remove deprecated wrapper |
| 2 | MODIFY | `agent_cli/core/runtime/orchestrator/orchestrator.py` | 1/2/3 | Remove legacy command fallback API |
| 3 | MODIFY | `agent_cli/core/ux/tui/views/common/command_popup.py` | 1/2/3 | Remove static fallback, enforce registry |
| 4 | MODIFY | `agent_cli/core/runtime/agents/schema.py` | 1/2/3 | Remove legacy `tool/args` repair |
| 5 | MODIFY | `agent_cli/core/ux/interaction/strict.py` | 1/3 | Remove `root_path` alias |
| 6 | MODIFY | `agent_cli/core/ux/interaction/sandbox.py` | 1/3 | Remove `root_path` alias |
| 7 | MODIFY | `agent_cli/core/ux/tui/app.py` | 1/3 | Remove temp debug binding/action |
| 8 | MODIFY | `agent_cli/core/runtime/session/file_store.py` | 1/2/3 | Tighten effort parsing policy |
| 9 | MODIFY | `dev/tests/core/test_orchestrator.py` | 2 | Migrate away from `register_command` |
| 10 | MODIFY | `dev/tests/tools/test_file_tools.py` | 2 | Replace `WorkspaceContext` |
| 11 | MODIFY | `dev/tests/tools/test_shell_tool.py` | 2 | Replace `WorkspaceContext` |
| 12 | MODIFY | `dev/tests/agent/test_schema.py` | 2 | Remove/update legacy repair expectations |
| 13 | MODIFY | `dev/tests/session/test_session_manager.py` | 2 | Align desired_effort policy expectations |
| 14 | MODIFY | `dev/specs/01_agent_logic/04_multi_agent_definitions.md` | 2 | Update spec text to new behavior |

---

## 7. Post-Removal Verification

- [ ] Zero references to removed code:
  - `Get-ChildItem -Path . -Recurse -File | Where-Object {$_.Extension -eq '.py'} | Select-String -Pattern '\bWorkspaceContext\b|\bregister_command\(|\b_handle_command\(|_COMMANDS|legacy tool/args|root_path\(|show_error_popup'`
- [ ] Full test suite passes: `pytest -q dev/tests`
- [ ] No lint/type errors: `N/A - no repo-standard lint/type command confirmed in this spike`
- [ ] Application starts without errors: `python -m agent_cli --help` (or project-standard launch command)
- [ ] Documentation updated (no stale behavior claims in spec/docs for removed paths)
- [ ] Runtime sanity check: `/help` still works via parser; command popup still lists commands; session load behavior matches new effort policy

---

## Appendix A: Version Control References

| Item | Last Commit | Branch/Tag |
|:-----|:------------|:-----------|
| D1-D8 legacy compatibility cleanup set | Current HEAD before cleanup PR merge | Active branch at cleanup start date (2026-03-06) |

## Appendix B: References

- `dev/reports/legacy_feature_leftovers_spike_2026-03-06.md`
- `dev/templates/deprecation_plan.md`
- `agent_cli/core/runtime/tools/workspace.py`
- `agent_cli/core/runtime/orchestrator/orchestrator.py`
- `agent_cli/core/ux/tui/views/common/command_popup.py`
- `agent_cli/core/runtime/agents/schema.py`
- `agent_cli/core/ux/interaction/strict.py`
- `agent_cli/core/ux/interaction/sandbox.py`
- `agent_cli/core/ux/tui/app.py`
- `agent_cli/core/runtime/session/file_store.py`
