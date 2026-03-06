# Spike / Research Plan: {TITLE}

<!--
=============================================================================
AGENT INSTRUCTIONS (remove this block after completing the plan)
=============================================================================
A spike is a time-boxed investigation to answer specific questions or
reduce uncertainty BEFORE committing to an implementation plan. The output
of a spike is KNOWLEDGE, not code.

This is fundamentally different from other plan templates:
- Migration/Feature/Refactor plans assume you KNOW what to do.
- A spike is for when you DON'T KNOW what to do yet.

Follow these rules:

1. DEFINE THE UNKNOWNS — Before doing anything, list the specific questions
   that need answers in §1.2. If you can't articulate what you don't know,
   you're not ready for a spike — you're ready for a plan.

2. TIME-BOX IT — Set a hard time/effort boundary in §1.1. A spike that
   runs forever is just unstructured work. When the time-box expires,
   document what you learned and what remains unknown.

3. ANSWER QUESTIONS, DON'T BUILD THINGS — The deliverable is a set of
   answered questions and a recommendation, not production code. Any code
   written during a spike is disposable (prototypes, proofs-of-concept).

4. REPLACE ALL {PLACEHOLDERS} — Every {PLACEHOLDER} must be replaced with
   real, specific values. If a section is genuinely not applicable, write
   "N/A" with a one-line justification.

5. ONE SPIKE, ONE TOPIC — Don't investigate 5 unrelated things. If you have
   multiple unknowns in different domains, create separate spikes.

6. DOCUMENT AS YOU GO — Capture findings in §3 as you discover them, not
   from memory after the fact. Include sources, evidence, and reasoning.

7. END WITH A RECOMMENDATION — §4 must provide a clear recommendation and
   identify which plan template to use next. A spike without a conclusion
   is wasted effort.

8. BE HONEST ABOUT WHAT YOU DIDN'T LEARN — §4.2 must list remaining
   unknowns. Pretending you answered everything when you didn't will
   cause problems in the downstream plan.
=============================================================================
-->

---

## 1. Overview

