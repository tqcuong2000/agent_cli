# Registry & Data-Driven Design — Comprehensive Guideline

---

## Table of Contents

1. [What is a Registry?](#1-what-is-a-registry)
2. [What is Data-Driven Design?](#2-what-is-data-driven-design)
3. [The Relationship Between Registry and Data-Driven](#3-the-relationship-between-registry-and-data-driven)
4. [Registration Strategies](#4-registration-strategies)
5. [The Pattern Family](#5-the-pattern-family)
6. [The Full Data-Driven Pipeline](#6-the-full-data-driven-pipeline)
7. [Building a Registry — Core Guidelines](#7-building-a-registry--core-guidelines)
8. [Testing](#8-testing)
9. [Evolution and Versioning](#9-evolution-and-versioning)
10. [The Complete Mental Model](#10-the-complete-mental-model)
11. [Anti-Patterns to Avoid](#11-anti-patterns-to-avoid)
12. [Reference Implementation](#12-reference-implementation)

---

## 1. What is a Registry?

A **registry** is a centralized store that maps keys (names/identifiers) to values (objects, factories, configurations, or metadata). It acts as a lookup table that decouples producers from consumers — neither needs to know about the other directly.

```
Registry
  ┌──────────────────────────────┐
  │  "pdf-parser"  → PdfParser   │
  │  "csv-parser"  → CsvParser   │
  │  "xml-parser"  → XmlParser   │
  └──────────────────────────────┘
       ↑ register()       ↓ resolve()
   (producers)         (consumers)
```

### Common Forms

| Type | Description | Example |
|---|---|---|
| **Service Registry** | Maps service names to network locations | Consul, Eureka |
| **Plugin Registry** | Maps plugin names to implementations | VS Code extensions |
| **Type Registry** | Maps string identifiers to classes | Serialization systems |
| **Handler Registry** | Maps event names to handler functions | Message brokers |
| **Schema Registry** | Maps message types to schemas | Kafka Schema Registry |
| **Config Registry** | OS-level key-value for configuration | Windows Registry |

---

## 2. What is Data-Driven Design?

A system is **data-driven** when behavior is determined by data, not hardcoded logic. Instead of writing `if/switch` branches for every case, behavior is externalized into a lookup.

### The Core Shift

```python
# ❌ Logic-driven — every new type requires a code change
def parse(file):
    if file.ext == "pdf":  return PdfParser().parse(file)
    if file.ext == "csv":  return CsvParser().parse(file)
    if file.ext == "xml":  return XmlParser().parse(file)

# ✅ Data-driven — new types require only a registration
def parse(file):
    return registry.resolve(file.ext).parse(file)
```

The `if/switch` block *is* the data, just written as code. Data-driven design externalizes it.

### The Spectrum

```
Hardcoded          Logic-Driven         Data-Driven         Fully Interpreted
────────────────────────────────────────────────────────────────────────────▶
  if/switch          Registry of          Registry built        Rules engine /
  everywhere         known types          from config/DB        scripting runtime
```

---

## 3. The Relationship Between Registry and Data-Driven

A registry is the **mechanism** that makes a system data-driven. They relate on three levels:

### Level 1 — Registry as the Engine

The registry holds the mapping that the data-driven system consults at runtime. Data drives which behavior is selected; the registry makes that selection possible without hardcoding.

```
Input data
    │
    ▼
  "csv"  ──→  registry.resolve("csv")  ──→  CsvParser
  "pdf"  ──→  registry.resolve("pdf")  ──→  PdfParser
```

### Level 2 — The Registry Entry Can Be Data Too

Instead of registering code, register data descriptors interpreted at runtime:

```yaml
# config.yaml — pure data, no code
parsers:
  - key: "csv"
    class: "myapp.parsers.CsvParser"
    options: { delimiter: ",", encoding: "utf-8" }
  - key: "tsv"
    class: "myapp.parsers.CsvParser"
    options: { delimiter: "\t", encoding: "utf-8" }
```

```python
for entry in config["parsers"]:
    cls = import_class(entry["class"])
    registry.register(entry["key"], lambda: cls(**entry["options"]))
```

Adding a `tsv` parser now requires **zero code changes**.

### Level 3 — Composing Multiple Registries

A mature data-driven architecture chains registries together:

```
Input Event
    │
    ▼
Event Registry     ──→  finds the right Handler
Handler            ──→  consults Schema Registry to validate payload
Handler            ──→  consults Formatter Registry to render output
Handler            ──→  consults Route Registry to decide destination
```

### The Key Insight

> **A registry is what closes the gap between "data says what to do" and "code knows how to do it."**
>
> Data-driven design is the *philosophy*. The registry is the *implementation pattern* that realizes it.

---

## 4. Registration Strategies

How things get *into* the registry is often the most important design decision.

| Strategy | How it works | Best for | Tradeoff |
|---|---|---|---|
| **Manual** | Code explicitly calls `register()` at startup | Small, explicit systems | Verbose, full control |
| **Convention** | Scans for classes matching a naming pattern | Internal frameworks | Magic, low boilerplate |
| **Decorator / Annotation** | `@register("csv")` on the class | Self-documenting APIs | Couples class to registry |
| **Config file** | YAML/JSON drives registration | Ops-configurable systems | No recompile needed |
| **Plugin discovery** | Scans installed packages (entry points) | Third-party extensibility | True openness |

### Choosing a Strategy

- If **you control all implementations** → Manual or Decorator
- If **operators configure behavior** → Config file
- If **third parties extend the system** → Plugin discovery
- If **convention is strong and consistent** → Convention-based scanning

---

## 5. The Pattern Family

A registry doesn't exist in isolation. It sits inside a family of related patterns:

```
Strategy Pattern        → defines interchangeable behaviors
       +
Factory Pattern         → creates instances without exposing constructors
       +
Registry                → maps keys to strategies or factories
       =
IoC Container / DI      → wires the whole graph automatically
```

### The Critical Boundary: Registry vs. Service Locator

| | Registry (healthy) | Service Locator (anti-pattern) |
|---|---|---|
| **Who holds it** | Composition root only | Injected into consumers |
| **Dependencies** | Explicit | Hidden |
| **Testability** | Easy to substitute | Hard to isolate |
| **Coupling** | Low | High |

**Rule:** The registry is used *at the boundary* to wire things. Consumers receive wired dependencies, never the registry itself.

---

## 6. The Full Data-Driven Pipeline

```
External Source          Bootstrap                  Runtime
───────────────         ───────────               ───────────
config / DB      →    populate registry    →    input data arrives
plugins          →    validate registry    →    key extracted from data
annotations      →    freeze registry      →    registry.resolve(key)
                                           →    behavior executes
                                           →    result returned
```

### The Three Phases

**Phase 1 — Populate**
The registry is filled from its source (code, config, plugins, DB). All registrations happen here.

**Phase 2 — Validate & Freeze**
Check internal consistency: no missing dependencies, no type mismatches, no orphaned keys. Then lock the registry against further writes.

```python
registry.validate()  # assert all referenced keys exist, types are correct
registry.freeze()    # prevent runtime mutations
```

**Phase 3 — Resolve**
At runtime, input data determines which key to look up. Resolution is a read-only operation.

---

## 7. Building a Registry — Core Guidelines

### 7.1 Define a Clear Key Strategy

Keys should be **stable, unique, and human-readable**.

```python
# Good — meaningful, namespaced
registry.register("parser.image/png", PngHandler)

# Bad — opaque, fragile
registry.register(3, PngHandler)
```

### 7.2 Store Factories, Not Instances (by default)

- **Instance (singleton):** simpler, but couples lifecycle to the registry
- **Factory (callable):** creates a new object on each resolve — safer for stateful things

```python
registry.register("mailer", lambda: SmtpMailer(config))  # factory — preferred
registry.register("logger", Logger())                     # instance — only if truly stateless
```

### 7.3 Fail Fast on Conflicts

Decide your collision policy upfront:

- **Error on duplicate** — safest, avoids silent overwrites (recommended default)
- **Last-write-wins** — useful for overriding defaults in tests
- **Namespacing** — prefix keys by module/domain (`"auth.mailer"` vs `"billing.mailer"`)

### 7.4 Separate Registration from Resolution

- Registration happens **once, at startup**
- Resolution happens **at runtime, on demand**
- Never let consumers register; never let producers resolve

### 7.5 Validate at Registration Time

Catch bad registrations early rather than failing at runtime.

```python
def register(self, key: str, factory):
    if not callable(factory):
        raise TypeError(f"Factory for '{key}' must be callable")
    if key in self._store:
        raise KeyError(f"Key '{key}' already registered")
    self._store[key] = factory
```

### 7.6 Support Discovery

A registry that only does point lookups is limited. Expose listing and filtering:

```python
registry.list_keys()               # all registered keys
registry.find_by_tag("parser")     # keys with a given tag
registry.all_of_type(BaseParser)   # keys whose factory produces a given type
```

### 7.7 Thread Safety

| Scenario | Recommendation |
|---|---|
| Registration only at startup | No locking needed after freeze |
| Runtime registration allowed | Read-write lock (`threading.RLock`) |
| High-read, low-write | `concurrent.futures` or immutable snapshots |

### 7.8 Implement a Freeze Phase

```
OPEN → FROZEN → runtime
  ↑ registration     ↑ resolution only
```

Freezing prevents accidental runtime mutations and enables whole-registry validation.

### 7.9 Make it Observable

- Log every registration with key, source, and timestamp
- Expose a debug dump of the full registry state
- Emit metrics on resolution frequency and failures

---

## 8. Testing

### 8.1 Never Use a Global Singleton Registry

```python
# ❌ Hard to test — global state bleeds between tests
registry = GlobalRegistry.instance()

# ✅ Easy to test — injected, substitutable
class MyService:
    def __init__(self, registry: Registry):
        self.registry = registry
```

### 8.2 Use a Test Registry with Mocks

```python
def test_csv_parsing():
    test_registry = Registry()
    test_registry.register("csv", lambda: MockCsvParser())

    service = ParserService(registry=test_registry)
    result = service.parse("data.csv")

    assert result == expected
```

### 8.3 Test the Registry Itself

```python
def test_registry_completeness():
    assert "csv" in production_registry.list_keys()
    assert "pdf" in production_registry.list_keys()
    assert callable(production_registry._store["csv"])
```

### 8.4 Test for Accidental Overwrites

```python
def test_no_duplicate_registration():
    registry = Registry()
    registry.register("csv", CsvParser)
    with pytest.raises(KeyError):
        registry.register("csv", AnotherCsvParser)
```

---

## 9. Evolution and Versioning

### 9.1 Key Stability

Once a key is public, changing it is a **breaking change**. Treat keys like public API contracts.

### 9.2 Deprecation

```python
def resolve(self, key: str):
    entry = self._store.get(key)
    if entry and entry.deprecated:
        warnings.warn(f"Key '{key}' is deprecated. Use '{entry.replacement}' instead.")
    return entry.factory()
```

### 9.3 Versioned Keys

```python
registry.register("parser.csv.v1", CsvParserV1)   # legacy
registry.register("parser.csv.v2", CsvParserV2)   # current
registry.register("parser.csv",    CsvParserV2)   # alias to current
```

### 9.4 Distributed Registries

When the registry lives outside the process (Consul, etcd, Zookeeper), additional concerns apply:

| Problem | Strategy |
|---|---|
| **Stale reads** | TTL-based local cache with background refresh |
| **Consensus** | Use leader election for registration writes |
| **Partial failure** | Circuit breaker on resolution; fallback to last known good |
| **Key sprawl** | Enforce namespacing and ownership conventions from day one |

---

## 10. The Complete Mental Model

```
┌─────────────────────────────────────────────────────────────────────┐
│                        EXTERNAL SOURCES                             │
│          config files │ database │ plugins │ annotations            │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ populate
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                           REGISTRY                                  │
│                                                                     │
│   [ populate ]  →  [ validate ]  →  [ freeze ]                     │
│                                                                     │
│   key  ──────────────────────────────────────→  factory/strategy   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ resolve(key)
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      COMPOSITION ROOT                               │
│                                                                     │
│   runtime input  →  extract key  →  resolve  →  wire dependency    │
│                                                                     │
│   consumer receives a wired dependency, never the registry itself   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 11. Anti-Patterns to Avoid

| Anti-Pattern | Problem | Fix |
|---|---|---|
| **Service Locator** | Consumers pull from registry directly, hiding dependencies | Inject resolved dependencies at the composition root |
| **Global singleton registry** | State bleeds between tests; tight coupling | Inject the registry as a dependency |
| **Resolving at registration time** | Eager instantiation defeats factory purpose | Always store callables; instantiate on resolve |
| **Opaque keys** | Hard to discover, document, or debug | Use namespaced, human-readable string keys |
| **No freeze phase** | Runtime mutations cause unpredictable behavior | Freeze after startup; make resolution read-only |
| **Registering everything** | Registry becomes a God Object | Register only things that genuinely vary by data |
| **Silent overwrites** | Later registrations silently replace earlier ones | Error on duplicate keys by default |

---

## 12. Reference Implementation

```python
import threading
import warnings
from typing import Any, Callable, Dict, List, Optional


class RegistryEntry:
    def __init__(self, factory: Callable, tags: List[str] = None,
                 deprecated: bool = False, replacement: str = None):
        self.factory = factory
        self.tags = tags or []
        self.deprecated = deprecated
        self.replacement = replacement


class Registry:
    def __init__(self, name: str = "default"):
        self.name = name
        self._store: Dict[str, RegistryEntry] = {}
        self._frozen = False
        self._lock = threading.RLock()

    # ── Registration ──────────────────────────────────────────────────

    def register(self, key: str, factory: Callable,
                 tags: List[str] = None, override: bool = False):
        with self._lock:
            if self._frozen:
                raise RuntimeError(f"Registry '{self.name}' is frozen")
            if not callable(factory):
                raise TypeError(f"Factory for '{key}' must be callable")
            if key in self._store and not override:
                raise KeyError(f"Key '{key}' already registered in '{self.name}'")
            self._store[key] = RegistryEntry(factory, tags)
            print(f"[registry:{self.name}] registered '{key}'")

    def deprecate(self, key: str, replacement: str):
        with self._lock:
            if key not in self._store:
                raise KeyError(f"Key '{key}' not found")
            self._store[key].deprecated = True
            self._store[key].replacement = replacement

    # ── Lifecycle ─────────────────────────────────────────────────────

    def validate(self):
        """Assert registry is internally consistent before freeze."""
        errors = []
        for key, entry in self._store.items():
            if entry.deprecated and entry.replacement:
                if entry.replacement not in self._store:
                    errors.append(
                        f"Key '{key}' points to missing replacement '{entry.replacement}'"
                    )
        if errors:
            raise RuntimeError(f"Registry validation failed:\n" + "\n".join(errors))

    def freeze(self):
        self.validate()
        self._frozen = True
        print(f"[registry:{self.name}] frozen with {len(self._store)} entries")

    # ── Resolution ────────────────────────────────────────────────────

    def resolve(self, key: str) -> Any:
        entry = self._store.get(key)
        if entry is None:
            raise KeyError(f"No entry for '{key}' in registry '{self.name}'")
        if entry.deprecated:
            msg = f"Key '{key}' is deprecated."
            if entry.replacement:
                msg += f" Use '{entry.replacement}' instead."
            warnings.warn(msg, DeprecationWarning, stacklevel=2)
        return entry.factory()

    def resolve_or_default(self, key: str, default: Any = None) -> Any:
        try:
            return self.resolve(key)
        except KeyError:
            return default

    # ── Discovery ─────────────────────────────────────────────────────

    def list_keys(self) -> List[str]:
        return list(self._store.keys())

    def find_by_tag(self, tag: str) -> List[str]:
        return [k for k, e in self._store.items() if tag in e.tags]

    def contains(self, key: str) -> bool:
        return key in self._store

    def summary(self) -> str:
        lines = [f"Registry '{self.name}' ({'frozen' if self._frozen else 'open'})"]
        for key, entry in self._store.items():
            tags = f"  [{', '.join(entry.tags)}]" if entry.tags else ""
            dep = "  [DEPRECATED]" if entry.deprecated else ""
            lines.append(f"  {key}{tags}{dep}")
        return "\n".join(lines)


# ── Usage Example ──────────────────────────────────────────────────────

class CsvParser:
    def parse(self, content): return f"parsed CSV: {content}"

class PdfParser:
    def parse(self, content): return f"parsed PDF: {content}"


if __name__ == "__main__":
    # 1. Populate
    registry = Registry(name="parsers")
    registry.register("csv", CsvParser, tags=["parser", "text"])
    registry.register("pdf", PdfParser, tags=["parser", "binary"])

    # 2. Validate & Freeze
    registry.freeze()

    # 3. Resolve at runtime driven by data
    file_ext = "csv"  # comes from user input / event / config
    parser = registry.resolve(file_ext)
    print(parser.parse("col1,col2\n1,2"))

    # 4. Discover
    print(registry.find_by_tag("parser"))  # ["csv", "pdf"]
    print(registry.summary())
```

---

*A registry is not about complexity — it's about giving data the power to change behavior without changing code. Apply it where that flexibility genuinely matters, and keep it out of places where straightforward dependency injection would be cleaner.*
