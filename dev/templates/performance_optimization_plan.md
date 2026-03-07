# Performance Optimization Plan: {TITLE}

<!--
=============================================================================
AGENT INSTRUCTIONS (remove this block after completing the plan)
=============================================================================
This template guides you through optimizing performance. Performance work
is MEASUREMENT-DRIVEN — every decision must be backed by profiling data
and every change must be validated with before/after benchmarks.

This is fundamentally different from a refactor:
- Refactoring improves CODE STRUCTURE (measured by readability, coupling, etc.)
- Performance optimization improves RUNTIME BEHAVIOR (measured by time, memory, throughput)
- A perf optimization may WORSEN code structure (e.g., caching adds complexity)
  and that's an acceptable trade-off IF the numbers justify it.

Follow these rules:

1. MEASURE FIRST — Before touching ANY code, establish baseline measurements
   in §2. You cannot improve what you haven't measured. "It feels slow" is
   not a baseline; "Function X takes 450ms for input of size 1000" is.

2. PROFILE, DON'T GUESS — Use profiling tools to find the actual bottleneck.
   Developers are notoriously bad at guessing where time is spent. §3 must
   contain real profiling data, not assumptions.

3. REPLACE ALL {PLACEHOLDERS} — Every {PLACEHOLDER} must be replaced with
   real, specific values. If a section is genuinely not applicable, write
   "N/A" with a one-line justification.

4. OPTIMIZE THE BOTTLENECK — The profiling data tells you what to optimize.
   If 80% of time is in function A, optimizing function B is a waste.
   Every optimization in §5 must target a bottleneck identified in §3.

5. ONE CHANGE AT A TIME — Make one optimization, measure the impact, then
   move to the next. If you batch multiple changes, you can't tell which
   one helped (or hurt).

6. CORRECTNESS FIRST — An optimization that produces wrong results is not
   an optimization. Run the full test suite after EVERY change. If tests
   don't cover the optimized code paths, write tests FIRST.

7. KNOW WHEN TO STOP — Define target performance in §1.1. Once you hit the
   target, stop. Over-optimizing past the target wastes effort and increases
   code complexity for no user benefit.

8. DOCUMENT THE TRADE-OFFS — Every optimization has a cost (complexity,
   memory, readability). §5 must explicitly state what you're trading away
   for performance.
=============================================================================
-->

---

## 1. Overview

| Field              | Value                                              |
|:-------------------|:---------------------------------------------------|
| **Target**         | {What is being optimized — function, module, endpoint, pipeline} |
| **Source**         | {Code review or report that triggered this — e.g., `code_review_xyz.md`, finding F5. Write "N/A — standalone" if not triggered by a review} |
| **Metric**         | {Primary metric — e.g., "Response time", "Memory usage", "Throughput (ops/sec)"} |
| **Current**        | {Current measured value — e.g., "450ms p95 latency"} |
| **Target**         | {Goal value — e.g., "< 100ms p95 latency"} |
| **Scope**          | {Bounded description of what IS and IS NOT included} |
| **Estimated Effort** | {S / M / L / XL with justification}              |

### 1.1 Performance Requirements

<!-- Define specific, measurable targets. The optimization is "done" when ALL
     mandatory targets are met. -->

| # | Metric | Current Value | Target Value | Priority | Method of Measurement |
|:-:|:-------|:--------------|:-------------|:--------:|:----------------------|
| P1 | {e.g., "Execution time for `process_batch(1000)`"} | {e.g., "450ms"} | {e.g., "< 100ms"} | {Must / Should / Nice} | {e.g., "`time.perf_counter()` wrapper / `pytest-benchmark`"} |
| P2 | {e.g., "Peak memory usage"} | {e.g., "340MB"} | {e.g., "< 150MB"} | {priority} | {method} |
| P3 | {e.g., "Throughput"} | {e.g., "200 ops/sec"} | {e.g., "> 1000 ops/sec"} | {priority} | {method} |

### 1.2 Constraints

<!-- Hard constraints that the optimization must NOT violate. -->

- {e.g., "Correctness: All existing tests must pass — zero tolerance for wrong results"}
- {e.g., "Memory: Must run within 512MB container limit"}
- {e.g., "Compatibility: Public API signatures must not change"}
- {e.g., "Readability: Optimization must be documented if it makes code less obvious"}

