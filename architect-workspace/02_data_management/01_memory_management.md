# Context & Memory Management Architecture

## Overview
As the Agent runs its Reasoning Loop, the context window (its "Short-Term Memory") grows linearly with every Thought, Action, and Tool Result. If left unmanaged, the LLM will hit its token limit, slow down to a crawl, and hallucinate due to "lost in the middle" phenomena.

This architecture defines how the system **aggressively controls and prunes the context window**, how episodic history is stored via the Session Database, and how long-term semantic memory persists knowledge across sessions.

---

## 1. Core Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Episodic Memory** | Merged into Session Persistence (SQLite `messages` table) | Single source of truth. No duplicate storage. Time-Travel = SQL query. |
| **Token Counting** | Provider-specific tokenizers (tiktoken, Anthropic, Vertex) + character fallback | Accurate budget tracking. No surprise context overflows. |
| **Semantic Learning** | Auto-summarize after task SUCCESS + agent `remember` tool | Nothing gets lost. Agent can also proactively store important context. |
| **Sliding Window** | Token-budget-based (not fixed turn count) | Adapts to different model context sizes (8K → 200K) automatically. |

---

## 2. The Memory Model (Three Layers)

```
┌───────────────────────────────────────────────────────────────┐
│                    WORKING MEMORY                             │
│  What the LLM sees on each call. Token-budget managed.        │
│  ┌──────────────┐ ┌──────────┐ ┌──────────────────────────┐  │
│  │ System Prompt │ │ Summary  │ │  Recent Turns (N most    │  │
│  │ (20% budget)  │ │ Block    │ │  recent within budget)   │  │
│  └──────────────┘ └──────────┘ └──────────────────────────┘  │
│                          ↑ compaction                         │
├───────────────────────────────────────────────────────────────┤
│                  EPISODIC MEMORY                              │
│  = Session Database (04_session_persistence.md)               │
│  SQLite `messages` table: every message, tool result, thought │
│  Queryable. Crash-recoverable. Time-Travel Debugging.         │
├───────────────────────────────────────────────────────────────┤
│                  SEMANTIC MEMORY                              │
│  Mem0-powered vector DB. Persistent across sessions.          │
│  User preferences, workspace facts, learned patterns.         │
│  Retrieved during ROUTING to enrich system prompts.           │
└───────────────────────────────────────────────────────────────┘
```

### Layer A: Working Memory (The Active Context Window)

This is exactly what is sent to the LLM on each `handle_task()` iteration.

- **Format:** A list of `{"role": str, "content": str}` messages
- **Contents:** System prompt → (optional) summary block → recent turns
- **Constraint:** Must stay within the model's context window budget
- **Lifecycle:** Reset per task via `reset_working()`. Isolated per agent in ExecutionPlans.

### Layer B: Episodic Memory (= Session Database)

The complete, untruncated history of everything that happened in a session. This is **not a separate store** — it IS the Session Database from `04_session_persistence.md`.

- **Storage:** SQLite `messages` table in `.agent_cli/sessions.db`
- **Contents:** Every user prompt, agent response, tool output, thinking block
- **Purpose:**
  - **Time-Travel Debugging:** Query exactly what happened 50 steps ago
  - **Crash Recovery:** Session restoration loads messages back into Working Memory
  - **Compaction Source:** When Working Memory needs summarization, the raw history is available

```sql
-- Time-Travel: What did the agent do at step 30?
SELECT content FROM messages 
WHERE session_id = 'abc' AND sequence = 30;

-- What tools were used?
SELECT content FROM messages 
WHERE session_id = 'abc' AND role = 'tool';
```

### Layer C: Semantic Memory (Long-Term Knowledge via Mem0)

Persistent knowledge that survives across sessions. Powered by **Mem0**.

- **Storage:** Mem0-managed vector database (local by default)
- **Contents:**
  - User preferences: *"The user prefers strict typing"*
  - Workspace facts: *"Main entry point is `cli.py`"*, *"Uses pytest for testing"*
  - Learned patterns: *"The auth module uses cookie-based sessions"*
  - Past task summaries: *"Successfully refactored auth to JWT on Feb 27"*
- **Retrieval:** During the `ROUTING` phase, the Orchestrator queries Mem0 to enrich the initial system prompt with relevant context.

---

## 3. Token Budget Management

### Token Budget Allocation

The context window is divided into zones with configurable percentages:

