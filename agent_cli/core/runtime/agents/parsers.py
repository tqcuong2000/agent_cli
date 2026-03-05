"""
Agent Response Parsers — ``AgentDecision``, ``ParsedAction``, and ``AgentResponse``.

These are the *output* data classes produced by the Schema Validator.
They provide a unified format regardless of whether the LLM response
came from native function calling or prompt JSON mode.

The Agent loop only ever sees ``AgentResponse`` — it never touches
raw ``LLMResponse`` parsing directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

# ══════════════════════════════════════════════════════════════════════
# Agent Decision
# ══════════════════════════════════════════════════════════════════════


class AgentDecision(Enum):
    """The decisions an agent can make per turn.

    REFLECT:        Thinking-only turn — no tool call, no output.
    EXECUTE_ACTION: Invoke exactly one tool and wait for the result.
    EXECUTE_ACTIONS: Invoke multiple tools in one turn.
    NOTIFY_USER:    Deliver the final answer — ends the task.
    YIELD:          Graceful abort — ends the task with a reason.
    """

    REFLECT = "reflect"
    EXECUTE_ACTION = "execute_action"
    EXECUTE_ACTIONS = "execute_actions"
    NOTIFY_USER = "notify_user"
    YIELD = "yield"


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
                         (populated in native FC mode, empty otherwise).
    """

    tool_name: str
    arguments: Dict[str, Any]
    native_call_id: str = ""
    action_id: str = ""


# ══════════════════════════════════════════════════════════════════════
# Agent Response
# ══════════════════════════════════════════════════════════════════════


@dataclass
class AgentResponse:
    """The unified output of the Schema Validator.

    Identical structure regardless of whether the response came from
    native FC or prompt JSON mode. The Agent loop dispatches on
    ``decision``:

    * **REFLECT**        — thinking-only turn, loop continues.
    * **EXECUTE_ACTION** — execute ``action``, feed result back.
    * **NOTIFY_USER**    — return ``final_answer`` to the user.
    * **YIELD**          — abort gracefully with ``final_answer``
                           containing the reason / partial results.
    """

    decision: AgentDecision = AgentDecision.REFLECT
    title: str = ""
    thought: str = ""
    action: Optional[ParsedAction] = None
    actions: Optional[List[ParsedAction]] = None
    final_answer: Optional[str] = None
