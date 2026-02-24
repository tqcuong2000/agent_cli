# Project Context - agent_cli

`agent_cli` is a Python-based CLI and TUI tool designed for both human users and AI agents. It follows a clean architecture with a strictly separated backend (core) and frontend (UX).

## Tech Stack
- **Core**: Python 3.8+
- **Configuration**: Pydantic & Pydantic-Settings (supports `.env` and environment variables with `AGENT_CLI_` prefix)
- **TUI Framework**: [Textual](https://textual.textualize.io/)
- **Build System**: Setuptools (modern `pyproject.toml`)
- **Testing**: Pytest

## Project Structure
- `agent_cli/`
  - `core/`: Backend business logic and configuration (`config.py`).
  - `ux/`: User Experience layer.
    - `tui/`: Textual TUI implementation.
      - `components/`: Atomic UI units (agent badges, usage stats, etc.).
      - `widgets/`: Collections of components (header, chat, status bar).
      - `layouts/`: Screen structure and positioning.
    - `cli/`: Simple interface for AI agents.
  - `__main__.py`: Entry point for `python -m agent_cli`.

## Design Principles
- **Abstraction**: Separate logic between frontend (UX) and backend (core).
- **Component-Based UI**: UI built from atomic components -> functional widgets -> full layouts.
- **Environment Aware**: Automatically sets the root folder to the caller terminal's location using `os.getcwd()`.
- **Theme Support**: Includes built-in dark mode toggle.

## Development
- **Installation**: `pip install -e .` (or `pip install -e ".[test]"` for testing tools)
- **Execution**: `agent-cli` or `python -m agent_cli`
- **Testing**: `pytest`
