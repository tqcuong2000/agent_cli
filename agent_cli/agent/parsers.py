"""
Agent Response Parsers — ``ParsedAction`` and ``AgentResponse``.

These are the *output* data classes produced by the Schema Validator.
They provide a unified format regardless of whether the LLM response
came from native function calling or XML prompting.

The Agent loop only ever sees ``AgentResponse`` — it never touches
raw ``LLMResponse`` parsing directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


# ══════════════════════════════════════════════════════════════════════
# Parsed Action
# ══════════════════════════════════════════════════════════════════════


@dataclass
class ParsedAction:
    """A validated tool invocation, ready for the Tool Executor.

    Attributes:
        tool_name:       Registered tool name (e.g. ``read_file``).
        arguments:       Validated argument dict.
        native_call_id:  Provider's call ID for response pairing
                         (populated in native FC mode, empty in XML mode).
    """

    tool_name: str
    arguments: Dict[str, Any]
    native_call_id: str = ""


# ══════════════════════════════════════════════════════════════════════
# Agent Response
# ══════════════════════════════════════════════════════════════════════


@dataclass
class AgentResponse:
    """The unified output of the Schema Validator.

    Identical structure regardless of whether the response came from
    native FC or XML prompting.  The Agent loop pattern-matches on
    the three fields:

    * **thought** — extracted from ``<thinking>`` tags (monologue).
    * **action**  — a tool call to execute (mutually exclusive with
                    ``final_answer``).
    * **final_answer** — the agent's final response to the user
                         (mutually exclusive with ``action``).

    At least one of ``action`` or ``final_answer`` must be present
    (or ``thought`` alone, though the loop will request more output).
    """

    thought: str = ""
    action: Optional[ParsedAction] = None
    final_answer: Optional[str] = None
