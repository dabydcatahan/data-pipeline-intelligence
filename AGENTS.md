# Agent Guidelines for Data Pipeline Intelligence

This document helps AI coding agents work productively in this codebase. Consult the [PROJECT_PLAN.md](PROJECT_PLAN.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for authoritative design decisions and feature status.

## Quick Start

**Python version:** 3.11+  
**Install:** `pip install -e .` (installs locally for development)  
**Run tests:** `pytest --cov=app --cov-report=term-missing` (175 tests, 91% coverage)  
**Run checks:** `scripts/check.sh` (ruff format/check, mypy, pytest)  
**Start UI:** `streamlit run app/ui/app.py` or `adf-intelligence`

## Architecture Overview

The system processes uploaded Azure Data Factory JSON through a deterministic pipeline:

```
Upload JSON → Parse (scrub secrets) → Normalize to models → Lineage engine
           → Documentation / Insights / Visualization → Streamlit UI
```

**Key rule:** Raw JSON access **only** in `app/parsers/` (decision DEC-004). All other layers work against typed Pydantic models.

### Layer Responsibilities

| Layer | Package | When to edit |
|-------|---------|-------------|
| **Core** | `app.core` | Config, logging, exception hierarchy |
| **Models** | `app.models` | Domain objects (Pipeline, DataFlow, Column, etc.) |
| **Parsers** | `app.parsers` | Ingesting ADF JSON, secret scrubbing, validation |
| **Lineage** | `app.lineage` | Column-level graph, impact scoring, traversal (deterministic) |
| **Insights** | `app.insights` | AI enhancement layer (data sanitization, deterministic templating) |
| **Documentation** | `app.documentation` | Markdown/HTML generation from models |
| **Services** | `app.services` | Orchestration (AnalysisSession, Inventory) |
| **UI** | `app.ui` | Streamlit pages (thin, untyped, read-only model access) |

### Entry Points

- **UI:** `app/ui/app.py` (Streamlit dashboard with 8 pages)
- **CLI:** `adf-intelligence` command (wraps UI launch)
- **API:** `app.services.AnalysisSession` (core orchestration)

## Code Patterns & Conventions

### Models (Always Pydantic v2.7+)

✅ **DO:**
```python
from app.models.common import ModelBase

class MyModel(ModelBase):
    """Immutable, typed domain object."""
    name: str
    items: tuple[str, ...] = Field(default_factory=tuple)  # Immutable collections
    count: int | None = None  # PEP 604 syntax
```

❌ **DON'T:**
- Use `list` or `dict` (must be `tuple`, `frozenset`, or readonly container)
- Use `Any` outside logging
- Omit type hints
- Forget `frozen=True` and `extra="forbid"` inheritance from `ModelBase`

### Parsing (Only in `app.parsers/`)

✅ **DO:**
```python
from app.parsers.base import ParseContext, scrub_secrets

def parse_something(raw_json: dict) -> Model:
    ctx = ParseContext()
    raw_json = scrub_secrets(raw_json, ctx)  # Always scrub first
    # ... use raw_json dict access freely here ...
    if problem:
        ctx.warn("Issue description")  # Non-fatal; surfaces in UI
    return Model(...)  # Return typed model, never raw dict
```

❌ **DON'T:**
- Access raw JSON from outside `app.parsers/`
- Skip secret scrubbing
- Let parse errors bubble up (use `ParseContext.warn()` instead)
- Return raw dicts from parsers

### Lineage (Deterministic, Never AI-Driven)

**Rule:** AI never determines lineage. The `ColumnLineageGraph` (NetworkX DiGraph) is the source of truth.

✅ **DO:**
```python
from app.lineage.graph import ColumnLineageGraph

graph = ColumnLineageGraph(name="my_flow")
graph.add_node("source::id", stream_name="source", column_name="id")
graph.add_node("sink::id", stream_name="sink", column_name="id")
graph.add_edge("source::id", "sink::id", relationship=RelationshipType.DIRECT)

ancestors = graph.upstream_nodes("sink::id")  # Lineage is deterministic
descendants = graph.downstream_nodes("source::id")
```

❌ **DON'T:**
- Use graph for anything other than structure (no AI-generated edges)
- Assume edge labels are definitive (inspect node metadata for details)

### Type Hints (Strict Mypy Required)

✅ **DO:**
```python
from app.core.config import get_settings

def analyze(artifact: ParsedArtifact, settings: AppSettings | None = None) -> AnalysisResult:
    settings = settings or get_settings()
    return AnalysisResult(...)

def process_items(items: Iterable[Item]) -> tuple[int, ...]:
    return tuple(len(i.name) for i in items)
```

❌ **DON'T:**
- Use `Any`
- Omit return types
- Use `list` return type instead of `Sequence` or `tuple`

### Error Handling

Exception hierarchy: `app/core/exceptions.py`

✅ **DO:**
```python
from app.core.exceptions import ParseError, UploadError

try:
    parse_json(...)
except json.JSONDecodeError as e:
    raise InvalidJsonError(str(e)) from e

if size > settings.max_upload_bytes:
    raise FileTooLargeError(size, settings.max_upload_bytes)
```

❌ **DON'T:**
- Use generic `Exception`
- Let raw parsing errors escape (wrap in domain exceptions)
- Swallow exceptions without logging

### Testing

**Test organization:**
- Unit tests in `tests/unit/` (one file per module, e.g., `test_pipeline_parser.py`)
- Integration tests in `tests/integration/` (marked `@pytest.mark.integration`)
- Fixtures (JSON samples) in `tests/fixtures/`

✅ **DO:**
```python
import pytest
from app.parsers.pipeline_parser import parse_pipeline
from tests.conftest import read_fixture_bytes

def test_parse_valid_pipeline(fixtures_dir):
    data = read_fixture_bytes(fixtures_dir / "pipeline_arm_form.json")
    result = parse_pipeline(data)
    assert result.name == "expected_name"
    assert len(result.activities) == 3
```

❌ **DON'T:**
- Read `.env` files in tests (use explicit `settings` fixture)
- Skip coverage for business logic
- Test implementation details instead of contracts

## Common Tasks

### Adding a new data model

1. Create class in `app/models/` inheriting from `ModelBase`
2. Use immutable collections (`tuple`, `frozenset`)
3. Add type hints for all fields
4. Write unit tests in `tests/unit/test_models.py` or new file
5. Update docstrings (used by AI insight generation)

### Parsing a new ADF construct

1. Add parsing logic in `app/parsers/` (raw dict access **only** here)
2. Always call `scrub_secrets()` on raw JSON
3. Return a typed model from `app.models/`
4. Collect non-fatal issues in `ParseContext`
5. Write unit tests with fixture JSON in `tests/fixtures/`

### Adding lineage rules

1. Edit `app/lineage/column_lineage.py` (add operator rules)
2. Add edges with proper `RelationshipType` (DIRECT, RENAME, DERIVED, JOIN_KEY, AGGREGATE, PASSTHROUGH, FILTER, UNION, CONDITIONAL_SPLIT)
3. Test with integration tests and fixture data
4. Document the rule in comments (cite PROJECT_PLAN section if applicable)

### Modifying AI insights

1. **Sanitization first:** Update `app/insights/sanitize.py` if new facts need exposure
2. **InsightProvider:** Implement in `app/insights/` (deterministic only; LLM providers
   have been removed — see PROJECT_PLAN.md for the decision)
3. Never pass raw JSON to AI; only use `InsightRequest` (structural facts)

### Adding a Streamlit page

1. Create `.py` file in `app/ui/pages/`
2. Import only from `app.services`, `app.core`, `app.models` (read-only)
3. Use `st.session_state.analysis_session` for data access
4. Use `st.error()` / `st.warning()` for error display
5. No type hints needed (UI layer deliberately thin/untyped)

## Configuration

Environment-driven, prefix `ADFPI_`:

```bash
ADFPI_MAX_UPLOAD_BYTES=5242880          # Default: 5 MiB
ADFPI_LOG_LEVEL=INFO                     # Default: WARNING
ADFPI_LOG_FORMAT=text                    # or 'json'
```

See `app/core/config.py` for all settings and defaults.

## Security & Compliance

### Secret Scrubbing (Automatic)

Keys matching `SECRET_KEY_HINTS` in `app.parsers.base` are redacted **before** any model is built:
- connectionString, password, accountKey, accessKey, sasUri, apiKey, credential, encryptedCredential, etc.

✅ Safe to assume: No secrets reach lineage, UI, or AI layers.

### Logging

- Structured logging via `app.core.logging`
- Only metadata and error types logged; never raw JSON or secrets
- Format: text (human-readable) or JSON (configurable)

### In-Memory Processing (No Persistence)

All uploads are processed in-memory; nothing is persisted to disk by design (DEC-005).
The one exception is opt-in and out of scope of DEC-005: the Error Analyzer's
"Flag this error" button, gated by `AppSettings.error_misses_path` (`None`/off by
default) — see DEC-012.

## Workflow & Quality Gates

### Before committing:

```bash
# Full quality gate (same as CI)
scripts/check.sh

# Breakdowns:
ruff check .                      # Lint
ruff format --check .              # Format
mypy                               # Type check (strict)
pytest --cov=app --cov-report=term-missing  # Tests + coverage (91% target)
```

### Common ruff rules enforced:

- `E, F` (errors, pyflakes)
- `I` (import sorting)
- `UP` (modern Python syntax)
- `B` (bugbear)
- `SIM` (simplification)
- `RUF` (ruff-specific)
- Line length: 100 characters
- Target: Python 3.11+

### Mypy overrides (known stubs missing):

- networkx, pyvis, graphviz, streamlit (stubs not available; overrides silently used)

## Feature Status

See [PROJECT_PLAN.md](PROJECT_PLAN.md#3-feature-catalogue) for detailed status.

**Complete (F001–F024):** Upload, parsing (Pipeline + Data Flow), column lineage, impact scoring, documentation, visualization, inventory, AI insights.

**New (F025):** Error Analysis — Parse ADF error messages and recommend lineage inspection checkpoints.

**Partial:** `pivot` (groupBy + approximated aggregate columns only), `unpivot`/`parse`/`surrogateKey` (passthrough approximation), higher-order expressions (`reduce`, `mapIndex`, …).

**Known unsupported:** Azure API, credentials, PowerBI, Spark lineage.

## Learning Resources

### Quick guides for specific tasks

| Guide | Best for |
|-------|----------|
| [LINEAGE_ENGINE_GUIDE.md](LINEAGE_ENGINE_GUIDE.md) | Understanding lineage graph, adding operator rules, determinism principle |
| [TESTING_GUIDE.md](TESTING_GUIDE.md) | Writing tests, achieving 91% coverage, test organization, fixtures |
| [PARSING_SECURITY_GUIDE.md](PARSING_SECURITY_GUIDE.md) | Adding new ADF constructs, secret scrubbing, input validation |
| [ERROR_ANALYZER_FEATURE.md](ERROR_ANALYZER_FEATURE.md) | Error analysis system, pattern detection, checkpoint recommendations |

### Authoritative documentation

| Resource | Purpose |
|----------|---------|
| [PROJECT_PLAN.md](PROJECT_PLAN.md) | Authoritative design, feature status, decision log (§1–16) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layer responsibilities, key rules, data flow |
| [app/core/exceptions.py](app/core/exceptions.py) | Exception hierarchy |
| [app/models/common.py](app/models/common.py) | `ModelBase` pattern |
| [app/parsers/base.py](app/parsers/base.py) | `ParseContext`, secret scrubbing |
| [app/lineage/graph.py](app/lineage/graph.py) | `ColumnLineageGraph` API |
| [app/services/analysis_service.py](app/services/analysis_service.py) | `AnalysisSession` orchestration |
| [tests/unit/](tests/unit/) | Test patterns for each module |

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Import errors in tests | Ensure `pip install -e .` (editable install) before running tests |
| Mypy fails on networkx | Expected; networkx has no stubs. Check `mypy.overrides` in `pyproject.toml` |
| Streamlit "module not found" | Ensure `PYTHONPATH` is set or run `streamlit run` from repo root |
| Tests pass but coverage < 91% | Likely UI layer (`app/ui/`) — deliberately excluded. Check `[tool.coverage]` |
| Secret not redacted | Verify key is in `SECRET_KEY_HINTS` in `app.parsers.base.scrub_secrets` |

## Questions?

Refer to the decision log in [PROJECT_PLAN.md](PROJECT_PLAN.md) for "why" behind each architectural choice. If guidance is unclear, default to:

1. **Immutability:** Prefer `tuple` over `list`, `frozendict` patterns
2. **Separation of concerns:** Raw JSON parsing stays in `app.parsers/`
3. **Type safety:** Always annotate; mypy in strict mode
4. **Testing:** 90%+ coverage, especially business logic
5. **Security:** Scrub secrets, log only metadata, persist nothing

---

Last updated: 2026-09-16  
Feature status: Complete (F001–F024); Release hardening phase
