# Deprecation & Removal Plan: {TITLE}

<!--
=============================================================================
AGENT INSTRUCTIONS (remove this block after completing the plan)
=============================================================================
This template guides you through safely deprecating and removing code —
the INVERSE of a feature implementation plan. Removing code is more
dangerous than adding it because you must ensure nothing still depends
on what you're removing.

Follow these rules:

1. MAP ALL CONSUMERS FIRST — Before deprecating anything, find EVERY
   consumer of the code being removed. This includes: direct imports,
   indirect usage via re-exports, tests, documentation, config files,
   scripts, CI/CD pipelines, and external consumers (if any).
   §2 must be exhaustive.

2. REPLACE ALL {PLACEHOLDERS} — Every {PLACEHOLDER} must be replaced with
   real, specific values. If a section is genuinely not applicable, write
   "N/A" with a one-line justification.

3. DEPRECATE BEFORE REMOVING — Never delete code in one step. Phase 1
   marks things as deprecated (with warnings or alternatives). Phase 2+
   removes them after consumers have migrated. This gives consumers a
   migration window.

4. PROVIDE MIGRATION PATHS — For every deprecated item, document what
   consumers should use instead. "Just stop using it" is NOT a migration
   path unless the functionality is truly no longer needed.

5. VERIFY ZERO CONSUMERS — Before the final removal phase, verify that
   NOTHING references the deprecated code. Use grep, import analysis,
   and test runs to confirm.

6. EVERY PHASE NEEDS A CHECKPOINT — Define what "done" looks like for each
   phase. The system must work at every step.

7. TEST AFTER REMOVAL — Removing code can silently break things that
   aren't well-tested. Run the full test suite after every removal step.

8. PRESERVE HISTORY — If the removed code contains valuable logic or
   patterns, note where it can be found in version control (commit hash)
   in the Appendix.
=============================================================================
-->

---

## 1. Overview

| Field              | Value                                              |
|:-------------------|:---------------------------------------------------|
| **What**           | {What is being deprecated/removed — module, class, function, API, feature} |
| **Source**         | {Code review or decision that triggered this — e.g., `code_review_xyz.md`, finding F4. Write "N/A — standalone" if not triggered by a review} |
| **Why**            | {Reason for removal — e.g., "Replaced by new implementation", "No longer used", "Security risk"} |
| **Replacement**    | {What consumers should use instead — or "None — functionality is being retired"} |
| **Scope**          | {Bounded description of what IS and IS NOT being removed} |
| **Estimated Effort** | {S / M / L / XL with justification}              |
| **Risk Level**     | {Low / Medium / High — with one-line reason}       |

### 1.1 Items to Deprecate

<!-- Complete inventory of everything being removed -->

| # | Item | Type | File Path | Status |
|:-:|:-----|:-----|:----------|:------:|
| D1 | {e.g., `LegacyAuthService`} | {Class} | {`src/services/legacy_auth.py`} | {Active / Already deprecated / Unused} |
| D2 | {e.g., `format_response()`} | {Function} | {`src/utils/formatters.py:L45`} | {status} |
| D3 | {e.g., `legacy_auth.py`} | {File} | {`src/services/legacy_auth.py`} | {status} |

### 1.2 Success Criteria

- [ ] All consumers migrated to replacement (§3 migration paths followed)
- [ ] All deprecated items removed from codebase
- [ ] Zero references to removed code: `{grep command}`
- [ ] All tests pass after removal
- [ ] {Additional criterion}

### 1.3 Out of Scope

- {Item 1 — e.g., "Removing `LegacyDBAdapter` — separate deprecation plan"}
- {Item 2}

---

## 2. Consumer Analysis

<!-- AGENT: This is the MOST CRITICAL section. Find EVERY consumer before
     proceeding. Miss one consumer and the removal will break things.

     Search methods you MUST use:
     - grep/ripgrep for imports and references
     - IDE "find usages" equivalent
     - Check re-exports (e.g., __init__.py files)
     - Check test files
     - Check documentation
     - Check config/CI files
     - Check scripts and CLI entry points -->

### 2.1 Direct Consumers

<!-- Code that directly imports or calls the deprecated items -->

