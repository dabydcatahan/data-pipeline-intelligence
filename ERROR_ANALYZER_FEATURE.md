# Error Analysis Feature (F025)

**Status:** ✅ Complete  
**Date:** 2026-09-16  
**Tests:** 10 unit tests, all passing

## Overview

The **Error Analyzer** is a new UI feature that helps users diagnose ADF errors by:
1. Parsing ADF error messages to extract entity names (columns, datasets, streams)
2. Classifying error types (data type mismatch, missing column, NULL constraint, join failure, etc.)
3. Recommending specific lineage inspection checkpoints to investigate

## Feature Capabilities

### Input Methods
- **Paste error text:** Users can paste ADF error messages, logs, or run output directly
- **Upload error file:** Support for `.txt`, `.log`, and `.json` error files

### Error Classification
The analyzer detects 16+ error patterns:
- Data type issues (mismatch, conversion, incompatible types)
- Missing columns
- NULL/empty value violations
- Row/record rejection
- Duplicate key violations
- Join/lookup mismatches
- Expression/transformation failures
- Division by zero
- Encoding/format errors
- Dataset/connection issues

### Smart Recommendations
For each error, the analyzer generates **lineage checkpoints** that suggest:
- Which columns/streams to inspect
- What type of transformation to review (derive, aggregate, join, filter, etc.)
- Full lineage paths to trace
- Severity level for each recommendation

### Context-Aware Analysis
- If artifacts are loaded in the session, the analyzer validates detected entities against the graph
- Provides **interactive lineage visualization** for affected columns
- Shows confidence scores for entity detection

## Implementation

### Files Added

| File | Purpose | Lines |
|------|---------|-------|
| `app/models/error_analysis.py` | Models for error analysis results | 42 |
| `app/insights/error_analyzer.py` | Error classification & recommendation logic | 250+ |
| `app/ui/app.py` | UI page for error analyzer | ~150 new |
| `tests/unit/test_error_analyzer.py` | Unit tests | 92 |

### Files Modified

| File | Change |
|------|--------|
| `app/services/analysis_service.py` | Added `analyze_error()` method |
| `app/ui/app.py` | Added `page_error_analyzer()`, navigation, dispatch |

### Integration Points

- **AnalysisService:** Provides `analyze_error()` method for orchestration
- **ColumnLineageGraph:** Optional context for entity validation
- **AnalysisSession:** Access to loaded artifacts for graph context
- **Streamlit UI:** New "Error Analyzer" page in main navigation

## Usage Example

1. **Upload ADF artifacts** (pipelines/data flows) for context
2. **Navigate to "Error Analyzer"** in the sidebar
3. **Paste an error message:**
   ```
   Error: Column 'customer_id' not found in stream 'CustomerSource'
   ```
4. **View results:**
   - ✅ Severity: ERROR
   - ✅ Error Type: missing_column
   - ✅ Detected Entities: customer_id
   - ✅ Recommendations:
     - Check if customer_id exists in source schema
     - Review select/derive transformations for renaming
     - Inspect full lineage path [with interactive graph]

## Error Pattern Detection

### Pattern Matching Algorithm

```python
ERROR_PATTERNS = {
    r"(type|datatype|data type).*mismatch": ("data_type_mismatch", ERROR),
    r"(column|field).*not found": ("missing_column", ERROR),
    r"(null|empty|blank).*not allowed": ("null_constraint_violation", ERROR),
    # ... 13 more patterns
}
```

- **Case-insensitive matching** using regex
- **First match wins** (patterns prioritized by order)
- **Fallback to "unknown_error"** if no pattern matches

### Entity Extraction

The analyzer looks for entity names in patterns like:
- `column 'name'`
- `dataset 'name'`
- `stream 'name'`
- Quoted identifiers anywhere in text

Extracted entities are validated against the loaded lineage graph for confidence scoring.

## Lineage Checkpoint Generation

### Data Type Errors
- ✅ Full lineage path (source → sink)
- ✅ Review transformations (derive, cast, select)

### Missing Column Errors
- ✅ Verify source columns
- ✅ Check select/derive for name changes

