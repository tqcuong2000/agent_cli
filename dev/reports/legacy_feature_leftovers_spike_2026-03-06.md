# Spike / Research Plan: Legacy Feature Leftovers Audit (Comment-Driven)

---

## 1. Overview

| Field              | Value                                              |
|:-------------------|:---------------------------------------------------|
| **Topic**          | Identify legacy/deprecated code paths in `agent_cli` that are already replaced and may be removable. |
| **Source**         | N/A - exploratory |
| **Motivation**     | Reduce maintenance cost and ambiguity by confirming which compatibility shims are still needed. |
| **Time-Box**       | 2 hours (comment-led static analysis only) |
| **Decision Blocked** | Cannot scope a safe deprecation/removal plan without knowing which legacy code is still exercised. |

### 1.1 Success Criteria

- [x] All questions in section 1.2 have answers (or are documented as "still unknown" with reasoning)
- [x] A clear recommendation exists in section 4.1
- [x] The recommended next template is identified in section 4.3

### 1.2 Questions to Answer

| # | Question | Priority | Why It Matters |
|:-:|:---------|:--------:|:---------------|
| Q1 | Which code comments in `agent_cli` indicate legacy/deprecated behavior? | Must | This is the requested discovery method and seed set. |
| Q2 | For each legacy marker, what replaced it? | Must | Determines whether code is truly leftover vs active transition path. |
| Q3 | Which legacy paths are likely removable now vs should be retained temporarily? | Must | Needed before drafting a deprecation plan. |

### 1.3 Out of Scope

- Full implementation/removal of legacy paths.
- Runtime telemetry-based confirmation in production environments.
- Non-code docs/spec cleanup (only used as context when needed).

---

## 2. Investigation Plan

### 2.1 Research Tasks

| # | Task | Answers | Method | Sources |
|:-:|:-----|:-------:|:-------|:--------|
| T1 | Scan `agent_cli/**/*.py` for legacy/deprecation comments and terms | Q1 | PowerShell `Select-String` keyword search | `agent_cli/**/*.py` |
| T2 | Trace replacements by reading bootstrap/wiring and call sites | Q2 | Read constructors, registry wiring, and references | `bootstrap.py`, `orchestrator.py`, `command_popup.py`, `workspace.py` |
| T3 | Classify removability and risk | Q3 | Check production usage vs test-only usage | source files + `dev/tests` references |

### 2.2 Proof-of-Concept Scope (if applicable)

| Aspect | Value |
|:-------|:------|
| **What it proves** | N/A |
| **What it does NOT prove** | N/A |
| **Location** | N/A |
| **Success condition** | N/A |

---

## 3. Findings

---

### Finding 1: Deprecated `WorkspaceContext` wrapper appears test-only

| Attribute | Value |
|:----------|:------|
| **Answers** | Q1, Q2, Q3 |
| **Source** | `agent_cli/core/runtime/tools/workspace.py`, `dev/tests/tools/test_file_tools.py`, `dev/tests/tools/test_shell_tool.py` |
| **Confidence** | High |

**Summary**: The module header explicitly marks `WorkspaceContext` as deprecated and says new code should use `StrictWorkspaceManager`. In production bootstrap, workspace wiring uses `StrictWorkspaceManager` + `SandboxWorkspaceManager`, not `WorkspaceContext`. Repository-wide references show `WorkspaceContext` used only in tests.

**Evidence**:
```python
# agent_cli/core/runtime/tools/workspace.py
"""Deprecated workspace wrapper kept for backward compatibility."""

class WorkspaceContext(StrictWorkspaceManager):
    """Thin wrapper around ``StrictWorkspaceManager``.
    Existing code still imports ``WorkspaceContext`` from this module.
    New code should import ``StrictWorkspaceManager`` ...
    """
```

```python
# agent_cli/core/infra/registry/bootstrap.py
strict_workspace = StrictWorkspaceManager(...)
workspace = SandboxWorkspaceManager(strict_workspace)
```

**Implications**: Strong candidate for staged removal once tests are migrated to instantiate modern workspace managers directly.

---

### Finding 2: Legacy dict-based slash-command path is fallback only

| Attribute | Value |
|:----------|:------|
| **Answers** | Q1, Q2, Q3 |
| **Source** | `agent_cli/core/runtime/orchestrator/orchestrator.py`, `agent_cli/core/infra/registry/bootstrap.py`, `dev/tests/core/test_orchestrator.py` |
| **Confidence** | High |

**Summary**: Orchestrator keeps a legacy `_commands` dictionary and `register_command()` API, but request handling prefers `CommandParser` when present. Bootstrap always wires `command_parser`, indicating modern runtime path is parser/registry based. Legacy path remains used in unit tests and as defensive fallback.

