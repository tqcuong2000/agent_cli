This plan outlines the refactoring of the schema error handling logic in [BaseAgent](file:///x:/agent_cli/agent_cli/core/runtime/agents/base.py#101-1351) to be data-driven and strongly typed. It replaces fragile keyword matching with explicit error IDs (`schema.error.*`) and introduces a `SchemaErrorMapping` dataclass for robust configuration.

## Objective

- Migrate hardcoded schema error resolution rules from `BaseAgent._classify_schema_error` into [schema.json](file:///x:/agent_cli/agent_cli/data/schema.json).
- Shift from keyword string matching to explicit Error IDs (e.g., `schema.error.exceed_concurrent_actions`).
- Introduce a `SchemaErrorMapping` dataclass to strongly type the rules loaded from [schema.json](file:///x:/agent_cli/agent_cli/data/schema.json).
- Modify [SchemaValidationError](file:///x:/agent_cli/agent_cli/core/infra/events/errors.py#115-133) to accept an `error_id`.
- Update [MultiActionValidator](file:///x:/agent_cli/agent_cli/core/runtime/agents/multi_action_validator.py#23-91) to raise errors with explicit IDs.
- Refactor `BaseAgent._classify_schema_error` to map errors by ID.

## Proposed Changes

### [agent_cli/core/infra/config/config_models.py](file:///x:/agent_cli/agent_cli/core/infra/config/config_models.py)
#### [MODIFY] config_models.py(file:///x:/agent_cli/agent_cli/core/infra/config/config_models.py)
- Define a new dataclass `SchemaErrorMapping`:
  ```python
  @dataclass
  class SchemaErrorMapping:
      error_id: str
      code: str
      field: str
      expected: str
      received: str
      fix: str
      example: Optional[str] = None
      ask_user_fix: Optional[str] = None
      ask_user_example: Optional[str] = None
      extract_received: bool = False
  ```

### [agent_cli/core/infra/events/errors.py](file:///x:/agent_cli/agent_cli/core/infra/events/errors.py)
#### [MODIFY] errors.py(file:///x:/agent_cli/agent_cli/core/infra/events/errors.py)
- Update [SchemaValidationError](file:///x:/agent_cli/agent_cli/core/infra/events/errors.py#115-133) to accept an optional `error_id` parameter.
  ```python
  def __init__(
      self,
      message: str = "Schema validation failed",
      *,
      raw_response: Optional[str] = None,
      error_id: Optional[str] = None,
      **kwargs,
  ) -> None:
      super().__init__(message, **kwargs)
      self.raw_response = raw_response
      self.error_id = error_id
  ```

### [agent_cli/core/runtime/agents/multi_action_validator.py](file:///x:/agent_cli/agent_cli/core/runtime/agents/multi_action_validator.py)
#### [MODIFY] multi_action_validator.py(file:///x:/agent_cli/agent_cli/core/runtime/agents/multi_action_validator.py)
- Update validation raises to include explicit `error_id`s:
  - `raise SchemaValidationError("... exceeds maximum ...", error_id="schema.error.exceed_concurrent_actions")`
  - `raise SchemaValidationError("Duplicate action_id ...", error_id="schema.error.duplicate_action_id")`
  - `raise SchemaValidationError("Unknown tool ...", error_id="schema.error.unknown_tool")`
  - `raise SchemaValidationError("execute_actions requires a non-empty actions list.", error_id="schema.error.empty_actions_list")`

### [agent_cli/data/schema.json](file:///x:/agent_cli/agent_cli/data/schema.json)
#### [MODIFY] schema.json(file:///x:/agent_cli/agent_cli/data/schema.json)
- Add a new `error_mappings` array to the `validation` key.
- Each mapping will use an [id](file:///x:/agent_cli/agent_cli/core/runtime/agents/multi_action_validator.py#37-91) instead of a [match](file:///x:/agent_cli/agent_cli/core/runtime/tools/file_tools.py#207-221) string.
- Example structure:
  ```json
  "error_mappings": [
    {
      "id": "schema.error.exceed_concurrent_actions",
      "code": "batch_size_exceeded",
      "field": "decision.actions",
      "expected": "fewer_actions",
      "received": "too_many",
      "fix": "CRITICAL: {message} You must reduce the number of actions in your batch and try again."
    },
    {
      "id": "schema.error.unknown_decision_type",
      "code": "enum_unknown",
      ...
      "extract_received": true
    }
  ]
  ```

### [agent_cli/core/infra/registry/registry.py](file:///x:/agent_cli/agent_cli/core/infra/registry/registry.py)
#### [MODIFY] registry.py(file:///x:/agent_cli/agent_cli/core/infra/registry/registry.py)
- Add a method `get_schema_error_mappings()` that returns a `dict[str, SchemaErrorMapping]`. It parses the JSON array and instantiates the dataclasses, returning a map keyed by `error_id`.

### [agent_cli/core/runtime/agents/schema.py](file:///x:/agent_cli/agent_cli/core/runtime/agents/schema.py)
#### [MODIFY] schema.py(file:///x:/agent_cli/agent_cli/core/runtime/agents/schema.py)
- (If necessary) Update the core `BaseSchemaValidator` (or Pydantic validation error parsing) to inject specific `error_id`s for standard validation failures (e.g., missing fields, invalid JSON), mapping standard error strings to IDs like `schema.error.invalid_json` or `schema.error.missing_field`. *Note: We will need to investigate how schema.py currently emits its errors to see if we can easily attach IDs there, otherwise BaseAgent might still need some minimal fallback parsing, or we just map everything we control in code.*

### [agent_cli/core/runtime/agents/base.py](file:///x:/agent_cli/agent_cli/core/runtime/agents/base.py)
#### [MODIFY] base.py(file:///x:/agent_cli/agent_cli/core/runtime/agents/base.py)
- Refactor [_classify_schema_error(self, error: SchemaValidationError)](file:///x:/agent_cli/agent_cli/core/runtime/agents/base.py#1179-1311):
  - Fetch mappings via `self._data_registry.get_schema_error_mappings()`.
  - Lookup the mapping directly: `mapping = mappings.get(error.error_id)`.
  - If a mapping is found, use its fields to construct the recovery output. Handle `extract_received` logic.
  - Apply `{message}` formatting.
  - If no mapping is found (or no `error_id` exists), fall back to a generic schema error that includes `Validation Detail: {message}`.

## Verification Plan

### Automated Tests
Run the project's test suite to ensure no regressions occur and all reasoning loops pass properly:
```bash
uv run pytest
```
*Note: Any unit tests specifically verifying agent behavior under invalid schemas should continue passing due to identical fallback output formats, but will become extensible via [schema.json](file:///x:/agent_cli/agent_cli/data/schema.json) now.*