```python
@dataclass
class TokenBudget:
    """Defines how the context window budget is allocated."""
    
    model_max_tokens: int               # Total context window (e.g., 200000 for Claude 3.5)
    
    # Budget allocation (percentages, must sum to 100)
    system_prompt_pct: float = 0.15     # 15% for system prompt
    summary_pct: float = 0.10           # 10% for compacted summary of older turns
    recent_turns_pct: float = 0.55      # 55% for recent conversation turns
    response_reserve_pct: float = 0.20  # 20% reserved for the LLM's response
    
    @property
    def system_prompt_budget(self) -> int:
        return int(self.model_max_tokens * self.system_prompt_pct)
    
    @property
    def summary_budget(self) -> int:
        return int(self.model_max_tokens * self.summary_pct)
    
    @property
    def recent_turns_budget(self) -> int:
        return int(self.model_max_tokens * self.recent_turns_pct)
    
    @property
    def response_reserve(self) -> int:
        return int(self.model_max_tokens * self.response_reserve_pct)
    
    @property
    def usable_context(self) -> int:
        """Total tokens available for input (everything except response reserve)."""
        return self.model_max_tokens - self.response_reserve


# ── Common Presets ──────────────────────────────────────────
TOKEN_BUDGETS = {
    "claude-3-5-sonnet": TokenBudget(model_max_tokens=200000),
    "claude-3-5-haiku":  TokenBudget(model_max_tokens=200000),
    "gpt-4o":            TokenBudget(model_max_tokens=128000),
    "gpt-4o-mini":       TokenBudget(model_max_tokens=128000),
    "gemini-2.0-flash":  TokenBudget(model_max_tokens=1048576),
    "llama-3-8b":        TokenBudget(model_max_tokens=8192,
                                     system_prompt_pct=0.20,
                                     summary_pct=0.05,
                                     recent_turns_pct=0.50,
                                     response_reserve_pct=0.25),
}
```

### Provider-Specific Token Counting

Each LLM provider has its own tokenizer. The `BaseTokenCounter` abstracts this:

```python
from abc import ABC, abstractmethod
from typing import List


class BaseTokenCounter(ABC):
    """
    Abstract interface for counting tokens in messages.
    Each LLM provider implements its own tokenizer.
    """
    
    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Count tokens in a single string."""
        pass
    
    @abstractmethod
    def count_messages(self, messages: List[dict]) -> int:
        """Count total tokens in a list of chat messages."""
        pass


class TiktokenCounter(BaseTokenCounter):
    """Token counter for OpenAI models using tiktoken."""
    
    def __init__(self, model: str = "gpt-4o"):
        import tiktoken
        self.encoder = tiktoken.encoding_for_model(model)
    
    def count_tokens(self, text: str) -> int:
        return len(self.encoder.encode(text))
    
    def count_messages(self, messages: List[dict]) -> int:
        total = 0
        for msg in messages:
            total += 4  # message overhead (role, content markers)
            total += self.count_tokens(msg.get("content", ""))
            total += self.count_tokens(msg.get("role", ""))
        total += 2  # reply priming
        return total


class AnthropicTokenCounter(BaseTokenCounter):
    """Token counter for Anthropic models using their tokenizer."""
    
    def __init__(self, model: str = "claude-3-5-sonnet-20241022"):
        from anthropic import Anthropic
        self.client = Anthropic()
        self.model = model
    
    def count_tokens(self, text: str) -> int:
        response = self.client.count_tokens(text)
        return response.tokens
    
    def count_messages(self, messages: List[dict]) -> int:
        # Anthropic's API can count message tokens directly
        return sum(self.count_tokens(m.get("content", "")) for m in messages)


class VertexTokenCounter(BaseTokenCounter):
    """Token counter for Google Vertex AI / Gemini models."""
    
    def __init__(self, model: str = "gemini-2.0-flash"):
        import vertexai
        from vertexai.generative_models import GenerativeModel
        self.model = GenerativeModel(model)
    
    def count_tokens(self, text: str) -> int:
        response = self.model.count_tokens(text)
        return response.total_tokens
    
    def count_messages(self, messages: List[dict]) -> int:
        combined = "\n".join(m.get("content", "") for m in messages)
        return self.count_tokens(combined)


class CharacterFallbackCounter(BaseTokenCounter):
    """
    Fallback for unknown models (e.g., local Ollama).
    Approximation: 1 token ≈ 4 characters.
    ~10-15% inaccurate but zero dependencies.
    """
    
    def count_tokens(self, text: str) -> int:
        return len(text) // 4
    
    def count_messages(self, messages: List[dict]) -> int:
        return sum(self.count_tokens(m.get("content", "")) for m in messages)


def get_token_counter(provider: str, model: str) -> BaseTokenCounter:
    """Factory: return the appropriate token counter for a provider/model."""
    if provider == "openai":
        return TiktokenCounter(model)
    elif provider == "anthropic":
        return AnthropicTokenCounter(model)
    elif provider == "vertex" or provider == "google":
        return VertexTokenCounter(model)
    else:
        return CharacterFallbackCounter()
```