**Evidence**:
```python
# orchestrator.py
# Legacy slash-command registry (kept for backward compat)
self._commands: Dict[str, CommandHandler] = {}

if self._command_parser is not None:
    result = await self._command_parser.execute(text)
    ...
    return result.message

# Fallback to legacy dict-based commands
return await self._handle_command(text)
```

```python
# bootstrap.py
context.orchestrator = Orchestrator(..., command_parser=context.command_parser, ...)
```

**Implications**: Not dead yet, but likely removable after migrating tests and any non-bootstrap construction paths to always provide parser support.

---

### Finding 3: Static command popup list is a compatibility fallback

| Attribute | Value |
|:----------|:------|
| **Answers** | Q1, Q2, Q3 |
| **Source** | `agent_cli/core/ux/tui/views/common/command_popup.py`, `agent_cli/core/ux/tui/app.py` |
| **Confidence** | Medium |

**Summary**: `CommandPopup` has a static `_COMMANDS` list explicitly marked to be replaced by dynamic registry. The popup already uses `CommandRegistry` when provided. TUI app constructs popup with live registry from app context.

**Evidence**:
```python
# command_popup.py
# Static command registry (will be replaced by dynamic registry later)
_COMMANDS = [...]

if self._registry is not None:
    return [ ... for cmd in self._registry.all() ]
# fallback: static list
```

```python
# app.py
registry = getattr(self.app_context, "command_registry", None)
self.command_popup = CommandPopup(registry=registry)
```

**Implications**: Low-to-medium risk cleanup candidate. Keep fallback only if tests or edge construction paths need it; otherwise remove static duplication.

---

### Finding 4: Multi-action schema still repairs legacy payload shape

| Attribute | Value |
|:----------|:------|
| **Answers** | Q1, Q2, Q3 |
| **Source** | `agent_cli/core/runtime/agents/schema.py` |
| **Confidence** | High |

**Summary**: For `execute_actions`, validator repairs legacy `decision.tool`/`decision.args` into single-item `decision.actions`. This indicates ongoing compatibility with older model outputs/sessions while moving toward canonical `actions` list format.

**Evidence**:
```python
# schema.py
legacy_tool = str(decision.get("tool", "")).strip()
legacy_args = decision.get("args", {})
...
raw_actions = [{"tool": legacy_tool, "args": legacy_args}]
```

**Implications**: Transitional compatibility likely still intentional. Removal should wait until replay/canary confidence demonstrates no legacy payload producers remain.

---

### Finding 5: Minor legacy aliases/fallbacks remain, with unclear immediate payoff

| Attribute | Value |
|:----------|:------|
| **Answers** | Q1, Q3 |
| **Source** | `agent_cli/core/ux/interaction/strict.py`, `agent_cli/core/ux/interaction/sandbox.py`, `agent_cli/core/runtime/session/file_store.py`, `agent_cli/core/ux/tui/app.py` |
| **Confidence** | Medium |

**Summary**: There are backward-compatible aliases (`root_path`) and persistence fallbacks (`_coerce_effort`) for legacy session payloads. `app.py` also labels some UI actions as legacy, including a temporary debug popup binding (`shift+up`). These are low-complexity but should be cleaned only with clear compatibility policy.

**Evidence**:
```python
# strict.py / sandbox.py
"""Backward-compatible alias used by legacy tool code/tests."""
def root_path(self) -> Path: ...

# file_store.py
"""Parse persisted effort values with backward-compatible fallback."""
```

```python
# app.py
# -- Legacy actions -------------------------------------------
("shift+up", "show_error_popup", "Show Error Popup (Temp)")
```

**Implications**: Keep until policy says old sessions/tests are out of support; debug-only binding can be removed sooner if undesired in production UX.

---

### Finding 6: Runtime probes confirm modern path preference and legacy shim behavior

| Attribute | Value |
|:----------|:------|
| **Answers** | Q2, Q3 |
| **Source** | Runtime probe script executed via inline Python; targeted pytest runs |
| **Confidence** | High |

**Summary**: Runtime checks confirm that the orchestrator uses `CommandParser` path when available and does not hit legacy dict fallback in normal bootstrap wiring. When parser is disabled, legacy fallback path remains functional. Schema legacy payload repair and session effort fallback both trigger as designed. Targeted tests around these surfaces pass.

**Evidence**:
```json
{
  "orchestrator_parser_path": {
    "result_non_empty": true,
    "legacy_fallback_calls": 0
  },
  "orchestrator_legacy_path": {
    "result": "legacy-help-ok",
    "legacy_fallback_calls": 1
  },
  "schema_legacy_repair": {
    "decision": "execute_action",
    "action_count": 1,
    "warning_emitted": true
  },
  "session_effort_fallback": {
    "invalid_to": "auto",
    "none_to": "auto"
  }
}
```