### NULL Constraint Errors
- ✅ Check filter conditions
- ✅ Review aggregate/join NULL handling

### Join Errors
- ✅ Inspect join conditions, key types, NULLs
- ✅ Check key column values

### Transformation/Expression Errors
- ✅ Review expression syntax
- ✅ Check input column types
- ✅ Verify column existence

## Testing

### Test Coverage

```python
✅ test_analyze_data_type_mismatch()
✅ test_analyze_missing_column()
✅ test_analyze_null_constraint()
✅ test_analyze_join_error()
✅ test_analyze_division_by_zero()
✅ test_lineage_checkpoints_generated()
✅ test_empty_error_text()
✅ test_unknown_error_type()
✅ test_entity_extraction_multiple()
✅ test_severity_levels()

10/10 passing
```

### Integration with Existing Tests

- All 174 existing unit tests still pass
- 1 skipped (pyvis, optional dependency)
- No regressions introduced

## UI Flow

```
Error Analyzer (new page)
├─ Input tabs
│  ├─ Paste error text (form)
│  └─ Upload error file (file uploader)
├─ Analysis Results
│  ├─ Severity badge
│  ├─ Error type summary
│  ├─ Detected entities table
│  ├─ Recommended checkpoints
│  │  ├─ Description
│  │  ├─ Rationale
│  │  └─ Affected entities
│  ├─ Column Lineage (real, not generic - only when a detected column
│  │  actually matches a node in an uploaded data flow/pipeline)
│  │  ├─ Origin(s) / sink columns reached
│  │  ├─ Plain-language narrative (ColumnJourney.narrative())
│  │  └─ Interactive lineage graph, focused on that column
│  ├─ Next steps advice
│  ├─ Unrecognized-pattern flag (opt-in, see below)
│  └─ Original error (expandable)
```

The "Column Lineage" section is driven by `ErrorAnalysis.suggested_lineage`
(`app/insights/error_analyzer.py::_generate_lineage_suggestions`), which the service
layer already computed by matching detected entities against the combined lineage
graph of everything currently uploaded (`AnalysisService.analyze_error`) - the UI
just needed to actually render it. This fixed a real gap: the previous UI only drew a
lineage graph inside a checkpoint of `checkpoint_type == "column"`, which
`missing_column` (the single most common error type) never produces, so real column
lineage was silently never shown for the most common case.

## Error Type to Checkpoint Mapping

| Error Type | Checkpoints Generated | Severity |
|---|---|---|
| data_type_mismatch | Full path, transformations | ERROR |
| type_conversion_error | Transformations, input columns | ERROR |
| missing_column | Source schema, select/derive | CRITICAL |
| null_constraint_violation | Filters, aggregates | ERROR |
| null_value_encountered | Upstream data quality | WARNING |
| duplicate_key_violation | Aggregate grouping, source | CRITICAL |
| join_mismatch | Join conditions, key types | CRITICAL |
| foreign_key_violation | Parent table, activity order | ERROR |
| invalid_expression | Transformation, input columns | ERROR |
| division_by_zero | Transformation expression | ERROR |
| throttling_error | Concurrency review | WARNING |
| resource_exhausted | Skew-prone transformations | ERROR |
| limit_exceeded | Activity input size vs. ADF limits | ERROR |
| schema_mismatch | Source/sink schema comparison | ERROR |
| transient_db_error | Retry guidance, lock contention | WARNING |
| debug_session_error | Restart debug session | WARNING |
| integration_runtime_error | IR status/version/connectivity | CRITICAL |
| component_not_found | Verify JSON reference (dataset/linked service/pipeline/trigger) | CRITICAL |
| unknown_error | Full lineage path | WARNING |

The last five (`schema_mismatch` through `component_not_found`) are deliberately
**not** lineage-graph checkpoints — their root cause is infrastructure, a broken
reference, or a transient database condition, not the data itself, so their
checkpoint says so explicitly rather than pointing at column lineage that isn't
the actual problem.