| # | Consumer | File Path | Usage | Migration Action |
|:-:|:---------|:----------|:------|:-----------------|
| C1 | {e.g., "`AuthMiddleware`"} | {`src/middleware/auth.py:L12`} | {e.g., "Imports `LegacyAuthService`, calls `.authenticate()`"} | {e.g., "Replace with `AuthService.verify()`"} |
| C2 | {consumer} | {path:line} | {usage} | {action} |

### 2.2 Indirect Consumers

<!-- Code that uses the deprecated items via re-exports, dependency injection,
     or dynamic references -->

| # | Consumer | File Path | How It Consumes | Migration Action |
|:-:|:---------|:----------|:----------------|:-----------------|
| I1 | {e.g., "`__init__.py` re-export"} | {`src/services/__init__.py:L5`} | {e.g., "`from .legacy_auth import LegacyAuthService`"} | {e.g., "Remove re-export in final phase"} |

### 2.3 Test Consumers

<!-- Tests that reference the deprecated items -->

| # | Test | File Path | Action |
|:-:|:-----|:----------|:-------|
| T1 | {e.g., `test_legacy_auth_flow`} | {`tests/test_auth.py:L34`} | {e.g., "Delete — tests functionality being removed"} |
| T2 | {e.g., `test_middleware_auth`} | {`tests/test_middleware.py:L12`} | {e.g., "Update — switch to new `AuthService` in test"} |

### 2.4 Other References

<!-- Documentation, config files, CI/CD, scripts, comments, etc. -->

| # | Reference | Location | Action |
|:-:|:----------|:---------|:-------|
| O1 | {e.g., "README mentions legacy auth setup"} | {`README.md:L45`} | {e.g., "Update documentation"} |
| O2 | {e.g., "CI config imports legacy module"} | {`.github/workflows/test.yml:L23`} | {e.g., "Remove reference"} |

### 2.5 Consumer Count Summary