---

## 4. The `BaseMemoryManager` Interface

```python
from abc import ABC, abstractmethod
from typing import List, Optional


class BaseMemoryManager(ABC):
    """
    Manages the three memory layers: Working, Episodic (Session DB), Semantic (Mem0).
    
    Each agent gets a MemoryManager instance. Working Memory is isolated per task
    (reset_working() called at the start of each handle_task()).
    """
    
    # ── Working Memory ────────────────────────────────────────
    
    @abstractmethod
    def reset_working(self) -> None:
        """
        Clear all working memory. Called at the start of each task
        to ensure isolated context per agent.
        """
        pass
    
    @abstractmethod
    def add_working_event(self, event: dict) -> None:
        """
        Add a message to working memory.
        event format: {"role": "user|assistant|system|tool", "content": "..."}
        
        Also persists to Episodic Memory (Session DB) if auto-save is enabled.
        """
        pass
    
    @abstractmethod
    def get_working_context(self) -> List[dict]:
        """
        Return the current working memory formatted for the LLM API.
        
        Applies token budget management:
        1. System prompt (always included, first position)
        2. Summary block (if older turns have been compacted)
        3. Most recent turns (as many as fit within budget)
        
        If total tokens exceed the budget, older turns are dropped
        from the middle (system prompt and most recent turns are preserved).
        """
        pass
    
    @abstractmethod
    async def summarize_and_compact(self) -> None:
        """
        Compress older working memory turns into a summary block.
        
        Triggered when:
        - Working memory exceeds 80% of the token budget
        - ContextLengthExceededError is caught in the agent loop
        
        Flow:
        1. Take all turns except the system prompt and last 3 turns
        2. Send to a fast/cheap model: "Summarize these interactions"
        3. Replace the old turns with a single summary message
        """
        pass
    
    @abstractmethod
    def get_token_count(self) -> int:
        """Return the current total token count of working memory."""
        pass
    
    @property
    @abstractmethod
    def is_near_capacity(self) -> bool:
        """True if working memory exceeds 80% of the token budget."""
        pass
    
    # ── Semantic Memory (Mem0) ────────────────────────────────
    
    @abstractmethod
    async def add_long_term_fact(self, fact: str, metadata: dict = None) -> None:
        """
        Store a learned fact in Mem0 for cross-session persistence.
        Called by the `remember` tool or auto-summarize on task success.
        """
        pass
    
    @abstractmethod
    async def get_relevant_context(self, query: str, limit: int = 5) -> str:
        """
        Retrieve relevant long-term facts from Mem0 via vector search.
        Called during ROUTING to enrich the system prompt.
        """
        pass
    
    @abstractmethod
    async def auto_summarize_task(self, task_description: str, result: str) -> None:
        """
        Automatically summarize a completed task and store in Mem0.
        Called by the Orchestrator after a task transitions to SUCCESS.
        """
        pass
```

---

## 5. Concrete Implementation: `ContextMemoryManager`

