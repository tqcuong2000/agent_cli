from textual.app import ComposeResult
from textual.containers import Container
from textual.widget import Widget
from typing_extensions import List, Optional


class BaseWidget(Container):
    """
    Base widget class.

    Attributes:
    - id: Unique identifier for the widget.
    - components: List of components to be displayed in the widget.
    """

    def __init__(self, id: str, components: Optional[List[Widget]] = None, **kwargs):
        super().__init__(id=id, **kwargs)
        self.components = components or []

    def compose(self) -> "ComposeResult":
        """Yield the components to be displayed in the widget."""
        for component in self.components:
            yield component

    def get_component(self, component_id: str) -> Optional[Widget]:
        """Helper to find a child component by its ID."""
        try:
            return self.query_one(f"#{component_id}", Widget)
        except Exception:
            return None

    def on_mount(self) -> None:
        """Called automatically by Textual when the widget is added to the screen."""
        pass
