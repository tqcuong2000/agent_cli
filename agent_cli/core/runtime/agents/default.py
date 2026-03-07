"""Default Agent implementation.

This is a general-purpose agent that inherits from ``BaseAgent``
and performs standard tasks utilizing the available tools.
"""

from __future__ import annotations

from agent_cli.core.runtime.agents.base import BaseAgent


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
        system_info = (
            self.system_info_provider.snapshot()
            if self.system_info_provider is not None
            else None
        )

        prompt = self.prompt_builder.build(
            persona=persona,
            tool_names=self.get_prompt_tool_names(),
            system_info=system_info,
            native_tool_mode=native_tools,
            multi_action=self.config.multi_action_enabled,
            provider_managed_capabilities=provider_capabilities,
        )
        return prompt

    async def on_tool_result(self, tool_name: str, result: str) -> None:
        """Hook called after every tool execution."""
        pass  # No custom logic needed for the default agent

    async def on_final_answer(self, answer: str) -> str:
        """Hook called before returning the final answer."""
        return answer
