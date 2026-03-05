"""Global registry of all available agent instances."""

from __future__ import annotations

from typing import Dict, List, Optional

from agent_cli.core.runtime.agents.base import BaseAgent
from agent_cli.core.infra.registry.registry_base import RegistryLifecycleMixin


class AgentRegistry(RegistryLifecycleMixin):
    """Global catalog of available agents.

    This registry tracks all instantiated agents that can be added
    to a session. It is distinct from SessionAgentRegistry, which
    tracks per-session ACTIVE/IDLE/INACTIVE state.
    """

    def __init__(self) -> None:
        super().__init__(registry_name="agents")
        self._agents: Dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        self._assert_mutable()

        if not hasattr(agent, "name") or not str(getattr(agent, "name", "")).strip():
            raise ValueError("Agent must have a non-empty 'name' attribute.")
        if not hasattr(agent, "handle_task"):
            raise ValueError(f"Agent '{agent.name}' must have a 'handle_task' method.")

        name = str(agent.name).strip()
        if name in self._agents:
            raise ValueError(f"Agent '{name}' is already registered.")
        self._agents[name] = agent

    def _freeze_summary(self) -> str:
        names = ", ".join(sorted(self._agents.keys()))
        return f"{len(self._agents)} agents: {names}" if names else "0 agents"

    def validate(self) -> None:
        if not self._agents:
            raise RuntimeError(
                "Agent registry must contain at least one agent before freeze."
            )

    def get(self, name: str) -> Optional[BaseAgent]:
        return self._agents.get(name)

    def has(self, name: str) -> bool:
        return name in self._agents

    def get_all(self) -> List[BaseAgent]:
        return list(self._agents.values())

    def names(self) -> List[str]:
        return sorted(self._agents.keys())

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, name: str) -> bool:
        return name in self._agents
