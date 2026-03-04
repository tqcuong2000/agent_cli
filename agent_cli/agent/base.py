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
from typing import Any, Dict, List, Optional

from agent_cli.agent.memory import BaseMemoryManager
from agent_cli.agent.parsers import AgentDecision, AgentResponse
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
from agent_cli.core.models.config_models import EffortLevel, normalize_effort
from agent_cli.core.state.state_manager import AbstractStateManager
from agent_cli.data import DataRegistry
from agent_cli.providers.base import BaseLLMProvider
from agent_cli.providers.models import LLMResponse, ProviderRequestOptions
from agent_cli.tools.executor import ToolExecutor

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
    """

    name: str = ""
    description: str = ""
    persona: str = ""
    model: str = ""
    tools: List[str] = field(default_factory=list)
    max_iterations_override: Optional[int] = None
    show_thinking: bool = True


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
        settings: Any = None,  # AgentSettings
        data_registry: Optional[DataRegistry] = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.tool_executor = tool_executor
        self.validator = schema_validator
        self.memory = memory_manager
        self.event_bus = event_bus
        self.state_manager = state_manager
        self.prompt_builder = prompt_builder

        # In tests this might be None, so fallback gracefully
        if settings is None:
            from agent_cli.core.config import AgentSettings

            self.settings = AgentSettings()
        else:
            self.settings = settings

        self._data_registry = data_registry or DataRegistry()
        self._schema_defaults = self._data_registry.get_schema_defaults()
        self._retry_defaults = self._data_registry.get_retry_defaults()
        self._cached_capability_snapshot: Any = None

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

    # ── The Core ReAct Loop ──────────────────────────────────────

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

        # First persisted turn: ask the agent to produce a concise session title.
        if session_messages is not None and not session_messages:
            _append_message(
                {
                    "role": "system",
                    "content": (
                        "This is the first user request in a new session. "
                        "Set a concise session title in the top-level `title` field "
                        "(2-8 words, plain text)."
                    ),
                },
                track_for_session=False,
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
                            )

                            # Add tool result to Working Memory
                            _append_message(
                                {"role": "tool", "content": result},
                                track_for_session=True,
                            )

                            # Agent-specific hook
                            await self.on_tool_result(action.tool_name, result)

                            # Stuck detection
                            if stuck_detector.is_stuck(action.tool_name, result):
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
                            if reflect_count >= max_reflects:
                                _append_message(
                                    {
                                        "role": "system",
                                        "content": (
                                            f"You have reflected {reflect_count} consecutive "
                                            "times. You must now execute an action or "
                                            "provide a final answer."
                                        ),
                                    },
                                    track_for_session=True,
                                )
                            else:
                                _append_message(
                                    {
                                        "role": "system",
                                        "content": (
                                            "Reasoning noted. Continue planning "
                                            "or execute an action."
                                        ),
                                    },
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
                for tc in tool_calls:
                    snippets.append(
                        json.dumps(
                            {
                                "type": "tool_call",
                                "version": "1.0",
                                "payload": {
                                    "tool": tc.tool_name,
                                    "args": tc.arguments,
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
        """Build a concrete, mode-aware schema recovery instruction."""
        header = (
            f"Schema Error: {error}\n"
            "Your NEXT response must be corrected and valid. "
            "Use exactly one decision, no empty response, and output exactly one JSON object."
        )

        native_mode = self._supports_native_tools_effective()
        if native_mode:
            return (
                f"{header}\n"
                "Valid native-mode JSON examples:\n"
                "1) Tool call: "
                '{"title":"Read file","thought":"I need the file contents.","decision":{"type":"execute_action","tool":"read_file","args":{"path":"README.md"}}} '
                "and call exactly one native tool in this turn.\n"
                "2) Final answer: "
                '{"title":"Task complete","thought":"I verified the result.","decision":{"type":"notify_user","message":"..."}}\n'
                "3) Yield: "
                '{"title":"Blocked","thought":"I cannot proceed safely.","decision":{"type":"yield","message":"..."}}'
            )

        return (
            f"{header}\n"
            "Valid prompt JSON examples:\n"
            "1) Tool call: "
            '{"title":"Read file","thought":"I need the file contents.","decision":{"type":"execute_action","tool":"read_file","args":{"path":"README.md"}}}\n'
            "2) Final answer: "
            '{"title":"Task complete","thought":"I verified the result.","decision":{"type":"notify_user","message":"..."}}\n'
            "3) Yield: "
            '{"title":"Blocked","thought":"I cannot proceed safely.","decision":{"type":"yield","message":"..."}}'
        )

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
