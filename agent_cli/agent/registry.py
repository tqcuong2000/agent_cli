"""Global registry of all available agent instances."""

from __future__ import annotations

from typing import Dict, List, Optional

from agent_cli.agent.base import BaseAgent


class AgentRegistry:
    """Global catalog of available agents.

    This registry tracks all instantiated agents that can be added
    to a session. It is distinct from SessionAgentRegistry, which
    tracks per-session ACTIVE/IDLE/INACTIVE state.
    """

    def __init__(self) -> None:
        self._agents: Dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        name = agent.name
        if name in self._agents:
            raise ValueError(f"Agent '{name}' is already registered.")
        self._agents[name] = agent

    def get(self, name: str) -> Optional[BaseAgent]:
        return self._agents.get(name)

    def has(self, name: str) -> bool:
        return name in self._agents

    def get_all(self) -> List[BaseAgent]:
        return list(self._agents.values())

    def names(self) -> List[str]:
        return sorted(self._agents.keys())
