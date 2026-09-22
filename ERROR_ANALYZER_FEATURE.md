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
│  │  ├─ Affected entities
│  │  └─ [Optional] Interactive lineage graph
│  ├─ Next steps advice
│  └─ Original error (expandable)
```

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
| invalid_expression | Transformation, input columns | ERROR |
| division_by_zero | Transformation expression | ERROR |
| unknown_error | Full lineage path | WARNING |

## Future Enhancements

Potential improvements for future releases:

1. **ML-based error classification** (LLM integration for better pattern matching)
2. **Error history tracking** (show similar errors and how they were resolved)
3. **Automated fixes** (suggest code changes to resolve common errors)
4. **Integration with Azure Monitor** (fetch real runtime errors directly)
5. **Error severity scoring** (impact assessment on downstream systems)
6. **Custom error patterns** (allow users to define domain-specific error patterns)

## Notes for Developers

### Adding a New Error Pattern

1. Add regex pattern to `_ERROR_PATTERNS` in `error_analyzer.py`:
   ```python
   _ERROR_PATTERNS = {
       r"your_pattern_here": ("error_type", ErrorSeverity.ERROR),
       # ...
   }
   ```

2. Add checkpoint generation logic in `_generate_checkpoints()`:
   ```python
   elif "error_type" in error_type:
       checkpoints.append(LineageCheckpoint(...))
   ```

3. Add test case in `tests/unit/test_error_analyzer.py`

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
- **UI page:** `app/ui/app.py::page_error_analyzer()`
- **Tests:** `tests/unit/test_error_analyzer.py`
- **Integration:** `app/services/analysis_service.py::analyze_error()`

---

**Feature ID:** F025  
**Last Updated:** 2026-09-16  
**Test Status:** ✅ 10/10 passing  
**Integration Status:** ✅ All 174 existing tests pass
