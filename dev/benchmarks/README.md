# Benchmark Workflows

These benchmarks are designed to measure communication quality, tool discipline, and token/cost usage after runtime changes.

## Available Scenarios

- `empty_folder_communication_v1`
  - Three-turn empty-workspace benchmark for communication discipline.
  - Expected tools: `list_directory`, `search_files`, then no tools.
- `file_operations_tool_flow_v1`
  - Five-turn empty-workspace benchmark for file-operation tool flow.
  - Expected tools: `write_file`, `read_file`, `run_command`, `str_replace`, `run_command`.

All scenarios:
- Use the real app bootstrap and real orchestrator.
- Isolate session files and observability logs into one output directory.
- Disable session-title generation and capability probes during the benchmark to reduce noise.
- Export one combined artifact with:
  - per-turn prompt
  - final answer
  - explicit task-local session messages
  - observed tool calls
  - per-task token/cost metrics
  - task-specific log entries
  - aggregate totals

## Run It

```powershell
python X:\agent_cli\dev\tools\run_empty_folder_benchmark.py --model gemini-2.5-flash-lite
```

Run the file-operations scenario with the same runner:

```powershell
python X:\agent_cli\dev\tools\run_empty_folder_benchmark.py `
  --scenario file_operations_tool_flow_v1 `
  --model gemini-2.5-flash-lite
```

Or use the dedicated wrapper:

```powershell
python X:\agent_cli\dev\tools\run_file_operations_benchmark.py --model gemini-2.5-flash-lite
```

Optional explicit output/workspace:

```powershell
python X:\agent_cli\dev\tools\run_empty_folder_benchmark.py `
  --output X:\agent_cli\tmp\benchmarks\baseline `
  --workspace X:\agent_cli\tmp\benchmarks\baseline\workspace `
  --scenario empty_folder_communication_v1 `
  --model gemini-2.5-flash-lite `
  --agent default
```

Artifacts:

- `benchmark_run.json`
- `benchmark_report.md`
- `logs/session_*.jsonl`
- `logs/session_*.summary`
- `sessions/<session_id>.json`

## Compare Two Runs

```powershell
python X:\agent_cli\dev\tools\compare_benchmark_runs.py `
  X:\agent_cli\tmp\benchmarks\baseline\benchmark_run.json `
  X:\agent_cli\tmp\benchmarks\candidate\benchmark_run.json
```

## Why These Workflows Are Stable

- Empty workspace removes project-specific variance.
- Prompts require explicit headings and constrained tool usage.
- The file-operations workflow uses a deterministic single-file script and a fixed edit target.
- Title generation and provider capability probes are disabled for the benchmark run.
- Output includes compliance scores and answer similarity inputs for cross-run comparison.