> Added in the real-error hardening pass (see PROJECT_PLAN.md session log): `column
> mapping cannot be found`, `invalid column name`, SQL Server truncation/NULL/FK wording,
> `DF-Executor-*` data-flow error text, throttling/OOM/limit patterns, and 4 new error
> types (`foreign_key_violation`, `throttling_error`, `resource_exhausted`,
> `limit_exceeded`) sourced from Microsoft's official troubleshooting docs and real
> Copy activity / SQL sink error text reported on Microsoft Q&A / Stack Overflow.

## Unrecognized-Error Capture (opt-in)

When `analyze_error()` returns `unknown_error`, the UI (`page_error_analyzer`) shows
a "🚩 Flag this error as unrecognized" button — but only if
`AppSettings.error_misses_path` is configured (env `ADFPI_ERROR_MISSES_PATH`); it's
`None`/off by default and the button simply doesn't render. Clicking it calls
`AnalysisService.flag_unrecognized_error()`, which redacts the text
(`app/insights/error_feedback.redact_text()` — best-effort regex redaction of
secret-shaped substrings, not a guarantee) and appends one JSON line (`{flagged_at,
error_text}`) to the configured file via `record_unrecognized_error()`. A maintainer
periodically reviews that file and adds patterns to `error_patterns.json` (see
"Adding a New Error Pattern" below). This is the app's one deliberate, opt-in
exception to "nothing is ever persisted" (see DEC-012 in `PROJECT_PLAN.md`) — chosen
over automatic logging specifically so nothing is written without the user's
explicit action.

## Future Enhancements

Potential improvements for future releases:

1. **Error history tracking** (show similar errors and how they were resolved)
2. **Automated fixes** (suggest code changes to resolve common errors)
3. **Integration with Azure Monitor** (fetch real runtime errors directly)
4. **Error severity scoring** (impact assessment on downstream systems)
5. **Miss-frequency dashboard** (rank the flagged-misses file by frequency so a
   maintainer knows which unrecognized patterns to prioritize)

Note: LLM-based classification was considered and explicitly rejected (see
PROJECT_PLAN.md) - the deterministic regex classifier is the only classification path.

## Notes for Developers

### Adding a New Error Pattern

The classification table is data, not code — see `app/insights/error_patterns.json`
(its own `_readme` field has the full schema/ordering rules).

1. Add an entry to `patterns` in `error_patterns.json` (order matters — more
   specific patterns must come before generic ones, since the first match wins):
   ```json
   { "group": "your_group", "regex": "your_pattern_here", "error_type": "error_type", "severity": "error" }
   ```
   Reusing an existing `error_type` (e.g. `missing_column`) gets you its existing
   checkpoints/summary/next-steps for free — no Python change needed. A brand new
   `error_type` still gets a safe generic checkpoint (see step 2) even before
   anyone writes tailored logic for it.
2. Add a `summaries` / `next_steps` entry for a new `error_type` (also in the
   JSON; `{entities}` is replaced with the detected entity names).
3. Optional: add tailored checkpoint generation logic for a new `error_type` in
   `_generate_checkpoints()` (`error_analyzer.py`) — this part stays Python since
   it's branching logic, not flat data.
4. Add a test case in `tests/unit/test_error_analyzer.py`.

### Debugging an Error Analysis

```python
# Interactive analysis
from app.insights.error_analyzer import analyze_error

error_text = "..."
result = analyze_error(error_text)

print(result.inferred_error_type)
print(result.detected_entities)
print(result.lineage_checkpoints)
```

## References

- **New models:** `app/models/error_analysis.py`
- **Analyzer logic:** `app/insights/error_analyzer.py`
- **Pattern data:** `app/insights/error_patterns.json`
- **Unrecognized-error capture:** `app/insights/error_feedback.py`
- **UI page:** `app/ui/app.py::page_error_analyzer()`
- **Tests:** `tests/unit/test_error_analyzer.py`, `tests/unit/test_error_feedback.py`
- **Integration:** `app/services/analysis_service.py::analyze_error() /
  flag_unrecognized_error()`

---

**Feature ID:** F025  
**Last Updated:** 2026-09-16  
**Test Status:** ✅ 10/10 passing  
**Integration Status:** ✅ All 174 existing tests pass
