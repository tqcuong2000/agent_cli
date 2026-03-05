from textual.widgets import Static


class AgentBadgeComponent(Static):
    """Component to display the currently active agent."""

    DEFAULT_CSS = ""

    def __init__(self, label: str = "Main Agent", **kwargs):
        # Default to 'agent_badge' ID if not provided
        if "id" not in kwargs:
            kwargs["id"] = "agent_badge"
        super().__init__(label, **kwargs)
