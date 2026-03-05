# [Feature Name] – Implementation Plan (v1)

## Epic: [EPIC-ID] – [Epic Title]

**Objective**
[Brief description of what this epic enables and the key technical goals.]

**Architecture Decisions (locked)**

| # | Decision | Choice |
|---|----------|--------|
| 1 | [Decision topic] | [Chosen approach] |
| 2 | [Decision topic] | [Chosen approach] |
| 3 | [Decision topic] | [Chosen approach] |

**Success Metrics**
- [Quantitative metric, e.g. latency improvement]
- [Regression metric, e.g. 0 regressions in X behavior]
- [Test coverage metric, e.g. 100% pass rate on new test suites]
- [Behavioral correctness metric]

---

## Plan Verification ([YYYY-MM-DD])

Validation was run against the current repository state (`[package]/...` layout) and baseline tests.

**Verified with no blockers**
- [Story IDs] map cleanly to existing modules.
- Baseline regression suite passed before Sprint 1 kickoff:
  - `[pytest command]`
  - Result: `[N passed]`

**Required corrections for execution**
- [Correction 1: path normalization, naming conventions, etc.]
- [Correction 2: shared state or instantiation concerns]
- [Correction 3: backward-compat timing / phasing notes]

---

## Current Architecture Reference

```
[module/
├── submodule/
│   ├── file.py    ← Description
│   └── file.py    ← Description
└── other/
    └── ...
]
```

**[Relevant Map / Table] (current values):**

| Item | Property 1 | Property 2 | Property 3 |
|------|------------|------------|------------|
| `tool_a` | `value` | Category | ✅ Yes |
| `tool_b` | `value` | Category | ❌ No |

---

## Story: [ID]-01 – [Story Title]

### [ID]-01-01: [Task Title]

**File:** `path/to/file.py`
**Priority:** [Highest / High / Medium / Low]
**Estimate:** [N] SP

**Changes:**
```python
# Code changes go here
```

**Acceptance Criteria:**
- [Criterion 1]
- [Criterion 2]
- [Criterion 3]

**Dependencies:** [None / ID of dependency]

---

### [ID]-01-02: [Task Title]

**File:** `path/to/file.py`
**Priority:** [Highest / High / Medium / Low]
**Estimate:** [N] SP

**Changes:**
```python
# Code changes go here
```

**Acceptance Criteria:**
- [Criterion 1]
- [Criterion 2]

**Dependencies:** [None / ID of dependency]

---

## Story: [ID]-02 – [Story Title]

### [ID]-02-01: [Task Title]

**Files:**
- `path/to/file_a.py` → [What changes]
- `path/to/file_b.json` → [What changes]

**Priority:** [Highest / High / Medium / Low]
**Estimate:** [N] SP

**Changes:**
```python
# Code or config changes go here
```

**Acceptance Criteria:**
- [Criterion 1]
- [Criterion 2]

**Dependencies:** [ID of dependency]

---

## Sprint Plan

### Sprint 1 – [Theme, e.g. Foundation]
| Task | SP | Description |
|------|----|-------------|
| [ID]-01-01 | N | [Short description] |
| [ID]-01-02 | N | [Short description] |
| **Total** | **N** | |

### Sprint 1 Kickoff Package (Ready)

**Sprint goal**
[One sentence describing what this sprint ships and the key constraint (e.g. zero behavioral change when disabled).]

**Execution order (recommended)**
1. [Task group 1 with file targets]
2. [Task group 2 with file targets]
3. [Task group 3 with file targets]
4. [Tests and compatibility checks]

**Definition of done for Sprint 1**
- [Condition 1]
- [Condition 2]
- [Config includes specific values]
- [Tests updated and passing]

**Sprint 1 test suite**
- `[pytest command]`

**Tracking artifact**
- [Path to detailed checklist or prep doc]

### Sprint 2 – [Theme, e.g. Core Runtime]
| Task | SP | Description |
|------|----|-------------|
| [ID]-02-01 | N | [Short description] |
| **Total** | **N** | |

### Sprint 3 – [Theme, e.g. Orchestration + Loop]
| Task | SP | Description |
|------|----|-------------|
| [ID]-03-01 | N | [Short description] |
| **Total** | **N** | |

### Sprint 4 – [Theme, e.g. Testing + Polish]
| Task | SP | Description |
|------|----|-------------|
| [ID]-04-01 | N | [Short description] |
| **Total** | **N** | |

### Sprint 5 – [Theme, e.g. Release]
| Task | SP | Description |
|------|----|-------------|
| [ID]-05-01 | N | [Short description] |
| **Total** | **N** | |

---

## Dependency Graph

```
[ID]-01-01 ([Short label])
[ID]-01-02 ([Short label])          ──┐
[ID]-01-03 ([Short label])            │
[ID]-01-04 ([Short label]) ◄──────────┘
        │
   ┌────┴────┐
   ▼         ▼
[ID]-02-01  [ID]-03-01
(config)    (core logic)
   │              │
   ▼              ▼
[ID]-04-01  [ID]-05-01 (final)
```

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| [Risk 1 description] | [Mitigation approach + relevant story ID] |
| [Risk 2 description] | [Mitigation approach + relevant story ID] |
| [Risk 3 description] | [Mitigation approach + relevant story ID] |

---

## Definition of Done (Epic-Level)

- [ ] Feature flag `[flag_name]` default-off merged to main
- [ ] All [ID]-[test story] tests passing in CI
- [ ] No regressions in existing test suites
- [ ] Canary rollout completed with stable metrics
- [ ] Documentation and developer guide published
