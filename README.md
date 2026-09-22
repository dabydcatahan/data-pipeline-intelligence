# Data Pipeline Intelligence

**Automated Azure Data Factory column-level lineage, documentation & AI insights — fully offline.**

Upload exported Azure Data Factory (ADF) JSON and get back reconstructed pipeline
logic, column-level lineage, impact analysis, generated documentation and
AI-assisted explanations. **No Azure connection, no credentials, no cloud
database.** Files are processed in memory and never persisted.

> Status: early development. The upload → validate → parse → summary vertical
> slice is complete and tested. The deterministic column-lineage engine is the
> next milestone. See [`PROJECT_PLAN.md`](PROJECT_PLAN.md) for authoritative
> progress and [the compatibility matrix](#compatibility-matrix) for what is
> actually supported today.

---

## Problem statement

Enterprise data teams operate hundreds of ADF pipelines and Mapping Data Flows.
The transformation logic lives in deeply nested JSON that is hard to read, hard
to audit, and effectively undocumented. Answering *"where does `customer_id`
come from, what happens to it, and what breaks if I change it?"* today means an
engineer tracing JSON by hand.

This tool reconstructs the pipeline and transformation model from uploaded JSON
and answers those questions — visually and in natural language — with a
deterministic lineage engine as the source of truth.

## Architecture

```
User → Streamlit UI → Upload/Validation → ADF JSON Parser
     → Normalized Internal Model → { Lineage Engine, Documentation Engine }
     → Lineage Graph (NetworkX) → Impact Analysis → AI Insight Layer → UI / Export
```

- **Raw JSON access is confined to `app/parsers/`** (DEC-004). Everything else
  works against the typed models in `app/models/`.
- **AI is an enhancement layer, never the source of truth** (F017 / §12 of the plan).
  It receives a sanitized metadata model, never raw uploaded JSON.
- Full architecture, decision log and diagrams: [`PROJECT_PLAN.md`](PROJECT_PLAN.md).

## Features

| ID | Feature | State |
| --- | --- | --- |
| F001–F003 | Upload, JSON validation, pipeline parsing | ✅ done |
| F004 | Mapping Data Flow parsing (Data Flow Script) | ✅ done |
| F021 | Expression parser (referenced-column resolution) | ✅ done |
| F009–F011 | Column lineage graph + upstream/downstream traversal (data flows) | ✅ done |
| F012 | Column impact analysis + deterministic LOW/MED/HIGH scoring | ✅ done |
| F015 | Lineage visualization (Graphviz DOT; optional PyVis) | ✅ done |
| F016 / F022–F024 | Dashboard, Pipeline / Data Flow explorers, Column Lineage & Impact UI + search | ✅ done |
| F013–F014 / F018 | Documentation generation + Markdown/HTML export | ✅ done |
| F017 | AI insight providers (deterministic default, optional LLM) | ✅ done |
| F008 | Pipeline column lineage + cross-artifact stitching | ✅ done |
| F005–F007 | Source / sink / transformation discovery (Inventory page) | ✅ done |

**The full feature catalogue (F001–F024) is implemented.** Remaining work is
release hardening (Docker build verification, security review, a 5 MiB
performance benchmark).
| F017 | AI insights (deterministic + optional LLM providers) | ⬜ planned |
| F008 | Pipeline / Copy-activity column lineage + cross-artifact stitching | ⬜ planned |

## Compatibility matrix

| ADF construct | Supported | Notes |
| --- | --- | --- |
| Pipeline JSON (plain export) | Yes | `{name, properties:{activities,...}}` |
| Pipeline JSON (ARM resource form) | Yes | `[concat(parameters('factoryName'), '/Name')]` names unwrapped |
| Activities (`dependsOn`, inputs/outputs) | Yes | Execution order via topological sort; cycles reported |
| Unknown activity types | Yes | Kept and flagged, never fatal |
| ExecuteDataFlow references | Yes | Data flow reference name captured |
| Control-flow inner activities (`ForEach`/`If`/`Switch`) | Partial | Container recorded; nested activities not yet expanded |
| Mapping Data Flow JSON | Yes | Data Flow Script parsed into sources, sinks, transformations, stream graph |
| Data flow `source` / `sink` | Yes | Dataset refs + parsed source schema (`output(...)`) |
| Data flow `derive` / `select` | Yes | Target columns + raw expression; `select` splits retained / renamed / derived |
| Data flow `aggregate` | Yes | `groupBy` columns + aggregation target columns |
| Data flow `join` / `filter` / `union` | Partial | Input streams + raw argument text captured; keys/predicates not yet decomposed |
| Data flow `lookup` / `pivot` / `window` / `flatten` / … | Partial | Recognized and kept; arguments not yet decomposed |
| Unknown data flow operators | Yes | Flagged as a warning, kept, never fatal |
| Expressions — data-flow dialect | Yes | Referenced columns resolved (`concat`, `iif`, `isNull`, `toString`, `substring`, `sum`, `byName`, `stream@col`, `$param`); unsupported → "Unable to determine automatically" |
| Expressions — pipeline dialect (`@concat`, `pipeline().parameters.x`) | Partial | Function names + parameter/variable references only |
| Higher-order expressions (`reduce`, `mapIndex`, …) | Partial | Flagged; column dependencies may be incomplete |
| Column-level lineage graph (Mapping Data Flow) | Yes | source→sink column graph; direct / rename / derived / join-key / aggregate / filter / union / split relationships |
| Upstream / downstream traversal + "explain" narrative | Yes | origin, journey steps, downstream columns, sinks reached |
| Column-level lineage (Pipeline / Copy activity) | Yes | Copy activities with an explicit `TabularTranslator` map; `dataset::column` nodes |
| Cross-artifact stitching | Yes | Pipeline Copy columns ↔ data-flow source/sink columns joined by shared dataset name; one combined graph, searchable in the UI ("All artifacts" scope) |
| Impact scoring (LOW/MEDIUM/HIGH) | Yes | Deterministic weighted formula (documented in PROJECT_PLAN §12); `ImpactReport.factors` shows the arithmetic; works on data-flow, pipeline or combined graphs |
| Lineage visualization | Yes | Graphviz DOT (`st.graphviz_chart`, no extra dep) with per-stream clusters + relationship-coloured edges + focus mode; interactive PyVis via `pip install '.[viz]'` |
| Streamlit UI | Yes | Dashboard, Pipeline Explorer, Data Flow Explorer, Column Lineage & Impact (search + narrative + score + graph), Inventory, Documentation, AI Insights; in-memory, no persistence |
| Inventory / discovery | Yes | Cross-artifact tables: datasets (reader/writer artifacts + column counts), sources, sinks, transformations |
| Linked-service names / dataset formats | No | Those live in Dataset / LinkedService JSON, which this tool classifies as "unknown" (and connection details must never be surfaced) |
| Documentation generation | Markdown + HTML | Deterministic; pipeline & data-flow docs incl. per-transformation column mapping + end-to-end column lineage; `.md` / standalone `.html` download in the UI (dependency-free, XSS-safe converter) |
| AI insights | Yes | Offline `DeterministicInsightProvider` (default) turns structural facts into summary / observations / risks / recommendations; optional `LLMInsightProvider` (`pip install '.[llm]'`, `ADFPI_*` config) receives **only** sanitized metadata and falls back to deterministic on any error. AI never determines lineage |
| Lineage through `window` / `pivot` / `flatten` / unknown ops | Approx. | Passthrough approximation + warning |
| Secrets / connection strings | Redacted | Secret-bearing keys dropped before they reach any model |

## Installation

Requires **Python 3.11+**.

```bash
git clone <repo-url> data-pipeline-intelligence
cd data-pipeline-intelligence
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Running the application

From the project root:

```bash
streamlit run app/ui/app.py
```

(`app/ui/app.py` prepends the project root to `sys.path` at startup, so this
works with or without `pip install -e .`.)

Then open the URL Streamlit prints (default <http://localhost:8501>). Upload an
ADF Pipeline or Mapping Data Flow JSON export on the **Upload** page.

Configuration is via environment variables (prefix `ADFPI_`) or a `.env` file —
copy [`.env.example`](.env.example). All settings have safe defaults; nothing is
required to run.

## Testing

```bash
pytest                     # unit + integration, synthetic fixtures only
ruff check . && ruff format --check .
mypy
```

The full suite runs offline with no Azure credentials. Synthetic fixtures live
in [`tests/fixtures/`](tests/fixtures/).

## Docker

```bash
docker compose up --build
# or:
docker build -t data-pipeline-intelligence .
docker run --rm -p 8501:8501 data-pipeline-intelligence
```

The container runs `streamlit run app/ui/app.py` on port 8501.

## Security considerations

- Uploads are size-limited (`ADFPI_MAX_UPLOAD_BYTES`, default 5 MiB) and
  extension-checked **before** parsing.
- Only `json.loads` is used — no `eval`, `pickle`, or YAML loaders. Expressions
  are analyzed, never evaluated.
- Uploaded content is processed in memory and **never written to disk**.
- Uploaded JSON content and column values are **never logged**.
- Secret-bearing keys (connection strings, keys, passwords, tokens, …) are
  dropped during parsing and can never reach the model, UI, export or AI layer.
- No outbound network calls unless an LLM insight provider is explicitly
  configured and enabled; even then it receives only sanitized metadata.

## Limitations

See the [compatibility matrix](#compatibility-matrix) and *Known Limitations* in
[`PROJECT_PLAN.md`](PROJECT_PLAN.md). In short: pipeline structure, Mapping Data
Flow logic, expression column-dependency resolution, **data-flow column-level
lineage + traversal + deterministic impact scoring**, an **interactive lineage
graph**, **generated Markdown documentation**, and a **Streamlit UI** (dashboard,
pipeline / data-flow explorers, column search + lineage & impact, documentation)
work today, with **Markdown & HTML export**, an **AI Insights** layer (offline by
default), **pipeline Copy-activity column lineage**, **cross-artifact stitching**
(search a column across every uploaded pipeline + data flow) and a cross-artifact
**Inventory**. The full feature catalogue is implemented; what remains is release
hardening.

## Roadmap

1. ~~Mapping Data Flow parsing (sources / sinks / transformations / `scriptLines`)~~ ✅
2. ~~Expression parser subsystem~~ ✅
3. ~~Deterministic column-level lineage engine (data flows)~~ ✅
4. ~~Upstream / downstream traversal + impact analysis & scoring~~ ✅
5. ~~Lineage visualization (Graphviz DOT + optional PyVis)~~ ✅
6. ~~Data Flow Explorer + Column Lineage / Impact UI + column search~~ ✅
7. ~~Documentation engine + Markdown/HTML export~~ ✅
8. ~~AI insight providers (deterministic default, optional LLM)~~ ✅
9. ~~Pipeline / Copy-activity column lineage + cross-artifact stitching~~ ✅
10. ~~Source / sink / transformation discovery & inventory views~~ ✅
11. Release hardening: Docker build check, security review, 5 MiB perf benchmark *(next)*

## Project layout

```
app/
  core/          config, logging, exceptions
  models/        normalized Pydantic model of ADF concepts
  parsers/       the only place raw ADF JSON is read
  lineage/       column lineage graph + traversal + impact + pipeline lineage + cross-artifact stitch
  visualization/ Graphviz DOT + optional PyVis rendering of the lineage graph
  documentation/ deterministic Markdown + dependency-free HTML export
  insights/      AI enhancement layer: sanitize + deterministic/LLM/safe providers
  services/      orchestration the UI calls
  ui/            Streamlit app
tests/
  unit/ integration/ fixtures/
```

## License

Proprietary — internal enterprise hackathon project.
