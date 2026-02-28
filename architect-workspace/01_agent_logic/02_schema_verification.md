# Schema Verification & Response Handling Architecture

## Overview
If agents are allowed to return unstructured text, parsing tool requests or state transitions becomes impossible. A rigid **Schema Verification System** ensures that *every* LLM response conforms to a predictable, machine-readable format before it is processed by the Event Bus or State Manager.

The Schema Validator operates in **dual mode**: handling both native function calling responses (from top-tier providers) and XML-prompted responses (from local models), while maintaining a single consistent output format.

## 1. What is a Schema Validator and What Does it Do?
A Schema Validator is a defensive layer between the LLM's response and the rest of your system. 
- **Validation:** It checks if the output matches a predefined structure — either a structured tool call object (native FC) or XML `<action>` tags (prompting mode).
- **Coercion/Fixing:** It attempts to fix minor formatting errors (e.g., adding a missing closing XML tag or fixing unescaped quotes in JSON arguments).
- **Feedback Loop:** If the output is completely malformed, the validator *does not* crash the CLI. Instead, it catches the error and generates an automatic feedback prompt to the LLM: *"Your last response was invalid. Error: Missing required field 'query'. Please try again."*

---

## 2. Dual-Mode Operation

The Schema Validator has a **single class** with a mode switch, determined by the `LLMResponse.tool_mode` field from the provider (see `01_ai_providers.md`).

### Mode A: Native Function Calling (`ToolCallMode.NATIVE`)
When the provider supports native FC (OpenAI, Anthropic, Gemini), tool calls arrive as **structured JSON objects** already parsed by the provider's API.

**What the validator does:**
1. **`<thinking>` extraction**: Parse `<thinking>` tags from `LLMResponse.text_content` (identical to XML mode).
2. **Tool call validation**: Verify the structured `ToolCall` objects have valid tool names and required arguments. No XML parsing needed — the API already enforced the schema.
3. **Final answer detection**: If no tool calls and text content contains a `<final_answer>` tag or plain text response → treat as final answer.

**Schema error rate:** ~0%. The API enforces tool schemas. Validation is mostly a safety check.

### Mode B: XML Prompting (`ToolCallMode.XML`)
When the provider does NOT support native FC (Ollama, local models), the entire response is raw text containing XML tags.

**What the validator does:**
1. **`<thinking>` extraction**: Parse `<thinking>` tags from text (identical to native mode).
2. **`<action>` parsing**: Extract `<tool>` and `<args>` from `<action>` blocks. Convert to `ToolCall` objects.
3. **Coercion**: Attempt to fix common XML errors (missing closing tags, unescaped quotes).
4. **Final answer detection**: If no `<action>` and text contains `<final_answer>` → treat as final answer.

**Schema error rate:** ~5-10%. Requires the feedback loop for self-correction.

### Why `<thinking>` Is Consistent Across Both Modes
Regardless of tool calling mode, **all providers are instructed to emit `<thinking>` tags in their text content**. This means:
- The Streaming Parser has **one parsing path** for `<thinking>` — it works identically for Anthropic and Ollama.
- The TUI always receives `AgentMessageEvent(is_monologue=True)` through the same mechanism.

---

## 3. Which Format Should the Agent Return?

### For XML Prompting Mode (Local Models):
**XML** is the recommended format for the outer reasoning loop, while **JSON** is used for tool arguments.

Why XML is superior to JSON for Agent text output:
1. **Streaming Stability:** Streaming a partial XML tag (like `<thou...`) is much easier to parse line-by-line than a massive, unclosed JSON object.
2. **Tolerance for Quotes:** LLMs frequently break JSON by forgetting to escape internal double-quotes. XML handles internal quotes naturally: `<command>echo "hello"</command>`.
3. **Model Training:** Modern models (especially Claude) are trained heavily on XML tags for structural boundaries.

**The standardized XML response (for prompting mode only):**
```xml
<thinking>
I need to calculate the sum. I will use the python_executor tool.
</thinking>
<action>
    <tool>python_executor</tool>
    <args>{"code": "print(2 + 2)"}</args>
</action>
```

### For Native FC Mode (Top-Tier Providers):
The model returns `<thinking>` in text content + structured tool calls via the API. **No `<action>` XML tags are generated.**

```
Text content:  "<thinking>I need to calculate the sum.</thinking>"
Tool calls:    [{"name": "python_executor", "input": {"code": "print(2 + 2)"}}]
```

---

## 4. The Response Pipeline (Three Phases)

Both modes follow the same three-phase pipeline:

### Phase 1: Streaming Parser (Real-Time `<thinking>`)
As tokens arrive from the LLM stream:
1. A lightweight parser looks for `<thinking>` and `</thinking>` tags.
2. Anything inside `<thinking>` is immediately published as `AgentMessageEvent(is_monologue=True)` to the Event Bus.
3. The TUI updates in real-time without waiting for the full response.

