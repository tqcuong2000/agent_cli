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
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, cast

from agent_cli.agent.memory import BaseMemoryManager
from agent_cli.agent.parsers import AgentResponse
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
from agent_cli.core.events.events import (
    AgentMessageEvent,
    BaseEvent,
    SettingsChangedEvent,
)
from agent_cli.core.models.config_models import EffortLevel
from agent_cli.core.state.state_manager import AbstractStateManager
from agent_cli.tools.executor import ToolExecutor

if TYPE_CHECKING:
    from agent_cli.core.events.event_bus import EventCallback

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Effort Level Constraints
# ══════════════════════════════════════════════════════════════════════


# (EFFORT_CONSTRAINTS moved to agent_cli.core.config to support TOML overrides)


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
    effort_level: Optional[EffortLevel] = (
        None  # None = follow global default_effort_level
    )
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
        settings: Any = None,  # AgentSettings
    ) -> None:
        self.config = config
        self.provider = provider
        self.tool_executor = tool_executor
        self.validator = schema_validator
        self.memory = memory_manager
        self.event_bus = event_bus
        self.state_manager = state_manager
        self.prompt_builder = prompt_builder

        # Reactive effort caching
        self._effort_constraints: Dict[str, Any] = {}
        self._last_resolved_effort: Optional[EffortLevel] = None

        # In tests this might be None, so fallback gracefully
        if settings is None:
            from agent_cli.core.config import AgentSettings

            self.settings = AgentSettings()
        else:
            self.settings = settings

        # Subscribe to settings changes
        if self.event_bus:
            self.event_bus.subscribe(
                "SettingsChangedEvent",
                cast("EventCallback", self._on_settings_changed),
            )

        # Task-local message delta captured from the most recent handle_task() call.
        self._last_task_messages: List[Dict[str, Any]] = []

    async def _on_settings_changed(self, event: BaseEvent) -> None:
        """Reactive hook for settings updates."""
        if not isinstance(event, SettingsChangedEvent):
            return
        if event.setting_name == "default_effort_level":
            # Invalidate cache
            self._last_resolved_effort = None
            self._effort_constraints = {}
            logger.debug(f"Agent '{self.name}' reactive effort cache invalidated.")

    @property
    def name(self) -> str:
        """Agent's unique name from config."""
        return self.config.name

    @property
    def effort(self) -> EffortLevel:
        """Resolve the current effort level (agent override → global default)."""
        return self.config.effort_level or self.settings.default_effort_level

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
        session_messages: Optional[List[Dict[str, Any]]] = None,
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
        effort = effort_override or self.effort

        # Reactive cache check
        if effort != self._last_resolved_effort or not self._effort_constraints:
            self._effort_constraints = self.settings.get_effort_config(effort)
            self._last_resolved_effort = effort

        constraints = self._effort_constraints

        max_iterations = self.config.max_iterations_override or constraints.get(
            "max_iterations", 15
        )

        # ── Build system prompt ──────────────────────────────────
        system_prompt = await self.build_system_prompt(task_description)

        # ── Initialize Working Memory ────────────────────────────
        task_delta: List[Dict[str, Any]] = []

        def _append_message(
            message: Dict[str, Any], *, track_for_session: bool
        ) -> None:
            self.memory.add_working_event(message)
            if track_for_session:
                task_delta.append(dict(message))

        self.memory.reset_working()
        if session_messages is not None:
            for msg in self._hydrate_session_messages(
                session_messages=session_messages,
                system_prompt=system_prompt,
            ):
                # Hydrated historical messages are already in persisted session history.
                _append_message(msg, track_for_session=False)
        else:
            _append_message(
                {"role": "system", "content": system_prompt}, track_for_session=False
            )

        # Inject prior context from previous agents (ExecutionPlan)
        if prior_context:
            _append_message(
                {
                    "role": "user",
                    "content": f"Context from previous steps:\n{prior_context}",
                },
                track_for_session=True,
            )

        # Inject the task itself
        _append_message(
            {"role": "user", "content": task_description},
            track_for_session=True,
        )

        # ── Tracking ─────────────────────────────────────────────
        schema_error_count = 0
        stuck_detector = StuckDetector()

        # ── The Loop ─────────────────────────────────────────────
        try:
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
                    # Token-aware compaction before making an LLM call.
                    if self.memory.should_compact():
                        await self.event_bus.emit(
                            AgentMessageEvent(
                                source=self.name,
                                agent_name=self.name,
                                content="Context budget threshold reached, compacting memory...",
                                is_monologue=True,
                            )
                        )
                        await self.memory.summarize_and_compact()

                    # ── STEP 1: Generate (LLM Call) ──────────────────
                    llm_response = await self.provider.safe_generate(
                        context=self.memory.get_working_context(),
                        tools=self._get_tool_definitions(),
                    )

                    # INTERCEPT: Extract and save raw response for debugging
                    self._debug_intercept_response(task_id, iteration, llm_response.text_content)

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
                    response: AgentResponse = self.validator.parse_and_validate(
                        llm_response
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
                        _append_message(
                            {
                                "role": "assistant",
                                "content": llm_response.text_content,
                            },
                            track_for_session=True,
                        )
                        _append_message(
                            {"role": "tool", "content": result},
                            track_for_session=True,
                        )

                        # Agent-specific hook
                        await self.on_tool_result(response.action.tool_name, result)

                        # Stuck detection
                        if stuck_detector.is_stuck(response.action.tool_name, result):
                            _append_message(
                                {
                                    "role": "user",
                                    "content": (
                                        "⚠You appear to be repeating the same "
                                        "action with the same result. "
                                        "Try a completely different approach."
                                    ),
                                },
                                track_for_session=True,
                            )

                        continue  # Next iteration

                    elif response.final_answer:
                        # ── FINAL ANSWER PATH ──
                        # Persist the model response that produced the final answer.
                        _append_message(
                            {
                                "role": "assistant",
                                "content": llm_response.text_content,
                            },
                            track_for_session=True,
                        )

                        final = await self.on_final_answer(response.final_answer)

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
                        _append_message(
                            {
                                "role": "assistant",
                                "content": llm_response.text_content,
                            },
                            track_for_session=True,
                        )
                        continue

                # ── ERROR HANDLING ───────────────────────────────────
                except ContextLengthExceededError:
                    # RECOVERABLE: Summarize and retry
                    await self.event_bus.emit(
                        AgentMessageEvent(
                            source=self.name,
                            agent_name=self.name,
                            content=("⚠ Context too long, summarizing older steps..."),
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
                    _append_message(
                        {
                            "role": "user",
                            "content": (
                                f"Schema Error: {e}. Fix your formatting and try again."
                            ),
                        },
                        track_for_session=True,
                    )
                    continue

                except ToolExecutionError as e:
                    # RECOVERABLE: Feed error back to agent as observation
                    _append_message(
                        {
                            "role": "tool",
                            "content": (f"Tool Error: {e}. Try a different approach."),
                        },
                        track_for_session=True,
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
        finally:
            self._last_task_messages = list(task_delta)

    # ── Private Helpers ──────────────────────────────────────────

    @staticmethod
    def _hydrate_session_messages(
        session_messages: List[Dict[str, Any]],
        system_prompt: str,
    ) -> List[Dict[str, Any]]:
        """Replace stale session system prompt with a fresh task-scoped one."""
        hydrated: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        skipped_system = False
        for message in session_messages:
            if not skipped_system and message.get("role") == "system":
                skipped_system = True
                continue
            hydrated.append(message)
        return hydrated

    def get_last_task_messages(self) -> List[Dict[str, Any]]:
        """Return messages added during the most recent task execution."""
        return list(self._last_task_messages)

    def _get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Retrieve tool definitions for the LLM."""
        if not self.config.tools:
            return []
        return self.tool_executor.registry.get_definitions_for_llm(self.config.tools)

    def _debug_intercept_response(self, task_id: str, iteration: int, raw_content: str) -> None:
        """Utility to intercept and save raw LLM responses to a debug file."""
        debug_path = Path.home() / ".agent_cli" / "debug" / "agent_response.txt"
        try:
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(debug_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"TIMESTAMP: {timestamp}\n")
                f.write(f"AGENT:     {self.name}\n")
                f.write(f"TASK_ID:   {task_id}\n")
                f.write(f"ITERATION: {iteration}\n")
                f.write(f"{'-'*80}\n")
                f.write(raw_content)
                f.write(f"\n{'='*80}\n")
        except Exception as e:
            logger.error(f"Failed to save debug interception: {e}")
