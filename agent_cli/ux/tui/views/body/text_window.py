from textual.containers import Container, VerticalScroll
from agent_cli.ux.tui.views.body.messages.user_message import UserMessageContainer


class TextWindowContainer(Container):
    """A blank container for the text window."""

    DEFAULT_CSS = """
    TextWindowContainer {
        width: 3fr;
        height: 100%;
        background: transparent;
    }
    """

    def __init__(self, **kwargs):
        if "id" not in kwargs:
            kwargs["id"] = "text_window"
        super().__init__(**kwargs)

    def compose(self):
        with VerticalScroll():
            yield UserMessageContainer("Hello, could you help me write a python script for my agent project? \nPython version is 3.13.7")
