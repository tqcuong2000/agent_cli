# Data-Driven System Specification

## 1. Problem Statement
The current codebase contains hard-coded values scattered across multiple modules — model context windows, pricing tables, effort constraints, prompt templates, safe command patterns, tool defaults, and summarizer internals. This causes three problems:

1. **Modification requires code changes** — tweaking a value means editing Python source.
2. **Duplication causes inconsistency** — the same value defined in multiple places drifts (e.g. `_DEFAULT_DENY_PATTERNS` in `strict.py` duplicates `workspace_deny_patterns` in `AgentSettings`; `_MAX_CONSECUTIVE_SCHEMA_ERRORS` in `base.py` shadows `max_consecutive_schema_errors` in `AgentSettings`).
3. **Harder to maintain** — adding a new model requires touching `cost.py`, `budget.py`, and `config.py`.

## 2. Design Principles

- **Separation**: Data-driven settings (developer-facing) are distinct from user configuration (user-facing). They live in different locations and serve different audiences.
- **Read-only**: Data-driven settings are immutable at runtime. No code mutates them after load.
- **No user override**: Users cannot override data-driven settings. User configuration (`AgentSettings`) remains the user-facing layer and is unchanged.
- **Single source of truth**: Each value is defined in exactly one data file. Code references the registry, never a local constant.
- **Backward compatible**: The existing TOML loading chain and user config file format are preserved. `AgentSettings` is slimmed down by removing internal tuning fields that were never meaningful user choices.

## 3. Settings vs Configuration

### Data-Driven Settings (Developer-facing)
- **Audience**: Developers maintaining the system.
- **Location**: `agent_cli/data/*.toml` and `agent_cli/data/prompts/*.txt` — shipped inside the package.
- **Purpose**: Define system behavior defaults that were previously hard-coded.
- **Mutability**: Immutable at runtime. Changed only via code/PR.
- **Override**: Not overridable by users.

### User Configuration (User-facing)
- **Audience**: End users of the CLI.
- **Location**: `~/.agent_cli/config.toml` (global), `.agent_cli/settings.toml` (workspace), env vars, CLI flags.
- **Purpose**: Customize behavior per-user or per-project.
- **Mutability**: User-editable at any time.
- **Override chain** (lowest → highest precedence):
  `AgentSettings field defaults → Global TOML → Workspace TOML → .env → Env vars → CLI flags`

The two systems are independent. Data-driven settings are not exposed in user configuration.

### AgentSettings Migration

13 fields are removed from `AgentSettings` and moved to data-driven files. These are internal tuning parameters that users have no meaningful reason to set:

**Removed from AgentSettings → `models.toml`:**
- `routing_model` — internal routing decision; user picks `default_model`, system decides the cheap routing model.
- `summarization_model` — internal implementation detail of context compaction.