| Field              | Value                                              |
|:-------------------|:---------------------------------------------------|
| **Topic**          | {What are we investigating — one sentence}         |
| **Source**         | {Code review or discussion that triggered this — e.g., `code_review_xyz.md`. Write "N/A — exploratory" if self-initiated} |
| **Motivation**     | {Why we need this investigation — what decision is blocked?} |
| **Time-Box**       | {Maximum effort — e.g., "2 hours", "1 day", "500 lines of reading"} |
| **Decision Blocked** | {What can't we do until this spike is complete — e.g., "Can't choose between SQLite and PostgreSQL for the persistence layer"} |

### 1.1 Success Criteria

<!-- The spike is "done" when these questions are answered. -->

- [ ] All questions in §1.2 have answers (or are documented as "still unknown" with reasoning)
- [ ] A clear recommendation exists in §4.1
- [ ] The recommended next template is identified in §4.3

### 1.2 Questions to Answer

<!-- These are the SPECIFIC unknowns that motivated this spike.
     Number them — you'll reference these IDs throughout the document. -->

| # | Question | Priority | Why It Matters |
|:-:|:---------|:--------:|:---------------|
| Q1 | {e.g., "Does library X support async operations?"} | {Must / Should / Nice} | {e.g., "Our system is fully async — sync library would require adapter layer"} |
| Q2 | {e.g., "What is the performance impact of approach A vs. B?"} | {priority} | {why} |
| Q3 | {e.g., "Is the existing schema compatible with the proposed migration?"} | {priority} | {why} |

### 1.3 Out of Scope

- {Item 1 — e.g., "Full implementation — this spike only determines feasibility"}
- {Item 2 — e.g., "Performance benchmarking under production load — only directional testing"}

---

## 2. Investigation Plan

<!-- How will you answer each question? Define your approach before diving in. -->

### 2.1 Research Tasks

| # | Task | Answers | Method | Sources |
|:-:|:-----|:-------:|:-------|:--------|
| T1 | {e.g., "Review library X documentation for async support"} | {Q1} | {e.g., "Read docs, check API signatures"} | {e.g., "Official docs, GitHub repo, changelog"} |
| T2 | {e.g., "Build minimal proof-of-concept for approach A"} | {Q2} | {e.g., "Write throwaway script, measure timing"} | {e.g., "Existing codebase patterns"} |
| T3 | {e.g., "Analyze current schema against proposed changes"} | {Q3} | {e.g., "Read schema definition, map fields"} | {e.g., "`src/models/schema.py`, migration docs"} |

### 2.2 Proof-of-Concept Scope (if applicable)

<!-- If a PoC is needed, define its minimal scope. Remember: PoC code is DISPOSABLE. -->

| Aspect | Value |
|:-------|:------|
| **What it proves** | {e.g., "That library X can be used with our async event loop"} |
| **What it does NOT prove** | {e.g., "Production readiness, error handling, edge cases"} |
| **Location** | {e.g., "`/tmp/spike_poc/` — throwaway, not production code"} |
| **Success condition** | {e.g., "PoC completes 100 async operations without deadlock"} |

---

## 3. Findings

<!-- AGENT: Fill this in AS YOU INVESTIGATE, not after the fact.
     Each finding should reference which question (Q#) it answers. -->

---

### Finding 1: {Title}

| Attribute | Value |
|:----------|:------|
| **Answers** | {Q# — which question(s) this finding addresses} |
| **Source** | {Where you found this — URL, file path, documentation section} |
| **Confidence** | {High / Medium / Low — how certain are you?} |

**Summary**: {2-4 sentences describing what you found}

**Evidence**:
```{language}
{Code snippet, documentation quote, test output, or data that supports the finding}
```

**Implications**: {What this means for the decision — e.g., "This confirms library X
supports async, so no adapter layer is needed"}

---

### Finding 2: {Title}

| Attribute | Value |
|:----------|:------|
| **Answers** | {Q#} |
| **Source** | {source} |
| **Confidence** | {level} |

**Summary**: {description}

**Evidence**:
```{language}
{evidence}
```

**Implications**: {what this means}

---

### Finding 3: {Title}

| Attribute | Value |
|:----------|:------|
| **Answers** | {Q#} |
| **Source** | {source} |
| **Confidence** | {level} |

**Summary**: {description}

**Evidence**:
```{language}
{evidence}
```

**Implications**: {what this means}

---

<!-- Add more findings as needed following the same format -->

---

## 4. Conclusion

### 4.1 Answers Summary

<!-- Map each original question to its answer. -->

| Question | Answer | Confidence | Finding |
|:--------:|:-------|:----------:|:-------:|
| Q1 | {Concise answer} | {High/Med/Low} | {F#} |
| Q2 | {Concise answer} | {confidence} | {F#} |
| Q3 | {Concise answer or "Still unknown — see §4.2"} | {confidence} | {F#} |

### 4.2 Remaining Unknowns

<!-- Be honest. List anything you couldn't answer and why. -->

| Unknown | Why It Remains | Impact on Recommendation | Suggested Follow-Up |
|:--------|:---------------|:-------------------------|:--------------------|
| {e.g., "Performance under production load"} | {e.g., "Would need production-like dataset to test; out of scope for this spike"} | {e.g., "Low — directional testing suggests it's fast enough"} | {e.g., "Monitor after deployment"} |

<!-- If all questions were answered: -->
<!-- **None** — All questions from §1.2 were answered. -->

### 4.3 Recommendation

**Recommended approach**: {Clear, actionable recommendation — 2-4 sentences}

**Next step**: Use **`{template_name}.md`** to create a {plan type} plan based on these findings.

| Aspect | Recommendation |
|:-------|:---------------|
| **Approach** | {e.g., "Use library X with async adapter"} |
| **Plan Template** | {e.g., `feature_implementation_plan.md` / `migration_plan.md` / `refactor_plan.md`} |
| **Key Constraints** | {e.g., "Must use version >= 3.0 for async support"} |
| **Risks to Address in Plan** | {e.g., "Schema compatibility needs careful migration — see Finding 3"} |

### 4.4 Alternatives Considered

| Alternative | Why Not Recommended | Would Reconsider If... |
|:------------|:--------------------|:-----------------------|
| {e.g., "Library Y instead of X"} | {e.g., "No async support, smaller community"} | {e.g., "Library X is deprecated or has security issues"} |
| {alternative} | {reason} | {condition} |

---

## 5. Artifacts Produced

<!-- List anything created during the spike. Mark disposable items clearly. -->

| Artifact | Path | Disposable? | Notes |
|:---------|:-----|:-----------:|:------|
| {e.g., "Async PoC script"} | {`/tmp/spike_poc/test_async.py`} | {Yes} | {e.g., "Proves async works; not production quality"} |
| {e.g., "Compatibility analysis"} | {`/tmp/spike_poc/compat_matrix.md`} | {No — useful for plan} | {e.g., "Reference this in the migration plan"} |

---

## Appendix: Sources Consulted

- {Source 1 — e.g., "Library X docs: `https://example.com/docs`"}
- {Source 2 — e.g., "Existing implementation: `src/services/similar_service.py`"}
- {Source 3 — e.g., "Team discussion: conversation `abc-123`"}
