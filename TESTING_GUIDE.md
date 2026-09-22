# Testing & Coverage Guide

**Purpose:** Establish patterns for achieving and maintaining 91% code coverage across the data-pipeline-intelligence codebase.

**Target:** 91% coverage on `app/` (UI layer deliberately excluded)

---

## Coverage Baseline

```bash
# Current status (session 12, 2026-09-16)
175 passing tests (unit + integration)
Coverage: 91% on app/ (excluding app/ui/)
Target Python: 3.11+
```

### Coverage breakdown by package

| Package | Target | Notes |
|---------|--------|-------|
| `app.core` | 100% | Config, logging, exceptions — all critical |
| `app.models` | 95%+ | Pydantic models, mostly dataclass-like |
| `app.parsers` | 90%+ | Parsing logic, including edge cases (malformed JSON) |
| `app.lineage` | 90%+ | Graph operations, traversal, impact scoring |
| `app.insights` | 85%+ | Deterministic provider (priority), LLM wrapping (lower priority) |
| `app.documentation` | 85%+ | Markdown generation, HTML export |
| `app.services` | 90%+ | Orchestration, inventory aggregation |
| `app.ui` | **Excluded** | Deliberately thin/untyped; Streamlit AppTest smoke tests only |
| `app.visualization` | 80%+ | DOT generation (lower risk) |

---

## Test Organization

### Directory structure

```
tests/
├── conftest.py                 # Shared fixtures (settings, fixtures_dir, read_fixture_bytes)
├── fixtures/                   # JSON test data
│   ├── pipeline_*.json
│   ├── dataflow_*.json
│   └── README.md
├── unit/                       # One file per module (~20 files)
│   ├── test_config.py
│   ├── test_exceptions.py
│   ├── test_pipeline_parser.py
│   ├── test_column_lineage.py
│   ├── test_analysis_service.py
│   └── ...
└── integration/                # End-to-end flows (~3 files)
    ├── test_analysis_flow.py
    ├── test_lineage_flow.py
    └── test_ui_smoke.py
```

### Test file naming

- **Unit:** `test_<module_name>.py` (one per `app/<module>/` file)
- **Integration:** `test_<feature>_flow.py` (e.g., `test_lineage_flow.py`)

---

## Unit Testing Patterns

### Example: Parsing (test_pipeline_parser.py)

```python
import json
import pytest
from app.parsers.pipeline_parser import parse_pipeline
from app.core.exceptions import InvalidJsonError
from tests.conftest import read_fixture_bytes

def test_parse_valid_pipeline_arm_form(fixtures_dir):
    """Happy path: ARM-form pipeline JSON (with concat expressions)."""
    data = json.loads(read_fixture_bytes(fixtures_dir / "pipeline_arm_form.json"))
    result = parse_pipeline(data)
    
    assert result.name == "ExamplePipeline"
    assert len(result.activities) == 3
    assert result.activities[0].name == "CopyToStaging"


def test_parse_pipeline_missing_name(fixtures_dir):
    """Non-fatal: missing name falls back to default."""
    data = {"properties": {"activities": []}}
    result = parse_pipeline(data)
    
    assert result.name == "Pipeline"  # fallback
    assert len(result.warnings) > 0
    assert "no usable name" in result.warnings[0].lower()


def test_parse_pipeline_cyclic_dependency(fixtures_dir):
    """Error case: cyclic activity dependencies."""
    data = json.loads(read_fixture_bytes(fixtures_dir / "pipeline_cyclic.json"))
    result = parse_pipeline(data)
    
    assert len(result.warnings) > 0
    assert "cycle" in result.warnings[0].lower()


def test_parse_unknown_activity_type(fixtures_dir):
    """Non-fatal: unknown activity is kept and flagged."""
    data = json.loads(read_fixture_bytes(fixtures_dir / "pipeline_unknown_activity.json"))
    result = parse_pipeline(data)
    
    assert len(result.activities) > 0
    # Unknown activity is still in the pipeline
    assert any(a.type == "UnknownType" for a in result.activities)
    assert len(result.warnings) > 0
```

### Example: Lineage (test_column_lineage.py)