**Removed from AgentSettings → `memory.toml`:**
- `context_budget_system_prompt_pct` — token budget math; users don't think in context window percentages.
- `context_budget_summary_pct` — internal memory management tuning.
- `context_budget_response_reserve_pct` — internal memory management tuning.
- `context_compaction_threshold` — internal summarization trigger threshold.
- `session_auto_save_interval_seconds` — internal autosave timing (users toggle `session_auto_save` on/off; they don't tune the interval).

**Removed from AgentSettings → `memory.toml [retry]`:**
- `llm_max_retries` — internal retry behavior for API errors.
- `llm_retry_base_delay` — internal backoff timing.
- `llm_retry_max_delay` — internal backoff cap.

**Removed from AgentSettings → `schema.toml`:**
- `max_consecutive_schema_errors` — internal error budget before failing.

**Removed from AgentSettings → `tools.toml`:**
- `terminal_max_lines` — internal RAM buffer size.
- `workspace_index_max_files` — internal indexer limit.

**Fields that remain in AgentSettings** (genuine user preferences):
`default_model`, `providers`, `core`, `default_effort_level`, `max_task_retries`, `tool_output_max_chars`, `workspace_deny_patterns`, `workspace_allow_overrides`, `disabled_tools`, `auto_approve_tools`, `auto_approve_safe_commands`, `approval_timeout_seconds`, `show_agent_thinking`, `execution_mode`, `log_level`, `log_directory`, `session_auto_save`, `session_retention_days`, `semantic_memory_enabled`, `semantic_memory_auto_learn`, API keys.

## 4. Folder Structure

```
agent_cli/
├── data/                           # NEW: data-driven defaults package
│   ├── __init__.py                 # Package marker + convenience re-exports
│   ├── registry.py                 # DataRegistry class
│   ├── models.toml                 # Model registry (context windows, pricing, providers)
│   ├── effort.toml                 # Effort level constraints
│   ├── tools.toml                  # Tool defaults + safe command patterns
│   ├── memory.toml                 # Summarizer and token counter defaults
│   ├── schema.toml                 # Schema validation constraints
│   └── prompts/                    # Prompt templates (plain text)
│       ├── output_format.txt       # XML output format instructions
│       ├── output_format_native.txt # Native FC variant
│       ├── clarification_policy.txt # ask_user policy
│       └── default_persona.txt     # Default agent persona
```

## 5. Data File Specifications

### 5.1 `models.toml` — Model Registry

Consolidates: `cost.py:PRICING_TABLE`, `budget.py:infer_model_max_context()`, `config.py:_BUILTIN_PROVIDERS`, `token_counter.py:_O200K_PREFIXES`, and `AgentSettings.routing_model` / `AgentSettings.summarization_model`.

```toml
# ── Internal Model Selection ─────────────────────────────────────
# These models are used internally by the system, not chosen by users.
[internal_models]
routing_model = "gemini-2.5-flash-lite"
summarization_model = "gemini-2.5-flash-lite"

# ── Context Windows ──────────────────────────────────────────────
[context_windows]
"gpt-4o"                    = 128_000
"o1"                        = 200_000
"o3"                        = 200_000
"gemini-1.5-pro"            = 2_000_000

[[context_window_prefixes]]
prefix = "gpt-4o"
tokens = 128_000

[[context_window_prefixes]]
prefix = "gpt-4.1"
tokens = 128_000

[[context_window_prefixes]]
prefix = "gpt-5"
tokens = 128_000

[[context_window_prefixes]]
prefix = "o1"
tokens = 200_000

[[context_window_prefixes]]
prefix = "o3"
tokens = 200_000

[[context_window_prefixes]]
prefix = "o4"
tokens = 200_000

[[context_window_prefixes]]
prefix = "claude"
tokens = 200_000

[[context_window_prefixes]]
prefix = "gemini-1.5-pro"
tokens = 2_000_000

[[context_window_prefixes]]
prefix = "gemini"
tokens = 1_000_000

default_context_window = 128_000

# ── Pricing (USD per 1M tokens) ─────────────────────────────────
[pricing]
"gpt-4.5"              = { input = 75.00,  output = 150.00 }
"gpt-4.5-mini"         = { input = 0.40,   output = 1.60 }
"gpt-4o"               = { input = 2.50,   output = 10.00 }
"gpt-4o-mini"          = { input = 0.15,   output = 0.60 }
"gpt-5"                = { input = 2.00,   output = 8.00 }
"gpt-5-mini"           = { input = 0.30,   output = 1.20 }
"o3"                   = { input = 2.00,   output = 8.00 }
"o3-mini"              = { input = 1.10,   output = 4.40 }
"o3-pro"               = { input = 20.00,  output = 80.00 }
"o4-mini"              = { input = 1.10,   output = 4.40 }
"codex-mini"           = { input = 1.50,   output = 6.00 }
"claude-sonnet-4.6"    = { input = 3.00,   output = 15.00 }
"claude-opus-4.6"      = { input = 15.00,  output = 75.00 }
"claude-haiku-4.5"     = { input = 0.80,   output = 4.00 }
"claude-sonnet-4"      = { input = 3.00,   output = 15.00 }
"claude-opus-4"        = { input = 15.00,  output = 75.00 }
"claude-3-5-sonnet"    = { input = 3.00,   output = 15.00 }
"claude-3-opus"        = { input = 15.00,  output = 75.00 }
"gemini-2.5-pro"       = { input = 1.25,   output = 10.00 }
"gemini-2.5-flash"     = { input = 0.15,   output = 0.60 }
"gemini-2.0-flash"     = { input = 0.10,   output = 0.40 }
"llama-3-8b"           = { input = 0.0,    output = 0.0 }
"codestral"            = { input = 0.0,    output = 0.0 }

default_pricing = { input = 0.0, output = 0.0 }

# ── Tokenizer Encoding Selection ────────────────────────────────
[tokenizer]
o200k_prefixes = ["gpt-4o", "gpt-4.1", "gpt-5", "o1", "o3", "o4"]
default_encoding = "cl100k_base"

# ── Built-in Providers ──────────────────────────────────────────
[providers.openai]
adapter_type = "openai"
models = ["gpt-4o", "gpt-4o-mini", "o1", "o1-mini"]
default_model = "gpt-4o"

[providers.anthropic]
adapter_type = "anthropic"
models = [
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
]
default_model = "claude-3-5-sonnet-20241022"

[providers.google]
adapter_type = "google"
models = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-pro",
]
default_model = "gemini-2.5-flash-lite"

[providers.huggingface]
adapter_type = "openai_compatible"
base_url = "https://router.huggingface.co/v1"
models = [
    "mistralai/Mistral-7B-Instruct-v0.3",
    "meta-llama/Llama-3.1-8B-Instruct",
    "microsoft/Phi-3-mini-4k-instruct",
]
default_model = "mistralai/Mistral-7B-Instruct-v0.3"

[providers.openrouter]
adapter_type = "openai_compatible"
base_url = "https://openrouter.ai/api/v1"
models = [
    "anthropic/claude-3.5-sonnet",
    "deepseek/deepseek-chat",
    "google/gemini-2.5-flash",
    "meta-llama/llama-3.1-8b-instruct",
]
default_model = "anthropic/claude-3.5-sonnet"
```

### 5.2 `effort.toml` — Effort Level Constraints

Consolidates: `config.py:_DEFAULT_EFFORT_CONSTRAINTS`.

```toml
[LOW]
max_iterations = 30
model_tier = "fast"
reasoning_instruction = "Be concise. Act immediately when the path is clear."
review_policy = "none"

[MEDIUM]
max_iterations = 50
model_tier = "capable"
reasoning_instruction = "Think step-by-step. Explain your reasoning before acting."
review_policy = "standard"

[HIGH]
max_iterations = 100
model_tier = "premium"
reasoning_instruction = "Think deeply. Consider multiple approaches before choosing one. After completing the task, review your work for correctness."
review_policy = "self_verify"

[XHIGH]
max_iterations = 250
model_tier = "premium"
reasoning_instruction = "Think exhaustively. Leave no stone unturned. Methodically plan every step, verify all assumptions, and rigorously double-check the final result."
review_policy = "strict_self_verify"
```

### 5.3 `tools.toml` — Tool Defaults and Safety

Consolidates: `shell_tool.py:_SAFE_COMMAND_PATTERNS`, `shell_tool.py` defaults, `file_tools.py` defaults, `output_formatter.py` defaults, `executor.py` timeout.

```toml
[shell]
default_timeout = 30
max_timeout = 120
safe_command_patterns = [
    '^(ls|dir|cat|type|echo|pwd|cd|head|tail|wc|grep|find|which|whoami|date|env)\b',
    '^python\s+-c\s+[\'"]print\b',
    '^(git\s+(status|log|diff|branch|show))\b',
    '^(pip|uv)\s+(list|show|freeze)\b',
    '^pytest\b',
    '^(node|python|ruby|go)\s+--version\b',
]

[output_formatter]
error_truncation_chars = 2000

[file_tools]
list_directory_default_depth = 2
search_files_default_max_results = 50
diff_context_lines = 2
diff_max_lines = 60

[executor]
approval_timeout_seconds = 300.0

# Migrated from AgentSettings (internal implementation details)
[workspace]
terminal_max_lines = 2000
index_max_files = 5000
```

### 5.4 `memory.toml` — Memory and Summarizer Defaults

Consolidates: `summarizer.py` defaults, `token_counter.py:chars_per_token`, `react_loop.py:StuckDetector`, and migrated `AgentSettings` fields for context budgets, retry behavior, and session timing.

```toml
# Migrated from AgentSettings (internal tuning — not user-facing)
[context_budget]
system_prompt_pct = 0.15
summary_pct = 0.10
response_reserve_pct = 0.20
compaction_threshold = 0.80

[retry]
llm_max_retries = 3
llm_retry_base_delay = 1.0
llm_retry_max_delay = 30.0

[session]
auto_save_interval_seconds = 300.0

# Existing hard-coded values
[summarizer]
keep_recent_turns = 5
summary_budget_tokens = 2000
summary_response_tokens = 600
summary_max_words = 250
min_summary_length = 240
summary_truncation_factor = 0.8

[summarizer.heuristic_limits]
max_goals = 4
max_decisions = 4
max_actions = 6
max_tools = 6
max_files = 8
max_open_items = 4
condensed_line_max_chars = 140
single_line_max_chars = 500

[token_counter]
heuristic_chars_per_token = 4.0

[stuck_detector]
threshold = 3
history_cap = 10
```

### 5.5 `schema.toml` — Schema Validation Constraints

Consolidates: `schema.py` title validation, `base.py:_MAX_CONSECUTIVE_SCHEMA_ERRORS`.

```toml
[title]
min_words = 2
max_words = 15

[validation]
max_consecutive_schema_errors = 3
```

### 5.6 Prompt Templates (`prompts/*.txt`)

Each file contains raw prompt text with `{variable}` placeholders for runtime substitution.

**`prompts/output_format.txt`** — XML mode output format:
```
# Output Format
You MUST structure every response as follows:

1. **Title**: Provide a short title in <title> tags (1 to {title_max_words} words).
2. **Thinking**: Wrap your reasoning chain in <thinking> tags.
3. **Action**: If you need to use a tool, wrap it in <action> tags:
   <action><tool>tool_name</tool><args>{"key": "value"}</args></action>
4. **Final Answer**: When the task is complete AND you are absolutely done, provide your **COMPLETE** response (including all tables, lists, and code) strictly inside <final_answer> tags:
   <final_answer>Your response to the user.</final_answer>

**STRICT TAG ENFORCEMENT:**
- EVERYTHING you want the user to see MUST be inside <final_answer>.
- Content outside of <thinking>, <action>, or <final_answer> WILL BE DISCARDED.

**CRITICAL ANTI-HALLUCINATION RULE:**
If you decide to use a tool, YOU MUST STOP IMMEDIATELY after defining the action. DO NOT continue writing the `<final_answer>`. DO NOT guess or invent the output of the tool. Wait for the system to execute the tool and provide the result back to you.

You must ALWAYS include both <title> and <thinking> before any action or final answer.
Required skeleton:
<title>Short 1-{title_max_words} word title</title>
<thinking>Your reasoning chain here.</thinking>
<final_answer>Your COMPLETE response here.</final_answer>
```

**`prompts/output_format_native.txt`** — Native FC variant (action step differs):
```
# Output Format
You MUST structure every response as follows:

1. **Title**: Provide a short title in <title> tags (1 to {title_max_words} words).
2. **Thinking**: Wrap your reasoning chain in <thinking> tags.
3. **Action**: To use a tool, call the function natively (as defined by the API). DO NOT write an <action> XML tag.
4. **Final Answer**: When the task is complete AND you are absolutely done, provide your **COMPLETE** response (including all tables, lists, and code) strictly inside <final_answer> tags:
   <final_answer>Your response to the user.</final_answer>

**STRICT TAG ENFORCEMENT:**
- EVERYTHING you want the user to see MUST be inside <final_answer>.
- Content outside of <thinking>, <action>, or <final_answer> WILL BE DISCARDED.

**CRITICAL ANTI-HALLUCINATION RULE:**
If you decide to use a tool, YOU MUST STOP IMMEDIATELY after defining the action. DO NOT continue writing the `<final_answer>`. DO NOT guess or invent the output of the tool. Wait for the system to execute the tool and provide the result back to you.

You must ALWAYS include both <title> and <thinking> before any action or final answer.
Required skeleton:
<title>Short 1-{title_max_words} word title</title>
<thinking>Your reasoning chain here.</thinking>
<final_answer>Your COMPLETE response here.</final_answer>
```

**`prompts/clarification_policy.txt`**:
```
# Clarification Policy
When you need to ask the user any question, you MUST use the `ask_user` tool.
Do NOT ask questions directly in `<final_answer>` while the task is still in progress.
Use 2-5 likely answer options in `ask_user` and wait for the tool result before continuing.
```

**`prompts/default_persona.txt`**:
```
You are a helpful, expert AI assistant. You have access to tools that let you interact with the user's system.
```

## 6. DataRegistry API

### 6.1 Class Definition

```python
class DataRegistry:
    """Read-only registry of data-driven system defaults.

    Loads TOML data files and prompt templates from agent_cli/data/
    at construction time. Provides typed accessors for each domain.

    Immutable after construction — no runtime mutation.
    """

    def __init__(self) -> None: ...

    # ── Model Data ───────────────────────────────────────────────

    def get_context_window(self, model: str) -> int:
        """Lookup order:
        1. Exact match in [context_windows]
        2. Longest prefix match in [[context_window_prefixes]]
        3. default_context_window fallback
        """

    def get_pricing(self, model: str) -> dict[str, float]:
        """Return {"input": float, "output": float}.
        Returns default_pricing for unknown models.
        """

    def get_builtin_providers(self) -> dict[str, ProviderConfig]: ...

    def get_tokenizer_encoding(self, model: str) -> str:
        """Check o200k_prefixes, fall back to default_encoding."""

    def get_internal_models(self) -> dict[str, str]:
        """Return {"routing_model": str, "summarization_model": str}."""

    # ── Effort ───────────────────────────────────────────────────

    def get_effort_constraints(self, level: EffortLevel) -> dict[str, Any]: ...

    # ── Tools ────────────────────────────────────────────────────

    def get_safe_command_patterns(self) -> list[str]: ...
    def get_tool_defaults(self) -> dict[str, Any]: ...

    # ── Memory ───────────────────────────────────────────────────

    def get_context_budget(self) -> dict[str, float]: ...
    def get_retry_defaults(self) -> dict[str, Any]: ...
    def get_session_defaults(self) -> dict[str, Any]: ...
    def get_summarizer_defaults(self) -> dict[str, Any]: ...
    def get_token_counter_defaults(self) -> dict[str, Any]: ...
    def get_stuck_detector_defaults(self) -> dict[str, Any]: ...

    # ── Schema ───────────────────────────────────────────────────

    def get_schema_defaults(self) -> dict[str, Any]: ...

    # ── Prompts ──────────────────────────────────────────────────

    def get_prompt_template(self, name: str) -> str:
        """Load by name (without .txt extension).
        Supports {variable} placeholders — caller uses str.format().
        Raises FileNotFoundError if template does not exist.
        """
```

### 6.2 Loading Strategy

- Uses `importlib.resources` to locate files relative to the `agent_cli.data` package.
- All TOML files loaded and parsed at `__init__` time.
- Prompt templates loaded lazily on first `get_prompt_template()` call and cached.
- All loaded data stored in private `dict` fields — no public mutation API.

### 6.3 Error Handling

- Missing or malformed TOML files raise `RuntimeError` at construction — fail fast on startup.
- Missing prompt templates raise `FileNotFoundError` at call time.
- Unknown model lookups return documented defaults (never raise).

## 7. Integration Plan

### 7.1 AppContext Wiring

`DataRegistry` is constructed in `bootstrap.py:create_app()` before all other components:

```python
def create_app(...):
    data_registry = DataRegistry()    # first thing created
    settings = AgentSettings()        # unchanged
    ...
    context = AppContext(
        data_registry=data_registry,  # new field
        settings=settings,
        ...
    )
```

### 7.2 Module-by-Module Migration

**`providers/cost.py`**
- Remove `PRICING_TABLE` dict.
- `estimate_cost()` reads from `DataRegistry.get_pricing()`.

**`memory/budget.py`**
- Replace `infer_model_max_context()` if/elif chain with `DataRegistry.get_context_window()`.

**`core/config.py`**
- Remove `_BUILTIN_PROVIDERS` and `_DEFAULT_EFFORT_CONSTRAINTS` module-level dicts.
- `load_providers()` reads from `DataRegistry.get_builtin_providers()`.
- `AgentSettings.get_effort_config()` reads defaults from `DataRegistry.get_effort_constraints()`.
- Remove 13 fields from `AgentSettings` (see Section 3 "AgentSettings Migration" for the full list).
- Remove validators for removed fields (`_budget_percentages_sanity`).

**`memory/token_counter.py`**
- Read `chars_per_token` from `DataRegistry.get_token_counter_defaults()`.
- Read encoding prefixes from `DataRegistry.get_tokenizer_encoding()`.

**`tools/shell_tool.py`**
- Read safe patterns from `DataRegistry.get_safe_command_patterns()`.

**`tools/output_formatter.py`**
- Read `error_truncation_chars` from `DataRegistry.get_tool_defaults()`.

**`tools/file_tools.py`**
- Read `list_directory_default_depth`, `search_files_default_max_results`, `diff_context_lines`, `diff_max_lines` from `DataRegistry.get_tool_defaults()`.

**`tools/executor.py`**
- Read `approval_timeout_seconds` from `DataRegistry.get_tool_defaults()`.

**`memory/summarizer.py`**
- Read all defaults from `DataRegistry.get_summarizer_defaults()`.

**`agent/react_loop.py`**
- Read `StuckDetector` defaults from `DataRegistry.get_stuck_detector_defaults()`.
- Prompt methods delegate to `DataRegistry.get_prompt_template()`.

**`agent/schema.py`**
- Read title `min_words`, `max_words` from `DataRegistry.get_schema_defaults()`.

**`agent/base.py`**
- Remove `_MAX_CONSECUTIVE_SCHEMA_ERRORS = 3`. Read from `DataRegistry.get_schema_defaults()`.

**`agent/default.py`**
- Read persona from `DataRegistry.get_prompt_template("default_persona")`.
- Detect OS at runtime via `platform.system()` instead of hard-coded `"Operating System: Windows"`.

**`workspace/strict.py`**
- Remove `_DEFAULT_DENY_PATTERNS` duplicate. Constructor always receives patterns from `AgentSettings` via `bootstrap.py`.

## 8. Passing DataRegistry to Consumers

Components receive `DataRegistry` through one of two paths:

1. **Constructor injection** — Components created in `bootstrap.py` receive the registry as a constructor argument (preferred for `PromptBuilder`, `SummarizingMemoryManager`, tool classes, etc.).
2. **Via AppContext** — Components that already receive `AppContext` access it as `context.data_registry` (for late-bound or dynamically-resolved components).

No global singleton. No module-level access. The registry flows through existing dependency injection.

## 9. Testing Strategy

- **Unit tests for DataRegistry**: Verify all accessors return expected types and values. Test fallback behavior (unknown model → default). Test missing file → `RuntimeError`.
- **Integration tests**: Verify `bootstrap.py` constructs `DataRegistry` and injects it. Verify end-to-end that changed modules read from the registry.
- **Regression**: Existing tests pass unchanged — data files contain the same values that were previously hard-coded.
- **Data file validation**: A test that loads every TOML file and checks structural integrity (required keys present, types correct).

## 10. Files Changed Summary

**New files:**
- `agent_cli/data/__init__.py`
- `agent_cli/data/registry.py`
- `agent_cli/data/models.toml`
- `agent_cli/data/effort.toml`
- `agent_cli/data/tools.toml`
- `agent_cli/data/memory.toml`
- `agent_cli/data/schema.toml`
- `agent_cli/data/prompts/output_format.txt`
- `agent_cli/data/prompts/output_format_native.txt`
- `agent_cli/data/prompts/clarification_policy.txt`
- `agent_cli/data/prompts/default_persona.txt`

**Modified files:**
- `agent_cli/core/bootstrap.py` — create and inject `DataRegistry`
- `agent_cli/core/config.py` — remove `_BUILTIN_PROVIDERS`, `_DEFAULT_EFFORT_CONSTRAINTS`, and 13 `AgentSettings` fields; read from registry
- `agent_cli/providers/cost.py` — remove `PRICING_TABLE`; read from registry
- `agent_cli/memory/budget.py` — replace `infer_model_max_context()` internals
- `agent_cli/memory/token_counter.py` — read encoding prefixes and `chars_per_token` from registry
- `agent_cli/memory/summarizer.py` — read all defaults from registry
- `agent_cli/tools/shell_tool.py` — read safe patterns from registry
- `agent_cli/tools/output_formatter.py` — read error truncation from registry
- `agent_cli/tools/file_tools.py` — read tool defaults from registry
- `agent_cli/tools/executor.py` — read approval timeout from registry
- `agent_cli/agent/react_loop.py` — read stuck detector defaults and prompt templates from registry
- `agent_cli/agent/schema.py` — read title validation bounds from registry
- `agent_cli/agent/base.py` — remove `_MAX_CONSECUTIVE_SCHEMA_ERRORS`; read from registry
- `agent_cli/agent/default.py` — read persona from registry; detect OS at runtime
- `agent_cli/workspace/strict.py` — remove `_DEFAULT_DENY_PATTERNS` duplicate