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

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Optional

from agent_cli.core.runtime.agents.memory import BaseMemoryManager
from agent_cli.core.runtime.agents.parsers import (
    AgentDecision,
    AgentResponse,
    ParsedAction,
)
from agent_cli.core.runtime.agents.react_loop import PromptBuilder, StuckDetector
from agent_cli.core.runtime.agents.resource_tracker import ResourceTracker
from agent_cli.core.runtime.agents.schema import BaseSchemaValidator
from agent_cli.core.infra.events.errors import (
    AgentCLIError,
    ContextLengthExceededError,
    ErrorTier,
    MaxIterationsExceededError,
    SchemaValidationError,
    ToolExecutionError,
)
from agent_cli.core.infra.events.event_bus import AbstractEventBus
from agent_cli.core.infra.events.events import AgentMessageEvent
from agent_cli.core.infra.config.config_models import EffortLevel, normalize_effort
from agent_cli.core.runtime.orchestrator.state_manager import AbstractStateManager
from agent_cli.core.infra.registry.registry import DataRegistry
from agent_cli.core.providers.base.base import BaseLLMProvider
from agent_cli.core.providers.base.models import LLMResponse, ProviderRequestOptions
from agent_cli.core.runtime.services.system_info import SystemInfoProvider
from agent_cli.core.runtime.tools.base import ToolResult
from agent_cli.core.runtime.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)


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
        tools:                    Tool names from ``ToolRegistry``.
        max_iterations_override:  Custom max (overrides global max_iterations).
        show_thinking:            Whether to stream reasoning to TUI.
        multi_action_enabled:     Enable multi-action planning/dispatch.
        max_concurrent_actions:   Maximum concurrent actions in a batch.
        plain_text:               Bypass ReAct loop and run raw text generation.
    """

    name: str = ""
    description: str = ""
    persona: str = ""
    model: str = ""
    tools: List[str] = field(default_factory=list)
    max_iterations_override: Optional[int] = None
    show_thinking: bool = True
    multi_action_enabled: bool = False
    max_concurrent_actions: int = 5
    plain_text: bool = False


# ══════════════════════════════════════════════════════════════════════
# Base Agent ABC
# ══════════════════════════════════════════════════════════════════════


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

    _provider_managed_tool_tokens = frozenset({"web_search"})

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
        *,
        data_registry: DataRegistry,
        system_info_provider: SystemInfoProvider | None = None,
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
        self.system_info_provider = system_info_provider

        # In tests this might be None, so fallback gracefully
        if settings is None:
            from agent_cli.core.infra.config.config import AgentSettings

            self.settings = AgentSettings()
        else:
            self.settings = settings

        self._data_registry = data_registry
        self._schema_defaults = self._data_registry.get_schema_defaults()
        self._retry_defaults = self._data_registry.get_retry_defaults()
        self._cached_capability_snapshot: Any = None
        configured_model = str(self.config.model).strip() or str(
            getattr(self.provider, "model_name", "")
        ).strip() or str(getattr(self.settings, "default_model", "")).strip()
        context_limit = (
            self._data_registry.get_context_window(configured_model)
            if configured_model
            else 128_000
        )
        core_settings = getattr(self.settings, "core", {})
        raw_budget = (
            core_settings.get("cost_budget_usd")
            if isinstance(core_settings, dict)
            else None
        )
        cost_budget: float | None = None
        if raw_budget is not None:
            try:
                parsed_budget = float(raw_budget)
            except (TypeError, ValueError):
                parsed_budget = 0.0
            if parsed_budget > 0:
                cost_budget = parsed_budget
        self._resource_tracker = ResourceTracker(
            context_limit=context_limit,
            cost_budget=cost_budget,
        )

        # Task-local message delta captured from the most recent handle_task() call.
        self._last_task_messages: List[Dict[str, Any]] = []
        self._last_task_title: str = ""

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

        Agents can override for self-verification or formatting adjustments.

        Returns:
            The (potentially modified) final answer.
        """

    async def on_batch_complete(
        self,
        actions: List[ParsedAction],
        results: List[ToolResult],
    ) -> None:
        """Hook called after all actions in a multi-action batch complete."""
        _ = actions, results  # Default no-op hook

    async def _handle_plain_text_task(
        self,
        task_id: str,
        task_description: str,
        *,
        prior_context: str = "",
        session_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Run a single plain-text generation turn without schema/tool loop."""
        task_delta: List[Dict[str, Any]] = []
        self._last_task_title = ""

        def _append_message(
            message: Dict[str, Any], *, track_for_session: bool
        ) -> None:
            self.memory.add_working_event(message)
            if track_for_session:
                task_delta.append(dict(message))

        try:
            self.memory.reset_working()
            system_prompt = self._build_plain_text_system_prompt()

            if session_messages is not None:
                for msg in self._hydrate_session_messages(
                    session_messages=session_messages,
                    system_prompt=system_prompt,
                ):
                    _append_message(msg, track_for_session=False)
            else:
                _append_message(
                    {"role": "system", "content": system_prompt},
                    track_for_session=False,
                )

            if prior_context:
                _append_message(
                    {
                        "role": "user",
                        "content": f"Context from previous steps:\n{prior_context}",
                    },
                    track_for_session=True,
                )

            _append_message(
                {"role": "user", "content": task_description},
                track_for_session=True,
            )

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

            llm_response = await self.provider.safe_generate(
                context=self.memory.get_working_context(),
                tools=None,
                max_retries=int(self._retry_defaults.get("llm_max_retries", 3)),
                base_delay=float(self._retry_defaults.get("llm_retry_base_delay", 1.0)),
                max_delay=float(self._retry_defaults.get("llm_retry_max_delay", 30.0)),
                task_id=task_id,
                event_bus=self.event_bus,
            )
            response_text = getattr(llm_response, "text_content", "") or ""
            self._on_llm_response(llm_response)
            self._debug_intercept_response(task_id, 1, response_text)

            _append_message(
                {"role": "assistant", "content": response_text},
                track_for_session=True,
            )

            await self.event_bus.emit(
                AgentMessageEvent(
                    source=self.name,
                    agent_name=self.name,
                    content=response_text,
                    is_monologue=False,
                )
            )
            return response_text
        finally:
            self._last_task_messages = list(task_delta)
            self._cached_capability_snapshot = None

    def _build_plain_text_system_prompt(self) -> str:
        """Build the plain text mode system prompt without tool/schema contract."""
        persona = str(self.config.persona or "").strip()
        if persona:
            return persona
        return "You are a helpful assistant."

    # The Core ReAct Loop
    async def handle_task(
        self,
        task_id: str,
        task_description: str,
        prior_context: str = "",
        session_messages: Optional[List[Dict[str, Any]]] = None,
        desired_effort: Optional[str] = None,
    ) -> str:
        """The main ReAct reasoning loop.

        Flow per iteration:
        1. **Generate** — call the LLM with working memory.
        2. **Stream thinking** — emit reasoning to TUI.
        3. **Validate** — parse response via Schema Validator.
        4. **Act or Answer** — execute tool or return final answer.

        Args:
            task_id:          Task being executed (for state/logging).
            task_description: What the agent should accomplish.
            prior_context:    Summary from previous agents in a plan.

        Returns:
            The agent's final answer string.

        Raises:
            MaxIterationsExceededError: Loop exhausted all iterations.
            AgentCLIError: On fatal errors (propagated to Orchestrator).
        """
        if self.config.plain_text:
            logger.error(
                "DEBUG: Entering _handle_plain_text_task for agent '%s' (model=%s)",
                self.name,
                self.config.model,
            )
            return await self._handle_plain_text_task(
                task_id=task_id,
                task_description=task_description,
                prior_context=prior_context,
                session_messages=session_messages,
            )

        max_iterations = int(
            self.config.max_iterations_override
            or getattr(self.settings, "max_iterations", 100)
        )
        effort_source = (
            desired_effort
            if desired_effort is not None
            else getattr(self.settings, "default_effort", EffortLevel.AUTO.value)
        )
        requested_effort = normalize_effort(effort_source).value
        effort_value = self._resolve_effective_effort_from_capabilities(
            requested_effort
        )
        multi_action_enabled = bool(self.config.multi_action_enabled)
        max_concurrent_actions = int(self.config.max_concurrent_actions)
        logger.debug(
            "Agent multi-action config resolved (enabled=%s, max_concurrent_actions=%d)",
            multi_action_enabled,
            max_concurrent_actions,
        )

        # ── Build system prompt ──────────────────────────────────
        system_prompt = await self.build_system_prompt(task_description)

        # ── Initialize Working Memory ────────────────────────────
        task_delta: List[Dict[str, Any]] = []
        self._last_task_title = ""

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
        tool_definitions = self._get_tool_definitions()
        request_options = ProviderRequestOptions(
            provider_managed_tools=self._get_provider_managed_tools()
        )

        schema_error_count = 0
        max_schema_errors = int(
            self._schema_defaults.get("validation", {}).get(
                "max_consecutive_schema_errors",
                3,
            )
        )
        stuck_defaults = self._data_registry.get_stuck_detector_defaults()
        stuck_detector = StuckDetector(
            threshold=int(stuck_defaults.get("threshold", 3)),
            history_cap=int(stuck_defaults.get("history_cap", 10)),
        )
        reflect_count = 0
        max_reflects = int(
            self._schema_defaults.get("validation", {}).get(
                "max_consecutive_reflects",
                3,
            )
        )
        batch_executor = None
        multi_action_validator = None
        if multi_action_enabled:
            from agent_cli.core.runtime.agents.batch_executor import BatchExecutor
            from agent_cli.core.runtime.agents.multi_action_validator import (
                MultiActionValidator,
            )

            batch_executor = BatchExecutor(
                tool_executor=self.tool_executor,
                tool_registry=self.tool_executor.registry,
                max_concurrent=max_concurrent_actions,
            )
            multi_action_validator = MultiActionValidator(
                tool_registry=self.tool_executor.registry,
                max_batch_size=max_concurrent_actions,
                observability=self._get_observability_manager(),
            )

        # ── The Loop ─────────────────────────────────────────────
        try:
            for iteration in range(1, max_iterations + 1):
                logger.debug(
                    "Agent '%s' iteration %d/%d (task=%s)",
                    self.name,
                    iteration,
                    max_iterations,
                    task_id,
                )

                llm_response: LLMResponse | None = None
                llm_response_text = ""
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
                        tools=tool_definitions,
                        effort=effort_value,
                        request_options=request_options,
                        max_retries=int(self._retry_defaults.get("llm_max_retries", 3)),
                        base_delay=float(
                            self._retry_defaults.get("llm_retry_base_delay", 1.0)
                        ),
                        max_delay=float(
                            self._retry_defaults.get("llm_retry_max_delay", 30.0)
                        ),
                        task_id=task_id,
                        event_bus=self.event_bus,
                    )
                    llm_response_text = getattr(llm_response, "text_content", "")
                    self._on_llm_response(llm_response)

                    # INTERCEPT: Extract and save raw response for debugging
                    self._debug_intercept_response(
                        task_id, iteration, llm_response_text
                    )

                    # ── STEP 2: Stream Thinking to TUI ───────────────
                    thinking_text = self.validator.extract_thinking(llm_response_text)
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
                    if llm_response is None:
                        raise SchemaValidationError(
                            "LLM provider returned no response object.",
                            raw_response=llm_response_text,
                        )
                    response: AgentResponse = self.validator.parse_and_validate(
                        llm_response
                    )
                    title_text = response.title.strip()
                    if title_text:
                        self._last_task_title = title_text
                    schema_error_count = 0  # Reset on success

                    # ── STEP 4: Dispatch on Decision ─────────────
                    match response.decision:
                        case AgentDecision.EXECUTE_ACTION:
                            # ── TOOL EXECUTION PATH ──
                            action = response.action
                            if action is None:
                                raise SchemaValidationError(
                                    "Invalid agent response: execute_action requires a non-null action payload.",
                                    raw_response=llm_response_text,
                                )

                            reflect_count = 0  # Reset on action
                            _append_message(
                                {
                                    "role": "assistant",
                                    "content": self._format_assistant_history(
                                        llm_response
                                    ),
                                },
                                track_for_session=True,
                            )

                            result = await self.tool_executor.execute(
                                tool_name=action.tool_name,
                                arguments=action.arguments,
                                task_id=task_id,
                                native_call_id=action.native_call_id,
                                action_id=action.action_id or "act_0",
                            )
                            tool_output = result.output

                            # Add tool result to Working Memory
                            _append_message(
                                {"role": "tool", "content": tool_output},
                                track_for_session=True,
                            )

                            # Agent-specific hook
                            await self.on_tool_result(action.tool_name, tool_output)

                            # Stuck detection
                            if stuck_detector.is_stuck(action.tool_name, tool_output):
                                _append_message(
                                    {
                                        "role": "system",
                                        "content": (
                                            "⚠ You appear to be repeating the same "
                                            "action with the same result. "
                                            "Try a completely different approach."
                                        ),
                                    },
                                    track_for_session=True,
                                )

                            continue  # Next iteration

                        case AgentDecision.EXECUTE_ACTIONS:
                            actions = response.actions
                            if not actions:
                                raise SchemaValidationError(
                                    "Invalid agent response: execute_actions requires a non-empty actions payload.",
                                    raw_response=llm_response_text,
                                )
                            if not multi_action_enabled:
                                raise SchemaValidationError(
                                    "Invalid agent response: execute_actions received while multi_action_enabled=False.",
                                    raw_response=llm_response_text,
                                )
                            if batch_executor is None or multi_action_validator is None:
                                raise SchemaValidationError(
                                    "Internal error: multi-action runtime is not initialized.",
                                    raw_response=llm_response_text,
                                )

                            reflect_count = 0
                            _append_message(
                                {
                                    "role": "assistant",
                                    "content": self._format_assistant_history(
                                        llm_response
                                    ),
                                },
                                track_for_session=True,
                            )

                            validated_actions = multi_action_validator.validate(
                                actions,
                                task_id=task_id,
                            )
                            batch_tool_names = [
                                action.tool_name for action in validated_actions
                            ]
                            batch_action_ids = [
                                action.action_id or f"act_{idx}"
                                for idx, action in enumerate(validated_actions)
                            ]
                            parallel_count = sum(
                                1
                                for action in validated_actions
                                if self._is_tool_parallel_safe(action.tool_name)
                            )
                            sequential_count = len(validated_actions) - parallel_count
                            batch_started = perf_counter()
                            batch_results = await batch_executor.execute_batch(
                                validated_actions,
                                task_id=task_id,
                            )
                            batch_duration_ms = int(
                                (perf_counter() - batch_started) * 1000
                            )
                            observability = self._get_observability_manager()
                            if observability is not None:
                                observability.record_multi_action_batch(
                                    task_id=task_id,
                                    batch_size=len(validated_actions),
                                    parallel_count=parallel_count,
                                    sequential_count=sequential_count,
                                    batch_duration_ms=batch_duration_ms,
                                    action_ids=batch_action_ids,
                                    tool_names=batch_tool_names,
                                )
                            else:
                                logger.info(
                                    "Multi-action batch executed",
                                    extra={
                                        "source": "agent_multi_action",
                                        "task_id": task_id,
                                        "data": {
                                            "batch_size": len(validated_actions),
                                            "parallel_count": parallel_count,
                                            "sequential_count": sequential_count,
                                            "batch_duration_ms": batch_duration_ms,
                                            "action_ids": batch_action_ids,
                                            "tool_names": batch_tool_names,
                                        },
                                    },
                                )

                            for tool_result in batch_results:
                                _append_message(
                                    {"role": "tool", "content": tool_result.output},
                                    track_for_session=True,
                                )

                            for action, tool_result in zip(
                                validated_actions,
                                batch_results,
                            ):
                                await self.on_tool_result(
                                    action.tool_name,
                                    tool_result.output,
                                )

                            await self.on_batch_complete(
                                validated_actions,
                                batch_results,
                            )

                            stuck_inputs = [
                                (action.tool_name, tool_result.output)
                                for action, tool_result in zip(
                                    validated_actions,
                                    batch_results,
                                )
                            ]
                            if stuck_detector.is_stuck_batch(stuck_inputs):
                                observability = self._get_observability_manager()
                                if observability is not None:
                                    observability.record_multi_action_stuck_batch(
                                        task_id=task_id,
                                        batch_size=len(validated_actions),
                                        action_ids=batch_action_ids,
                                        tool_names=batch_tool_names,
                                    )
                                _append_message(
                                    {
                                        "role": "system",
                                        "content": (
                                            "Warning: you appear to be repeating the same batch of "
                                            "actions with the same results. "
                                            "Try a completely different approach."
                                        ),
                                    },
                                    track_for_session=True,
                                )

                            continue  # Next iteration

                        case AgentDecision.NOTIFY_USER:
                            # ── FINAL ANSWER PATH ──
                            final_answer = response.final_answer
                            if final_answer is None:
                                raise SchemaValidationError(
                                    "Invalid agent response: notify_user requires a non-null final answer message.",
                                    raw_response=llm_response_text,
                                )
                            final_answer_text: str = final_answer

                            reflect_count = 0
                            _append_message(
                                {
                                    "role": "assistant",
                                    "content": self._format_assistant_history(
                                        llm_response
                                    ),
                                },
                                track_for_session=True,
                            )

                            store_content = getattr(self.tool_executor, "store_content", None)
                            if callable(store_content) and final_answer_text:
                                try:
                                    store_content(final_answer_text)
                                except Exception:
                                    logger.debug(
                                        "Failed to store notify_user content for task %s",
                                        task_id,
                                        exc_info=True,
                                    )

                            final = await self.on_final_answer(final_answer_text)

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
                                "Agent '%s' completed task in %d iteration(s) (task=%s)",
                                self.name,
                                iteration,
                                task_id,
                            )
                            return final

                        case AgentDecision.REFLECT:
                            # ── MULTI-TURN REASONING PATH ──
                            _append_message(
                                {
                                    "role": "assistant",
                                    "content": self._format_assistant_history(
                                        llm_response
                                    ),
                                },
                                track_for_session=True,
                            )
                            reflect_count += 1
                            reflect_message = (
                                f"Reasoning noted ({reflect_count}/{max_reflects} reflects used)."
                            )
                            if reflect_count >= max_reflects:
                                reflect_message += (
                                    " You have reached the reflect limit. You must now "
                                    "execute an action or provide a final answer."
                                )
                            elif reflect_count >= max_reflects - 1:
                                reflect_message += (
                                    " You must act or respond on your next turn."
                                )

                            resource_summary = self._resource_tracker.summary()
                            if resource_summary:
                                reflect_message += f" {resource_summary}"
                            reflect_message += " Continue planning or execute an action."

                            _append_message(
                                {"role": "system", "content": reflect_message},
                                track_for_session=True,
                            )
                            continue

                        case AgentDecision.YIELD:
                            # ── GRACEFUL ABORT PATH ──
                            reflect_count = 0
                            _append_message(
                                {
                                    "role": "assistant",
                                    "content": self._format_assistant_history(
                                        llm_response
                                    ),
                                },
                                track_for_session=True,
                            )

                            yield_msg = (
                                response.final_answer or "Task cannot be completed."
                            )

                            await self.event_bus.emit(
                                AgentMessageEvent(
                                    source=self.name,
                                    agent_name=self.name,
                                    content=yield_msg,
                                    is_monologue=False,
                                )
                            )

                            logger.warning(
                                "Agent '%s' yielded on task '%s': %s",
                                self.name,
                                task_id,
                                yield_msg[:100],
                            )
                            return yield_msg

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
                    if schema_error_count >= max_schema_errors:
                        raise MaxIterationsExceededError(
                            f"Agent '{self.name}' produced "
                            f"{max_schema_errors} consecutive "
                            f"malformed responses.",
                            iterations=iteration,
                            max_iterations=max_iterations,
                            task_id=task_id,
                        )
                    if llm_response is not None:
                        _append_message(
                            {
                                "role": "assistant",
                                "content": self._format_assistant_history(llm_response),
                            },
                            track_for_session=True,
                        )
                    _append_message(
                        {
                            "role": "system",
                            "content": self._build_schema_recovery_message(e),
                        },
                        track_for_session=True,
                    )
                    continue

                except ToolExecutionError as e:
                    # RECOVERABLE: Feed error back to agent as observation
                    _append_message(
                        {
                            "role": "system",
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
                "without completing the task.",
                iterations=max_iterations,
                max_iterations=max_iterations,
                task_id=task_id,
            )
        finally:
            self._last_task_messages = list(task_delta)
            self._cached_capability_snapshot = None

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

    def get_last_task_title(self) -> str:
        """Return the best-effort title generated during the most recent task."""
        return self._last_task_title

    @staticmethod
    def _format_assistant_history(llm_response: Any) -> str:
        """Serialize native tool calls as JSON snippets for session history."""
        if llm_response is None:
            return ""
        content = llm_response.text_content or ""
        mode = getattr(llm_response, "tool_mode", None)
        if mode and getattr(mode, "value", str(mode)) == "NATIVE":
            tool_calls = getattr(llm_response, "tool_calls", [])
            if tool_calls:
                snippets: List[str] = []
                for idx, tc in enumerate(tool_calls):
                    snippets.append(
                        json.dumps(
                            {
                                "type": "tool_call",
                                "version": "1.0",
                                "payload": {
                                    "tool": tc.tool_name,
                                    "args": tc.arguments,
                                    "action_id": tc.native_call_id or f"act_{idx}",
                                },
                                "metadata": {"native_call_id": tc.native_call_id}
                                if tc.native_call_id
                                else {},
                            },
                            ensure_ascii=True,
                            separators=(",", ":"),
                        )
                    )
                snippet_block = "\n".join(snippets)
                if content:
                    content = f"{content}\n{snippet_block}"
                else:
                    content = snippet_block
        return content

    def _get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Retrieve tool definitions for the LLM."""
        executable_tools, _ = self._resolve_configured_tool_names()
        if not executable_tools:
            return []
        return self.tool_executor.registry.get_definitions_for_llm(executable_tools)

    def _is_tool_parallel_safe(self, tool_name: str) -> bool:
        """Whether a tool can run concurrently with other actions."""
        tool = self.tool_executor.registry.get(tool_name)
        if tool is None:
            return False
        return bool(getattr(tool, "parallel_safe", True))

    def _get_observability_manager(self) -> Any:
        """Get observability manager from tool executor when configured."""
        getter = getattr(self.tool_executor, "get_observability_manager", None)
        if callable(getter):
            return getter()
        return None

    def _on_llm_response(self, response: LLMResponse | None) -> None:
        """Track token/cost usage for budget-aware reflect hints."""
        if response is None:
            return

        input_tokens = max(int(getattr(response, "input_tokens", 0) or 0), 0)
        output_tokens = max(int(getattr(response, "output_tokens", 0) or 0), 0)
        cost = max(float(getattr(response, "cost_usd", 0.0) or 0.0), 0.0)
        if input_tokens == 0 and output_tokens == 0 and cost <= 0:
            return
        self._resource_tracker.update(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        )

    def get_prompt_tool_names(self) -> List[str]:
        """Return locally executable tool names for prompt construction."""
        executable_tools, _ = self._resolve_configured_tool_names()
        return executable_tools

    def _get_provider_managed_tools(self) -> List[str]:
        """Return provider-managed capability tokens from configured tools."""
        _, provider_managed = self._resolve_configured_tool_names()
        supported: List[str] = []
        for capability_name in provider_managed:
            if self._is_effective_capability_supported(capability_name):
                supported.append(capability_name)
        return supported

    def _resolve_configured_tool_names(self) -> tuple[List[str], List[str]]:
        """Split configured tools into executable and provider-managed groups."""
        if not self.config.tools:
            return [], []

        executable: List[str] = []
        provider_managed: List[str] = []
        unknown: List[str] = []
        known_tools = set(self.tool_executor.registry.get_all_names())

        for raw_name in self.config.tools:
            name = str(raw_name).strip()
            if not name:
                continue
            if name in known_tools:
                executable.append(name)
                continue
            if name in self._provider_managed_tool_tokens:
                provider_managed.append(name)
                continue
            unknown.append(name)

        if unknown:
            available = sorted(known_tools | set(self._provider_managed_tool_tokens))
            raise ValueError(
                "Unknown configured tool(s): "
                f"{', '.join(sorted(set(unknown)))}. Available: {', '.join(available)}"
            )
        return executable, provider_managed

    def _resolve_effective_effort_from_capabilities(self, desired_effort: str) -> str:
        """Resolve effort against effective capability status for this runtime."""
        normalized = normalize_effort(desired_effort).value
        if normalized == EffortLevel.AUTO.value:
            return normalized
        if self._is_effective_capability_supported("effort"):
            return normalized
        return EffortLevel.AUTO.value

    def _is_effective_capability_supported(self, capability_name: str) -> bool:
        """Whether the effective capability snapshot says this capability is supported."""
        snapshot = self._get_capability_snapshot()
        if snapshot is None:
            return False
        effective = getattr(snapshot, "effective", {})
        capability = effective.get(str(capability_name).strip())
        status = str(getattr(capability, "status", "unknown")).strip().lower()
        return status == "supported"

    def _supports_native_tools_effective(self) -> bool:
        """Effective native-tools support for prompt/schema behavior."""
        return self._is_effective_capability_supported("native_tools")

    def _get_capability_snapshot(self) -> Any:
        """Load effective capability snapshot for current provider/model identity."""
        cached = self._cached_capability_snapshot
        if cached is not None:
            return cached

        provider = self.provider
        if not isinstance(provider, BaseLLMProvider):
            return None

        provider_name = str(getattr(provider, "provider_name", "")).strip()
        model_name = str(getattr(provider, "model_name", "")).strip()
        base_url = str(getattr(provider, "base_url", "") or "").strip()
        if not provider_name or not model_name:
            return None

        deployment_id = self._build_deployment_id(
            provider_name=provider_name,
            model_name=model_name,
            base_url=base_url,
        )
        snapshot = self._data_registry.get_capability_snapshot(
            provider=provider_name,
            model=model_name,
            deployment_id=deployment_id,
        )
        self._cached_capability_snapshot = snapshot
        return snapshot

    @staticmethod
    def _build_deployment_id(
        *,
        provider_name: str,
        model_name: str,
        base_url: str = "",
    ) -> str:
        provider = str(provider_name).strip() or "unknown"
        model = str(model_name).strip() or "unknown"
        base = str(base_url).strip()
        if base:
            return f"{provider}:{model}@{base}"
        return f"{provider}:{model}"

    def _build_schema_recovery_message(self, error: SchemaValidationError) -> str:
        """Build a short, machine-actionable schema recovery instruction."""
        code, field, expected, received, fix_instruction, valid_example = (
            self._classify_schema_error(error)
        )
        fallback_payload = {
            "title": "Blocked",
            "thought": "Schema correction failed safely.",
            "decision": {
                "type": "yield",
                "message": (
                    "I could not produce a valid action format after schema correction. "
                    "Please retry."
                ),
            },
        }
        fallback_json = json.dumps(
            fallback_payload,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return (
            f"SCHEMA_ERROR|code={code}|field={field}|expected={expected}|received={received}\n\n"
            "Return exactly ONE JSON object and no other text.\n\n"
            "Apply this fix now:\n"
            f"{fix_instruction}\n\n"
            "Valid example:\n"
            f"{valid_example}\n\n"
            "If you are still uncertain, return this fallback JSON exactly:\n"
            f"{fallback_json}"
        )

    def _classify_schema_error(
        self,
        error: SchemaValidationError,
    ) -> tuple[str, str, str, str, str, str]:
        """Map schema errors to deterministic recovery instructions."""
        message = str(error).strip()
        lowered = message.lower()

        allowed_decisions = "reflect,execute_action,execute_actions,notify_user,yield"
        generic_example = (
            '{"title":"Read file","thought":"I need file contents.","decision":{"type":"execute_action",'
            '"tool":"read_file","args":{"path":"README.md"}}}'
        )
        generic_fix = (
            "Use one allowed decision.type value and include all required fields for that type."
        )

        if "unknown decision.type" in lowered:
            received = self._extract_decision_type_from_raw_response(error.raw_response)
            if received == "ask_user":
                return (
                    "enum_unknown",
                    "decision.type",
                    allowed_decisions,
                    received,
                    (
                        'Use decision.type="execute_action", decision.tool="ask_user", '
                        "and put question/options inside decision.args."
                    ),
                    (
                        '{"title":"Ask clarification","thought":"Need user choice before proceeding.","decision":'
                        '{"type":"execute_action","tool":"ask_user","args":{"question":"Which format do you prefer?",'
                        '"options":["A","B","C"]}}}'
                    ),
                )
            return (
                "enum_unknown",
                "decision.type",
                allowed_decisions,
                received,
                (
                    'Set decision.type to one allowed value. For tool usage, use decision.type="execute_action" '
                    "with decision.tool and decision.args."
                ),
                generic_example,
            )

        if "must contain a 'decision' object" in lowered:
            return (
                "missing_field",
                "decision",
                "object",
                "missing",
                'Add a top-level decision object with a valid decision.type and required fields.',
                generic_example,
            )

        if "decision.type is required" in lowered:
            return (
                "missing_field",
                "decision.type",
                allowed_decisions,
                "missing",
                "Add decision.type and choose one allowed value.",
                generic_example,
            )

        if "decision.tool is required" in lowered:
            return (
                "missing_field",
                "decision.tool",
                "registered_tool_name",
                "missing",
                'Set decision.tool when decision.type is "execute_action".',
                generic_example,
            )

        if "decision.args must be an object" in lowered:
            return (
                "type_mismatch",
                "decision.args",
                "object",
                "non_object",
                "Set decision.args to a JSON object ({} if no arguments).",
                generic_example,
            )

        if "decision.actions must be a non-empty list" in lowered:
            return (
                "type_mismatch",
                "decision.actions",
                "non_empty_list",
                "invalid",
                (
                    'Set decision.actions to a non-empty list of {"tool":"...","args":{...}} '
                    'items when decision.type is "execute_actions".'
                ),
                (
                    '{"title":"Batch read","thought":"Need independent tool results.","decision":{"type":"execute_actions",'
                    '"actions":[{"tool":"read_file","args":{"path":"README.md"}},'
                    '{"tool":"search_files","args":{"pattern":"TODO"}}]}}'
                ),
            )

        if "response is not valid json" in lowered:
            return (
                "invalid_json",
                "response",
                "single_json_object",
                "malformed",
                "Return one valid JSON object only (no markdown, no prose, no code fences).",
                generic_example,
            )

        if "contains no reasoning, no tool call, and no final answer" in lowered:
            return (
                "empty_response",
                "response",
                "single_json_object",
                "empty",
                "Return a non-empty JSON object with one valid decision.",
                generic_example,
            )

        return (
            "schema_invalid",
            "response",
            "valid_schema",
            "invalid",
            generic_fix,
            generic_example,
        )

    @staticmethod
    def _extract_decision_type_from_raw_response(raw_response: str | None) -> str:
        """Best-effort extraction of decision.type from raw model output."""
        if not raw_response:
            return "unknown"
        stripped = raw_response.strip()
        if not stripped:
            return "unknown"
        try:
            payload = json.loads(stripped)
            if isinstance(payload, dict):
                decision = payload.get("decision")
                if isinstance(decision, dict):
                    value = str(decision.get("type", "")).strip().lower()
                    if value:
                        return value
        except json.JSONDecodeError:
            pass
        return "unknown"

    def _debug_intercept_response(
        self, task_id: str, iteration: int, raw_content: str
    ) -> None:
        """Utility to intercept and save raw LLM responses to a debug file."""
        debug_path = Path.home() / ".agent_cli" / "debug" / "agent_response.txt"
        try:
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(debug_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 80}\n")
                f.write(f"TIMESTAMP: {timestamp}\n")
                f.write(f"AGENT:     {self.name}\n")
                f.write(f"TASK_ID:   {task_id}\n")
                f.write(f"ITERATION: {iteration}\n")
                f.write(f"{'-' * 80}\n")
                f.write(raw_content)
                f.write(f"\n{'=' * 80}\n")
        except Exception as e:
            logger.error(f"Failed to save debug interception: {e}")

