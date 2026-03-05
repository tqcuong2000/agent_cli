"""
Human-in-the-Loop interaction models and interface.

This module defines the common request/response contract used when
any subsystem needs user input (tool approval, clarification, plan
review, fatal error escalation).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional


class InteractionType(Enum):
    """Types of human interaction supported by the system."""

    APPROVAL = auto()
    CLARIFICATION = auto()
    PLAN_APPROVAL = auto()
    FATAL_ERROR = auto()


@dataclass
class UserInteractionRequest:
    """A request for human input emitted by a system component."""

    interaction_type: InteractionType
    message: str
    task_id: str = ""
    source: str = ""
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    plan_assignments: Optional[List[Any]] = None
    error_details: Optional[str] = None
    options: List[str] = field(default_factory=list)


@dataclass
class UserInteractionResponse:
    """The structured result of a human interaction."""

    action: str = ""
    feedback: str = ""
    edited_args: Optional[Dict[str, Any]] = None


class BaseInteractionHandler(ABC):
    """Abstract interface for all human-in-the-loop adapters."""

    @abstractmethod
    async def request_human_input(
        self, request: UserInteractionRequest
    ) -> UserInteractionResponse:
        """Pause execution and return the user's response."""

    @abstractmethod
    async def notify(self, message: str) -> None:
        """Send a non-blocking notification to the user."""
