"""Built-in coding specialist agent."""

from __future__ import annotations

from agent_cli.core.runtime.agents.default import DefaultAgent


class CoderAgent(DefaultAgent):
    """Agent tuned for implementation and refactoring tasks."""

    persona_template_name = "coder_persona"