```python
import json
import pytest
from app.lineage import build_data_flow_lineage
from app.models.lineage import RelationshipType, NodeKind
from app.parsers.dataflow_parser import DataFlowParser
from tests.conftest import read_fixture_bytes


@pytest.fixture
def lineage(fixtures_dir):
    """Factory to build lineage from a fixture JSON file."""
    def _build(name):
        df, _ = DataFlowParser().parse(
            json.loads(read_fixture_bytes(fixtures_dir / name)),
            source_filename=name
        )
        return build_data_flow_lineage(df)
    return _build


def _rel(graph, src, dst):
    """Shorthand: get the relationship type of an edge."""
    return graph.edge(src, dst).relationship


def test_simple_select_direct_mapping(lineage):
    """Basic case: select with identity column mapping."""
    lin = lineage("dataflow_select.json")
    g = lin.graph
    
    assert g.node("CustomerSource::customer_id").kind is NodeKind.SOURCE_COLUMN
    assert g.node("SelectColumns::customer_id").kind is NodeKind.OUTPUT_COLUMN
    assert _rel(g, "CustomerSource::customer_id", "SelectColumns::customer_id") is (
        RelationshipType.DIRECT
    )


def test_derive_creates_derived_and_passthrough(lineage):
    """Derive operator: keeps inputs (PASSTHROUGH) + creates derived columns."""
    g = lineage("dataflow_customer_full.json").graph
    
    # Derived edges (from expression)
    assert _rel(g, "CustomerSource::first_name", "DeriveCustomerKey::full_name") is (
        RelationshipType.DERIVED
    )
    assert _rel(g, "CustomerSource::last_name", "DeriveCustomerKey::full_name") is (
        RelationshipType.DERIVED
    )
    
    # Passthrough edges (kept from input)
    edge = g.edge("CustomerSource::customer_id", "DeriveCustomerKey::customer_id")
    assert edge.passthrough is True
    assert _rel(g, "CustomerSource::customer_id", "DeriveCustomerKey::customer_id") is (
        RelationshipType.PASSTHROUGH
    )


def test_join_key_and_passthrough(lineage):
    """Join operator: join-key columns + passthrough of non-keys."""
    g = lineage("dataflow_customer_full.json").graph
    
    # Join key from left side
    assert _rel(g, "RenameColumns::region_code", "JoinRegion::region_code") is (
        RelationshipType.JOIN_KEY
    )
    
    # Join key from right side
    assert _rel(g, "RegionRef::region_code", "JoinRegion::region_code") is (
        RelationshipType.JOIN_KEY
    )
    
    # Non-key column (passthrough)
    assert _rel(g, "RegionRef::region_name", "JoinRegion::region_name") is (
        RelationshipType.PASSTHROUGH
    )


def test_aggregate_group_keys_and_measures(lineage):
    """Aggregate operator: group-by columns (DIRECT) + measures (AGGREGATE)."""
    g = lineage("dataflow_aggregate_union.json").graph
    
    # Group key
    assert _rel(g, "UnionSales::sale_date", "AggregateDaily::sale_date") is (
        RelationshipType.DIRECT
    )
    
    # Measure
    assert _rel(g, "UnionSales::amount", "AggregateDaily::total_amount") is (
        RelationshipType.AGGREGATE
    )
    
    # Raw input column not in output (dropped by aggregate)
    assert not g.find("AggregateDaily.amount")


def test_union_multiple_inputs_same_column(lineage):
    """Union operator: multiple inputs contributing to same output column."""
    g = lineage("dataflow_aggregate_union.json").graph
    
    # Both sources contribute to the union
    assert _rel(g, "OnlineSales::amount", "UnionSales::amount") is RelationshipType.UNION
    assert _rel(g, "StoreSales::amount", "UnionSales::amount") is RelationshipType.UNION


def test_upstream_traversal(lineage):
    """Verify ancestors (upstream) query."""
    g = lineage("dataflow_customer_full.json").graph
    
    upstream = g.upstream_nodes("CustomerSink::full_name")
    
    # Should include all columns that full_name depends on
    assert "CustomerSource::first_name" in upstream
    assert "CustomerSource::last_name" in upstream
    assert "CustomerSource::customer_id" in upstream  # via passthrough


def test_downstream_traversal(lineage):
    """Verify descendants (downstream) query."""
    g = lineage("dataflow_customer_full.json").graph
    
    downstream = g.downstream_nodes("CustomerSource::customer_id")
    
    # Should include all columns that depend on customer_id
    assert "CustomerSink::customer_id" in downstream
```

### Example: Models (test_models.py)

