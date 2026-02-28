"""
Base Agent — the ``BaseAgent`` ABC with the full ReAct reasoning loop.

Every agent in the system inherits from ``BaseAgent`` and implements
three hooks:

* ``build_system_prompt()`` — construct the system prompt.
* ``on_tool_result()``      — post-processing after tool execution.
* ``on_final_answer()``     — post-processing before returning.

The ``handle_task()`` method implements the core loop:
Think → Act → Observe → repeat until done or exhausted.

See ``01_reasoning_loop.md`` for the full specification.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from agent_cli.agent.memory import BaseMemoryManager
from agent_cli.agent.parsers import AgentResponse, ParsedAction
from agent_cli.agent.react_loop import PromptBuilder, StuckDetector
from agent_cli.agent.schema import BaseSchemaValidator
from agent_cli.core.error_handler.errors import (
    AgentCLIError,
    ContextLengthExceededError,
    ErrorTier,
    MaxIterationsExceededError,
    SchemaValidationError,
    ToolExecutionError,
)
from agent_cli.core.events.event_bus import AbstractEventBus
from agent_cli.core.events.events import AgentMessageEvent
from agent_cli.core.models.config_models import EffortLevel
from agent_cli.core.state.state_manager import AbstractStateManager
from agent_cli.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Effort Level Constraints
# ══════════════════════════════════════════════════════════════════════


EFFORT_CONSTRAINTS: Dict[EffortLevel, Dict[str, Any]] = {
    EffortLevel.LOW: {
        "max_iterations": 5,
        "model_tier": "fast",
        "reasoning_instruction": (
            "Be concise. Act immediately when the path is clear."
        ),
        "review_policy": "none",
    },
    EffortLevel.MEDIUM: {
        "max_iterations": 15,
        "model_tier": "capable",
        "reasoning_instruction": (
            "Think step-by-step. Explain your reasoning before acting."
        ),
        "review_policy": "standard",
    },
    EffortLevel.HIGH: {
        "max_iterations": 30,
        "model_tier": "premium",
        "reasoning_instruction": (
            "Think deeply. Consider multiple approaches before choosing one. "
            "After completing the task, review your work for correctness."
        ),
        "review_policy": "self_verify",
    },
}


# ══════════════════════════════════════════════════════════════════════
# Agent Config
# ══════════════════════════════════════════════════════════════════════


@dataclass
class AgentConfig:
    """Configuration for an agent instance.

    Built-in agents have defaults.  User-defined agents set these
    in config files.

    Attributes:
        name:                     Unique identifier (e.g. ``coder``).
        description:              Role description for the Orchestrator.
        persona:                  System prompt persona text.
        model:                    LLM model override (empty = default).
        effort_level:             Default effort (overridable per-task).
        tools:                    Tool names from ``ToolRegistry``.
        max_iterations_override:  Custom max (overrides effort default).
        show_thinking:            Whether to stream ``<thinking>`` to TUI.
    """

    name: str = ""
    description: str = ""
    persona: str = ""
    model: str = ""
    effort_level: EffortLevel = EffortLevel.MEDIUM
    tools: List[str] = field(default_factory=list)
    max_iterations_override: Optional[int] = None
    show_thinking: bool = True


# ══════════════════════════════════════════════════════════════════════
# Base Agent ABC
# ══════════════════════════════════════════════════════════════════════

# Max consecutive schema errors before failing the task
_MAX_CONSECUTIVE_SCHEMA_ERRORS = 3


class BaseAgent(ABC):
    """Abstract base class for all agents — implements the ReAct loop.

    The ReAct (Reasoning and Acting) loop is the **heart** of the
    system.  It connects:
    - ``BaseLLMProvider`` for generation
    - ``BaseSchemaValidator`` for response parsing
    - ``ToolExecutor`` for tool invocation
    - ``BaseMemoryManager`` for context management
    - ``AbstractEventBus`` for TUI updates
    - ``AbstractStateManager`` for task lifecycle

    Concrete agents (``CoderAgent``, ``ResearcherAgent``, etc.)
    implement the three abstract hooks to customize behavior while
    inheriting the full loop logic.
    """

    def __init__(
        self,
        config: AgentConfig,
        provider: Any,  # BaseLLMProvider (avoid circular import)
        tool_executor: ToolExecutor,
        schema_validator: BaseSchemaValidator,
        memory_manager: BaseMemoryManager,
        event_bus: AbstractEventBus,
        state_manager: AbstractStateManager,
        prompt_builder: PromptBuilder,
    ) -> None:
        self.config = config
        self.provider = provider
        self.tool_executor = tool_executor
        self.validator = schema_validator
        self.memory = memory_manager
        self.event_bus = event_bus
        self.state_manager = state_manager
        self.prompt_builder = prompt_builder

    @property
    def name(self) -> str:
        """Agent's unique name from config."""
        return self.config.name

    # ── Abstract Hooks ───────────────────────────────────────────

    @abstractmethod
    async def build_system_prompt(self, task_context: str) -> str:
        """Construct the system prompt for this agent.

        Called once at the start of ``handle_task()``.
        Implementations typically delegate to ``self.prompt_builder``
        with agent-specific persona and configuration.
        """

    @abstractmethod
    async def on_tool_result(self, tool_name: str, result: str) -> None:
        """Hook called after every tool execution.

        Agents can override to add custom behavior.  For example,
        a code agent might auto-run tests after file edits.
        """

    @abstractmethod
    async def on_final_answer(self, answer: str) -> str:
        """Hook called before returning the final answer.

        Agents can override for self-verification (HIGH effort) or
        formatting adjustments.

        Returns:
            The (potentially modified) final answer.
        """

    # ── The Core ReAct Loop ──────────────────────────────────────

    async def handle_task(
        self,
        task_id: str,
        task_description: str,
        prior_context: str = "",
        effort_override: Optional[EffortLevel] = None,
    ) -> str:
        """The main ReAct reasoning loop.

        Flow per iteration:
        1. **Generate** — call the LLM with working memory.
        2. **Stream thinking** — emit ``<thinking>`` to TUI.
        3. **Validate** — parse response via Schema Validator.
        4. **Act or Answer** — execute tool or return final answer.

        Args:
            task_id:          Task being executed (for state/logging).
            task_description: What the agent should accomplish.
            prior_context:    Summary from previous agents in a plan.
            effort_override:  Orchestrator can override default effort.

        Returns:
            The agent's final answer string.

        Raises:
            MaxIterationsExceededError: Loop exhausted all iterations.
            AgentCLIError: On fatal errors (propagated to Orchestrator).
        """
        # ── Resolve effort level ─────────────────────────────────
        effort = effort_override or self.config.effort_level
        constraints = EFFORT_CONSTRAINTS[effort]
        max_iterations = (
            self.config.max_iterations_override
            or constraints["max_iterations"]
        )

        # ── Build system prompt ──────────────────────────────────
        system_prompt = await self.build_system_prompt(task_description)

        # ── Initialize Working Memory ────────────────────────────
        self.memory.reset_working()
        self.memory.add_working_event(
            {"role": "system", "content": system_prompt}
        )

        # Inject prior context from previous agents (ExecutionPlan)
        if prior_context:
            self.memory.add_working_event(
                {
                    "role": "user",
                    "content": f"Context from previous steps:\n{prior_context}",
                }
            )

        # Inject the task itself
        self.memory.add_working_event(
            {"role": "user", "content": task_description}
        )

        # ── Tracking ─────────────────────────────────────────────
        schema_error_count = 0
        stuck_detector = StuckDetector()

        # ── The Loop ─────────────────────────────────────────────
        for iteration in range(1, max_iterations + 1):
            logger.debug(
                "Agent '%s' iteration %d/%d (effort=%s, task=%s)",
                self.name,
                iteration,
                max_iterations,
                effort.name,
                task_id,
            )

            try:
                # ── STEP 1: Generate (LLM Call) ──────────────────
                llm_response = await self.provider.safe_generate(
                    context=self.memory.get_working_context(),
                    tools=self._get_tool_definitions(),
                )

                # ── STEP 2: Stream Thinking to TUI ───────────────
                thinking_text = self.validator.extract_thinking(
                    llm_response.text_content
                )
                if thinking_text and self.config.show_thinking:
                    await self.event_bus.emit(
                        AgentMessageEvent(
                            source=self.name,
                            agent_name=self.name,
                            content=thinking_text,
                            is_monologue=True,
                        )
                    )

                # ── STEP 3: Validate & Parse Response ────────────
                response: AgentResponse = (
                    self.validator.parse_and_validate(llm_response)
                )
                schema_error_count = 0  # Reset on success

                # ── STEP 4: Process Action or Final Answer ───────
                if response.action:
                    # ── TOOL EXECUTION PATH ──
                    result = await self.tool_executor.execute(
                        tool_name=response.action.tool_name,
                        arguments=response.action.arguments,
                        task_id=task_id,
                        native_call_id=response.action.native_call_id,
                    )

                    # Add LLM response + tool result to Working Memory
                    self.memory.add_working_event(
                        {
                            "role": "assistant",
                            "content": llm_response.text_content,
                        }
                    )
                    self.memory.add_working_event(
                        {"role": "tool", "content": result}
                    )

                    # Agent-specific hook
                    await self.on_tool_result(
                        response.action.tool_name, result
                    )

                    # Stuck detection
                    if stuck_detector.is_stuck(
                        response.action.tool_name, result
                    ):
                        self.memory.add_working_event(
                            {
                                "role": "user",
                                "content": (
                                    "⚠ You appear to be repeating the same "
                                    "action with the same result. "
                                    "Try a completely different approach."
                                ),
                            }
                        )

                    continue  # Next iteration

                elif response.final_answer:
                    # ── FINAL ANSWER PATH ──
                    final = await self.on_final_answer(
                        response.final_answer
                    )

                    # Publish to TUI
                    await self.event_bus.emit(
                        AgentMessageEvent(
                            source=self.name,
                            agent_name=self.name,
                            content=final,
                            is_monologue=False,
                        )
                    )

                    logger.info(
                        "Agent '%s' completed task in %d iteration(s) "
                        "(effort=%s, task=%s)",
                        self.name,
                        iteration,
                        effort.name,
                        task_id,
                    )
                    return final

                else:
                    # Thinking-only response (no action, no answer)
                    # Add it to memory and let the LLM continue
                    self.memory.add_working_event(
                        {
                            "role": "assistant",
                            "content": llm_response.text_content,
                        }
                    )
                    continue

            # ── ERROR HANDLING ───────────────────────────────────
            except ContextLengthExceededError:
                # RECOVERABLE: Summarize and retry
                await self.event_bus.emit(
                    AgentMessageEvent(
                        source=self.name,
                        agent_name=self.name,
                        content=(
                            "⚠ Context too long, summarizing older steps..."
                        ),
                        is_monologue=True,
                    )
                )
                await self.memory.summarize_and_compact()
                continue

            except SchemaValidationError as e:
                # RECOVERABLE: Feedback loop (re-prompt LLM)
                schema_error_count += 1
                if schema_error_count >= _MAX_CONSECUTIVE_SCHEMA_ERRORS:
                    raise MaxIterationsExceededError(
                        f"Agent '{self.name}' produced "
                        f"{_MAX_CONSECUTIVE_SCHEMA_ERRORS} consecutive "
                        f"malformed responses.",
                        iterations=iteration,
                        max_iterations=max_iterations,
                        task_id=task_id,
                    )
                self.memory.add_working_event(
                    {
                        "role": "user",
                        "content": (
                            f"Schema Error: {e}. "
                            f"Fix your formatting and try again."
                        ),
                    }
                )
                continue

            except ToolExecutionError as e:
                # RECOVERABLE: Feed error back to agent as observation
                self.memory.add_working_event(
                    {
                        "role": "tool",
                        "content": (
                            f"Tool Error: {e}. Try a different approach."
                        ),
                    }
                )
                continue

            except AgentCLIError as e:
                if e.tier == ErrorTier.FATAL:
                    raise  # Propagated to Orchestrator
                raise

        # ── LOOP EXHAUSTED ───────────────────────────────────────
        raise MaxIterationsExceededError(
            f"Agent '{self.name}' reached {max_iterations} iterations "
            f"(effort={effort.name}) without completing the task.",
            iterations=max_iterations,
            max_iterations=max_iterations,
            task_id=task_id,
        )

    # ── Private Helpers ──────────────────────────────────────────

    def _get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Retrieve tool definitions for the LLM."""
        if not self.config.tools:
            return []
        return self.tool_executor.registry.get_definitions_for_llm(
            self.config.tools
        )
