"""Per-session agent registry with ACTIVE/IDLE/INACTIVE state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from agent_cli.agent.base import BaseAgent


class AgentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    IDLE = "IDLE"
    INACTIVE = "INACTIVE"


@dataclass
class SessionAgent:
    name: str
    status: AgentStatus
    agent_instance: BaseAgent

    @property
    def is_available(self) -> bool:
        return self.status != AgentStatus.INACTIVE


class SessionAgentRegistry:
    """Tracks which agents participate in a session and their state."""

    def __init__(self) -> None:
        self._agents: Dict[str, SessionAgent] = {}
        self._active_name: Optional[str] = None

    def add(self, agent: BaseAgent, *, activate: bool = False) -> None:
        if not hasattr(agent, "name") or not str(getattr(agent, "name", "")).strip():
            raise ValueError("Agent must have a non-empty 'name' attribute.")

        if agent.name in self._agents:
            raise ValueError(f"Agent '{agent.name}' is already in this session.")

        self._agents[agent.name] = SessionAgent(
            name=agent.name,
            status=AgentStatus.IDLE,
            agent_instance=agent,
        )
        if activate:
            self.switch_to(agent.name)

    def switch_to(self, name: str) -> BaseAgent:
        if name not in self._agents:
            raise KeyError(
                f"Agent '{name}' is not in this session. Use /agent add {name} first."
            )

        target = self._agents[name]
        if target.status == AgentStatus.INACTIVE:
            raise ValueError(f"Agent '{name}' is inactive. Use /agent enable {name}.")

        if self._active_name and self._active_name in self._agents:
            active = self._agents[self._active_name]
            if active.status == AgentStatus.ACTIVE:
                active.status = AgentStatus.IDLE

        target.status = AgentStatus.ACTIVE
        self._active_name = name
        return target.agent_instance

    def disable(self, name: str) -> None:
        if name not in self._agents:
            raise KeyError(f"Agent '{name}' is not in this session.")
        if name == self._active_name:
            raise ValueError("Cannot disable the active agent. Switch first.")
        self._agents[name].status = AgentStatus.INACTIVE

    def enable(self, name: str) -> None:
        if name not in self._agents:
            raise KeyError(f"Agent '{name}' is not in this session.")
        if name == self._active_name:
            self._agents[name].status = AgentStatus.ACTIVE
            return
        self._agents[name].status = AgentStatus.IDLE

    def remove(self, name: str) -> None:
        if name not in self._agents:
            raise KeyError(f"Agent '{name}' is not in this session.")
        if name == self._active_name:
            raise ValueError("Cannot remove the active agent. Switch first.")
        self._agents.pop(name, None)

    def has(self, name: str) -> bool:
        return name in self._agents

    @property
    def active_agent(self) -> Optional[BaseAgent]:
        if self._active_name and self._active_name in self._agents:
            return self._agents[self._active_name].agent_instance
        return None

    @property
    def active_name(self) -> Optional[str]:
        return self._active_name

    def list_agents(self) -> List[SessionAgent]:
        return list(self._agents.values())
