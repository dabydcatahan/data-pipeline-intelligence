# Parsing & Security Guide

**Purpose:** Step-by-step reference for ingesting new ADF JSON constructs safely, scrubbing secrets, and validating inputs.

**Core principle:** Raw JSON access **only** in `app/parsers/`; never in lineage, UI, or AI layers.

---

## Security Architecture

### The parsing layer is the sole gateway

```
User Upload (JSON bytes)
    ↓
json_loader.load_adf_json()    ← JSON validation, kind detection
    ↓
scrub_secrets()                ← **All secrets stripped here**
    ↓
*_parser.parse()               ← Transform to typed models
    ↓
Pydantic models                ← Safe, typed, no raw JSON
    ↓
Lineage, UI, AI (no JSON access)
```

**Guarantee:** If a secret passes through `scrub_secrets()`, it is redacted before any model is built.

---

## Secret Scrubbing

### How it works

**File:** `app/parsers/base.py`

```python
SECRET_KEY_HINTS = frozenset({
    "connectionstring",
    "connectionString",
    "accountkey",
    "accesskey",
    "password",
    "secret",
    "sasuri",
    "sastoken",
    "token",
    "apikey",
    "credential",
    "serviceprincipalkey",
    "clientsecret",
    "encryptedcredential",
})

def scrub_secrets(value: object) -> object:
    """Recursively drop secret-bearing keys from parsed JSON."""
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        for key, item in value.items():
            if key.lower() in {hint.lower() for hint in SECRET_KEY_HINTS}:
                cleaned[key] = "***redacted***"
                continue
            cleaned[key] = scrub_secrets(item)
        return cleaned
    if isinstance(value, list):
        return [scrub_secrets(item) for item in value]
    return value
```

### Scrubbing rules

1. **Recursive:** Walks entire JSON tree (dicts and lists)
2. **Case-insensitive:** `connectionString`, `ConnectionString`, `connectionstring` all match
3. **Marker value:** Redacted keys become `"***redacted***"` (placeholder, never actual secret)
4. **No exceptions:** If a key hints at a secret, it's scrubbed, even if it doesn't contain one

### Example

**Input:**
```json
{
  "name": "CopyActivity",
  "typeProperties": {
    "source": {
      "type": "SqlServer",
      "connectionString": "Server=prod.database.windows.net;Password=SuperSecret123"
    }
  }
}
```

**After scrub_secrets():**
```json
{
  "name": "CopyActivity",
  "typeProperties": {
    "source": {
      "type": "SqlServer",
      "connectionString": "***redacted***"
    }
  }
}
```

### Adding a new secret key hint

If ADF adds a new secret-bearing property:

1. Edit `app/parsers/base.py`
2. Add to `SECRET_KEY_HINTS`:
   ```python
   SECRET_KEY_HINTS = frozenset({
       # ... existing hints ...
       "newSecretKeyName",
   })
   ```
3. Write a test:
   ```python
   def test_scrub_new_secret_key():
       data = {"newSecretKeyName": "secret_value", "name": "ok"}
       result = scrub_secrets(data)
       assert result["newSecretKeyName"] == "***redacted***"
       assert result["name"] == "ok"
   ```
4. No need to update every parser; `scrub_secrets()` is called uniformly

---

## Parsing Pattern: Step-by-Step

### Phase 1: Load & Validate (app/parsers/json_loader.py)

```python
def load_adf_json(uploaded_bytes: bytes) -> dict[str, object]:
    """
    1. Decode JSON safely
    2. Detect artifact kind (pipeline vs. data flow)
    3. Perform basic schema validation
    """
    try:
        data = json.loads(uploaded_bytes.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise InvalidJsonError(f"Malformed JSON: {e}") from e
    
    if not isinstance(data, dict):
        raise InvalidJsonError("Root must be a JSON object")
    
    # Basic schema validation
    kind = detect_artifact_kind(data)  # returns ArtifactKind enum
    
    return data  # Returns raw dict; not yet scrubbed


def detect_artifact_kind(data: dict[str, object]) -> ArtifactKind:
    """Classify JSON as Pipeline, DataFlow, or Unknown."""
    if "properties" in data and "activities" in data.get("properties", {}):
        return ArtifactKind.PIPELINE
    elif "sheets" in data or ("config" in data and "flows" in data):
        return ArtifactKind.DATA_FLOW
    else:
        return ArtifactKind.UNKNOWN
```

### Phase 2: Scrub & Parse (app/parsers/*_parser.py)

