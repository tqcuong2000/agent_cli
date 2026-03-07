# Code Review: {TITLE}

<!--
=============================================================================
AGENT INSTRUCTIONS (remove this block after completing the review)
=============================================================================
This template guides you through reviewing code against defined standards.
Follow these rules:

1. READ THE STANDARDS FIRST — Before looking at any code, read and internalize
   every standard/guideline listed in §1.2. You cannot review against rules
   you haven't read.

2. READ THE CODE THOROUGHLY — Do not skim. Read every file in scope from top
   to bottom. Understand the flow, the data model, the error paths, and the
   edge cases before writing a single finding.

3. REPLACE ALL {PLACEHOLDERS} — Every {PLACEHOLDER} must be replaced with real,
   specific values. If a section is genuinely not applicable, write "N/A" with
   a one-line justification.

4. EVIDENCE-BASED FINDINGS — Every finding MUST include:
   - The exact file path and line number(s)
   - A quote or code snippet showing the violation
   - The specific standard/guideline it violates (by ID from §1.2)
   - A concrete recommendation — not just "fix this"

5. SEVERITY MUST BE JUSTIFIED — Do not inflate severity. Use the definitions
   in the severity legend. A naming inconsistency is not "Critical."

6. ACKNOWLEDGE WHAT'S GOOD — The review must include §4 (Strengths). If the
   code is well-written, say so with specific examples. Reviews that only
   list negatives are incomplete and demoralizing.

7. BE CONSTRUCTIVE — Every finding should include a recommendation that the
   author can act on. "This is wrong" without "here's how to fix it" is not
   a useful review.

8. NO FALSE POSITIVES — If you're uncertain whether something is a violation,
   note it as an "Observation" (severity: Info), not a finding. Do not
   manufacture issues to make the review look thorough.

9. SEPARATE VIOLATIONS FROM OPINIONS — A violation is an objective breach of
   a stated standard. A suggestion is your opinion on how to improve.
   Use the correct severity (Violation vs. Suggestion) accordingly.

10. ROUTE TO THE RIGHT PLAN — This review feeds into downstream plan templates.
    In §6 (Recommended Action Plan), classify each action group by the type
    of work required and reference the correct template:
    - Structural/quality issues      → refactor_plan.md
    - Missing capabilities/features  → feature_implementation_plan.md
    - System/version/platform moves  → migration_plan.md
    - Broken behavior / bugs         → bug_fix_plan.md
    - Unknowns that need research    → spike_research_plan.md
    - Code/features to remove        → deprecation_plan.md
    - Speed/memory/throughput issues  → performance_optimization_plan.md
    The plan template's "Source" field should reference this review.
=============================================================================
-->

---

## 1. Review Scope

| Field               | Value                                             |
|:--------------------|:--------------------------------------------------|
| **Feature(s)**      | {What is being reviewed — feature name(s) or description} |
| **Review Type**     | {Standards Compliance / Architecture / Security / Performance / General Quality} |
| **Date**            | {YYYY-MM-DD}                                      |

### 1.1 Files Under Review

<!-- AGENT: List EVERY file you will review. Read all of them before writing findings. -->

| # | File Path | Lines | Purpose |
|:-:|:----------|:-----:|:--------|
| 1 | {`path/to/file.ext`} | {count} | {Brief description of role} |
| 2 | {`path/to/file.ext`} | {count} | {description} |

### 1.2 Standards & Guidelines Applied

<!-- List every standard, guideline, or rule set the code is reviewed against.
     Each one gets an ID for cross-referencing in findings.
     These can be: project-specific style guides, architectural rules, language
     best practices, security policies, performance requirements, etc. -->

| ID | Standard | Source | Summary |
|:--:|:---------|:-------|:--------|
| S1 | {e.g., "Project naming conventions"} | {e.g., "`docs/style_guide.md`"} | {e.g., "Classes: PascalCase, functions: snake_case, constants: UPPER_SNAKE"} |
| S2 | {e.g., "Error handling policy"} | {e.g., "`docs/error_handling.md`"} | {e.g., "No bare excepts; all errors must be typed and logged"} |
| S3 | {e.g., "Single Responsibility Principle"} | {e.g., "SOLID principles"} | {e.g., "Each class/module should have one reason to change"} |
| S4 | {e.g., "Test coverage requirement"} | {e.g., "Team policy"} | {e.g., "All public functions must have unit tests; minimum 80% coverage"} |