| Category | Count | All Identified? |
|:---------|:-----:|:---------------:|
| Direct consumers | {#} | {✅ Yes / ❌ In progress} |
| Indirect consumers | {#} | {✅ Yes / ❌ In progress} |
| Test consumers | {#} | {✅ Yes / ❌ In progress} |
| Other references | {#} | {✅ Yes / ❌ In progress} |

**Search commands used**:
```
{e.g., rg "LegacyAuthService|legacy_auth" src/ tests/ --type py}
{e.g., rg "legacy_auth" . --type-not py  (for non-Python references)}
```

---

## 3. Migration Paths

<!-- For each deprecated item, define what consumers should do instead. -->

### D1: {Item Name} → {Replacement}

**Before** (deprecated):
```{language}
{Current usage pattern that consumers must stop using}
```

**After** (replacement):
```{language}
{New usage pattern that consumers should adopt}
```

**Migration notes**: {Any gotchas, behavioral differences, or special considerations}

---

### D2: {Item Name} → {Replacement}

**Before** (deprecated):
```{language}
{current pattern}
```

**After** (replacement):
```{language}
{new pattern}
```

**Migration notes**: {notes}

---

<!-- Add more migration paths as needed -->

---

## 4. Risk Assessment

| # | Risk | Likelihood | Impact | Detection | Mitigation |
|:-:|:-----|:----------:|:------:|:----------|:-----------|
| 1 | {e.g., "Missed consumer breaks at runtime"} | {L/M/H} | {L/M/H} | {e.g., "ImportError or AttributeError on startup"} | {e.g., "Exhaustive grep search in §2, run full test suite"} |
| 2 | {e.g., "External consumer not in this repo"} | {L/M/H} | {L/M/H} | {detection} | {mitigation} |

### 4.1 Rollback Strategy

| Phase | Rollback Method | Estimated Rollback Time |
|:------|:----------------|:------------------------|
| Phase 1 | {e.g., "Remove deprecation warnings — no code deleted yet"} | {< 5 min} |
| Phase 2 | {e.g., "git revert — consumers already migrated"} | {< 5 min} |
| Phase 3 | {e.g., "git revert — restore deleted files"} | {< 5 min} |

---

## 5. Deprecation Phases

---

### Phase 1: Mark as Deprecated

**Goal**: {e.g., "All deprecated items are marked with warnings; no code deleted yet; all consumers identified"}

**Prerequisites**: {e.g., "Consumer analysis (§2) is complete and verified"}

#### Steps

1. **Add deprecation markers to all items in §1.1**
   - Files: {list all files}
   - Details: {e.g., "Add deprecation warnings, docstring notices, or decorator markers"}
   ```{language}
   {Example deprecation marker — e.g., warnings.warn("LegacyAuthService is deprecated, use AuthService", DeprecationWarning)}
   ```

2. **Verify no immediate breakage**
   - Run: `{full test command}`
   - Expected: All tests pass (deprecation warnings may appear, but nothing breaks)

#### Checkpoint

- [ ] All items from §1.1 have deprecation markers
- [ ] All tests pass
- [ ] Deprecation warnings visible in test output (if using runtime warnings)

---

### Phase 2: Migrate Consumers

**Goal**: {e.g., "All consumers from §2 switched to replacement; deprecated code still exists but is unused"}

**Prerequisites**: {Phase 1 checkpoint passed}

#### Steps

1. **Migrate direct consumers (C# from §2.1)**
   - {C1}: Update `{file:line}` — {action}
   - {C2}: Update `{file:line}` — {action}

2. **Migrate indirect consumers (I# from §2.2)**
   - {I1}: Update `{file:line}` — {action}

3. **Update tests (T# from §2.3)**
   - {T1}: {action — delete or update}
   - {T2}: {action}

4. **Update other references (O# from §2.4)**
   - {O1}: {action}

5. **Run full test suite**
   - Expected: All tests pass

#### Checkpoint

- [ ] All consumers from §2 migrated
- [ ] All tests pass
- [ ] Deprecated code is still present but has zero live references

---

### Phase 3: Remove Deprecated Code

**Goal**: {e.g., "All deprecated items deleted; codebase is clean"}

**Prerequisites**: {Phase 2 checkpoint passed; zero references verified}

#### Steps

1. **Verify zero references**
   ```
   {grep/search command to confirm nothing references the deprecated items}
   ```
   - Expected: Zero results

2. **Delete deprecated files/code**
   - {D1}: Delete `{file}` or remove lines {L##-L##}
   - {D2}: Delete `{file}` or remove lines {L##-L##}

3. **Remove re-exports and __init__ references**
   - {Files to update}

4. **Remove deprecation-only tests**
   - {Tests that only tested deprecated functionality}

5. **Run full test suite**
   - Expected: All tests pass

#### Checkpoint

- [ ] All deprecated items removed
- [ ] Zero references remain: `{verification command}`
- [ ] All tests pass
- [ ] No dead imports or unused variables: `{lint command}`

---

## 6. File Change Summary

| # | Action | File Path | Phase | Description |
|:-:|:------:|:----------|:-----:|:------------|
| 1 | MODIFY | `{path}` | {1} | {Add deprecation warning} |
| 2 | MODIFY | `{path}` | {2} | {Migrate to replacement} |
| 3 | DELETE | `{path}` | {3} | {Remove deprecated file} |
| 4 | MODIFY | `{path}` | {3} | {Remove re-export} |

---

## 7. Post-Removal Verification

- [ ] Zero references to removed code: `{grep command}`
- [ ] Full test suite passes: `{command}`
- [ ] No lint/type errors: `{command}`
- [ ] Application starts without errors: `{command}`
- [ ] Documentation updated (no references to removed features)
- [ ] {Any domain-specific validation}

---

## Appendix A: Version Control References

<!-- Where the removed code can be found if ever needed again -->

| Item | Last Commit | Branch/Tag |
|:-----|:------------|:-----------|
| {e.g., `LegacyAuthService`} | {e.g., "commit `abc123` or 'current HEAD before removal'"} | {e.g., "`main` as of 2026-03-06"} |

## Appendix B: References

- {Reference 1 — e.g., "Decision to deprecate: code review `code_review_xyz.md`"}
- {Reference 2 — e.g., "Replacement implementation: `feature_plan_new_auth.md`"}