```python
import logging
from typing import List, Optional
from mem0 import Memory

logger = logging.getLogger(__name__)


class ContextMemoryManager(BaseMemoryManager):
    """
    Production implementation of the three-layer memory model.
    
    Working Memory: In-memory list with token budget management.
    Episodic Memory: Delegated to SessionManager (SQLite).
    Semantic Memory: Powered by Mem0 vector DB.
    """
    
    def __init__(
        self,
        token_counter: BaseTokenCounter,
        token_budget: TokenBudget,
        session_manager: "AbstractSessionManager",
        summarization_provider: "BaseLLMProvider",  # Fast/cheap model for summarization
        user_id: str = "default",
        workspace_id: str = ""
    ):
        self.token_counter = token_counter
        self.budget = token_budget
        self.session_manager = session_manager
        self.summarizer = summarization_provider
        
        # Working Memory state
        self._system_prompt: Optional[dict] = None
        self._summary_block: Optional[dict] = None
        self._turns: List[dict] = []
        
        # Semantic Memory (Mem0)
        self._mem0 = Memory()
        self._user_id = user_id
        self._workspace_id = workspace_id
    
    # ── Working Memory ────────────────────────────────────────
    
    def reset_working(self) -> None:
        """Clear working memory for a new task."""
        self._system_prompt = None
        self._summary_block = None
        self._turns = []
    
    def add_working_event(self, event: dict) -> None:
        """Add a message. System prompts are stored separately."""
        if event.get("role") == "system" and self._system_prompt is None:
            self._system_prompt = event
        else:
            self._turns.append(event)
    
    def get_working_context(self) -> List[dict]:
        """
        Build the context list within the token budget.
        
        Strategy:
        1. System prompt always included (first)
        2. Summary block included if it exists (second)
        3. Recent turns filled from newest to oldest within remaining budget
        """
        context = []
        used_tokens = 0
        
        # 1. System prompt (always)
        if self._system_prompt:
            context.append(self._system_prompt)
            used_tokens += self.token_counter.count_messages([self._system_prompt])
        
        # 2. Summary block (if compacted)
        if self._summary_block:
            context.append(self._summary_block)
            used_tokens += self.token_counter.count_messages([self._summary_block])
        
        # 3. Recent turns (fill within budget, newest first)
        remaining_budget = self.budget.recent_turns_budget
        fitting_turns = []
        
        for turn in reversed(self._turns):
            turn_tokens = self.token_counter.count_messages([turn])
            if used_tokens + turn_tokens <= self.budget.usable_context:
                fitting_turns.insert(0, turn)  # Maintain chronological order
                used_tokens += turn_tokens
            else:
                break  # Budget exhausted
        
        context.extend(fitting_turns)
        
        # Log if turns were dropped
        dropped = len(self._turns) - len(fitting_turns)
        if dropped > 0:
            logger.debug(f"Dropped {dropped} older turns to fit token budget "
                        f"({used_tokens}/{self.budget.usable_context} tokens used)")
        
        return context
    
    async def summarize_and_compact(self) -> None:
        """
        Compress older turns into a summary block.
        Keeps system prompt + last 3 turns intact.
        """
        if len(self._turns) <= 3:
            return  # Nothing to summarize
        
        # Split: older turns (to summarize) vs recent turns (to keep)
        older_turns = self._turns[:-3]
        recent_turns = self._turns[-3:]
        
        # Build summarization prompt
        history_text = "\n".join(
            f"[{t['role']}]: {t['content'][:500]}" for t in older_turns
        )
        
        summary_response = await self.summarizer.safe_generate(
            context=[{
                "role": "system",
                "content": (
                    "Summarize the following agent interaction history into a concise "
                    "3-paragraph context block. Focus on: what was accomplished, what "
                    "was learned, and what the current state of the task is. "
                    "Be specific about file names, function names, and decisions made."
                )
            }, {
                "role": "user",
                "content": history_text
            }]
        )
        
        self._summary_block = {
            "role": "system",
            "content": (
                f"[Context Summary — {len(older_turns)} earlier steps compacted]\n"
                f"{summary_response.text_content}"
            )
        }
        self._turns = recent_turns
        
        logger.info(f"Compacted {len(older_turns)} turns into summary block. "
                    f"Token usage: {self.get_token_count()}/{self.budget.usable_context}")
    
    def get_token_count(self) -> int:
        """Current total token count of working memory."""
        all_messages = []
        if self._system_prompt:
            all_messages.append(self._system_prompt)
        if self._summary_block:
            all_messages.append(self._summary_block)
        all_messages.extend(self._turns)
        return self.token_counter.count_messages(all_messages)
    
    @property
    def is_near_capacity(self) -> bool:
        """True if working memory exceeds 80% of usable context."""
        return self.get_token_count() > (self.budget.usable_context * 0.8)
    
    # ── Semantic Memory (Mem0) ────────────────────────────────
    
    async def add_long_term_fact(self, fact: str, metadata: dict = None) -> None:
        """Store a fact in Mem0 for cross-session persistence."""
        meta = {"workspace": self._workspace_id}
        if metadata:
            meta.update(metadata)
        self._mem0.add(
            fact,
            user_id=self._user_id,
            metadata=meta
        )
        logger.info(f"Stored long-term fact: {fact[:80]}...")
    
    async def get_relevant_context(self, query: str, limit: int = 5) -> str:
        """Retrieve relevant facts from Mem0 via vector search."""
        results = self._mem0.search(
            query,
            user_id=self._user_id,
            limit=limit
        )
        if not results:
            return ""
        
        facts = [r.get("text", r.get("memory", "")) for r in results]
        return "Relevant context from previous sessions:\n" + "\n".join(f"- {f}" for f in facts)
    
    async def auto_summarize_task(self, task_description: str, result: str) -> None:
        """
        Auto-called by Orchestrator after task SUCCESS.
        Summarizes the task and stores key findings in Mem0.
        """
        summary_response = await self.summarizer.safe_generate(
            context=[{
                "role": "system",
                "content": (
                    "Extract the most important facts learned from this completed task. "
                    "Focus on: architectural decisions, file locations, user preferences, "
                    "and patterns discovered. Output as a bullet list of facts."
                )
            }, {
                "role": "user",
                "content": f"Task: {task_description}\n\nResult: {result[:3000]}"
            }]
        )
        
        # Store each fact individually in Mem0
        facts = summary_response.text_content.strip().split("\n")
        for fact in facts:
            fact = fact.strip().lstrip("- •")
            if len(fact) > 10:  # Skip empty/trivial lines
                await self.add_long_term_fact(fact, metadata={"source": "auto_summarize"})
```