---

## 2. Review Summary

<!-- AGENT: Write this section LAST, after completing all findings. -->

### 2.1 Verdict

| Metric | Value |
|:-------|:------|
| **Overall Assessment** | {🟢 Pass / 🟡 Pass with Conditions / 🔴 Fail — see criteria below} |
| **Total Findings**     | {count} |
| **Critical**           | {count} |
| **Warning**            | {count} |
| **Info**               | {count} |
| **Suggestion**         | {count} |

<!--
Verdict criteria:
- 🟢 PASS: No Critical or Warning findings
- 🟡 PASS WITH CONDITIONS: No Critical findings, but Warning findings exist that should be addressed
- 🔴 FAIL: One or more Critical findings that must be resolved before merge/deployment
-->

### 2.2 Executive Summary

<!-- 2-4 sentences: What is the overall quality? What are the most important findings?
     What should be prioritized? -->

{Summary paragraph}

### 2.3 Standards Compliance Matrix

<!-- Quick reference: which standards are met, partially met, or violated? -->

| Standard ID | Standard Name | Status | Finding Count |
|:-----------:|:-------------|:------:|:-------------:|
| S1 | {name} | {✅ Compliant / ⚠️ Partial / ❌ Violated} | {count} |
| S2 | {name} | {status} | {count} |
| S3 | {name} | {status} | {count} |
| S4 | {name} | {status} | {count} |

---

## 3. Findings

<!--
=============================================================================
SEVERITY LEGEND
=============================================================================

🔴 CRITICAL — Must fix. Clear violation of a stated standard that causes or
              risks: incorrect behavior, data loss, security vulnerability,
              or system instability. Blocks approval.

🟡 WARNING  — Should fix. Violation of a stated standard that degrades
              maintainability, readability, or reliability but does not cause
              immediate breakage. Does not block but should be addressed.

🔵 INFO     — Observation. Not a clear violation, but something the reviewer
              noticed that may warrant discussion or monitoring. No action
              required.

💡 SUGGESTION — Opinion. The reviewer believes this would improve the code,
               but it is not a violation of any stated standard. Take or
               leave at author's discretion.
=============================================================================
-->

---

### F1: {Finding Title}

