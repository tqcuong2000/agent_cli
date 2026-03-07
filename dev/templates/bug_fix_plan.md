# Bug Fix Plan: {TITLE}

<!--
=============================================================================
AGENT INSTRUCTIONS (remove this block after completing the plan)
=============================================================================
This template guides you through investigating and fixing a bug.
A bug is code that DOES NOT behave as intended. This is fundamentally
different from a refactor (working code that needs better structure) or
a feature (behavior that doesn't exist yet).

Follow these rules:

1. REPRODUCE FIRST — You cannot fix what you cannot reproduce. Before doing
   anything else, create a reliable reproduction of the bug. If you can't
   reproduce it, say so in §2.1 and investigate why.

2. UNDERSTAND THE EXPECTED BEHAVIOR — Before looking at code, be crystal
   clear about what SHOULD happen. Document this in §1.1. If the expected
   behavior is ambiguous, ask the user to clarify.

3. FIND THE ROOT CAUSE, NOT JUST THE SYMPTOM — A fix that patches the
   symptom without addressing the root cause will break again. §3 must
   identify the actual root cause with evidence.

4. REPLACE ALL {PLACEHOLDERS} — Every {PLACEHOLDER} must be replaced with
   real, specific values. If a section is genuinely not applicable, write
   "N/A" with a one-line justification.

5. MINIMAL FIX — Change the least amount of code necessary to fix the bug.
   Resist the urge to refactor or improve adjacent code. If you find other
   issues during investigation, note them in §6 but do NOT fix them here.

6. PROVE THE FIX — Every fix must have a test that:
   a) FAILS before the fix (reproduces the bug)
   b) PASSES after the fix (proves the bug is resolved)
   If you can't write such a test, your fix is unverified.

7. CHECK FOR SIBLINGS — The same root cause may produce bugs elsewhere.
   §3.3 must assess whether the bug pattern exists in other locations.

8. DON'T BREAK OTHER THINGS — Run the full test suite after the fix.
   A fix that introduces new failures is not a fix.
=============================================================================
-->

---

## 1. Bug Report

| Field              | Value                                              |
|:-------------------|:---------------------------------------------------|
| **Summary**        | {One sentence describing the bug}                  |
| **Source**         | {Code review or report that identified this — e.g., `code_review_xyz.md`, finding F2. Write "N/A — direct report" if not from a review} |
| **Severity**       | {Critical / High / Medium / Low — see definitions below} |
| **Discovered By**  | {e.g., "User report", "Test failure", "Code review F3", "Runtime error log"} |
| **Affected Component** | {Module, class, or function where the bug manifests} |

<!--
Severity definitions:
- CRITICAL: System crash, data loss, security vulnerability, or complete feature failure
- HIGH: Major feature broken, no workaround available
- MEDIUM: Feature partially broken, workaround exists
- LOW: Minor incorrect behavior, cosmetic, or edge case only
-->

### 1.1 Expected vs. Actual Behavior

| Aspect | Description |
|:-------|:------------|
| **Expected** | {What SHOULD happen — be precise about inputs, outputs, and conditions} |
| **Actual** | {What ACTUALLY happens — include exact error messages, wrong outputs, or incorrect state} |
| **Conditions** | {When does this occur — specific inputs, environment, timing, or sequence of actions} |

### 1.2 Impact Assessment

- **Who is affected**: {e.g., "All users", "Only when using feature X", "Only on Windows"}
- **How often**: {e.g., "Every time", "Intermittent (~30%)", "Only under load"}
- **Workaround exists**: {Yes — describe it / No}

---

## 2. Reproduction

<!-- AGENT: This is your FIRST task. Do not look at code until you can reproduce the bug. -->

### 2.1 Reproduction Steps

<!-- Exact steps to trigger the bug. Must be repeatable. -->

1. {Step 1 — e.g., "Start the application with `python main.py`"}
2. {Step 2 — e.g., "Call `service.process(input_data)` with `input_data = {'key': None}`"}
3. {Step 3 — e.g., "Observe: `TypeError: 'NoneType' has no attribute 'strip'` at `processor.py:L47`"}

**Reproduction rate**: {e.g., "100% — reproduces every time" / "~50% — timing dependent"}

### 2.2 Minimal Reproduction

<!-- Reduce to the simplest possible case that triggers the bug -->

```{language}
{Minimal code/command/test that reproduces the bug}
```

**Result**:
```
{Exact output, error message, or stack trace}
```

### 2.3 Environment Details (if relevant)

<!-- Include only if the bug is environment-specific -->

| Factor | Value |
|:-------|:------|
| {e.g., "OS"} | {e.g., "Windows 11"} |
| {e.g., "Python version"} | {e.g., "3.12.1"} |
| {e.g., "Dependency version"} | {e.g., "requests==2.31.0"} |

---

## 3. Root Cause Analysis

<!-- AGENT: Do NOT write this section until you have:
     1. Reproduced the bug (§2)
     2. Read the relevant source code thoroughly
     3. Traced the execution path from input to failure point -->

### 3.1 Investigation Trail

<!-- Document your investigation path. This helps future readers understand
     how the root cause was found, not just what it is. -->

