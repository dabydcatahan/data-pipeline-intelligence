"""Data Pipeline Intelligence - Streamlit application.

Run with:  streamlit run app/ui/app.py

The UI is intentionally thin: all analysis lives in ``app.services``.
"""

from __future__ import annotations

# ``streamlit run app/ui/app.py`` puts this file's directory on sys.path, not the
# project root, so ensure the repo root is importable before any ``app.*`` import.
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from app.core.config import get_settings
from app.core.exceptions import ADFIntelligenceError
from app.core.logging import configure_logging
from app.models.artifact import ParsedArtifact
from app.models.pipeline import Pipeline
from app.services.analysis_service import (
    AnalysisService,
    AnalysisSession,
    DataFlowSummary,
    PipelineSummary,
)

_IMPACT_ICON = {"low": "🟢", "medium": "🟠", "high": "🔴"}

# ING brand palette: signature orange, deep petrol/navy for contrast, light greys for surfaces.
_ING_ORANGE = "#FF6200"
_ING_ORANGE_DARK = "#D65200"
_ING_NAVY = "#00252E"
_ING_GREY = "#F5F6F7"
_ING_BORDER = "#E4E7EB"

configure_logging()
SETTINGS = get_settings()

st.set_page_config(
    page_title=SETTINGS.app_title,
    page_icon="🦁",
    layout="wide",
)


