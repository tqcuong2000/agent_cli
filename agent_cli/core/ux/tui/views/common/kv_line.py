from textual.containers import Horizontal
from textual.widgets import Static

class KVLine(Horizontal):
    """A key-value line."""

    DEFAULT_CSS = """
    KVLine {
        height: auto;
    }

    KVLine .kv_key {
        color: $text-muted;
        width: auto;
    }

    KVLine .kv_seperator {
        color: $text-muted;
        width: auto;
    }

    KVLine .kv_value {
        color: $text-muted;
        width: auto;
    }
    """

    def __init__(self, key: str, value: str, seperator: str ,**kwargs):
        super().__init__(**kwargs)
        self.key = key
        self.value = value
        self.seperator = seperator
    
    def compose(self):
        yield Static(self.key, classes="kv_key")
        yield Static(self.seperator, classes="kv_seperator")
        yield Static(self.value, classes="kv_value")