### 1.3 Out of Scope

- {Item 1 — e.g., "Infrastructure scaling (adding more machines) — this plan focuses on code-level optimization only"}
- {Item 2 — e.g., "UI rendering performance — separate plan"}

---

## 2. Baseline Measurements

<!-- AGENT: This section must be completed BEFORE looking at code for optimization
     opportunities. Establish the ground truth. -->

### 2.1 Measurement Environment

<!-- Document the environment so measurements are reproducible. -->

| Factor | Value |
|:-------|:------|
| **Hardware** | {e.g., "Intel i7-12700, 32GB RAM, NVMe SSD"} |
| **OS** | {e.g., "Windows 11 23H2" / "Ubuntu 22.04"} |
| **Runtime** | {e.g., "Python 3.12.1, CPython"} |
| **Data Size** | {e.g., "Test dataset: 10,000 records, 45MB"} |
| **Concurrency** | {e.g., "Single-threaded" / "4 workers"} |

### 2.2 Benchmark Script

<!-- The exact script/command used to measure. Must be repeatable. -->

```{language}
{Benchmark script or command — this is run before AND after each optimization}
```

### 2.3 Baseline Results

<!-- Run the benchmark at least 3 times. Record all runs. -->

| Run | P1: {metric name} | P2: {metric name} | P3: {metric name} |
|:---:|:------------------:|:------------------:|:------------------:|
| 1 | {value} | {value} | {value} |
| 2 | {value} | {value} | {value} |
| 3 | {value} | {value} | {value} |
| **Mean** | **{value}** | **{value}** | **{value}** |
| **σ (std dev)** | {value} | {value} | {value} |

### 2.4 Baseline Assessment

{1-3 sentences: How far are we from the targets? Is this a 2x improvement or 10x?
What does that imply about the type of optimization needed — algorithmic change vs. micro-optimization?}

---

## 3. Profiling & Bottleneck Analysis

<!-- AGENT: Use actual profiling tools. Do NOT guess where the bottleneck is. -->

### 3.1 Profiling Method

| Aspect | Value |
|:-------|:------|
| **Tool** | {e.g., "`cProfile`", "`py-spy`", "`perf`", "`memory_profiler`", Chrome DevTools} |
| **Command** | {Exact command used to profile} |
| **Data** | {Input used for profiling — same as benchmark or different?} |

### 3.2 Profiling Results

<!-- Show the top bottlenecks identified by the profiler. -->

| Rank | Function/Location | Time (ms) | % of Total | Calls | Category |
|:----:|:------------------|:---------:|:----------:|:-----:|:---------|
| 1 | {`module.function()` at `file.py:L##`} | {value} | {%} | {count} | {CPU / IO / Memory / Allocation} |
| 2 | {`module.function()`} | {value} | {%} | {count} | {category} |
| 3 | {`module.function()`} | {value} | {%} | {count} | {category} |
| 4 | {`module.function()`} | {value} | {%} | {count} | {category} |
| 5 | {`module.function()`} | {value} | {%} | {count} | {category} |

### 3.3 Bottleneck Diagnosis

<!-- For each bottleneck you plan to address, explain WHY it's slow. -->

#### Bottleneck 1: {Function/Location} — {% of total}

