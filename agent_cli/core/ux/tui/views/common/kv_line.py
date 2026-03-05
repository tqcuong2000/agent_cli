from textual.containers import Horizontal
from textual.widgets import Static

class KVLine(Horizontal):
    """A key-value line."""

    DEFAULT_CSS = ""

    def __init__(self, key: str, value: str, seperator: str ,**kwargs):
        super().__init__(**kwargs)
        self.key = key
        self.value = value
        self.seperator = seperator
    
    def compose(self):
        yield Static(self.key, classes="kv_key")
        yield Static(self.seperator, classes="kv_seperator")
        yield Static(self.value, classes="kv_value")