```python
def parse_pipeline(raw_json: dict[str, object], source_filename: str = "") -> PipelineParseResult:
    """
    1. Scrub secrets
    2. Normalize structure
    3. Collect non-fatal warnings
    4. Return typed model
    """
    ctx = ParseContext()
    
    # **STEP 1: SCRUB SECRETS** (before accessing any values)
    raw_json = scrub_secrets(raw_json)
    
    # STEP 2: Extract metadata
    name = _clean_name(raw_json.get("name"), ctx, fallback="Pipeline")
    
    # STEP 3: Parse activities
    activities_block = raw_json.get("properties", {}).get("activities", [])
    activities = []
    
    for activity_json in activities_block:
        try:
            activity = _parse_activity(activity_json, ctx)
            activities.append(activity)
        except ValueError as e:
            ctx.warn(f"Activity parsing error: {e}")
            # Continue; one bad activity doesn't abort the whole pipeline
    
    # STEP 4: Detect cycles
    activity_names = {a.name for a in activities}
    if _has_cycle(activities):
        ctx.warn("Pipeline has cyclic dependencies; execution order may be ambiguous.")
    
    # STEP 5: Return typed model (no raw JSON)
    return PipelineParseResult(
        pipeline=Pipeline(name=name, activities=tuple(activities), ...),
        warnings=ctx.warnings,
        source_filename=source_filename,
    )


def _parse_activity(activity_json: dict, ctx: ParseContext) -> Activity:
    """Parse one activity from raw JSON dict."""
    # Still in app/parsers/, so raw dict access is OK
    # But always use .get() with defaults to avoid KeyError
    
    name = activity_json.get("name", "UnnamedActivity")
    activity_type = activity_json.get("type", "Unknown")
    
    inputs = _parse_dataset_refs(activity_json.get("inputs"))
    outputs = _parse_dataset_refs(activity_json.get("outputs"))
    
    if not name or not activity_type:
        raise ValueError("Activity missing name or type")
    
    return Activity(
        name=name,
        type=activity_type,
        inputs=inputs,
        outputs=outputs,
    )
```

### Phase 3: Model Never Accesses Raw JSON

```python
# ❌ DO NOT DO THIS in app/lineage/ or app/services/
# data = raw_json["properties"]["activities"][0]  # Type: dict

# ✅ DO THIS
pipeline = parse_pipeline(raw_json)
for activity in pipeline.activities:
    print(activity.name, activity.type)  # Typed attributes only
```

---

## Adding Support for a New ADF Construct

### Scenario: Support for new "CustomTransform" operator in Data Flow

#### Step 1: Understand the construct

**Research:** What does `CustomTransform` do?
- Example JSON:
  ```json
  {
    "name": "CustomTransform",
    "type": "transformation",
    "operator": "customTransform",
    "inputStreams": ["input1", "input2"],
    "outputStream": "output1",
    "expression": "col1 + col2"
  }
  ```

#### Step 2: Add to model (app/models/dataflow.py)

```python
RECOGNIZED_OPERATORS = frozenset({
    # ... existing operators ...
    "customTransform",
})

# If the operator has new/different fields, add to parsing
```

#### Step 3: Add to parser (app/parsers/dataflow_parser.py)

```python
def parse_transformation(tf_json: dict, ctx: ParseContext, parent_flow: DataFlow) -> DataFlowTransformation:
    """Parse one transformation from Data Flow JSON."""
    operator = tf_json.get("operator", "unknown").lower()
    
    # ... existing operator handling ...
    
    elif operator == "customTransform":
        return _parse_custom_transform(tf_json, ctx)
    
    else:
        # Unknown operator: keep it but warn
        ctx.warn(f"Unknown operator: {operator}")
        return DataFlowTransformation(
            name=tf_json.get("name", "Unknown"),
            operator=operator,
            inputs=tuple(_parse_input_refs(tf_json.get("inputStreams", []))),
            output=tf_json.get("outputStream", "unknown"),
            raw_arguments=json.dumps(tf_json.get("properties", {})),
            warnings=(),
        )


def _parse_custom_transform(tf_json: dict, ctx: ParseContext) -> DataFlowTransformation:
    """Parse customTransform-specific fields."""
    name = tf_json.get("name", "CustomTransform")
    inputs = tuple(_parse_input_refs(tf_json.get("inputStreams", [])))
    output = tf_json.get("outputStream", "output1")
    expression = tf_json.get("expression", "")
    
    # Validate
    if not inputs or not output:
        ctx.warn(f"CustomTransform '{name}' has missing inputs or output")
    
    return DataFlowTransformation(
        name=name,
        operator="customTransform",
        inputs=inputs,
        output=output,
        raw_arguments=expression,
    )
```

#### Step 4: Add lineage rule (app/lineage/column_lineage.py)