```python
import pytest
from app.models.pipeline import Pipeline, Activity
from app.models.common import ModelBase, DatasetReference


def test_model_immutable():
    """Pydantic models are frozen; no mutation after construction."""
    activity = Activity(
        name="CopyActivity",
        type="Copy",
        inputs=(),
        outputs=(),
    )
    
    with pytest.raises(Exception):  # FrozenInstanceError or ValueError
        activity.name = "NewName"


def test_model_forbids_unknown_fields():
    """Extra fields are rejected."""
    with pytest.raises(Exception):  # ValidationError
        Activity(
            name="CopyActivity",
            type="Copy",
            inputs=(),
            outputs=(),
            unknown_field="value",
        )


def test_dataset_reference_no_credentials():
    """DatasetReference never stores connection strings."""
    ref = DatasetReference(name="MyDataset", format="Parquet")
    
    assert ref.name == "MyDataset"
    assert ref.format == "Parquet"
    assert not hasattr(ref, "connectionString")
    assert not hasattr(ref, "password")


def test_pipeline_execution_order_topological():
    """Pipeline.execution_order uses topological sort."""
    # Build a pipeline with explicit dependencies
    activities = (
        Activity(name="A", type="Wait", inputs=(), outputs=()),
        Activity(
            name="B",
            type="Copy",
            inputs=(),
            outputs=(),
            depends_on=("A",),
        ),
        Activity(
            name="C",
            type="Copy",
            inputs=(),
            outputs=(),
            depends_on=("B",),
        ),
    )
    
    pipeline = Pipeline(name="test", activities=activities)
    order = pipeline.execution_order
    
    # B must come after A, C after B
    assert order.index("A") < order.index("B")
    assert order.index("B") < order.index("C")
```

### Example: Config (test_config.py)

```python
import pytest
from app.core.config import AppSettings, get_settings


def test_settings_defaults():
    """Settings have sensible defaults."""
    settings = AppSettings()
    
    assert settings.max_upload_bytes == 5_242_880  # 5 MiB
    assert settings.log_level == "WARNING"
    assert settings.enable_llm_insights is False


def test_settings_from_env(monkeypatch):
    """Settings read from environment (ADFPI_* prefix)."""
    monkeypatch.setenv("ADFPI_MAX_UPLOAD_BYTES", "1000000")
    monkeypatch.setenv("ADFPI_LOG_LEVEL", "DEBUG")
    
    settings = AppSettings()
    
    assert settings.max_upload_bytes == 1_000_000
    assert settings.log_level == "DEBUG"


def test_settings_cached():
    """get_settings() caches the result."""
    s1 = get_settings()
    s2 = get_settings()
    
    assert s1 is s2  # Same object (cached)


def test_settings_immutable():
    """Settings are frozen."""
    settings = AppSettings()
    
    with pytest.raises(Exception):  # FrozenInstanceError
        settings.max_upload_bytes = 1000
```

---

## Integration Testing Patterns

### Example: End-to-end analysis flow (test_analysis_flow.py)

```python
import json
import pytest
from app.services.analysis_service import AnalysisSession
from tests.conftest import read_fixture_bytes


@pytest.mark.integration
def test_upload_parse_lineage_full_flow(fixtures_dir):
    """End-to-end: upload → parse → lineage → summary."""
    session = AnalysisSession()
    
    # Upload a pipeline
    data = json.loads(read_fixture_bytes(fixtures_dir / "pipeline_arm_form.json"))
    session.add_pipeline(data, "pipeline_arm_form.json")
    
    # Upload a data flow
    df_data = json.loads(read_fixture_bytes(fixtures_dir / "dataflow_customer_full.json"))
    session.add_data_flow(df_data, "dataflow_customer_full.json")
    
    # Verify summaries
    assert len(session.pipelines) == 1
    assert len(session.data_flows) == 1
    
    pipeline_summary = session.pipelines[0]
    assert pipeline_summary.name == "ExamplePipeline"
    assert len(pipeline_summary.activities) > 0
    
    df_summary = session.data_flows[0]
    assert df_summary.name == "CustomerFlow"


@pytest.mark.integration
def test_inventory_aggregates_artifacts(fixtures_dir):
    """Inventory correctly aggregates sources, sinks, transformations."""
    session = AnalysisSession()
    
    # Add multiple artifacts
    session.add_data_flow(
        json.loads(read_fixture_bytes(fixtures_dir / "dataflow_customer_full.json")),
        "customer.json"
    )
    session.add_data_flow(
        json.loads(read_fixture_bytes(fixtures_dir / "dataflow_aggregate_union.json")),
        "sales.json"
    )
    
    inventory = session.inventory
    
    # Verify aggregation
    assert len(inventory.sources) > 0
    assert len(inventory.sinks) > 0
    assert len(inventory.datasets) > 0
    assert len(inventory.transformations) > 0


@pytest.mark.integration
def test_cross_artifact_search(fixtures_dir):
    """Search finds columns across all uploaded artifacts."""
    session = AnalysisSession()
    
    session.add_data_flow(
        json.loads(read_fixture_bytes(fixtures_dir / "dataflow_customer_full.json")),
        "customer.json"
    )
    session.add_data_flow(
        json.loads(read_fixture_bytes(fixtures_dir / "dataflow_aggregate_union.json")),
        "sales.json"
    )
    
    # Search all artifacts
    matches = session.search_columns("customer_id")
    assert len(matches) > 0
```