def _inject_ing_theme() -> None:
    """Apply ING-branded colours and typography on top of the base Streamlit theme."""
    st.markdown(
        f"""
        <style>
        .stApp {{
            background-color: #FFFFFF;
        }}
        [data-testid="stSidebar"] {{
            background-color: {_ING_NAVY};
        }}
        [data-testid="stSidebar"] * {{
            color: #FFFFFF !important;
        }}
        [data-testid="stSidebar"] .stRadio label:hover {{
            color: {_ING_ORANGE} !important;
        }}
        [data-testid="stSidebar"] hr {{
            border-color: rgba(255, 255, 255, 0.2);
        }}
        div[data-testid="stMetric"] {{
            background-color: {_ING_GREY};
            border: 1px solid {_ING_BORDER};
            border-left: 4px solid {_ING_ORANGE};
            border-radius: 6px;
            padding: 0.75rem 1rem;
        }}
        div[data-testid="stMetricLabel"] {{
            color: {_ING_NAVY};
        }}
        h1, h2, h3 {{
            color: {_ING_NAVY};
        }}
        .stButton > button, .stFormSubmitButton > button {{
            background-color: {_ING_ORANGE};
            color: #FFFFFF;
            border: none;
            font-weight: 600;
        }}
        .stButton > button:hover, .stFormSubmitButton > button:hover {{
            background-color: {_ING_ORANGE_DARK};
            color: #FFFFFF;
        }}
        .stTabs [data-baseweb="tab"][aria-selected="true"] {{
            color: {_ING_ORANGE};
            border-bottom-color: {_ING_ORANGE};
        }}
        [data-testid="stExpander"] summary {{
            font-weight: 600;
        }}
        #ing-header {{
            background: linear-gradient(90deg, {_ING_ORANGE} 0%, {_ING_ORANGE_DARK} 100%);
            padding: 1.1rem 1.5rem;
            border-radius: 8px;
            margin-bottom: 1.25rem;
            color: #FFFFFF;
        }}
        #ing-header h1 {{
            color: #FFFFFF !important;
            margin: 0;
            font-size: 1.6rem;
        }}
        #ing-header p {{
            color: #FFF3EB;
            margin: 0.15rem 0 0 0;
            font-size: 0.9rem;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


_inject_ing_theme()


@st.cache_resource
def get_service() -> AnalysisService:
    return AnalysisService(SETTINGS)


def get_session() -> AnalysisSession:
    if "session" not in st.session_state:
        st.session_state["session"] = AnalysisSession()
    return st.session_state["session"]


# --------------------------------------------------------------------------- pages
def _columns_discovered(service: AnalysisService, session: AnalysisSession) -> int:
    seen: set[str] = set()
    for artifact in session.data_flow_artifacts():
        try:
            lineage = service.data_flow_lineage(artifact, session=session)
        except ValueError:
            continue
        seen.update(node.id for node in lineage.graph.iter_nodes())
    return len(seen)


def page_dashboard(service: AnalysisService, session: AnalysisSession) -> None:
    st.header("Dashboard")
    stats = session.dashboard_stats()
    if stats["files_analyzed"] == 0:
        st.info("No files analyzed yet. Go to **Upload** to add ADF JSON exports.")
        return

    row1 = st.columns(4)
    row1[0].metric("Files analyzed", stats["files_analyzed"])
    row1[1].metric("Pipelines", stats["pipelines"])
    row1[2].metric("Data flows", stats["data_flows"])
    row1[3].metric("Activities", stats["activities"])

    row2 = st.columns(4)
    row2[0].metric("Transformations", stats["transformations"])
    row2[1].metric("Source datasets", stats["source_datasets"])
    row2[2].metric("Sink datasets", stats["sink_datasets"])
    row2[3].metric("Columns discovered", _columns_discovered(service, session))

    if stats["failed_uploads"]:
        st.metric("Failed uploads", stats["failed_uploads"])

    if session.failures:
        with st.expander(
            f"{len(session.failures)} upload(s) could not be processed", expanded=False
        ):
            for filename, message in session.failures.items():
                st.warning(f"**{filename}** — {message}")


def _reset_uploader() -> None:
    """Bump the file_uploader key so the widget empties and won't re-ingest."""
    st.session_state["uploader_key"] = st.session_state.get("uploader_key", 0) + 1


def _ingest_one(service: AnalysisService, session: AnalysisSession, name: str, data: bytes) -> None:
    """Run one file/paste through the analysis pipeline and show the outcome."""
    try:
        artifact = service.analyze_upload(name, data)
    except ADFIntelligenceError as exc:
        session.failures[name] = str(exc)
        st.error(f"**{name}** — {exc.category}: {exc}")
        return
    session.add(artifact)
    session.failures.pop(name, None)
    badge = "✅" if artifact.is_supported else "⚠️"
    st.success(f"{badge} **{name}** parsed as **{artifact.kind.label}**: `{artifact.name}`")
    for warning in artifact.warnings:
        st.warning(warning)


def page_upload(service: AnalysisService, session: AnalysisSession) -> None:
    st.header("Add ADF JSON")
    st.caption(
        f"Accepted: {', '.join(SETTINGS.allowed_extensions)} · "
        f"max {SETTINGS.max_upload_mib:.1f} MiB each · "
        "processed in memory, never stored."
    )

    tab_upload, tab_paste = st.tabs(["Upload file(s)", "Paste JSON"])

    with tab_upload:
        uploads = st.file_uploader(
            "Drop exported Azure Data Factory Pipeline or Mapping Data Flow JSON",
            type=[ext.lstrip(".") for ext in SETTINGS.allowed_extensions],
            accept_multiple_files=True,
            key=f"uploader_{st.session_state.get('uploader_key', 0)}",
        )
        for upload in uploads or []:
            _ingest_one(service, session, upload.name, upload.getvalue())

    with tab_paste:
        with st.form("paste_json", clear_on_submit=True):
            name = st.text_input("Name (optional)", placeholder="my-pipeline")
            text = st.text_area("Paste one ADF Pipeline or Mapping Data Flow JSON", height=260)
            submitted = st.form_submit_button("Analyze pasted JSON")
        if submitted:
            if not text.strip():
                st.warning("Nothing to analyze — paste some JSON first.")
            else:
                stem = name.strip().removesuffix(".json") or "pasted"
                _ingest_one(service, session, f"{stem}.json", text.encode("utf-8"))

    _render_loaded_artifacts(session)


def _render_loaded_artifacts(session: AnalysisSession) -> None:
    st.divider()
    st.subheader("Loaded artifacts")
    if not session.artifacts and not session.failures:
        st.caption("Nothing loaded yet.")
        return

    for artifact in list(session.artifacts):
        row = st.columns([6, 1])
        row[0].write(f"{artifact.kind.label}: **{artifact.name}** · `{artifact.source_filename}`")
        if row[1].button("Remove", key=f"rm_{artifact.content_sha256}"):
            session.remove(artifact.content_sha256)
            _reset_uploader()
            st.rerun()

    for filename in list(session.failures):
        row = st.columns([6, 1])
        row[0].write(f"⚠️ failed: `{filename}`")
        if row[1].button("Dismiss", key=f"rmf_{filename}"):
            session.failures.pop(filename, None)
            _reset_uploader()
            st.rerun()

    if st.button("Clear all", type="primary"):
        session.clear()
        _reset_uploader()
        st.rerun()


def _render_pipeline_summary(summary: PipelineSummary) -> None:
    st.subheader(summary.name)
    if summary.description:
        st.write(summary.description)
    meta = st.columns(3)
    meta[0].metric("Activities", summary.activity_count)
    meta[1].metric("Parameters", len(summary.parameter_names))
    meta[2].metric("Data flow refs", len(summary.data_flow_references))

    if summary.folder:
        st.caption(f"Folder: {summary.folder}")
    if summary.cyclic_activities:
        st.warning("Circular dependency among: " + ", ".join(summary.cyclic_activities))
    if summary.unrecognized_activities:
        st.info("Unrecognized activity types: " + ", ".join(summary.unrecognized_activities))

    left, right = st.columns(2)
    with left:
        st.markdown("**Activity types**")
        st.table(
            {"type": list(summary.activity_types), "count": list(summary.activity_types.values())}
        )
        st.markdown("**Execution order**")
        st.write(" → ".join(summary.execution_order) or "_none_")
    with right:
        st.markdown("**Parameters**")
        st.write(", ".join(summary.parameter_names) or "_none_")
        st.markdown("**Variables**")
        st.write(", ".join(summary.variable_names) or "_none_")
        st.markdown("**Annotations**")
        st.write(", ".join(summary.annotations) or "_none_")

    st.markdown("**Datasets**")
    ds = st.columns(2)
    ds[0].write("Inputs: " + (", ".join(summary.input_datasets) or "_none_"))
    ds[1].write("Outputs: " + (", ".join(summary.output_datasets) or "_none_"))

    if summary.dependency_edges:
        st.markdown("**Dependencies**")
        for upstream, downstream in summary.dependency_edges:
            st.write(f"- `{upstream}` → `{downstream}`")

    if summary.data_flow_references:
        st.markdown("**Referenced data flows**")
        st.write(", ".join(summary.data_flow_references))


def page_pipeline_explorer(service: AnalysisService, session: AnalysisSession) -> None:
    st.header("Pipeline Explorer")
    pipelines = session.pipelines
    if not pipelines:
        st.info("No pipelines parsed yet. Upload a Pipeline JSON to begin.")
        return
    names = [p.name for p in pipelines]
    selected = st.selectbox("Pipeline", names)
    pipeline = session.get_pipeline(selected)
    if pipeline is None:
        st.error("Selected pipeline is no longer available.")
        return
    _render_pipeline_summary(service.summarize_pipeline(pipeline))
    _render_data_flow_chain(service, session, pipeline)

    lineage = service.pipeline_lineage(pipeline)
    with st.expander("Column lineage (Copy activities)"):
        if len(lineage.graph):
            st.graphviz_chart(service.lineage_dot(lineage), use_container_width=True)
        else:
            st.caption(
                "No Copy activity carries an explicit column mapping (translator), "
                "so pipeline-level column lineage cannot be derived."
            )
        for warning in lineage.warnings:
            st.warning(warning)


def _render_data_flow_chain(
    service: AnalysisService, session: AnalysisSession, pipeline: Pipeline
) -> None:
    """Show a pipeline's ExecuteDataFlow activities in run order, with continuity."""
    statuses = service.data_flow_chain_status(pipeline, session)
    if not statuses:
        return

    st.markdown("**Data flow execution order**")
    st.caption(
        "Order derived from `dependsOn`; a data flow is marked linked when its upstream "
        "data flow's sink dataset matches one of its own source datasets."
    )
    chain_labels = [s.data_flow_reference for s in statuses]
    st.write(" → ".join(chain_labels))

    for status in statuses:
        upload_badge = "✅" if status.is_uploaded else "⚠️ not uploaded"
        line = f"**{status.order + 1}. {status.data_flow_reference}** ({upload_badge})"
        st.markdown(line)
        st.caption(f"Activity: `{status.activity_name}`")

        if status.is_first:
            st.caption("No upstream data flow; this is the first in the chain.")
            continue

        for upstream in status.linked_upstream:
            datasets = next(ds for ref, ds in status.shared_datasets if ref == upstream)
            st.success(
                f"Continuity confirmed: **{upstream}** → **{status.data_flow_reference}** "
                f"via shared dataset(s): {', '.join(datasets)}."
            )
        for upstream in status.unlinked_upstream:
            st.warning(
                f"**{upstream}** runs before **{status.data_flow_reference}**, but no shared "
                "dataset was found between them. Upload both data flows to confirm the "
                "handoff, or check whether the link is only implied by pipeline parameters."
            )


def _select_data_flow(session: AnalysisSession, key: str) -> ParsedArtifact | None:
    artifacts = session.data_flow_artifacts()
    if not artifacts:
        st.info("No Mapping Data Flows parsed yet. Upload a data flow JSON to begin.")
        return None
    name = st.selectbox("Data flow", [a.name for a in artifacts], key=key)
    return next((a for a in artifacts if a.name == name), None)


def _render_data_flow_summary(summary: DataFlowSummary) -> None:
    st.subheader(summary.name)
    if summary.description:
        st.write(summary.description)
    cols = st.columns(3)
    cols[0].metric("Sources", summary.source_count)
    cols[1].metric("Sinks", summary.sink_count)
    cols[2].metric("Transformations", summary.transformation_count)
    if summary.unrecognized_operators:
        st.info("Unrecognized DFS operators: " + ", ".join(summary.unrecognized_operators))

    st.markdown("**Sources**")
    st.table(
        [
            {"source": name, "dataset": dataset, "schema columns": ", ".join(schema) or "—"}
            for name, dataset, schema in summary.sources
        ]
        or [{"source": "—", "dataset": "—", "schema columns": "—"}]
    )

    st.markdown("**Sinks**")
    st.table(
        [
            {"sink": name, "dataset": dataset, "fed by": feeding}
            for name, dataset, feeding in summary.sinks
        ]
        or [{"sink": "—", "dataset": "—", "fed by": "—"}]
    )

    st.markdown("**Transformations**")
    st.table(
        [
            {
                "name": row.name,
                "operator": row.operator + ("" if row.recognized else " (?)"),
                "inputs": ", ".join(row.inputs) or "—",
                "columns": row.column_count,
                "group by": ", ".join(row.group_by) or "—",
            }
            for row in summary.transformations
        ]
        or [{"name": "—", "operator": "—", "inputs": "—", "columns": 0, "group by": "—"}]
    )


def page_data_flow_explorer(service: AnalysisService, session: AnalysisSession) -> None:
    st.header("Data Flow Explorer")
    artifact = _select_data_flow(session, key="dfx")
    if artifact is None or artifact.data_flow is None:
        return

    if artifact.warnings:
        with st.expander(f"{len(artifact.warnings)} parser warning(s)"):
            for warning in artifact.warnings:
                st.warning(warning)

    summary = service.summarize_data_flow(artifact.data_flow)
    _render_data_flow_summary(summary)

    st.markdown("**Transformation detail**")
    for transformation in artifact.data_flow.transformations:
        with st.expander(f"{transformation.name} — {transformation.kind or 'unknown'}"):
            if transformation.raw_arguments:
                st.code(transformation.raw_arguments, language="text")
            if transformation.columns:
                st.table(
                    [
                        {
                            "target": col.target,
                            "kind": col.kind,
                            "from": ", ".join(col.referenced_columns) or "—",
                            "confidence": col.analysis_confidence,
                            "expression": col.expression or "—",
                        }
                        for col in transformation.columns
                    ]
                )
            else:
                st.caption("No column-level detail for this operator.")

    if summary.script_lines:
        with st.expander("Raw Data Flow Script"):
            st.code("\n".join(summary.script_lines), language="text")


def _impact_badge(level_value: str) -> str:
    return f"{_IMPACT_ICON.get(level_value, '⚪')} {level_value.upper()}"


def page_column_lineage(service: AnalysisService, session: AnalysisSession) -> None:
    st.header("Column Lineage & Impact")
    scope = st.radio(
        "Scope",
        ["This data flow", "All artifacts (stitched)"],
        horizontal=True,
        help="Stitched scope links pipeline Copy-activity columns to data-flow "
        "sources/sinks by shared dataset identity.",
    )

    if scope == "All artifacts (stitched)":
        if not session.artifacts:
            st.info("Upload pipelines and/or data flows to build a combined graph.")
            return
        lineage = service.combined_lineage(session)
    else:
        artifact = _select_data_flow(session, key="clx")
        if artifact is None:
            return
        lineage = service.data_flow_lineage(artifact, session=session)

    if lineage.warnings:
        with st.expander(f"{len(lineage.warnings)} lineage warning(s)"):
            for warning in lineage.warnings:
                st.warning(warning)

    all_columns = service.list_columns(lineage)
    controls = st.columns([3, 2])
    query = (
        controls[0]
        .text_input("Search a column", placeholder="e.g. customer_id or CustomerSink.cust_key")
        .strip()
    )
    if not query:
        query = controls[1].selectbox("…or pick one", all_columns) if all_columns else ""
    direction = st.radio("Direction", ["both", "downstream", "upstream"], horizontal=True)

    if not query:
        st.info("Enter or pick a column to trace its lineage and impact.")
        return

    matches = service.search_columns(lineage, query)
    if not matches:
        st.warning(f"No column matches '{query}'. Known columns: " + ", ".join(all_columns))
        return

    if len(matches) > 6:
        labels = {node_id: lineage.graph.node(node_id).label for node_id in matches}
        st.caption(f"{len(matches)} matches for '{query}'.")
        selected_labels = st.multiselect(
            "Which one(s) to inspect?",
            options=list(labels.values()),
            default=list(labels.values())[:6],
        )
        matches = [node_id for node_id, label in labels.items() if label in selected_labels]
        if not matches:
            st.info("Select at least one match above to see its lineage and impact.")
            return

    for node_id in matches:
        (journey,) = service.explain_column(lineage, node_id)
        (report,) = service.impact(lineage, node_id)
        st.subheader(f"{journey.stream}.{journey.column}")

        top = st.columns(4)
        top[0].metric("Impact", _impact_badge(report.level.value))
        top[1].metric("Score", report.score)
        top[2].metric("Downstream columns", report.downstream_column_count)
        top[3].metric("Streams affected", report.affected_stream_count)
        st.caption(report.summary())

        detail = st.columns(2)
        with detail[0]:
            st.markdown("**Origin(s)**")
            st.write(", ".join(journey.origins) or "_this is a source column_")
            st.markdown("**Sink columns reached**")
            st.write(", ".join(journey.sink_columns) or "_none_")
        with detail[1]:
            st.markdown("**Transformations affected**")
            st.write(", ".join(report.transformations_affected) or "_none_")
            if report.joins_affected or report.filters_affected or report.splits_affected:
                st.write(
                    "joins: "
                    + (", ".join(report.joins_affected) or "—")
                    + " · filters: "
                    + (", ".join(report.filters_affected) or "—")
                    + " · splits: "
                    + (", ".join(report.splits_affected) or "—")
                )

        with st.expander("Impact scoring factors"):
            for factor in report.factors:
                st.write(f"- {factor}")

        st.markdown("**Lineage narrative**")
        st.code(journey.narrative() or "(no upstream steps)", language="text")

        if report.critical_path:
            st.markdown("**Critical path** (riskiest route to a sink)")
            st.code(" → ".join(report.critical_path), language="text")

        st.graphviz_chart(
            service.lineage_dot(
                lineage,
                focus=node_id,
                direction=direction,
                level=report.level.value,
                critical_path=report.critical_path,
            ),
            use_container_width=True,
        )
        st.divider()

    with st.expander("Full data-flow column graph"):
        st.graphviz_chart(service.lineage_dot(lineage), use_container_width=True)


def page_documentation(service: AnalysisService, session: AnalysisSession) -> None:
    st.header("Documentation")
    artifacts = session.artifacts
    if not artifacts:
        st.info("Nothing to document yet. Upload ADF JSON to begin.")
        return

    labels = {f"{a.kind.label}: {a.name}": a for a in artifacts}
    picked = labels[st.selectbox("Artifact", list(labels))]

    include_lineage = True
    if picked.data_flow is not None:
        include_lineage = st.checkbox("Include column lineage", value=True)

    if picked.pipeline is not None:
        markdown = service.document_pipeline(picked.pipeline)
    elif picked.data_flow is not None:
        markdown = service.document_data_flow(
            picked, session=session, include_lineage=include_lineage
        )
    else:
        st.warning("This artifact type has no documentation generator.")
        return

    fmt = st.radio("Format", ["Markdown", "HTML", "Word (.docx)"], horizontal=True)
    if fmt == "HTML":
        html = service.export_html(markdown, title=picked.name)
        st.download_button(
            "Download .html",
            html,
            file_name=f"{picked.name}.html",
            mime="text/html",
        )
        st.code(html, language="html")
    elif fmt == "Word (.docx)":
        if not service.docx_export_available():
            st.info(
                "Word export needs the optional `docx` extra: "
                "`pip install 'data-pipeline-intelligence[docx]'`, then restart the app."
            )
        else:
            try:
                if picked.pipeline is not None:
                    docx_bytes = service.document_pipeline_docx(picked.pipeline)
                else:
                    docx_bytes = service.document_data_flow_docx(
                        picked, session=session, include_lineage=include_lineage
                    )
            except Exception as e:
                st.error(f"Could not generate the Word document: {e}")
            else:
                st.download_button(
                    "Download .docx",
                    docx_bytes,
                    file_name=f"{picked.name}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
                st.caption(
                    "Styled Word document: title page, table of contents (right-click → "
                    "Update Field in Word to populate it), and the same content as the "
                    "Markdown/HTML export."
                )
    else:
        st.download_button(
            "Download .md",
            markdown,
            file_name=f"{picked.name}.md",
            mime="text/markdown",
        )
        if st.toggle("Show Markdown source"):
            st.code(markdown, language="markdown")
        else:
            st.markdown(markdown)


def _render_insight(result) -> None:  # InsightResult
    st.markdown(result.summary)
    for heading, items in (
        ("Observations", result.observations),
        ("Risks", result.risks),
        ("Recommendations", result.recommendations),
    ):
        if items:
            st.markdown(f"**{heading}**")
            for item in items:
                st.markdown(f"- {item}")
    st.caption(f"generated by: {result.provider}")


def page_ai_insights(service: AnalysisService, session: AnalysisSession) -> None:
    st.header("AI Insights")
    st.caption(
        "The deterministic lineage engine is authoritative. These are generated "
        "observations built **only** from structural metadata — no raw JSON or column "
        f"values are used. Active provider: `{service.insight_provider_name}`."
    )
    if not session.artifacts:
        st.info("Nothing to analyze yet. Upload ADF JSON to begin.")
        return

    labels = {f"{a.kind.label}: {a.name}": a for a in session.artifacts}
    picked = labels[st.selectbox("Artifact", list(labels))]

    if picked.pipeline is not None:
        _render_insight(service.insights_for_pipeline(picked.pipeline))
    elif picked.data_flow is not None:
        _render_insight(service.insights_for_data_flow(picked, session=session))
        lineage = service.data_flow_lineage(picked, session=session)
        columns = service.list_columns(lineage)
        if columns:
            with st.expander("Insight for a specific column"):
                column = st.selectbox("Column", columns, key="ai_col")
                matches = service.search_columns(lineage, column)
                if matches:
                    _render_insight(service.insights_for_column(lineage, matches[0]))
    else:
        st.warning("No insight template for this artifact type.")


def page_inventory(service: AnalysisService, session: AnalysisSession) -> None:
    st.header("Inventory")
    st.caption("Every source, sink and transformation across all uploaded artifacts.")
    inv = service.inventory(session)
    if inv.is_empty:
        st.info("Nothing analyzed yet. Upload ADF JSON to begin.")
        return

    tabs = st.tabs(
        [
            f"Datasets ({len(inv.datasets)})",
            f"Sources ({len(inv.sources)})",
            f"Sinks ({len(inv.sinks)})",
            f"Transformations ({len(inv.transformations)})",
        ]
    )
    with tabs[0]:
        st.table(
            [
                {
                    "dataset": d.name,
                    "written by": ", ".join(d.written_by) or "—",
                    "read by": ", ".join(d.read_by) or "—",
                    "columns": str(d.column_count) if d.column_count else "—",
                }
                for d in inv.datasets
            ]
        )
    with tabs[1]:
        st.table(
            [
                {
                    "artifact": s.artifact,
                    "source": s.name,
                    "dataset": s.dataset,
                    "columns": str(s.column_count),
                }
                for s in inv.sources
            ]
        )
    with tabs[2]:
        st.table(
            [{"artifact": s.artifact, "sink": s.name, "dataset": s.dataset} for s in inv.sinks]
        )
    with tabs[3]:
        st.table(
            [
                {
                    "artifact": t.artifact,
                    "name": t.name,
                    "kind": t.kind + ("" if t.recognized else " (?)"),
                    "columns": str(t.column_count),
                }
                for t in inv.transformations
            ]
        )


def page_error_analyzer(service: AnalysisService, session: AnalysisSession) -> None:
    """Analyze ADF errors and recommend lineage inspection checkpoints."""
    st.header("ADF Error Analyzer")
    st.caption(
        "Paste or upload an ADF error message to get rule-based lineage inspection "
        "recommendations based on your uploaded artifacts. Classification is fully "
        "deterministic (regex pattern matching)."
    )

    if not session.artifacts:
        st.info(
            "No artifacts loaded yet. Upload pipelines/data flows first for better error context."
        )
        st.info(
            "You can still analyze errors without artifacts, but recommendations will be generic."
        )

    st.divider()

    # Input tabs
    tab_paste, tab_upload = st.tabs(["Paste error text", "Upload error file"])

    if "error_analyzer_paste_input" not in st.session_state:
        st.session_state["error_analyzer_paste_input"] = st.session_state.get(
            "error_analyzer_text", ""
        )

    new_error_text: str | None = None

    with tab_paste:
        with st.form("paste_error", clear_on_submit=False):
            pasted_text = st.text_area(
                "Paste your ADF error message, stack trace, or run log",
                placeholder="e.g., Column 'customer_id' not found\n\nData type mismatch...",
                height=200,
                key="error_analyzer_paste_input",
            )
            submitted = st.form_submit_button("Analyze error")
        if submitted and pasted_text.strip():
            new_error_text = pasted_text

    with tab_upload:
        uploaded_file = st.file_uploader(
            "Upload error log file",
            type=["txt", "log", "json"],
            accept_multiple_files=False,
        )
        if uploaded_file:
            new_error_text = uploaded_file.getvalue().decode("utf-8")
            st.info(f"Loaded: {uploaded_file.name}")

    # Persist the latest error text so it survives navigation to other pages
    if new_error_text is not None and new_error_text.strip():
        st.session_state["error_analyzer_text"] = new_error_text
        st.session_state.pop("error_analyzer_result", None)

    error_text = st.session_state.get("error_analyzer_text", "")

    if not error_text.strip():
        st.warning("No error text provided.")
        return

    col1, col2 = st.columns([3, 1])
    with col1:
        pass
    with col2:
        if st.button("Clear current error"):
            st.session_state.pop("error_analyzer_text", None)
            st.session_state.pop("error_analyzer_result", None)
            st.session_state.pop("error_analyzer_paste_input", None)
            st.session_state.pop("error_analyzer_flagged_text", None)
            st.rerun()

    # Reuse the cached analysis unless the error text has changed
    cached = st.session_state.get("error_analyzer_result")
    cache_valid = cached is not None and cached[0] == error_text

    if cache_valid:
        analysis = cached[1]
    else:
        try:
            analysis = service.analyze_error(error_text, session=session)
        except Exception as e:
            st.error(f"Error analysis failed: {e}")
            return
        st.session_state["error_analyzer_result"] = (error_text, analysis)

    # Display results
    st.divider()
    st.subheader("Analysis Results")

    # Summary section
    col1, col2, col3 = st.columns(3)
    severity_icon = {"info": "i", "warning": "!", "error": "ERROR", "critical": "CRITICAL"}
    col1.metric(
        "Severity",
        f"{severity_icon.get(analysis.severity.value)} {analysis.severity.value.upper()}",
    )
    col2.metric("Error Type", analysis.inferred_error_type.replace("_", " ").title())
    col3.metric("Entities Detected", len(analysis.detected_entities))

    st.markdown("**Summary**")
    st.write(analysis.error_summary)

    # Detected entities
    if analysis.detected_entities:
        st.markdown("**Detected Columns/Datasets**")
        entity_rows = [
            {"name": e.name, "type": e.entity_type, "confidence": f"{e.confidence * 100:.0f}%"}
            for e in analysis.detected_entities
        ]
        st.table(entity_rows)

    # Lineage checkpoints (recommendations)
    st.markdown("**Recommended Lineage Checks**")
    if analysis.lineage_checkpoints:
        for i, checkpoint in enumerate(analysis.lineage_checkpoints, 1):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.markdown(f"**{i}. {checkpoint.description}**")
                st.caption(f"Rationale: {checkpoint.rationale}")

                # Show affected entities
                if checkpoint.target_entities:
                    affected = ", ".join(f"`{e.name}`" for e in checkpoint.target_entities[:5])
                    if len(checkpoint.target_entities) > 5:
                        affected += f", +{len(checkpoint.target_entities) - 5} more"
                    st.caption(f"Affects: {affected}")
            with col2:
                sev_icon = severity_icon.get(checkpoint.severity.value, "⚪")
                st.caption(f"{sev_icon} {checkpoint.severity.value}")
    else:
        st.info("No specific checkpoints recommended for this error type.")

    # Actual column lineage for any column(s) this error mentions - computed against
    # the real uploaded data flow(s)/pipeline(s), not a generic recommendation.
    st.markdown("**Column Lineage**")
    if analysis.suggested_lineage:
        st.caption(
            "Real lineage for the column(s) detected in this error, from your uploaded artifacts."
        )
        try:
            lineage = service.combined_lineage(session)
        except Exception as e:
            st.warning(f"Could not rebuild the lineage graph to render this: {e}")
            lineage = None
        for journey in analysis.suggested_lineage:
            st.markdown(f"**`{journey.stream}.{journey.column}`**")
            cols = st.columns(2)
            with cols[0]:
                st.caption("Origin(s)")
                st.write(", ".join(journey.origins) or "_this is a source column_")
            with cols[1]:
                st.caption("Sink columns reached")
                st.write(", ".join(journey.sink_columns) or "_none_")
            st.code(journey.narrative() or "(no upstream steps)", language="text")
            if lineage is not None:
                with st.expander(f"📊 Lineage graph for {journey.column}", expanded=False):
                    st.graphviz_chart(
                        service.lineage_dot(lineage, focus=journey.node_id, direction="both"),
                        use_container_width=True,
                    )
    elif session.artifacts and analysis.detected_entities:
        st.caption(
            "None of the detected entities matched a column in your uploaded "
            "artifacts — check the spelling, or upload the relevant data flow/pipeline."
        )
    else:
        st.caption("Upload the relevant data flow or pipeline to see real column lineage here.")

    # Additional notes
    if analysis.additional_notes:
        st.markdown("**Next Steps**")
        st.info(analysis.additional_notes)

    # Unrecognized pattern: let the user opt in to flagging it, or fall back to a
    # copy-to-clipboard nudge if the team hasn't configured error_misses_path.
    if analysis.inferred_error_type == "unknown_error":
        st.divider()
        st.markdown("**Unrecognized error pattern**")
        if service.error_misses_enabled:
            st.warning(
                "This error text didn't match any known pattern — the recommendation "
                "above is a generic fallback, not a specific diagnosis."
            )
            flagged_key = "error_analyzer_flagged_text"
            if st.session_state.get(flagged_key) == error_text:
                st.success("Flagged — thanks, this will help expand the analyzer.")
            elif st.button("🚩 Flag this error as unrecognized"):
                try:
                    service.flag_unrecognized_error(error_text)
                except Exception as e:
                    st.error(f"Couldn't save this: {e}")
                else:
                    st.session_state[flagged_key] = error_text
                    st.rerun()
        else:
            st.info(
                "This error text didn't match any known pattern. Copy it from "
                "'Show original error text' below and share it with your team so "
                "this pattern can be added."
            )

    # Show raw error for reference
    with st.expander("Show original error text"):
        st.code(analysis.error_text, language="text")

    st.divider()


PAGES = (
    "Dashboard",
    "Upload",
    "Pipeline Explorer",
    "Data Flow Explorer",
    "Column Lineage & Impact",
    "Inventory",
    "Documentation",
    "Error Analyzer",
    "AI Insights",
)

_DISPATCH = {
    "Dashboard": page_dashboard,
    "Upload": page_upload,
    "Pipeline Explorer": page_pipeline_explorer,
    "Data Flow Explorer": page_data_flow_explorer,
    "Column Lineage & Impact": page_column_lineage,
    "Inventory": page_inventory,
    "Documentation": page_documentation,
    "Error Analyzer": page_error_analyzer,
    "AI Insights": page_ai_insights,
}


def main() -> None:
    st.sidebar.markdown(f"## 🦁 {SETTINGS.app_title}")
    st.sidebar.caption("X-ray vision for Azure Data Factory")
    choice = st.sidebar.radio("Navigate", PAGES)
    st.sidebar.divider()
    st.sidebar.caption(
        "No Azure connection. Uploaded definitions are processed in memory and "
        "never persisted or sent to any external service."
    )

    st.markdown(
        f"""
        <div id="ing-header">
            <h1>{SETTINGS.app_title}</h1>
            <p>Deterministic lineage &amp; impact analysis for Azure Data Factory · {choice}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    service = get_service()
    session = get_session()
    _DISPATCH[choice](service, session)


main()