**This phase is identical for both modes.** Native FC providers stream their text content, which contains `<thinking>` tags.

### Phase 2: Buffer
The rest of the response is buffered in memory until the LLM stops generating.

### Phase 3: Dual-Mode Validation
The complete `LLMResponse` is passed to the Schema Validator:

```
┌───────────────────────────────────────────────┐
│              LLMResponse arrives              │
└───────────────────┬───────────────────────────┘
                    │
          ┌─────────┴──────────┐
          │  tool_mode check   │
          └──┬──────────────┬──┘
             │              │
    NATIVE   │              │   XML
             ▼              ▼
  ┌──────────────┐  ┌──────────────────┐
  │ Validate     │  │ Parse <action>   │
  │ structured   │  │ tags from text   │
  │ tool_calls   │  │ Extract tool +   │
  │ (name, args) │  │ args, coerce     │
  └──────┬───────┘  └────────┬─────────┘
         │                   │
         └────────┬──────────┘
                  │
                  ▼
         ┌──────────────┐
         │ AgentResponse │   ← Identical output regardless of mode
         │  .thought     │
         │  .action      │
         │  .final_answer│
         └──────────────┘
```

---

## 5. The Structured Data Classes

```python
from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class ParsedAction:
    """A validated tool invocation, ready for the Tool Executor."""
    tool_name: str
    arguments: Dict[str, Any]
    native_call_id: str = ""  # Populated in native FC mode (for response pairing)


@dataclass
class AgentResponse:
    """
    The unified output of the Schema Validator.
    Identical structure regardless of whether the response came from
    native FC or XML prompting.
    """
    thought: str = ""                        # Extracted from <thinking> tags
    action: Optional[ParsedAction] = None    # Tool call (if any)
    final_answer: Optional[str] = None       # Final response (if no action)
```

---

## 6. The Schema Validator (Single Class, Dual Mode)