---

## 6. The `remember` Tool (Agent-Initiated Learning)

Agents can proactively store important findings during a task:

```python
class RememberArgs(BaseModel):
    fact: str = Field(description="An important fact to remember across sessions")


class RememberTool(BaseTool):
    name = "remember"
    description = (
        "Store an important fact in long-term memory for future sessions. "
        "Use this when you learn something significant about the codebase, "
        "user preferences, or project architecture."
    )
    is_safe = True
    category = ToolCategory.UTILITY
    
    def __init__(self, memory_manager: BaseMemoryManager):
        self.memory = memory_manager
    
    @property
    def args_schema(self) -> Type[BaseModel]:
        return RememberArgs
    
    async def execute(self, fact: str) -> str:
        await self.memory.add_long_term_fact(fact)
        return f"Stored in long-term memory: {fact}"
```

---

## 7. Integration Points

### A. Agent Reasoning Loop (`01_reasoning_loop.md`)

```python
# At the start of handle_task():
self.memory.reset_working()
self.memory.add_working_event({"role": "system", "content": system_prompt})

# Each iteration:
context = self.memory.get_working_context()  # Token-budget-managed
response = await self.provider.safe_generate(context=context)

# After tool result:
self.memory.add_working_event({"role": "tool", "content": result})

# On ContextLengthExceededError:
await self.memory.summarize_and_compact()
```

### B. Orchestrator Routing (`04_multi_agent_definitions.md`)

```python
# During ROUTING, enrich prompt with Mem0 context
semantic_context = await self.memory.get_relevant_context(user_request)
if semantic_context:
    routing_prompt += f"\n\n{semantic_context}"
```

### C. Orchestrator Task Completion

```python
# After task SUCCESS, auto-learn
await self.memory.auto_summarize_task(
    task_description=task.description,
    result=task.result
)
```

### D. Session Restoration (`04_session_persistence.md`)

```python
# When loading a saved session, messages → Working Memory
for msg in loaded_messages:
    self.memory.add_working_event({"role": msg.role, "content": msg.content})
```

---

## 8. Working Memory Message Format

Every message in Working Memory follows the OpenAI-style chat format:

```python
# System prompt
{"role": "system", "content": "You are an expert coder..."}

# User request
{"role": "user", "content": "Fix the login bug"}

# Agent thinking (internal, may be excluded from some providers)
{"role": "assistant", "content": "<thinking>Let me analyze...</thinking>"}

# Agent action (for providers that don't use native FC)
{"role": "assistant", "content": "<action><tool>read_file</tool><args>{\"path\": \"auth.py\"}</args></action>"}

# Tool result
{"role": "tool", "content": "[Tool: read_file] Result:\ndef login(user, pwd):..."}

# Agent final answer
{"role": "assistant", "content": "<final_answer>I fixed the bug by...</final_answer>"}

# Compacted summary (replaces older turns)
{"role": "system", "content": "[Context Summary — 15 earlier steps compacted]\nThe agent explored..."}

# Semantic context injection (from Mem0)
{"role": "system", "content": "Relevant context from previous sessions:\n- Main entry is cli.py\n- User prefers strict typing"}
```

