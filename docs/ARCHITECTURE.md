# Architecture

The authoritative architecture, decision log, feature catalogue and task
breakdown live in [`../PROJECT_PLAN.md`](../PROJECT_PLAN.md). This file is a quick
orientation for new contributors.

## Layer responsibilities

| Layer | Package | Responsibility | May import |
| --- | --- | --- | --- |
| Core | `app.core` | Config, logging, exception hierarchy | stdlib, pydantic only |
| Models | `app.models` | Normalized, typed, immutable ADF concepts | `app.core` |
| Parsers | `app.parsers` | **Only** place raw ADF JSON dicts are read; produces models | `app.core`, `app.models` |
| Lineage | `app.lineage` | `ColumnLineageGraph` (`graph.py`); builders for data flows (`column_lineage.py`), pipelines (`pipeline_lineage.py`) and a stitched cross-artifact graph (`stitch.py`); traversal/`explain` (`traversal.py`); impact scoring (`impact.py`) | models, `parsers.dfs_script`, `parsers.expression_parser`, `parsers.translator` |
| Documentation | `app.documentation` | Deterministic Markdown from models + lineage (`generator.py`); dependency-free, XSS-safe Markdown→HTML (`export.py`) | models, lineage |
| Insights | `app.insights` | AI enhancement layer: `sanitize.py` → `InsightRequest` (structural facts only); `DeterministicInsightProvider` (default, offline), `LLMInsightProvider` (optional), `SafeInsightProvider` (fallback); `get_provider(settings)` | models, lineage, summaries (sanitized) |
| Visualization | `app.visualization` | `build_dot` (Graphviz DOT, no dep) + optional `to_pyvis_html` (`viz` extra) rendering of `ColumnLineageGraph` | `lineage`, `models` |
| Services | `app.services` | Orchestration (`analysis_service.py`) + cross-artifact aggregation (`inventory.py`); the only API the UI calls | everything above |
| UI | `app.ui` | Streamlit; thin, untyped, widgets → services. Pages: Dashboard, Upload, Pipeline Explorer, Data Flow Explorer, Column Lineage & Impact, Inventory, Documentation, AI Insights | `app.services`, `app.core`, `app.models` (read-only) |

## Key rules

1. No raw ADF `dict` indexing outside `app.parsers` (DEC-004).
2. Lineage is deterministic; an LLM never determines lineage (§12).
3. Uploads are in-memory only; nothing is persisted (DEC-005).
4. Secret-bearing keys are scrubbed in `app.parsers.base.scrub_secrets`
   before any model is built.
5. Non-fatal parse problems become `ParseContext` warnings, surfaced per
   artifact in the UI — one bad activity never aborts the whole analysis.

## Data flow (vertical slice 1)

```
bytes ──▶ json_loader.load_adf_json ──▶ detect_artifact_kind
      ──▶ PipelineParser.parse / DataFlowParser.parse ──▶ ParsedArtifact
      ──▶ AnalysisSession.add ──▶ PipelineSummary ──▶ Streamlit render
```