### Example: Streamlit smoke test (test_ui_smoke.py)

```python
import json
import pytest
from streamlit.testing.v1 import AppTest
from tests.conftest import read_fixture_bytes


@pytest.mark.integration
def test_ui_upload_and_display(fixtures_dir):
    """Smoke test: UI loads and responds to uploads."""
    # Create an AppTest instance (Streamlit's testing framework)
    at = AppTest.from_file("app/ui/app.py")
    at.run()
    
    # Verify initial state
    assert "Upload your ADF JSON" in at.text


@pytest.mark.integration
def test_ui_column_lineage_page(fixtures_dir):
    """Smoke test: Column Lineage page renders with fixtures."""
    # This would require fixtures loaded into session state
    # Simplified example:
    at = AppTest.from_file("app/ui/app.py")
    at.run()
    
    # Navigate to Column Lineage page (implementation varies)
    # Verify page content
    assert len(at.text) > 0
```

---

## Fixture Management

### Fixture files (tests/fixtures/)

Each fixture is a **real, minimal ADF JSON** that demonstrates one feature or edge case:

| File | Purpose | Size |
|------|---------|------|
| `pipeline_arm_form.json` | Pipeline with ARM template expressions | ~2 KB |
| `pipeline_copy_direct.json` | Simple copy activity | ~1 KB |
| `pipeline_copy_translator.json` | Copy with TabularTranslator mapping | ~3 KB |
| `pipeline_cyclic.json` | Cyclic dependency (for error testing) | ~1 KB |
| `pipeline_multi_activity.json` | Multiple activities with dependencies | ~2 KB |
| `pipeline_unknown_activity.json` | Unknown activity type (non-fatal) | ~1 KB |
| `dataflow_select.json` | Simple select transformation | ~2 KB |
| `dataflow_customer_full.json` | Derive + select + join + filter | ~3 KB |
| `dataflow_aggregate_union.json` | Union + aggregate | ~3 KB |
| `dataflow_flatten.json` | Flatten operator | ~2 KB |
| `dataflow_split.json` | Conditional split operator | ~2 KB |
| `dataflow_unknown_op.json` | Unknown operator (non-fatal) | ~1 KB |

### Adding a fixture

1. **Export from ADF** (or create minimal example)
2. **Redact secrets** (connection strings, passwords, API keys)
3. **Minify JSON** (remove unnecessary whitespace; keep it < 5 KB)
4. **Name descriptively:** `<artifact_type>_<feature>.json`
5. **Add to fixture discovery** (automatically detected by pytest)
6. **Update [tests/fixtures/README.md](tests/fixtures/README.md)** with purpose

Example fixture (minimal pipeline):

```json
{
  "name": "ExamplePipeline",
  "properties": {
    "activities": [
      {
        "name": "CopyToStaging",
        "type": "Copy",
        "inputs": [{"referenceName": "SourceDataset", "type": "DatasetReference"}],
        "outputs": [{"referenceName": "StagingDataset", "type": "DatasetReference"}],
        "typeProperties": {
          "source": {"type": "***"},
          "sink": {"type": "***"}
        }
      }
    ]
  }
}
```

### Using fixtures in tests

```python
from pathlib import Path
import json

@pytest.fixture
def fixtures_dir():
    return Path(__file__).parent / "fixtures"

def test_something(fixtures_dir):
    data = json.loads((fixtures_dir / "pipeline_arm_form.json").read_bytes())
    result = parse_pipeline(data)
    assert result.name == "expected"

# OR, using the helper from conftest.py:

def test_something_else(read_fixture_bytes):
    data = json.loads(read_fixture_bytes("pipeline_arm_form.json"))
    result = parse_pipeline(data)
    assert result.name == "expected"
```

---

## Running Tests

### All tests with coverage

```bash
pytest --cov=app --cov-report=term-missing
```

