# Lineage Engine Guide

**Purpose:** Deep reference for understanding and extending the deterministic column-level lineage system.

**Key principle:** The `ColumnLineageGraph` is the **source of truth for lineage**. AI never determines edges; lineage is reconstructed from ADF JSON structure alone.

---

## Core Architecture

### ColumnLineageGraph (app/lineage/graph.py)

A typed wrapper around NetworkX `DiGraph`:

- **Nodes:** `"<stream>::<column>"` strings representing columns at a point in a flow
- **Edges:** Point **downstream** — `A → B` means "column B depends on column A"
- **Consequence:** `networkx.ancestors(B)` = upstream/origin; `networkx.descendants(A)` = downstream/impact

```python
from app.lineage.graph import ColumnLineageGraph, node_id

graph = ColumnLineageGraph(name="customer_flow")

# Register streams with metadata
graph.register_stream("CustomerSource", "source")
graph.register_stream("SelectColumns", "transformation", source_schema=["id", "name"])
graph.register_stream("CustomerSink", "sink")

# Add column nodes
src_id = graph.add_column("CustomerSource", "id", NodeKind.SOURCE_COLUMN)
sink_id = graph.add_column("CustomerSink", "id", NodeKind.SINK_COLUMN)

# Link with typed relationship
graph.add_dependency(
    src_stream="CustomerSource",
    src_column="id",
    dst_stream="CustomerSink",
    dst_column="id",
    relationship=RelationshipType.DIRECT,
)

# Query
print(graph.upstream_nodes("CustomerSink::id"))  # upstream sources
print(graph.downstream_nodes("CustomerSource::id"))  # downstream sinks
```

### Key Graph Properties

- **Immutable node metadata:** stream name, column name, node kind (SOURCE_COLUMN, SINK_COLUMN, etc.)
- **Rich edge metadata:** relationship type, transformation name, expression, passthrough flag
- **Stream metadata:** Stores per-stream facts like filter expressions, join conditions, group-by columns
- **No mutation after lineage is built:** Graph is read-only for queries/traversal; built once via lineage builders

### Node Kinds (NodeKind enum)

| Kind | Used for | Emitted by |
|------|----------|-----------|
| `SOURCE_DATASET` | A dataset entity (pipeline scoped) | Pipeline lineage builder |
| `SOURCE_COLUMN` | Column from a source stream | Data flow source operator |
| `TRANSFORMATION` | Intermediate transformation (logical step) | Pipeline activity |
| `OUTPUT_COLUMN` | Named output of a transformation | Data flow sink operator |
| `SINK_DATASET` | Sink dataset entity (pipeline scoped) | Pipeline lineage builder |
| `SINK_COLUMN` | Column flowing to a sink | Data flow sink operator |
| `ACTIVITY` | A pipeline activity node | Pipeline lineage builder |

---

## RelationshipType: The Lineage Taxonomy

**Critical:** When adding lineage edges, choose the most specific relationship type.

### Direct mappings

**DIRECT:** Column passes unchanged (same name, same meaning)
```python
# select: name -> name
# join: keeping a passthrough column
graph.add_dependency(..., relationship=RelationshipType.DIRECT)
```

**RENAME:** Column passes through but with a different name
```python
# select: customer_id -> cust_id
graph.add_dependency(..., relationship=RelationshipType.RENAME)
```

**PASSTHROUGH:** Column flows through unchanged but in a context where transformation occurs (join, filter, union)
```python
# join: city column from left input (not a join key, just along for the ride)
graph.add_dependency(..., relationship=RelationshipType.PASSTHROUGH)
```

### Transformation relationships

**DERIVED:** Column is computed from one or more input columns via an expression
```python
# derive: full_name = concat(first_name, ' ', last_name)
graph.add_dependency(
    src_stream="Source",
    src_column="first_name",
    dst_stream="Derive",
    dst_column="full_name",
    relationship=RelationshipType.DERIVED,
    expression="concat(first_name, ' ', last_name)",
)
```

**AGGREGATE:** Column is produced by an aggregation function
```python
# aggregate: total_revenue = sum(amount)
graph.add_dependency(
    src_stream="UnionedSales",
    src_column="amount",
    dst_stream="AggregateByRegion",
    dst_column="total_revenue",
    relationship=RelationshipType.AGGREGATE,
    transformation="sum()",
)
```

### Join/lookup operations

**JOIN_KEY:** Column is used as a join condition key
```python
# join: left.region_id == right.region_id
graph.add_dependency(..., relationship=RelationshipType.JOIN_KEY)
graph.add_dependency(..., relationship=RelationshipType.JOIN_KEY)  # both sides
```

