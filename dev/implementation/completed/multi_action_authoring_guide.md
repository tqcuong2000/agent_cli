# Multi-Action Authoring Guide

## Decision Schema
Use exactly one JSON decision per turn.

Single action:
```json
{
  "title": "Read config",
  "thought": "Need one file first.",
  "decision": {
    "type": "execute_action",
    "tool": "read_file",
    "args": {"path": "pyproject.toml"}
  }
}
```

Multi action:
```json
{
  "title": "Read project files",
  "thought": "These reads are independent.",
  "decision": {
    "type": "execute_actions",
    "actions": [
      {"tool": "read_file", "args": {"path": "README.md"}},
      {"tool": "search_files", "args": {"pattern": "TODO"}}
    ]
  }
}
```

## When To Use `execute_actions`
- Use it for independent tool calls that do not depend on each other.
- Prefer `execute_action` for one tool call or when ordering dependency exists.
- Avoid grouping unrelated risky operations into one batch.

## `parallel_safe` Guide For New Tools
- Default `parallel_safe=True` for read-only/idempotent tools.
- Set `parallel_safe=False` for:
  - file mutation (`write_file`, `str_replace`, `insert_lines`)
  - shell execution (`run_command`)
  - human interaction (`ask_user`)
- Rule of thumb: if concurrent execution can cause races, mark it unsafe.

## `ask_user` Singleton Rule
- `ask_user` must be the only action in a batch.
- If batched with other tools, runtime strips the rest and executes only `ask_user`.
- This behavior is logged and counted via `multi_action.ask_user_strip_count`.

## Agent Config Overrides
Configure per agent:
```python
AgentConfig(
    name="coder",
    tools=["read_file", "search_files", "write_file", "ask_user"],
    multi_action_enabled=True,
    max_concurrent_actions=5,
)
```

Defaults:
- `multi_action_enabled=False`
- `max_concurrent_actions=5`

## Native Tool Call Notes
- Native multi-tool responses are accepted when multi-action is enabled.
- History serialization includes per-call `action_id` for traceability.

## Troubleshooting
- Error: `decision.actions must be a non-empty list`
  - Ensure `decision.type=execute_actions` includes an array with at least one action.
- Error: `Unknown tool ... in action[idx]`
  - Use only tools registered for the agent.
- Repeated batch warning
  - Runtime detected same batch+outputs repeatedly; choose a different strategy.
- Frequent `ask_user` stripping
  - Prompt should emit only `ask_user` when clarification is needed.