- **What it does**: {Brief description of the function's purpose}
- **Why it's slow**: {Root cause — e.g., "O(n²) nested loop", "repeated database calls in loop", "unnecessary object allocation", "blocking I/O on hot path"}
- **Evidence**:
  ```{language}
  {Code snippet or profiler output showing the problem}
  ```
- **Optimization opportunity**: {e.g., "Replace O(n²) with O(n) using hash map lookup"}
- **Expected impact**: {e.g., "~60% reduction in execution time (this is 65% of total runtime)"}

#### Bottleneck 2: {Function/Location} — {%}

- **What it does**: {description}
- **Why it's slow**: {root cause}
- **Evidence**:
  ```{language}
  {evidence}
  ```
- **Optimization opportunity**: {approach}
- **Expected impact**: {estimate}

---

## 4. Optimization Strategy

### 4.1 Approach Summary

{2-4 sentences: Overall optimization strategy. Which bottlenecks will you address and in what order?
Start with the highest-impact, lowest-risk optimization.}

### 4.2 Optimization Plan

<!-- Order by expected impact (highest first). Each maps to a bottleneck from §3. -->

| # | Optimization | Bottleneck | Technique | Expected Impact | Trade-off |
|:-:|:-------------|:----------:|:----------|:----------------|:----------|
| O1 | {e.g., "Replace linear search with hash lookup"} | {B1} | {e.g., "Algorithmic — O(n²) → O(n)"} | {e.g., "~60% faster"} | {e.g., "Higher memory usage for hash table"} |
| O2 | {e.g., "Batch database queries"} | {B2} | {e.g., "I/O batching"} | {e.g., "~25% faster"} | {e.g., "Slightly more complex query logic"} |
| O3 | {e.g., "Cache computed results"} | {B1} | {e.g., "Memoization"} | {e.g., "~10% faster on repeated calls"} | {e.g., "Memory cost of cache, invalidation complexity"} |

### 4.3 Optimization Techniques Reference

<!--
Common optimization categories (for agent reference):

ALGORITHMIC (highest impact)
- Better data structures (list → set/dict for lookups)
- Better algorithms (O(n²) → O(n log n) or O(n))
- Early termination / short-circuiting

I/O (high impact for I/O-bound code)
- Batching (N queries → 1 query)
- Async I/O (concurrent operations)
- Buffered I/O
- Connection pooling

MEMORY (for memory-bound code)
- Generators instead of lists (lazy evaluation)
- Object pooling / reuse
- Smaller data types
- Streaming instead of loading-all-into-memory

CPU (for CPU-bound code)
- Caching / memoization
- Avoiding redundant computation
- Vectorization / bulk operations
- Compiled extensions (Cython, C, Rust via FFI)

MICRO-OPTIMIZATION (lowest impact — only after big wins)
- String concatenation → join
- Local variable caching
- Avoiding attribute lookups in tight loops
- Built-in functions over manual loops
-->

---

## 5. Implementation Phases

<!-- AGENT: ONE optimization per phase. Measure after each one.
     Do NOT batch optimizations together. -->

---

### Phase 1: {Optimization O1 — Highest Impact}

**Target Bottleneck**: {B# from §3}

**Goal**: {e.g., "Replace O(n²) search with O(n) hash lookup — expected ~60% speedup"}

**Prerequisites**: {e.g., "Baseline measurements established, all tests pass"}

#### Steps

1. **{Action}**
   - File: `{path/to/file.ext:L##-L##}`
   - Before:
     ```{language}
     {Current slow code}
     ```
   - After:
     ```{language}
     {Optimized code}
     ```
   - Trade-off: {What you're giving up — e.g., "Uses ~2x memory for the lookup table"}

2. **Run correctness tests**
   - Command: `{test command}`
   - Expected: All tests pass

3. **Run benchmark**
   - Command: `{same benchmark command from §2.2}`
   - Record results below

#### Results

| Run | P1: {metric} | P2: {metric} | P3: {metric} |
|:---:|:------------:|:------------:|:------------:|
| 1 | {value} | {value} | {value} |
| 2 | {value} | {value} | {value} |
| 3 | {value} | {value} | {value} |
| **Mean** | **{value}** | **{value}** | **{value}** |

**Improvement**: {e.g., "P1: 450ms → 180ms (60% reduction) ✅ — matches expected impact"}

#### Checkpoint

- [ ] All tests pass (correctness preserved)
- [ ] Benchmark shows measurable improvement
- [ ] Results recorded in table above

---

### Phase 2: {Optimization O2}

**Target Bottleneck**: {B#}

**Goal**: {description with expected improvement}

**Prerequisites**: {Phase 1 checkpoint passed}

#### Steps

1. **{Action}**
   - File: `{path}`
   - Before:
     ```{language}
     {slow code}
     ```
   - After:
     ```{language}
     {optimized code}
     ```
   - Trade-off: {cost}

2. **Run correctness tests**
3. **Run benchmark**

#### Results

| Run | P1: {metric} | P2: {metric} | P3: {metric} |
|:---:|:------------:|:------------:|:------------:|
| 1 | {value} | {value} | {value} |
| 2 | {value} | {value} | {value} |
| 3 | {value} | {value} | {value} |
| **Mean** | **{value}** | **{value}** | **{value}** |

**Improvement from Phase 1**: {delta}
**Cumulative improvement from baseline**: {delta}

#### Checkpoint

- [ ] All tests pass
- [ ] Benchmark shows improvement
- [ ] Cumulative improvement on track toward target

---

### Phase 3: {Optimization O3 (if needed)}

<!-- Only proceed if targets from §1.1 are NOT yet met after Phase 2.
     If targets are met, skip remaining phases and go to §7. -->

**Target Bottleneck**: {B#}

**Goal**: {description}

**Prerequisites**: {Phase 2 checkpoint passed; targets NOT yet met}

#### Steps

1. **{Action}**
   - File: `{path}`
   - Trade-off: {cost}

2. **Run correctness tests**
3. **Run benchmark**

#### Results

| Run | P1: {metric} | P2: {metric} | P3: {metric} |
|:---:|:------------:|:------------:|:------------:|
| 1 | {value} | {value} | {value} |
| 2 | {value} | {value} | {value} |
| 3 | {value} | {value} | {value} |
| **Mean** | **{value}** | **{value}** | **{value}** |

**Cumulative improvement from baseline**: {delta}

#### Checkpoint

- [ ] All tests pass
- [ ] Targets met? {Yes → proceed to §7 / No → add Phase 4 or reassess targets}

---

## 6. Cumulative Results Summary

<!-- AGENT: Fill this in after all phases are complete. -->

| Metric | Baseline | After Phase 1 | After Phase 2 | After Phase 3 | Target | Met? |
|:-------|:--------:|:-------------:|:-------------:|:-------------:|:------:|:----:|
| P1: {name} | {value} | {value} | {value} | {value} | {value} | {✅/❌} |
| P2: {name} | {value} | {value} | {value} | {value} | {value} | {✅/❌} |
| P3: {name} | {value} | {value} | {value} | {value} | {value} | {✅/❌} |

### Impact Visualization

```
P1: {metric name}
Baseline  ████████████████████████████████████████  {baseline value}
Phase 1   ████████████████                          {value} ({%} reduction)
Phase 2   ██████████                                {value} ({%} reduction)
Phase 3   ████                                      {value} ({%} reduction)
Target    ─── ──── ───                              {target value}
```

---

## 7. File Change Summary

| # | Action | File Path | Phase | Description | Trade-off |
|:-:|:------:|:----------|:-----:|:------------|:----------|
| 1 | MODIFY | `{path}` | {1} | {Optimization description} | {What was traded for speed} |
| 2 | MODIFY | `{path}` | {2} | {description} | {trade-off} |

---

## 8. Post-Optimization Verification

- [ ] All performance targets from §1.1 are met (see §6)
- [ ] All existing tests pass: `{command}`
- [ ] No lint/type errors: `{command}`
- [ ] Benchmark script committed for future regression testing: `{path}`
- [ ] Trade-offs documented in code comments where applicable
- [ ] {Any domain-specific validation}

### Regression Prevention

<!-- How to prevent performance from regressing in the future -->

| Method | Details |
|:-------|:--------|
| **Benchmark in CI** | {e.g., "Add `pytest-benchmark` to CI pipeline with threshold alerts"} |
| **Performance test** | {e.g., "Committed benchmark script at `tests/benchmarks/test_perf.py`"} |
| **Monitoring** | {e.g., "Dashboard tracks p95 latency — alert if > 150ms"} |

---

## Appendix A: Trade-off Register

<!-- Complete record of what was traded for performance -->

| Optimization | Gained | Cost | Reversible? |
|:-------------|:-------|:-----|:-----------:|
| {O1} | {e.g., "60% faster execution"} | {e.g., "2x memory for hash table"} | {Yes — revert to linear scan} |
| {O2} | {e.g., "25% fewer I/O calls"} | {e.g., "Batch query is harder to debug"} | {Yes — revert to individual queries} |

## Appendix B: Profiling Data

<!-- Raw profiling output for reference -->

```
{Full profiler output — cProfile dump, flame graph data, etc.}
```

## Appendix C: References

- {Reference 1 — e.g., "Profiling guide: `docs/performance.md`"}
- {Reference 2 — e.g., "Algorithm reference: `https://example.com/big-o-cheatsheet`"}
