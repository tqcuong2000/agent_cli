from textual.containers import Container
from textual.app import ComposeResult
from typing import List, Optional
from agent_cli.ux.tui.widgets.base import BaseWidget

class BaseLayout(Container):
    """
    Base layout class.
    
    Attributes:
    - id: Unique identifier for the layout.
    - widgets: List of widgets to be displayed in the layout.
    """
    def __init__(self, id: str, widgets: Optional[List[BaseWidget]] = None, **kwargs):
        super().__init__(id=id, **kwargs)
        self.widgets = widgets or []

    def compose(self) -> ComposeResult:
        """Yield the widgets to be displayed in the layout."""
        for widget in self.widgets:
            yield widget

    def get_widget(self, widget_id: str) -> Optional[BaseWidget]:
        """Helper to safely query widgets within this layout."""
        try:
            return self.query_one(f"#{widget_id}", BaseWidget)
        except Exception:
            return None
            
    def toggle_widget_visibility(self, widget_id: str, visible: Optional[bool] = None) -> None:
        """Show or hide a specific widget."""
        widget = self.get_widget(widget_id)
        if widget:
            if visible is None:
                widget.display = not widget.display
            else:
                widget.display = visible