| Attribute     | Value |
|:-------------|:------|
| **Severity** | {🔴 Critical / 🟡 Warning / 🔵 Info / 💡 Suggestion} |
| **Standard** | {S# — which standard is violated, or "N/A" for suggestions} |
| **Location** | {`file/path.ext:L##-L##`} |

**Description**: {What the issue is — be specific}

**Evidence**:
```{language}
{Exact code snippet showing the violation — copy from source, include line numbers if helpful}
```

**Why it matters**: {Impact — what goes wrong or could go wrong because of this}

**Recommendation**:
```{language}
{Concrete fix or improved code — not just "fix this", show what the fix looks like}
```

---

### F2: {Finding Title}

| Attribute     | Value |
|:-------------|:------|
| **Severity** | {🔴 Critical / 🟡 Warning / 🔵 Info / 💡 Suggestion} |
| **Standard** | {S#} |
| **Location** | {`file/path.ext:L##-L##`} |

**Description**: {description}

**Evidence**:
```{language}
{code snippet}
```

**Why it matters**: {impact}

**Recommendation**:
```{language}
{fix}
```

---

### F3: {Finding Title}

| Attribute     | Value |
|:-------------|:------|
| **Severity** | {severity} |
| **Standard** | {S#} |
| **Location** | {location} |

**Description**: {description}

**Evidence**:
```{language}
{code snippet}
```

**Why it matters**: {impact}

**Recommendation**:
```{language}
{fix}
```

---

<!-- Add more findings as needed following the same F# format -->

---

## 4. Strengths

<!-- AGENT: This section is NOT optional. Identify what the code does WELL.
     Be as specific as you are with findings — cite exact examples. -->

### ✅ {Strength 1 Title — e.g., "Consistent Error Handling Pattern"}

{Description with specific example — e.g., "All service methods in `user_service.py` use the
`try/except/log/raise` pattern consistently. See `get_user()` at L45 and `create_user()` at L78.
This makes error flows predictable and debuggable."}

### ✅ {Strength 2 Title}

{Description with specific example}

### ✅ {Strength 3 Title}

{Description with specific example}

---

## 5. Findings by File

<!-- Cross-reference: for each file, which findings apply?
     This helps the author fix issues file-by-file. -->

| File Path | Findings | Most Severe |
|:----------|:---------|:-----------:|
| {`path/to/file1.ext`} | {F1, F3} | {🔴 Critical} |
| {`path/to/file2.ext`} | {F2} | {🟡 Warning} |
| {`path/to/file3.ext`} | {—} | {✅ No findings} |

---

## 6. Recommended Action Plan

<!-- AGENT: Prioritize the findings into an actionable sequence.
     Group by priority, not by file.

     ROUTING: For each action group, specify which plan template should be used
     to address the findings. The downstream plan's "Source" field should
     reference this code review report.

     Template options:
     - refactor_plan.md               → Structural/quality improvements
     - feature_implementation_plan.md  → Missing capabilities that need building
     - migration_plan.md               → System/version/platform transitions
     - bug_fix_plan.md                 → Broken behavior, incorrect outputs
     - spike_research_plan.md          → Unknowns requiring investigation first
     - deprecation_plan.md             → Dead code, obsolete features to remove
     - performance_optimization_plan.md → Speed, memory, or throughput problems
-->

### Priority 1: Must Fix (Blocking)

<!-- Critical findings that must be resolved -->

| Finding | Action | Effort | Plan Template |
|:-------:|:-------|:------:|:--------------|
| {F1} | {Brief action description} | {S/M/L} | {e.g., `refactor_plan.md`} |

### Priority 2: Should Fix (Non-blocking)

<!-- Warning findings that should be addressed soon -->

| Finding | Action | Effort | Plan Template |
|:-------:|:-------|:------:|:--------------|
| {F2} | {Brief action description} | {S/M/L} | {e.g., `feature_implementation_plan.md`} |

### Priority 3: Consider (Optional)

<!-- Info and Suggestion findings — nice to have -->

| Finding | Action | Effort | Plan Template |
|:-------:|:-------|:------:|:--------------|
| {F3} | {Brief action description} | {S/M/L} | {e.g., `refactor_plan.md`} |

---

## Appendix A: Review Checklist

<!-- Generic checklist the agent should mentally walk through for each file.
     Customize this section based on your project's standards. -->

| Category | Check | Verified |
|:---------|:------|:--------:|
| **Correctness** | Logic produces expected outputs for all input cases | {✅/❌/N/A} |
| **Correctness** | Edge cases handled (null, empty, boundary values) | {✅/❌/N/A} |
| **Correctness** | Error paths handled — no silent failures | {✅/❌/N/A} |
| **Structure** | Functions/methods have single, clear responsibility | {✅/❌/N/A} |
| **Structure** | No excessive nesting (< 4 levels) | {✅/❌/N/A} |
| **Structure** | No god classes or functions (< 200 lines each) | {✅/❌/N/A} |
| **Naming** | Names are descriptive, consistent with conventions | {✅/❌/N/A} |
| **Naming** | No ambiguous abbreviations | {✅/❌/N/A} |
| **Dependencies** | No circular imports | {✅/❌/N/A} |
| **Dependencies** | Minimal coupling between modules | {✅/❌/N/A} |
| **Testing** | Public functions have corresponding tests | {✅/❌/N/A} |
| **Testing** | Tests verify behavior, not implementation | {✅/❌/N/A} |
| **Documentation** | Public API has docstrings | {✅/❌/N/A} |
| **Documentation** | Complex logic has inline comments explaining WHY | {✅/❌/N/A} |
| **Performance** | No obvious N+1 or O(n²) where O(n) is possible | {✅/❌/N/A} |
| **Security** | No hardcoded secrets or credentials | {✅/❌/N/A} |
| **Security** | User input is validated/sanitized | {✅/❌/N/A} |

## Appendix B: References

- {Reference 1 — e.g., "Style guide: `docs/style_guide.md`"}
- {Reference 2 — e.g., "Architecture docs: `docs/architecture.md`"}
