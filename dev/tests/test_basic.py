import pytest
from agent_cli import __version__

def test_version():
    """Verify that the version matches the expected value."""
    assert __version__ == "0.1.0"

def test_root_exists():
    """A simple placeholder test."""
    assert True