**LOOKUP:** Column is used as a lookup key (asymmetric lookup, exists, etc.)
```python
# lookup: stream_left uses stream_right.lookup_key
graph.add_dependency(..., relationship=RelationshipType.LOOKUP)
```

### Filtering and splitting

**FILTER:** Column participates in a filter predicate
```python
# filter: where status == 'active'
graph.add_dependency(
    src_stream="Joined",
    src_column="status",
    dst_stream="FilterActive",
    dst_column="status",
    relationship=RelationshipType.FILTER,
)
```

**CONDITIONAL_SPLIT:** Column is used in a conditional split branch
```python
# split: if(amount > 1000, 'high', 'low')
graph.add_dependency(..., relationship=RelationshipType.CONDITIONAL_SPLIT)
```

**UNION:** Column is unioned from multiple input streams
```python
# union: amount from both OnlineSales and StoreSales
graph.add_dependency(
    src_stream="OnlineSales",
    src_column="amount",
    dst_stream="UnionedSales",
    dst_column="amount",
    relationship=RelationshipType.UNION,
)
graph.add_dependency(
    src_stream="StoreSales",
    src_column="amount",
    dst_stream="UnionedSales",
    dst_column="amount",
    relationship=RelationshipType.UNION,
)
```

**WINDOW:** Column is part of a window function
```python
# window: row_number() over (partition by region order by date)
graph.add_dependency(..., relationship=RelationshipType.WINDOW)
```

**UNKNOWN:** Fallback for indeterminate or complex expressions
```python
# Partial reduction or unknown function
graph.add_dependency(..., relationship=RelationshipType.UNKNOWN)
```

---

## Building Lineage for a Data Flow

### Entry Point: build_data_flow_lineage()

Location: `app/lineage/column_lineage.py`

```python
from app.lineage.column_lineage import build_data_flow_lineage
from app.models.dataflow import DataFlow

df: DataFlow = ...  # parsed data flow
lineage_result = build_data_flow_lineage(df)
# Returns DataFlowLineage with:
#   - graph: ColumnLineageGraph (ready to query)
#   - warnings: non-fatal issues encountered
```

### How it works: Topological Walk

1. **Initialize graph** with all streams (source, transformation, sink) and register stream metadata
2. **Add source columns** from each source's output schema (marked as SOURCE_COLUMN)
3. **Walk transformations in topological order** (dependencies ordered first)
4. **For each transformation**, apply operator-specific rules:
   - Determine output schema
   - Emit edges based on operator type
   - Record stream-specific metadata (join conditions, filter expressions, etc.)
5. **Add sink columns** from the final sinks (marked as SINK_COLUMN)
6. **Return graph + warnings**

### Per-Operator Rules (T009.3–T009.9)

#### source
- **Output schema:** Parsed from `output(...)` expression
- **Edges:** None (seed operator)
- **Nodes:** Each column in output → SOURCE_COLUMN node

#### select
- **Output schema:** Only columns in `mapColumn` targets
- **Edges:**
  - If `new_col = old_col` (identity) → **DIRECT**
  - If `new_col = old_col_2` (rename) → **RENAME**
  - If `new_col = expr(...)` (expression) → **DERIVED** (parse expression for dependencies)
- **Edge specificity:** RENAME > DIRECT (rename takes precedence if edge exists)
- **Stream metadata:** None

```python
# Example: select(@address as address_full, @id as customer_id, concat(@fname, ' ', @lname) as full_name)
# Emits:
#   source::address -> select::address_full (DIRECT or DIRECT depending on name match)
#   source::id -> select::customer_id (DIRECT)
#   source::fname -> select::full_name (DERIVED)
#   source::lname -> select::full_name (DERIVED)
```

#### derive
- **Output schema:** All input columns (passthrough) + derived targets
- **Edges:**
  - Input columns → output stream columns (same name) → **PASSTHROUGH** (`passthrough=True`)
  - Input columns → derived column → **DERIVED** (parsed from expression)
- **Expression parsing:** Uses `analyze_expression()` to extract referenced columns

#### filter
- **Output schema:** All input columns (passthrough)
- **Edges:** All input columns → output stream columns → **PASSTHROUGH**
- **Stream metadata:** `filter_columns` (tuple of column names used in the condition)

#### join / lookup / exists
- **Output schema:** All input columns from both sides (passthrough)
- **Edges:**
  - Participating columns (from condition) → **JOIN_KEY** or **LOOKUP**
  - Non-key columns → **PASSTHROUGH**