```python
from abc import ABC, abstractmethod
from enum import Enum, auto
import re
import json
import logging

logger = logging.getLogger(__name__)


class BaseSchemaValidator(ABC):
    """
    Validates and normalizes LLM responses into AgentResponse objects.
    Supports dual mode: native function calling and XML prompting.
    """
    
    @abstractmethod
    def parse_and_validate(self, response: "LLMResponse") -> AgentResponse:
        """
        Parse and validate an LLM response.
        Automatically selects the correct parsing mode based on response.tool_mode.
        
        Returns an AgentResponse if valid.
        Raises SchemaValidationError if the response is malformed.
        """
        pass
    
    @abstractmethod
    def extract_thinking(self, text: str) -> str:
        """
        Extract content from <thinking> tags.
        Used by both modes (shared parsing logic).
        """
        pass


class SchemaValidator(BaseSchemaValidator):
    """
    Production implementation with dual-mode support.
    """
    
    def __init__(self, registered_tools: list[str]):
        # Known tool names for validation
        self._registered_tools = set(registered_tools)
    
    def parse_and_validate(self, response: "LLMResponse") -> AgentResponse:
        # ── Step 1: Extract thinking (same for both modes) ──
        thinking = self.extract_thinking(response.text_content)
        
        # ── Step 2: Mode-specific action parsing ──
        if response.tool_mode == ToolCallMode.NATIVE:
            action = self._parse_native_fc(response)
        else:
            action = self._parse_xml_prompting(response.text_content)
        
        # ── Step 3: Check for final answer ──
        final_answer = None
        if action is None:
            final_answer = self._extract_final_answer(response.text_content)
        
        # ── Step 4: Validate at least one output exists ──
        if action is None and final_answer is None and not thinking:
            raise SchemaValidationError(
                "Response contains no <thinking>, no tool call, and no final answer. "
                "Please respond with either a tool action or a final answer."
            )
        
        return AgentResponse(
            thought=thinking,
            action=action,
            final_answer=final_answer
        )
    
    def extract_thinking(self, text: str) -> str:
        """Extract content between <thinking> tags."""
        match = re.search(r"<thinking>(.*?)</thinking>", text, re.DOTALL)
        return match.group(1).strip() if match else ""
    
    # ── Native FC Parsing (Trivial — already structured) ────────────

    def _parse_native_fc(self, response: "LLMResponse") -> Optional[ParsedAction]:
        """
        Parse structured tool calls from native function calling.
        The API already enforced the schema, so this is mostly validation.
        """
        if not response.tool_calls:
            return None
        
        # Take the first tool call (multi-tool support is a future extension)
        tc = response.tool_calls[0]
        
        # Validate tool name exists in our registry
        if tc.tool_name not in self._registered_tools:
            raise SchemaValidationError(
                f"Unknown tool '{tc.tool_name}'. "
                f"Available tools: {', '.join(sorted(self._registered_tools))}"
            )
        
        return ParsedAction(
            tool_name=tc.tool_name,
            arguments=tc.arguments,
            native_call_id=tc.native_call_id
        )
    
    # ── XML Prompting Parsing (Complex — needs coercion) ────────────

    def _parse_xml_prompting(self, text: str) -> Optional[ParsedAction]:
        """
        Parse <action> XML tags from raw text output.
        Includes coercion for common LLM formatting errors.
        """
        # Look for <action>...</action> block
        action_match = re.search(
            r"<action>(.*?)</action>", text, re.DOTALL
        )
        if not action_match:
            return None
        
        action_block = action_match.group(1)
        
        # Extract <tool> name
        tool_match = re.search(r"<tool>(.*?)</tool>", action_block, re.DOTALL)
        if not tool_match:
            raise SchemaValidationError(
                "Found <action> block but missing <tool> tag. "
                "Expected format: <action><tool>name</tool><args>{...}</args></action>"
            )
        tool_name = tool_match.group(1).strip()
        
        # Validate tool name
        if tool_name not in self._registered_tools:
            raise SchemaValidationError(
                f"Unknown tool '{tool_name}'. "
                f"Available tools: {', '.join(sorted(self._registered_tools))}"
            )
        
        # Extract <args> JSON
        args_match = re.search(r"<args>(.*?)</args>", action_block, re.DOTALL)
        if not args_match:
            raise SchemaValidationError(
                f"Tool '{tool_name}' is missing <args> block."
            )
        
        raw_args = args_match.group(1).strip()
        try:
            arguments = json.loads(raw_args)
        except json.JSONDecodeError as e:
            # Attempt coercion: fix common quote escaping issues
            coerced = self._attempt_json_coercion(raw_args)
            if coerced is not None:
                arguments = coerced
                logger.warning(f"Coerced malformed JSON args for tool '{tool_name}'")
            else:
                raise SchemaValidationError(
                    f"Invalid JSON in <args> for tool '{tool_name}': {e}. "
                    f"Raw content: {raw_args[:200]}"
                )
        
        return ParsedAction(tool_name=tool_name, arguments=arguments)
    
    def _extract_final_answer(self, text: str) -> Optional[str]:
        """Extract <final_answer> or treat remaining text as the answer."""
        match = re.search(r"<final_answer>(.*?)</final_answer>", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        
        # If no tags at all, the clean text (minus <thinking>) might be the answer
        clean = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL).strip()
        return clean if clean else None
    
    def _attempt_json_coercion(self, raw: str) -> Optional[dict]:
        """
        Attempt to fix common JSON formatting errors from LLMs:
        - Unescaped internal double quotes
        - Trailing commas
        - Single quotes instead of double quotes
        """
        try:
            # Try replacing single quotes with double quotes
            fixed = raw.replace("'", '"')
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        
        try:
            # Try removing trailing commas
            fixed = re.sub(r",\s*}", "}", raw)
            fixed = re.sub(r",\s*]", "]", fixed)
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        
        return None
```

---

## 7. Integration with the Agent Loop

Inside the `BaseAgent` `while True` loop:

```python
try:
    # 1. Get normalized response from LLM provider
    llm_response: LLMResponse = await self.provider.safe_generate(
        self.memory.get_working_context(),
        tools=self.tool_definitions
    )
    
    # 2. Validate and convert to AgentResponse (mode-aware)
    response: AgentResponse = self.validator.parse_and_validate(llm_response)
    
    # 3. Process (identical regardless of mode)
    if response.action:
        result = await self.tool_executor.execute(response.action)
        self.memory.add_working_event({"role": "tool", "content": result})
    elif response.final_answer:
        return response.final_answer
        
except SchemaValidationError as e:
    # 4. The Feedback Loop (primarily triggered in XML mode)
    self.memory.add_working_event({
        "role": "user", 
        "content": f"Schema Error: {str(e)}. Fix your formatting."
    })
    # Loop restarts automatically
```

**Key insight:** The Agent loop code is **identical** regardless of whether native FC or XML prompting is used. The `SchemaValidator` and `BaseLLMProvider` abstract away all mode differences. The Agent only sees `AgentResponse` objects.

---

## 8. Mode Comparison Summary

| Aspect | Native FC Mode | XML Prompting Mode |
|---|---|---|
| **`<thinking>` parsing** | Same (from text content) | Same (from text content) |
| **Tool call parsing** | Trivial (structured JSON from API) | Complex (XML regex + JSON coercion) |
| **Schema error rate** | ~0% | ~5-10% |
| **Feedback loop needed** | Rarely | Frequently |
| **Token cost** | Lower (tools as API params) | Higher (tools injected into prompt) |
| **Model compatibility** | Top-tier only | Universal |
| **Agent loop code** | Identical | Identical |
