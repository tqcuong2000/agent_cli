"""Built-in research specialist agent."""

from __future__ import annotations

from agent_cli.core.runtime.agents.default import DefaultAgent


class ResearcherAgent(DefaultAgent):
    """Agent tuned for analysis, exploration, and explanation tasks."""

    persona_template_name = "researcher_persona"
