"""Default Agent implementation.

This is a general-purpose agent that inherits from ``BaseAgent``
and performs standard tasks utilizing the available tools.
"""

from __future__ import annotations

from agent_cli.agent.base import BaseAgent


class DefaultAgent(BaseAgent):
    """A general-purpose agent implementing the ReAct loop."""

    async def build_system_prompt(self, task_context: str) -> str:
        """Construct the system prompt for this agent."""
        # The user's original roadmap mentions researchers/coders,
        # but for now we provide a solid generalist.
        persona = (
            "You are a helpful, expert AI assistant. "
            "You have access to tools that let you interact with the user's system."
        )

        effort_constraints = self.effort  # Resolved dynamic effort level
        constraints = self.settings.get_effort_config(effort_constraints)

        native_tools = getattr(self.provider, "supports_native_tools", False)

        prompt = self.prompt_builder.build(
            persona=persona,
            tool_names=self.config.tools,
            effort_constraints=constraints,
            workspace_context="Operating System: Windows",
            extra_instructions=(
                "Answer carefully and accurately. "
                "If required details are missing or ambiguous, call the "
                "ask_user tool instead of guessing."
            ),
            native_tool_mode=native_tools,
        )
        return prompt

    async def on_tool_result(self, tool_name: str, result: str) -> None:
        """Hook called after every tool execution."""
        pass  # No custom logic needed for the default agent

    async def on_final_answer(self, answer: str) -> str:
        """Hook called before returning the final answer."""
        return answer