```python
def build_data_flow_lineage(data_flow: DataFlow) -> DataFlowLineage:
    # ... initialization ...
    
    for tf in topologically_sorted(data_flow.transformations):
        # ... existing operators ...
        
        elif tf.operator == "customTransform":
            # Parse the expression to find input columns
            input_schema = get_input_schema(tf)  # Columns from input streams
            deps = analyze_expression(tf.raw_arguments, data_flow, ctx)
            
            # Emit edges for each dependency
            for in_col in input_schema:
                if in_col in deps:
                    for in_stream in tf.inputs:
                        graph.add_dependency(
                            src_stream=in_stream,
                            src_column=in_col,
                            dst_stream=tf.output,
                            dst_column=in_col,  # Or transformed name
                            relationship=RelationshipType.DERIVED,
                            expression=tf.raw_arguments,
                        )
            
            graph.register_stream(tf.output, "transformation")
```

#### Step 5: Write tests

**Fixture:** `tests/fixtures/dataflow_custom_transform.json`
```json
{
  "name": "CustomTransformFlow",
  "sheets": [{
    "name": "dataflow",
    "config": {
      "streams": [
        {"name": "source1", "type": "source", "config": {"output": "(col1 int, col2 string)"}},
        {"name": "customTx", "type": "transformation", "operator": "customTransform", "inputStreams": ["source1"], "outputStream": "sink1", "expression": "col1 + col2"},
        {"name": "sink1", "type": "sink"}
      ]
    }
  }]
}
```

**Test:** `tests/unit/test_dataflow_parser.py`
```python
def test_parse_custom_transform(read_fixture_bytes):
    """Parse dataflow with customTransform operator."""
    data = json.loads(read_fixture_bytes("dataflow_custom_transform.json"))
    df, _ = DataFlowParser().parse(data, source_filename="custom_transform.json")
    
    assert len(df.transformations) > 0
    custom_tf = next((t for t in df.transformations if t.operator == "customTransform"), None)
    assert custom_tf is not None
    assert custom_tf.raw_arguments == "col1 + col2"
```

**Lineage test:** `tests/unit/test_column_lineage.py`
```python
def test_custom_transform_lineage(lineage):
    """CustomTransform correctly emits DERIVED edges."""
    g = lineage("dataflow_custom_transform.json").graph
    
    assert g.node("source1::col1").kind is NodeKind.SOURCE_COLUMN
    assert g.node("customTx::col1").kind is NodeKind.OUTPUT_COLUMN
    assert _rel(g, "source1::col1", "customTx::col1") is RelationshipType.DERIVED
    assert _rel(g, "source1::col2", "customTx::col1") is RelationshipType.DERIVED
```

---

## Handling Parse Errors Gracefully

### Non-fatal errors (use ParseContext.warn)

```python
# Missing optional field
if not activity_json.get("name"):
    ctx.warn("Activity has no name; using default.")
    name = f"Activity_{uuid.uuid4()}"

# Unknown operator
if tf.operator not in RECOGNIZED_OPERATORS:
    ctx.warn(f"Unknown operator: {tf.operator}; lineage may be incomplete.")

# Malformed expression
try:
    deps = analyze_expression(tf.raw_arguments, data_flow, ctx)
except Exception as e:
    ctx.warn(f"Could not parse expression: {e}")
    deps = {}
```

### Fatal errors (raise exceptions)

```python
# Corrupted JSON
try:
    data = json.loads(uploaded_bytes)
except json.JSONDecodeError as e:
    raise InvalidJsonError(f"Malformed JSON: {e}") from e

# Completely unknown artifact kind
kind = detect_artifact_kind(data)
if kind == ArtifactKind.UNKNOWN:
    raise UnsupportedArtifactError(
        f"Could not determine artifact type from JSON; expected pipeline or data flow."
    )
```

### Non-fatal error collection

```python
class ParseContext:
    """Collects non-fatal warnings so processing continues."""
    
    def __init__(self):
        self._warnings: list[str] = []
    
    def warn(self, message: str) -> None:
        """Add a warning; never aborts parsing."""
        self._warnings.append(message)
        logger.warning("parse warning: %s", message)
    
    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(self._warnings)
```

**Result:** One bad activity doesn't prevent analysis of the rest of the pipeline.

---

## Input Validation Checklist

### For any new parser

- [ ] **Secret scrubbing first:** Call `scrub_secrets()` before accessing sensitive fields
- [ ] **Type checking:** Validate expected JSON structure (`isinstance()` checks)
- [ ] **Missing fields:** Use `.get()` with defaults; don't assume keys exist
- [ ] **Malformed data:** Use `ParseContext.warn()` for non-fatal issues
- [ ] **Fatal errors:** Raise domain exception (e.g., `InvalidJsonError`) for unrecoverable problems
- [ ] **Model return:** Always return a typed Pydantic model, never raw dict
- [ ] **Logging:** Only log metadata, never secrets or raw column values
- [ ] **Testing:** Add unit tests with both happy path and error fixtures

