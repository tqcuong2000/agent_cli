from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Static


class TitleComponent(Static):
    """Component to display the application or conversation title."""

    DEFAULT_CSS = ""

    def __init__(self, content: str = "Engine CLI", **kwargs):
        comp_id = kwargs.pop("id", "title")
        super().__init__(content, id=comp_id, **kwargs)

    def update_title(self, content: str) -> None:
        """Update the rendered title text."""
        self.update(content)


class AgentBadgeComponent(Static):
    """Component to display the currently active agent."""

    DEFAULT_CSS = ""

    def __init__(self, label: str = "Main Agent", **kwargs):
        # Default to 'agent_badge' ID if not provided
        if "id" not in kwargs:
            kwargs["id"] = "agent_badge"
        super().__init__(label, **kwargs)


class HeaderContainer(Container):
    """The main header container holding the title, quick actions, and agent badge."""

    DEFAULT_CSS = ""

    def __init__(self):
        self.title_comp = TitleComponent()
        self.badge_comp = AgentBadgeComponent()
        super().__init__(id="header")

    def compose(self) -> ComposeResult:
        """Compose the horizontal layout."""
        with Horizontal():
            yield self.title_comp
            yield Static(" ", id="spacer_left", classes="spacer-left")
            yield self.badge_comp
