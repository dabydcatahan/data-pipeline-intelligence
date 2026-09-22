# Test fixtures

All JSON here is **synthetic** and hand-written for testing. No real or
confidential Azure Data Factory definitions are committed to this repository
(DEC-005, Development Rule 3).

| File | Purpose |
| --- | --- |
| `pipeline_copy_direct.json` | Minimal single Copy activity, direct dataset in/out |
| `pipeline_multi_activity.json` | Copy → ExecuteDataFlow → Stored proc, `dependsOn` chain, params & vars |
| `pipeline_arm_form.json` | ARM-template resource form with `[concat(parameters('factoryName'), '/...')]` name |
| `pipeline_cyclic.json` | Two activities that depend on each other (cycle handling) |
| `pipeline_unknown_activity.json` | Contains an activity with an unrecognized `type` |
| `pipeline_copy_translator.json` | Copy activity with a `TabularTranslator` column map into `StageCustomer` (+ an ExecuteDataFlow ref); stitches to `dataflow_customer_full.json` |
| `dataflow_select.json` | Mapping Data Flow with a source, a select transformation and a sink |
| `dataflow_customer_full.json` | Data flow with source schema, `derive`, `select` rename, `join`, `filter` |
| `dataflow_aggregate_union.json` | Two sources → `union` → `aggregate` (`groupBy` + aggregations) → sink |
| `dataflow_unknown_op.json` | Data flow whose transformation uses an unrecognized DFS operator |
| `dataflow_split.json` | Conditional `split` into two branches feeding two sinks |
| `dataflow_flatten.json` | Flatten (`foldDown` / `unroll`) of a nested array with `mapColumn` |
| `unknown_artifact.json` | Valid JSON object that is neither a pipeline nor a data flow |
| `not_json.txt` | Not JSON at all (invalid-input handling) |
