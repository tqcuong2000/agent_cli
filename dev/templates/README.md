# Templates — Agent Planning Pipeline

## Overview

These templates form a **pipeline**, not a loose collection. The code review
template produces findings that feed into one or more plan templates, which
then guide implementation.

```
                          ┌──────────────────┐
                          │  Spike/Research  │──── produces knowledge ────┐
                          │  (when unknowns  │                            │
                          │   block a plan)  │                            │
                          └──────────────────┘                            │
                                                                         ▼
┌──────────────────┐     ┌──────────────────┐                ┌───────────────────┐
│    Incident /    │     │   Code Review    │                │   Any Plan        │
│  Bug Report      │────▶│   (diagnosis)    │──── routes ──▶│   Template        │
└──────────────────┘     └──────────────────┘    to one or   └───────────────────┘
                                │                more plans
                                │
               ┌────────┬───────┼───────┬─────────┬───────────┬────────────┐
               ▼        ▼       ▼       ▼         ▼           ▼            ▼
         ┌──────────┐ ┌────┐ ┌──────┐ ┌───────┐ ┌──────┐ ┌───────┐ ┌──────────┐
         │ Refactor │ │Bug │ │Feat. │ │Migra- │ │Depre-│ │ Perf  │ │  Spike   │
         │  Plan    │ │Fix │ │Plan  │ │tion   │ │cation│ │ Optim.│ │ Research │
         │          │ │Plan│ │      │ │Plan   │ │Plan  │ │ Plan  │ │  Plan    │
         └──────────┘ └────┘ └──────┘ └───────┘ └──────┘ └───────┘ └──────────┘
```

## Template Inventory

| Template | File | Purpose | When to Use |
|:---------|:-----|:--------|:------------|
| **Code Review** | `code_review.md` | Audit code against standards and produce actionable findings | Before any planned work — to identify what needs doing |
| **Refactor Plan** | `refactor_plan.md` | Improve existing code without changing behavior (or with explicit behavioral changes) | Code review found structural/quality issues |
| **Feature Plan** | `feature_implementation_plan.md` | Build new functionality into the codebase | Code review found missing capabilities, or standalone feature request |
| **Migration Plan** | `migration_plan.md` | Move from one state/system/version to another | Code review found platform/version/architecture issues, or standalone migration need |
| **Bug Fix Plan** | `bug_fix_plan.md` | Investigate and fix broken behavior | Code review found incorrect behavior, or direct bug report |
| **Spike/Research** | `spike_research_plan.md` | Time-boxed investigation to answer questions before committing to a plan | Too many unknowns to create any other plan — need research first |
| **Deprecation Plan** | `deprecation_plan.md` | Safely remove code, features, or APIs | Code review found dead code, obsolete features, or superseded implementations |
| **Performance Plan** | `performance_optimization_plan.md` | Optimize speed, memory, or throughput with measurement-driven approach | Code review found performance issues, or standalone performance requirement |

## Pipeline Flow

### Step 1: Code Review (Diagnosis)

Use `code_review.md` to audit the target code. The review produces:

- **Findings** (F1, F2, ...) — specific issues with severity, evidence, and recommendations
- **Action Plan** (§6) — prioritized groups of findings, each routed to a plan template

### Step 2: Route to Plan Template

For each action group in the code review's §6, create a plan using the
indicated template. The plan's **Source** field references back to the
code review report and specific finding IDs.

**Routing rules:**

| Finding Type | Route To | Example |
|:-------------|:---------|:--------|
| Structural problems (god classes, duplication, tight coupling) | `refactor_plan.md` | "Extract `SessionManager` into 3 focused classes" |
| Missing functionality (no validation, no feature X) | `feature_implementation_plan.md` | "Add input validation layer" |
| Platform/version/architecture moves | `migration_plan.md` | "Migrate from SQLite to PostgreSQL" |
| Incorrect behavior, wrong outputs, crashes | `bug_fix_plan.md` | "Fix null pointer when config key is missing" |
| Dead code, obsolete modules, superseded APIs | `deprecation_plan.md` | "Remove `LegacyAuthService` — replaced by `AuthService`" |
| Too many unknowns to plan | `spike_research_plan.md` | "Investigate async compatibility before choosing approach" |
| Slow execution, high memory, low throughput | `performance_optimization_plan.md` | "Reduce `process_batch()` latency from 450ms to < 100ms" |

### Step 3: Execution (Implementation)

Execute the plan phase by phase, following the checkpoints defined in each
plan template.

## Special Flows

### Spike → Plan Flow

A spike is the only template whose output is another template:

```
spike_research_plan.md  →  answers questions  →  recommendation  →  {any plan template}
```

The spike's §4.3 (Recommendation) specifies which plan template to use next
and what constraints to carry forward.

### Bug Fix → Related Issues Flow

Bug investigations often uncover adjacent problems. The bug fix template's
§6 (Related Issues Discovered) routes these to other templates:

```
bug_fix_plan.md  →  fixes the bug  →  §6 related issues  →  refactor_plan.md
                                                          →  deprecation_plan.md
                                                          →  bug_fix_plan.md (sibling bugs)
```

### Deprecation ←→ Feature Flow

Deprecation and feature plans are often paired — you build the replacement,
then remove the old:

```
feature_implementation_plan.md  →  builds replacement  →  deprecation_plan.md  →  removes old
```

## Traceability

The pipeline maintains end-to-end traceability:

```
Code Review Finding F3          →  links to  →  Standard S2 (from §1.2)
Code Review Action Plan §6      →  routes to →  refactor_plan.md
Refactor Plan §1 Source field   →  traces to →  code_review_xyz.md, finding F3
Refactor Plan §2 Problem Analysis → expands  →  Finding F3's description
```

This means you can always trace:
- **Forward**: From a standard violation → to the plan that fixes it → to the code that implements the fix
- **Backward**: From a code change → to the plan phase that prescribed it → to the code review finding that identified the need

## Standalone Usage

Each template can also be used **standalone** (without a preceding code review):

- Set the **Source** field to `"N/A — standalone"` (or `"N/A — direct report"` for bug fixes, `"N/A — exploratory"` for spikes)
- Fill in the motivation/problem analysis sections from direct observation
  rather than code review findings

## Naming Convention

When creating plans from a code review, use this naming pattern:

```
{template_type}_{target}_{date}.md

Examples:
  code_review_auth_module_2026-03-06.md
  refactor_plan_session_manager_2026-03-06.md
  feature_implementation_plan_input_validation_2026-03-06.md
  migration_plan_database_upgrade_2026-03-06.md
  bug_fix_plan_null_config_key_2026-03-06.md
  spike_research_plan_async_compatibility_2026-03-06.md
  deprecation_plan_legacy_auth_2026-03-06.md
  performance_optimization_plan_batch_processing_2026-03-06.md
```