- **Stream metadata:** `join_type`, `join_condition`, `join_columns` (tuple of column names)
- **Condition parsing:** Raw condition stored; column names extracted (e.g., `left.id == right.region_id`)

#### aggregate
- **Output schema:** Group-by keys (output as-is) + aggregation targets
- **Edges:**
  - Group-by columns → output (same name) → **DIRECT**
  - Aggregate source columns → output → **AGGREGATE** (stores aggregation function name)
  - Non-key input columns → **dropped** (no edge)
- **Stream metadata:** `group_by_columns`, `aggregate_targets`

```python
# Example: aggregate(groupBy(region, sale_date), total_amount = sum(amount), count = count())
# Emits:
#   input::region -> agg::region (DIRECT)
#   input::sale_date -> agg::sale_date (DIRECT)
#   input::amount -> agg::total_amount (AGGREGATE)
#   input::amount -> agg::count (AGGREGATE)  # count() depends on amount for its input
```

#### union
- **Output schema:** Union of input schemas by column name
- **Edges:** Each input's column → output column (same name) → **UNION**
- **Multiple inputs:** Each input contributes a UNION edge to the same output column

#### split
- **Output schema:** All input columns (passthrough to base + all branches)
- **Edges:**
  - Base stream: all inputs → base output → **PASSTHROUGH**
  - Branch streams: all inputs → branch output → **PASSTHROUGH**
  - Condition columns → marked in stream metadata
- **Stream metadata:** `split_conditions` (tuple), `split_columns` (tuple of columns used)

#### flatten / window / pivot / other
- **Output schema:** Passthrough approximation (same columns as input)
- **Edges:** All inputs → outputs → **PASSTHROUGH** (conservative)
- **Warning:** Logged to indicate partial support

#### sink
- **Output schema:** Same as input stream
- **Edges:** Input columns → SINK_COLUMN nodes (marked as **DIRECT**)
- **Stream metadata:** `dataset` (dataset name, if available)

---

## Adding New Operator Rules

**When to do this:** ADF adds a new transformation operator that needs lineage support.

### Step 1: Understand the operator semantics

- What columns does it read?
- What columns does it output?
- How do output columns relate to inputs (1:1, many:1, 1:many)?
- Does it use column references in expressions or conditions?

### Step 2: Add rule to build_data_flow_lineage()

Location: `app/lineage/column_lineage.py`

```python
def build_data_flow_lineage(data_flow: DataFlow) -> DataFlowLineage:
    # ... initialization ...
    
    for tf in topologically_sorted_transformations:
        if tf.operator == "your_new_operator":
            # Determine output schema from tf.raw_arguments
            output_schema = parse_your_operator_schema(tf.raw_arguments)
            
            # For each output column, determine its dependencies
            for out_col in output_schema:
                # Parse expression or condition to find input columns
                inputs = extract_input_columns(tf.raw_arguments, out_col)
                
                # Emit appropriate edges
                for in_col in inputs:
                    for in_stream in get_input_streams(tf):
                        graph.add_dependency(
                            src_stream=in_stream,
                            src_column=in_col,
                            dst_stream=tf.output_stream_name,
                            dst_column=out_col,
                            relationship=RelationshipType.DERIVED,  # or appropriate type
                            expression=tf.raw_arguments,
                        )
            
            # Register output schema
            graph.register_stream(tf.output_stream_name, "transformation")
```

### Step 3: Write a test

Location: `tests/unit/test_column_lineage.py`

```python
def test_your_operator_lineage(lineage):
    """Test that new_operator correctly emits edges."""
    # Fixture that includes your operator in a data flow
    g = lineage("dataflow_with_new_operator.json").graph
    
    # Verify nodes exist
    assert g.node("InputStream::col1").kind is NodeKind.SOURCE_COLUMN
    assert g.node("YourOp::output_col").kind is NodeKind.OUTPUT_COLUMN
    
    # Verify edges
    assert _rel(g, "InputStream::col1", "YourOp::output_col") is RelationshipType.DERIVED
    
    # Verify metadata
    assert g.stream_meta["YourOp"]["some_metadata"] == "expected_value"
```

### Step 4: Add a fixture (if needed)

Create JSON in `tests/fixtures/dataflow_your_operator.json` with:
- A source with columns
- Your operator referencing those columns
- A sink consuming the output

---

## Querying the Lineage Graph

### Basic queries