---

## 9. Configuration

```python
class AgentSettings(BaseSettings):
    # ... existing fields ...
    
    # Memory settings
    context_budget_system_prompt_pct: float = Field(
        default=0.15, ge=0.05, le=0.50,
        description="Percentage of context window allocated to system prompt."
    )
    context_budget_summary_pct: float = Field(
        default=0.10, ge=0.0, le=0.30,
        description="Percentage allocated to compacted summary block."
    )
    context_budget_response_reserve_pct: float = Field(
        default=0.20, ge=0.10, le=0.40,
        description="Percentage reserved for LLM response generation."
    )
    context_compaction_threshold: float = Field(
        default=0.80, ge=0.50, le=0.95,
        description="Trigger compaction when working memory exceeds this % of budget."
    )
    semantic_memory_enabled: bool = Field(
        default=True,
        description="Enable Mem0 semantic memory for cross-session learning."
    )
    semantic_memory_auto_learn: bool = Field(
        default=True,
        description="Auto-summarize and learn after every successful task."
    )
```

---

## 10. Testing Strategy

```python
import pytest

def test_token_budget_allocation():
    budget = TokenBudget(model_max_tokens=100000)
    assert budget.system_prompt_budget == 15000
    assert budget.summary_budget == 10000
    assert budget.recent_turns_budget == 55000
    assert budget.response_reserve == 20000
    assert budget.usable_context == 80000

def test_working_memory_stays_within_budget():
    counter = CharacterFallbackCounter()
    budget = TokenBudget(model_max_tokens=1000)  # Small for testing
    manager = ContextMemoryManager(counter, budget, ...)
    
    manager.reset_working()
    manager.add_working_event({"role": "system", "content": "System prompt"})
    
    # Add many turns
    for i in range(100):
        manager.add_working_event({"role": "user", "content": f"Message {i} " * 50})
    
    context = manager.get_working_context()
    total_tokens = counter.count_messages(context)
    assert total_tokens <= budget.usable_context

@pytest.mark.asyncio
async def test_summarize_and_compact():
    manager = ContextMemoryManager(...)
    manager.reset_working()
    manager.add_working_event({"role": "system", "content": "System"})
    
    # Add 20 turns
    for i in range(20):
        manager.add_working_event({"role": "user", "content": f"Turn {i}"})
    
    assert len(manager._turns) == 20
    
    await manager.summarize_and_compact()
    
    # Should have summary + last 3 turns
    assert len(manager._turns) == 3
    assert manager._summary_block is not None
    assert "Context Summary" in manager._summary_block["content"]

def test_near_capacity_detection():
    counter = CharacterFallbackCounter()
    budget = TokenBudget(model_max_tokens=100)  # Tiny for testing
    manager = ContextMemoryManager(counter, budget, ...)
    
    manager.reset_working()
    manager.add_working_event({"role": "system", "content": "x" * 400})  # Exceeds 80%
    
    assert manager.is_near_capacity == True

@pytest.mark.asyncio
async def test_auto_summarize_after_task_success():
    manager = ContextMemoryManager(...)
    
    await manager.auto_summarize_task(
        task_description="Fix login bug",
        result="Fixed by updating the JWT validation in auth.py"
    )
    
    # Verify facts were stored in Mem0
    context = await manager.get_relevant_context("login bug")
    assert "auth.py" in context or "JWT" in context

@pytest.mark.asyncio
async def test_remember_tool_stores_fact():
    manager = ContextMemoryManager(...)
    tool = RememberTool(memory_manager=manager)
    
    result = await tool.execute(fact="The auth module uses bcrypt for password hashing")
    assert "Stored" in result
    
    # Verify it's retrievable
    context = await manager.get_relevant_context("password hashing")
    assert "bcrypt" in context

def test_tiktoken_counter_accuracy():
    counter = TiktokenCounter("gpt-4o")
    # "Hello, world!" is typically 4 tokens
    tokens = counter.count_tokens("Hello, world!")
    assert 3 <= tokens <= 5

def test_character_fallback_approximation():
    counter = CharacterFallbackCounter()
    # 400 chars ≈ 100 tokens
    tokens = counter.count_tokens("x" * 400)
    assert tokens == 100
```
