# File Discovery & Context Injection Architecture (The '@' Prefix)

## Overview
A major friction point in CLI interactions is explaining context to the agent. If a user wants to ask about a specific file or folder, typing out *"Can you explain the config.py file in the src/core directory?"* is tedious.

The `@` prefix is a powerful UX utility that allows users to seamlessly inject files, directories, or specific code symbols directly into the prompt (e.g., `agent run "Refactor @src/core/config.py"`). 

Crucially, **this must operate entirely outside the Agent's reasoning loop**. It is a pre-processing step handled by the TUI or the Orchestrator *before* the LLM even sees the prompt.

## 1. How It Works (The Pre-Processing Pipeline)

When a user submits a string like `Explain how @utils/logger.py works with @src/main.py`:

### A. The Parsing Phase (Regex Extraction)
1. **Detection:** The CLI intercepts the raw user string.
2. **Regex:** It runs a regex (e.g., `(?:^|\s)@([a-zA-Z0-9_./\-]+)`) to find all paths immediately following an `@` symbol.
3. **Extraction:** It extracts `utils/logger.py` and `src/main.py` into a list, removing the `@...` text from the original prompt (or leaving it as plain text if preferred).

### B. The Resolution Phase (Path Validation)
1. **Absolute/Relative:** The utility attempts to resolve the extracted strings against the current working directory (`CWD`).
2. **Wildcards/Globs:** If the user types `@src/**/*.rs`, the resolver expands this into a list of all matching Rust files.
3. **Directories:** If the path is a folder (e.g., `@src/core`), the resolver recursively fetches all text files within it (respecting `.gitignore` limits to prevent crashing the memory).
4. **Validation:** If a path does not exist, the utility immediately warns the user in the TUI *before* spending tokens: `"Warning: Could not find @utils/logger_v2.py. Continue anyway?"`

### C. The Injection Phase (Context Formatting)
1. **File Reading:** The utility reads the contents of the resolved files.
2. **Formatting:** It wraps the contents in a standardized XML block.
    ```xml
    <injected_context>
      <file path="utils/logger.py">
      import logging
      ...
      </file>
      <file path="src/main.py">
      ...
      </file>
    </injected_context>
    ```
3. **Appending to Memory:** The Orchestrator takes this massive XML block and secretly appends it to the `WorkingMemory` as a `System` message or prepends it to the `User` message.

## 2. Advanced "@" Features (The Roadmap)

### A. Line Number Slicing
Users rarely want to inject a 10,000-line file.
*   **Syntax:** `@src/main.py:100-150`
*   **Resolution:** The parser reads only lines 100 through 150, drastically reducing token usage.

### B. Symbol Extraction (AST Parsing)
*   **Syntax:** `@src/main.py:class:DatabaseConnector` or `@src/main.py::DatabaseConnector`
*   **Resolution:** The utility uses Python's `ast` module (or Tree-sitter for other languages) to locate the exact class or function and extracts only that block of code.

### C. The TUI Autocomplete (The Ultimate UX)
Because Textual (or `prompt-toolkit`) supports custom autocompletion:
1. When the user types `@` in the TUI input box, a dropdown menu appears.
2. The UI fuzzy-searches the local directory structure dynamically as they type (`@s` -> `src/`, `scripts/`).
3. Pressing `Tab` auto-completes the path.

## 3. Abstract Python Interface

Following the `python-abstraction.md` rule, the pre-processor must be decoupled from the Orchestrator.

```python
from abc import ABC, abstractmethod
from typing import List, Dict
import re
import os

class ResolvedContext:
    def __init__(self, original_prompt: str, files: Dict[str, str]):
        self.clean_prompt = original_prompt # The prompt with or without @ symbols
        self.files = files                  # Dict[filepath, file_content]

class BaseContextInjector(ABC):
    """
    Parses user input for special symbols (@), resolves the paths, 
    and returns the injected context.
    """
    
    @abstractmethod
    def parse_and_resolve(self, raw_input: str, cwd: str) -> ResolvedContext:
        """Finds @paths, validates them, and reads their contents."""
        pass
        
    @abstractmethod
    def format_for_llm(self, context: ResolvedContext) -> str:
        """Converts the resolved files into the standard XML <injected_context> format."""
        pass

# --- Example Concrete Implementation ---
class AtPrefixInjector(BaseContextInjector):
    def parse_and_resolve(self, raw_input: str, cwd: str) -> ResolvedContext:
        # 1. Regex find all @...
        pattern = r"(?:^|\s)@([a-zA-Z0-9_.\/\-]+)"
        matches = re.findall(pattern, raw_input)
        
        files_content = {}
        for match in matches:
            full_path = os.path.join(cwd, match)
            if os.path.isfile(full_path):
                # Always enforce a size limit or .gitignore check here!
                with open(full_path, "r", encoding="utf-8") as f:
                    files_content[match] = f.read()
            else:
                # Handle error or ignore
                pass
                
        return ResolvedContext(raw_input, files_content)
```

## 4. Why this is an Architectural Prerequisite
By building this as a **Pre-Processing Utility**, you completely avoid the "Infinite Tool Loop" problem. 
If a user just typed `"Explain logger.py"`, the Agent would have to:
1. Think: "I need to find logger.py"
2. Action: `<tool>search</tool> logger.py`
3. Action: `<tool>read_file</tool> src/utils/logger.py`
4. Think: "Now I can explain it."

By using `@src/utils/logger.py`, you bypass 3 expensive API calls. The file is already in the prompt on turn 0.