```python
from app.lineage.graph import ColumnLineageGraph

g: ColumnLineageGraph = ...

# Get node by ID
node = g.node("CustomerSource::id")
print(node.kind, node.stream, node.column)

# Get edge metadata
edge = g.edge("CustomerSource::id", "Sink::customer_id")
print(edge.relationship, edge.expression, edge.passthrough)

# List all nodes
for node in g.iter_nodes():
    print(f"{node.stream}::{node.column} ({node.kind})")

# List all edges
for edge in g.edges():
    print(f"{edge.source} -> {edge.target} ({edge.relationship})")
```

### Traversal

```python
# Upstream (origin)
ancestors = g.upstream_nodes("Sink::customer_id")  # returns list of node IDs

# Downstream (impact)
descendants = g.downstream_nodes("CustomerSource::id")  # returns list of node IDs

# Search by partial name
matches = g.find("customer_id")  # returns list of node IDs
matches = g.find("Source.customer_id")  # scoped search
matches = g.find("Source::customer_id")  # alternate syntax
```

### Explain (narrative)

```python
from app.lineage.traversal import explain

explanation = explain(g, "Sink::customer_id")
# Returns ColumnJourney with:
#   - origin: first node in the journey
#   - steps: list of ColumnJourneyStep objects
#   - downstream_sinks: list of sink nodes that depend on this column
```

---

## Determinism Guarantee

**The lineage engine is fully deterministic:** Given the same ADF JSON input and the same code, the output graph is identical.

- No randomness in traversal (topological sort is deterministic)
- No LLM-generated edges (all edges come from operator rules)
- No heuristics or guessing (unknown operators default to conservative passthrough + warning)

**Implications:**
- Tests are reproducible; lineage can be compared across versions
- AI can analyze and summarize lineage but never determines it
- Results are auditable: every edge has a documented operator rule

---

## Common Patterns

### Handling partial support

For operators where we don't have full decomposition (e.g., complex join conditions):

```python
# Store raw condition for reference, but be conservative
graph.register_stream(tf.output_stream_name, "transformation", raw_condition=tf.raw_arguments)

# Passthrough all columns (safe approximation)
for col in input_schema:
    graph.add_dependency(
        src_stream=input_stream,
        src_column=col,
        dst_stream=tf.output_stream_name,
        dst_column=col,
        relationship=RelationshipType.PASSTHROUGH,
    )

# Log a warning
ctx.warn(f"{tf.operator} condition not fully decomposed; column-level lineage may be incomplete.")
```

### Handling expressions

Use `analyze_expression()` from `app/parsers/expression_parser.py`:

```python
from app.parsers.expression_parser import analyze_expression

expr = "concat(@first_name, ' ', @last_name)"
deps = analyze_expression(expr, data_flow, ctx)
# Returns dict: {"first_name": [...], "last_name": [...]} or similar
```

### Handling schema ambiguity

When output schema isn't explicit (inferred from operators):

```python
# Conservative: assume all input columns appear in output
output_schema = input_schema
# OR
# Explicit: parse the operator to determine which columns are kept
output_schema = parse_explicit_output(tf.raw_arguments)

# If uncertain, log a warning
ctx.warn(f"{tf.operator} output schema ambiguous; assumed passthrough.")
```

---

## Debugging Lineage Issues

### Visualize the graph

```python
from app.visualization.lineage import build_dot

dot_source = build_dot(g)
print(dot_source)  # View in Graphviz

# Or in Streamlit:
import streamlit as st
st.graphviz_chart(dot_source)
```

### Check stream metadata

```python
print(g.stream_meta["FilterActive"])
# {'filter_columns': ('status', 'region')}
```

### Inspect edge specificity

If edges seem wrong, check if a more specific relationship is being chosen:

```python
edge = g.edge("Source::col", "Sink::col")
print(edge.relationship)  # Should be most specific (RENAME > DIRECT, etc.)
```

### Trace with logging

Enable logging to see warnings and processing order:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

lineage_result = build_data_flow_lineage(df)
for warning in lineage_result.warnings:
    print(f"⚠️ {warning}")
```

---

## References

- **ColumnLineageGraph:** [app/lineage/graph.py](app/lineage/graph.py)
- **Lineage builder:** [app/lineage/column_lineage.py](app/lineage/column_lineage.py)
- **Traversal & explain:** [app/lineage/traversal.py](app/lineage/traversal.py)
- **Relationship types:** [app/models/lineage.py](app/models/lineage.py)
- **Tests:** [tests/unit/test_column_lineage.py](tests/unit/test_column_lineage.py)
- **Fixtures:** [tests/fixtures/](tests/fixtures/) (dataflow_*.json files)
- **Expression parsing:** [app/parsers/expression_parser.py](app/parsers/expression_parser.py)

---

**Last updated:** 2026-09-16
