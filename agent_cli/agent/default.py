"""Default Agent implementation.

This is a general-purpose agent that inherits from ``BaseAgent``
and performs standard tasks utilizing the available tools.
"""

from __future__ import annotations

import platform

from agent_cli.agent.base import BaseAgent


class DefaultAgent(BaseAgent):
    """A general-purpose agent implementing the ReAct loop."""

    persona_template_name = "default_persona"

    async def build_system_prompt(self, task_context: str) -> str:
        """Construct the system prompt for this agent."""
        persona = self.config.persona.strip() if self.config.persona else ""
        if not persona:
            persona = self._data_registry.get_prompt_template(
                self.persona_template_name
            ).strip()

        native_tools = self._supports_native_tools_effective()
        provider_capabilities = self._get_provider_managed_tools()

        prompt = self.prompt_builder.build(
            persona=persona,
            tool_names=self.get_prompt_tool_names(),
            workspace_context=f"Operating System: {platform.system() or 'Unknown'}",
            native_tool_mode=native_tools,
            provider_managed_capabilities=provider_capabilities,
        )
        return prompt

    async def on_tool_result(self, tool_name: str, result: str) -> None:
        """Hook called after every tool execution."""
        pass  # No custom logic needed for the default agent

    async def on_final_answer(self, answer: str) -> str:
        """Hook called before returning the final answer."""
        return answer