1. **Started at**: {e.g., "Error stack trace points to `processor.py:L47`"}
2. **Traced to**: {e.g., "`processor.py:L47` calls `data.strip()`, but `data` comes from `get_data()` at L32"}
3. **Found**: {e.g., "`get_data()` returns `None` when key is missing from config, instead of raising an error"}
4. **Root cause**: {e.g., "Missing null check in `get_data()` — returns `dict.get(key)` without default, which returns `None` for missing keys"}

### 3.2 Root Cause

| Attribute | Value |
|:----------|:------|
| **Location** | {`file/path.ext:L##`} |
| **Cause** | {Precise description — what is wrong with this code and why} |
| **Introduced By** | {If known — e.g., "commit abc123" or "initial implementation" or "unknown"} |

**The buggy code**:
```{language}
{Exact code snippet containing the bug — copy from source with line numbers}
```

**Why it fails**:
{1-3 sentences explaining the logical error — e.g., "The function uses `dict.get(key)` which returns
`None` for missing keys, but the caller at L47 assumes a non-None string and calls `.strip()` on it."}

### 3.3 Sibling Check

<!-- Does the same bug pattern exist elsewhere in the codebase? -->

| Location | Same Pattern? | Also Buggy? |
|:---------|:--------------|:------------|
| {`path/to/similar_code.py:L##`} | {Yes/No — describe} | {Yes — needs fix / No — has guard / N/A} |

**Search performed**: {e.g., "`grep -rn 'dict.get(' src/` — checked all 12 occurrences"}

---

## 4. Fix Design

### 4.1 Fix Strategy

{1-3 sentences describing the approach — e.g., "Add a null check in `get_data()` that raises
`KeyError` with a descriptive message when the key is missing. This matches the convention used
in `get_config()` at `config.py:L23`."}

### 4.2 The Fix

<!-- Show the exact code change. Keep it minimal. -->

**Before**:
```{language}
{Current buggy code — exact copy from source}
```

**After**:
```{language}
{Fixed code — the minimal change needed}
```

### 4.3 Why This Fix Is Correct

- {Reason 1 — e.g., "Raises a clear error at the source instead of letting `None` propagate"}
- {Reason 2 — e.g., "Consistent with error handling pattern used elsewhere in the codebase"}
- {Reason 3 — e.g., "Does not change behavior for valid inputs — only affects the failure case"}

### 4.4 What This Fix Does NOT Do

<!-- Explicitly list what you're NOT changing and why. Prevents scope creep. -->

- {e.g., "Does not refactor `get_data()` — that's a separate concern"}
- {e.g., "Does not fix sibling issue in `other_module.py:L55` — separate bug fix needed"}

### 4.5 Sibling Fixes (if applicable)

<!-- If §3.3 found other locations with the same bug, list fixes here -->

| Location | Fix | Same as Primary Fix? |
|:---------|:----|:--------------------:|
| {`path.py:L##`} | {Brief description} | {Yes — identical / No — adapted because...} |

---

## 5. Verification

### 5.1 Regression Test

<!-- This test MUST fail before the fix and pass after the fix. -->

```{language}
{Test code that reproduces the bug — this becomes a permanent regression test}
```

- **Without fix**: {Expected failure — e.g., "Raises `TypeError: 'NoneType'...`"}
- **With fix**: {Expected pass — e.g., "Raises `KeyError('missing_key')` as intended"}

### 5.2 Existing Tests

| Test | File | Expected Impact |
|:-----|:-----|:----------------|
| {e.g., `test_process_valid_input`} | {`tests/test_processor.py`} | {Should still pass — valid inputs unaffected} |
| {e.g., `test_get_data_missing_key`} | {`tests/test_data.py`} | {May need update if it expected `None` return — now expects `KeyError`} |

### 5.3 Verification Commands

```
{e.g., pytest tests/test_processor.py tests/test_data.py -v}
{e.g., pytest tests/ -v  (full suite)}
```

---

## 6. Related Issues Discovered

<!-- Issues found during investigation that are NOT part of this fix.
     These should become separate tickets/plans. -->

| Issue | Location | Type | Suggested Template |
|:------|:---------|:-----|:-------------------|
| {e.g., "Similar null-check missing"} | {`other.py:L30`} | {Bug} | {`bug_fix_plan.md`} |
| {e.g., "Function too complex, hard to trace"} | {`processor.py`} | {Refactor} | {`refactor_plan.md`} |

---

## 7. File Change Summary

| # | Action | File Path | Description |
|:-:|:------:|:----------|:------------|
| 1 | MODIFY | `{path/to/buggy/file.ext}` | {Fix: brief description} |
| 2 | CREATE | `{tests/test_regression.py}` | {Regression test for this bug} |
| 3 | MODIFY | `{tests/test_existing.py}` | {Update: expected behavior changed} |

---

## 8. Post-Fix Verification

- [ ] Bug no longer reproduces (§2.1 steps now produce expected behavior)
- [ ] Regression test passes: `{command}`
- [ ] All existing tests pass: `{command}`
- [ ] Sibling bugs fixed (if applicable): {list}
- [ ] No lint/type errors: `{command}`