### Specific test file

```bash
pytest tests/unit/test_column_lineage.py -v
```

### Specific test function

```bash
pytest tests/unit/test_column_lineage.py::test_derive_creates_derived_and_passthrough -v
```

### Integration tests only

```bash
pytest -m integration
```

### Unit tests only

```bash
pytest -m "not integration"
```

### With output

```bash
pytest -v -s  # -v: verbose, -s: print stdout/stderr
```

### Generate HTML coverage report

```bash
pytest --cov=app --cov-report=html
open htmlcov/index.html
```

---

## Coverage Maintenance

### Check coverage for a package

```bash
pytest --cov=app.lineage --cov-report=term-missing tests/unit/test_column_lineage.py
```

### Identify uncovered lines

```bash
pytest --cov=app --cov-report=term-missing --cov-branch
```

The `--cov-branch` flag shows branch coverage (if/else paths).

### Update pyproject.toml coverage targets

```toml
[tool.coverage.report]
precision = 2
show_missing = True
skip_covered = False
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
]

[tool.coverage.run]
branch = True
source = ["app"]
omit = ["app/ui/*"]  # Exclude UI layer
```

---

## Best Practices

### Do

✅ **Test contracts, not implementation**
```python
# Good: test what the function returns
assert parse_pipeline(data).name == "expected"

# Bad: test internal details
assert pipeline._activities is not None
```

✅ **Use fixtures for shared data**
```python
@pytest.fixture
def settings():
    return AppSettings(max_upload_bytes=1_000_000, ...)

def test_upload_limit(settings):
    # Use settings directly
    assert uploaded_file_size < settings.max_upload_bytes
```

✅ **Test edge cases and errors**
```python
def test_parse_empty_json():
    """Ensure non-fatal errors are caught."""
    result = parse_pipeline({})
    assert len(result.warnings) > 0

def test_parse_malformed_json():
    """Ensure fatal errors raise the right exception."""
    with pytest.raises(InvalidJsonError):
        json.loads("not valid json")
```

✅ **Group related tests**
```python
class TestPipelineParser:
    def test_valid_pipeline(self): ...
    def test_missing_name(self): ...
    def test_cyclic_dependency(self): ...
```

### Don't

❌ **Test implementation details**
```python
# Bad: testing internal state
assert len(graph._g.nodes) > 0

# Good: test the public API
assert len(graph.node_ids()) > 0
```

❌ **Skip coverage for "hard to test" code**
- If it's hard to test, it's probably poorly designed
- Refactor to make it testable

❌ **Write integration tests that should be unit tests**
- Unit tests are faster; use them first
- Integration tests verify the seams between components

❌ **Depend on test execution order**
- Each test must be independent
- Avoid shared mutable state

---

## Coverage Targets per Module

| Module | Coverage | Why |
|--------|----------|-----|
| `app.core` | 100% | Config/logging/exceptions are critical |
| `app.models` | 95%+ | Few branches; high confidence needed |
| `app.parsers` | 90%+ | Error cases should be tested |
| `app.lineage` | 90%+ | Graph operations are the heart of the system |
| `app.insights` | 85%+ | Deterministic provider is priority; LLM is optional |
| `app.documentation` | 85%+ | Generation logic is tested; markup formatting is lower risk |
| `app.services` | 90%+ | Orchestration logic is important |
| `app.visualization` | 80%+ | DOT output is mostly string formatting; visual validation is UX-level |

---

## Continuous Improvement

### Before committing

```bash
# Full quality gate
scripts/check.sh

# Specific: test + coverage
pytest --cov=app --cov-report=term-missing
ruff check .
mypy
```

### After adding a feature

1. Write unit tests for the feature
2. Add integration test if it crosses module boundaries
3. Verify coverage: `pytest --cov=app --cov-report=term-missing`
4. If coverage < 91%, add tests until it reaches target

### Refactoring

- Run tests before refactoring: `pytest`
- Refactor
- Run tests after: `pytest`
- Verify coverage unchanged or improved

---

## References

- **Pytest docs:** https://docs.pytest.org/
- **Pydantic testing:** https://docs.pydantic.dev/latest/concepts/models/#validating-without-instantiation
- **Coverage.py:** https://coverage.readthedocs.io/
- **Streamlit AppTest:** https://docs.streamlit.io/develop/api-reference/app-testing
- **Test fixtures:** [tests/fixtures/README.md](tests/fixtures/README.md)

---

**Last updated:** 2026-09-16