```text
pytest -q dev/tests/core/test_orchestrator.py dev/tests/agent/test_schema.py dev/tests/session/test_session_manager.py dev/tests/tools/test_file_tools.py dev/tests/tools/test_shell_tool.py
68 passed in 6.89s

pytest -vv dev/tests -k "legacy or backward or deprecated or removed"
3 passed, 465 deselected
```

**Implications**: Current runtime is already on replacement paths by default, while compatibility shims are still active and test-covered. This supports phased removal with telemetry gates rather than immediate hard deletion.

---

## 4. Conclusion

### 4.1 Answers Summary

| Question | Answer | Confidence | Finding |
|:--------:|:-------|:----------:|:-------:|
| Q1 | Legacy/deprecation markers are concentrated in workspace wrapper, orchestrator command handling, command popup fallback, schema payload repair, and a few compatibility aliases. | High | F1-F5 |
| Q2 | Replacements are present: `StrictWorkspaceManager`/`SandboxWorkspaceManager`, `CommandRegistry`+`CommandParser`, dynamic popup registry path, and canonical `decision.actions` payload. | High | F1-F4 |
| Q3 | Best immediate removal candidates: deprecated `WorkspaceContext` (after test migration) and debug/legacy UI actions; defer schema legacy repair and parser fallback until compatibility cutover criteria are met. | Medium | F1-F5 |

### 4.2 Remaining Unknowns

| Unknown | Why It Remains | Impact on Recommendation | Suggested Follow-Up |
|:--------|:---------------|:-------------------------|:--------------------|
| Whether any external/non-test runtime path instantiates `Orchestrator` without `CommandParser` | Static scan only; no runtime telemetry | Medium | Add one-time runtime warning metric when legacy `_handle_command` executes |
| Whether legacy session/tool payloads still appear in real user data | No production/session corpus sampled | Medium | Sample stored sessions and count legacy `tool/args` payload usage |
| Whether `WorkspaceContext` is imported outside this repo | Repo-local scan cannot prove ecosystem usage | Low | Announce deprecation window before deletion |

### 4.3 Recommendation

**Recommended approach**: Start with a low-risk deprecation plan that removes or migrates test-only and debug leftovers first, while instrumenting usage of higher-risk compatibility shims (`Orchestrator` legacy command fallback and schema legacy repair). Use data from instrumentation/session sampling to decide hard removal timing for remaining legacy paths.

**Next step**: Use **`deprecation_plan.md`** to create a phased removal plan.

| Aspect | Recommendation |
|:-------|:---------------|
| **Approach** | Phase 1: migrate tests off `WorkspaceContext`; remove temp debug binding. Phase 2: add metrics/warnings on legacy command/schema fallback. Phase 3: remove fallbacks once observed usage is zero for a defined window. |
| **Plan Template** | `deprecation_plan.md` |
| **Key Constraints** | Preserve replay compatibility until legacy payload usage is confirmed zero. |
| **Risks to Address in Plan** | Silent breakage for old tests/sessions if compatibility shims are removed prematurely. |

### 4.4 Alternatives Considered

| Alternative | Why Not Recommended | Would Reconsider If... |
|:------------|:--------------------|:-----------------------|
| Remove all legacy paths in one PR | High regression risk for tests and old session replay | Strong telemetry proves zero legacy usage and full test migration completed |
| Keep all shims indefinitely | Ongoing maintenance and conceptual complexity | Compatibility window is explicitly open-ended by product policy |

---

## 5. Artifacts Produced

| Artifact | Path | Disposable? | Notes |
|:---------|:-----|:-----------:|:------|
| Legacy feature spike report | `dev/reports/legacy_feature_leftovers_spike_2026-03-06.md` | No | Input to a deprecation plan and cleanup backlog |
| Runtime probe output | Inline Python execution in terminal session | Yes | Confirms parser-vs-legacy routing and schema/session fallback behavior |
| Targeted legacy test run | `pytest` commands listed in Finding 6 | Yes | Confirms regression surface for compatibility paths |

---

## Appendix: Sources Consulted

- `agent_cli/core/runtime/tools/workspace.py`
- `agent_cli/core/infra/registry/bootstrap.py`
- `agent_cli/core/runtime/orchestrator/orchestrator.py`
- `agent_cli/core/ux/tui/views/common/command_popup.py`
- `agent_cli/core/runtime/agents/schema.py`
- `agent_cli/core/ux/interaction/strict.py`
- `agent_cli/core/ux/interaction/sandbox.py`
- `agent_cli/core/runtime/session/file_store.py`
- `agent_cli/core/ux/tui/app.py`
- `dev/tests/core/test_orchestrator.py`
- `dev/tests/session/test_session_commands.py`
- `dev/tests/tools/test_file_tools.py`
- `dev/tests/tools/test_shell_tool.py`