---

## Security Checklist

### Before shipping a new parser

- [ ] No raw JSON `dict` access outside `app/parsers/`
- [ ] `scrub_secrets()` called before any `.get()` access
- [ ] No hardcoded credentials or API keys in code
- [ ] All test fixtures have secrets redacted (or are fictional)
- [ ] Logging doesn't output secrets (check `get_logger().warning()` calls)
- [ ] No file I/O (uploads processed in-memory only)
- [ ] Models are `frozen=True` (Pydantic immutability)
- [ ] Types are strict (mypy `strict=true` passes)

---

## Expression Parsing (app/parsers/expression_parser.py)

### Purpose

Extract column references from ADF expressions so lineage can determine which input columns a derived column depends on.

### Example

```python
from app.parsers.expression_parser import analyze_expression

expr = "concat(first_name, ' ', last_name)"
data_flow: DataFlow = ...
ctx: ParseContext = ...

deps = analyze_expression(expr, data_flow, ctx)
# Returns: {"first_name": [...], "last_name": [...]}
# (column name -> list of source columns it references)
```

### Supported functions

- **String:** `concat`, `substring`, `toLower`, `toUpper`, `ltrim`, `rtrim`, `trim`
- **Numeric:** `round`, `ceil`, `floor`, `abs`, `sum`, `count`, `avg`, `min`, `max`
- **Conditional:** `iif`, `isNull`, `isNotNull`, `coalesce`
- **Type:** `toString`, `toInteger`, `toDecimal`, `toDate`, `toTimestamp`
- **Column ref:** `@col_name`, `stream@col_name`, `$param_name`, `byName(col_name)`

### If expression is unsupported

```python
# Conservative: return empty or unknown
if not can_parse_expression(expr):
    ctx.warn(f"Expression not fully supported: {expr}")
    return {}  # Empty deps; lineage will be conservative
```

---

## Testing Parsers

### Key patterns

```python
import pytest
from app.parsers.pipeline_parser import parse_pipeline
from app.core.exceptions import InvalidJsonError, ParseError


def test_parse_valid_pipeline(read_fixture_bytes):
    """Happy path: valid pipeline parses successfully."""
    data = json.loads(read_fixture_bytes("pipeline_arm_form.json"))
    result = parse_pipeline(data)
    
    assert result.pipeline is not None
    assert result.pipeline.name == "ExamplePipeline"
    assert len(result.warnings) == 0


def test_parse_malformed_json():
    """Malformed JSON raises fatal error."""
    with pytest.raises(InvalidJsonError):
        parse_pipeline(b"not json at all")


def test_parse_missing_required_field():
    """Missing critical field raises fatal error."""
    data = {"properties": {}}  # No activities
    
    with pytest.raises(ParseError):
        parse_pipeline(data)


def test_parse_unknown_activity_is_kept(read_fixture_bytes):
    """Unknown activity type is kept but warned."""
    data = json.loads(read_fixture_bytes("pipeline_unknown_activity.json"))
    result = parse_pipeline(data)
    
    assert len(result.pipeline.activities) > 0
    assert any(a.type == "UnknownType" for a in result.pipeline.activities)
    assert len(result.warnings) > 0


def test_parse_secrets_scrubbed(read_fixture_bytes):
    """Secrets are scrubbed before parsing."""
    data = {
        "name": "Pipeline",
        "properties": {
            "activities": [{
                "name": "Copy",
                "type": "Copy",
                "typeProperties": {
                    "source": {
                        "connectionString": "Server=prod;Password=Secret123"
                    }
                }
            }]
        }
    }
    
    result = parse_pipeline(data)
    
    # Verify secret is redacted
    # (Note: actual activity parsing extracts connection string)
    # This test verifies scrub_secrets() was called
    assert "Secret123" not in json.dumps(result)  # Never in output
```

---

## References

- **Secret scrubbing:** [app/parsers/base.py](app/parsers/base.py)
- **JSON loading:** [app/parsers/json_loader.py](app/parsers/json_loader.py)
- **Pipeline parser:** [app/parsers/pipeline_parser.py](app/parsers/pipeline_parser.py)
- **Data Flow parser:** [app/parsers/dataflow_parser.py](app/parsers/dataflow_parser.py)
- **Expression parser:** [app/parsers/expression_parser.py](app/parsers/expression_parser.py)
- **Exception hierarchy:** [app/core/exceptions.py](app/core/exceptions.py)
- **Exceptions taxonomy:**
  - `InvalidJsonError` – JSON decode failed
  - `ParseError` – Parsing logic failed (unrecoverable)
  - `UploadError` – File size, type, etc.
  - `UnsupportedArtifactError` – Unknown artifact kind

---

**Last updated:** 2026-09-16
