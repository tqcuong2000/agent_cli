"""
Agent Core Logic — reasoning loop, schema verification, and memory.

This package contains the agent's brain:
- ``parsers``  — ``ParsedAction`` and ``AgentResponse`` data classes.
- ``schema``   — ``BaseSchemaValidator`` and ``SchemaValidator`` for
                 dual-mode (native FC + XML) response validation.
- ``base``     — ``BaseAgent`` ABC with the ReAct reasoning loop.
- ``memory``   — ``BaseMemoryManager`` and ``WorkingMemoryManager``.
"""
