"""Upload -> validate -> parse -> summarize orchestration (vertical slice 1)."""

from __future__ import annotations

import hashlib
import importlib.util
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.core.config import AppSettings, get_settings
from app.core.exceptions import ADFIntelligenceError, UnsupportedArtifactError
from app.core.logging import get_logger
from app.documentation.docx_export import data_flow_docx, pipeline_docx
from app.documentation.export import render_html
from app.documentation.generator import data_flow_markdown, pipeline_markdown
from app.insights import (
    column_request,
    data_flow_request,
    get_provider,
    pipeline_request,
)
from app.insights.interface import InsightResult
from app.lineage.column_lineage import DataFlowLineage, build_data_flow_lineage
from app.lineage.impact import analyze_impact
from app.lineage.pipeline_lineage import build_data_flow_chain, build_pipeline_lineage
from app.lineage.stitch import build_combined_lineage, data_flow_bindings, shared_datasets
from app.lineage.traversal import explain
from app.models.artifact import ArtifactKind, ParsedArtifact
from app.models.dataflow import DataFlow
from app.models.error_analysis import ErrorAnalysis
from app.models.impact import ImpactReport
from app.models.lineage import ColumnJourney
from app.models.pipeline import Pipeline
from app.parsers.base import ParseContext
from app.parsers.dataflow_parser import DataFlowParser
from app.parsers.json_loader import detect_artifact_kind, load_adf_json
from app.parsers.pipeline_parser import PipelineParser
from app.services.inventory import Inventory, build_inventory
from app.visualization.lineage import Direction, build_dot

logger = get_logger(__name__)


@dataclass(frozen=True)
class PipelineSummary:
    """A UI-ready summary of a single pipeline (F023)."""

    name: str
    description: str | None
    folder: str | None
    activity_count: int
    activity_types: dict[str, int]
    parameter_names: tuple[str, ...]
    variable_names: tuple[str, ...]
    annotations: tuple[str, ...]
    input_datasets: tuple[str, ...]
    output_datasets: tuple[str, ...]
    data_flow_references: tuple[str, ...]
    execution_order: tuple[str, ...]
    cyclic_activities: tuple[str, ...]
    dependency_edges: tuple[tuple[str, str], ...]
    unrecognized_activities: tuple[str, ...]

    @classmethod
    def from_pipeline(cls, pipeline: Pipeline) -> PipelineSummary:
        order = pipeline.execution_order()
        return cls(
            name=pipeline.name,
            description=pipeline.description,
            folder=pipeline.folder,
            activity_count=pipeline.activity_count,
            activity_types=pipeline.activity_types,
            parameter_names=tuple(p.name for p in pipeline.parameters),
            variable_names=tuple(v.name for v in pipeline.variables),
            annotations=pipeline.annotations,
            input_datasets=pipeline.input_datasets,
            output_datasets=pipeline.output_datasets,
            data_flow_references=pipeline.data_flow_references,
            execution_order=order.order,
            cyclic_activities=order.cyclic,
            dependency_edges=pipeline.dependency_edges(),
            unrecognized_activities=tuple(a.name for a in pipeline.unrecognized_activities),
        )


@dataclass(frozen=True)
class DataFlowChainStepStatus:
    """UI-ready status of one step in a pipeline's data-flow execution chain (F026)."""

    activity_name: str
    data_flow_reference: str
    order: int
    is_uploaded: bool
    upstream_data_flows: tuple[str, ...]
    #: subset of ``upstream_data_flows`` confirmed linked via a shared dataset
    linked_upstream: tuple[str, ...]
    #: subset of ``upstream_data_flows`` with no automatic dataset-level link found
    unlinked_upstream: tuple[str, ...]
    #: ``(upstream_data_flow_reference, shared_dataset_names)`` pairs
    shared_datasets: tuple[tuple[str, tuple[str, ...]], ...]

    @property
    def is_first(self) -> bool:
        return not self.upstream_data_flows


@dataclass(frozen=True)
class DataFlowTransformationRow:
    """One UI-ready row of a data flow's transformation table (F024)."""

    name: str
    operator: str
    inputs: tuple[str, ...]
    column_count: int
    group_by: tuple[str, ...]
    recognized: bool
    raw_arguments: str


@dataclass(frozen=True)
class DataFlowSummary:
    """A UI-ready summary of a single Mapping Data Flow (F024)."""

    name: str
    description: str | None
    source_count: int
    sink_count: int
    transformation_count: int
    sources: tuple[tuple[str, str, tuple[str, ...]], ...]  # (name, dataset, schema columns)
    sinks: tuple[tuple[str, str, str], ...]  # (name, dataset, feeding stream)
    transformations: tuple[DataFlowTransformationRow, ...]
    unrecognized_operators: tuple[str, ...]
    script_lines: tuple[str, ...]

    @classmethod
    def from_data_flow(cls, df: DataFlow) -> DataFlowSummary:
        return cls(
            name=df.name,
            description=df.description,
            source_count=df.source_count,
            sink_count=df.sink_count,
            transformation_count=df.transformation_count,
            sources=tuple(
                (s.name, s.dataset.name if s.dataset else "-", s.schema_columns) for s in df.sources
            ),
            sinks=tuple(
                (s.name, s.dataset.name if s.dataset else "-", s.input_stream or "-")
                for s in df.sinks
            ),
            transformations=tuple(
                DataFlowTransformationRow(
                    name=t.name,
                    operator=t.kind or "unknown",
                    inputs=t.input_streams,
                    column_count=len(t.columns),
                    group_by=t.group_by,
                    recognized=t.is_recognized,
                    raw_arguments=t.raw_arguments,
                )
                for t in df.transformations
            ),
            unrecognized_operators=df.unrecognized_operators,
            script_lines=df.script_lines,
        )


@dataclass
class AnalysisSession:
    """In-memory collection of everything analyzed so far (no persistence)."""

    artifacts: list[ParsedArtifact] = field(default_factory=list)
    #: filename -> error message for uploads that could not be processed.
    failures: dict[str, str] = field(default_factory=dict)
    #: content hash -> built data-flow lineage (built once per artifact).
    lineage_cache: dict[str, DataFlowLineage] = field(default_factory=dict)

    def add(self, artifact: ParsedArtifact) -> None:
        # De-duplicate by content hash so Streamlit reruns don't pile up copies.
        if any(a.content_sha256 == artifact.content_sha256 for a in self.artifacts):
            return
        self.artifacts.append(artifact)

    def remove(self, content_sha256: str) -> bool:
        """Drop one analyzed artifact (and its cached lineage). Returns True if removed."""
        before = len(self.artifacts)
        self.artifacts = [a for a in self.artifacts if a.content_sha256 != content_sha256]
        self.lineage_cache.pop(content_sha256, None)
        return len(self.artifacts) != before

    def clear(self) -> None:
        """Forget everything analyzed so far."""
        self.artifacts.clear()
        self.failures.clear()
        self.lineage_cache.clear()

    # -------------------------------------------------------------- accessors
    @property
    def pipelines(self) -> list[Pipeline]:
        return [a.pipeline for a in self.artifacts if a.pipeline is not None]

    def pipeline_artifacts(self) -> list[ParsedArtifact]:
        return [a for a in self.artifacts if a.kind is ArtifactKind.PIPELINE]

    def data_flow_artifacts(self) -> list[ParsedArtifact]:
        return [a for a in self.artifacts if a.kind is ArtifactKind.MAPPING_DATA_FLOW]

    def get_pipeline(self, name: str) -> Pipeline | None:
        return next((p for p in self.pipelines if p.name == name), None)

    def get_data_flow_artifact(self, name: str) -> ParsedArtifact | None:
        return next(
            (a for a in self.data_flow_artifacts() if a.name == name),
            None,
        )

    # ------------------------------------------------------- dashboard stats
    def dashboard_stats(self) -> dict[str, int]:
        pipelines = self.pipelines
        data_flows = [a.data_flow for a in self.artifacts if a.data_flow is not None]
        total_activities = sum(p.activity_count for p in pipelines)
        source_datasets: set[str] = set()
        sink_datasets: set[str] = set()
        for p in pipelines:
            source_datasets.update(p.input_datasets)
            sink_datasets.update(p.output_datasets)
        for df in data_flows:
            source_datasets.update(s.dataset.name for s in df.sources if s.dataset)
            sink_datasets.update(s.dataset.name for s in df.sinks if s.dataset)
        return {
            "files_analyzed": len(self.artifacts),
            "pipelines": len(pipelines),
            "data_flows": len(data_flows),
            "activities": total_activities,
            "transformations": sum(df.transformation_count for df in data_flows),
            "source_datasets": len(source_datasets),
            "sink_datasets": len(sink_datasets),
            "failed_uploads": len(self.failures),
        }


class AnalysisService:
    """Stateless orchestrator. Hold an :class:`AnalysisSession` for state."""

    def __init__(self, settings: AppSettings | None = None) -> None:
        self._settings = settings or get_settings()
        self._pipeline_parser = PipelineParser()
        self._data_flow_parser = DataFlowParser()
        self._insights = get_provider(self._settings)

    def analyze_upload(self, filename: str, data: bytes) -> ParsedArtifact:
        """Validate and parse one uploaded file into a :class:`ParsedArtifact`.

        Raises :class:`app.core.exceptions.ADFIntelligenceError` subclasses for
        anything the user needs to know about.
        """
        obj = load_adf_json(data, filename=filename, settings=self._settings)
        kind = detect_artifact_kind(obj)
        content_hash = hashlib.sha256(data).hexdigest()
        ctx = ParseContext()

        logger.info(
            "analyzing upload filename=%s kind=%s bytes=%d", filename, kind.value, len(data)
        )

        if kind is ArtifactKind.PIPELINE:
            pipeline, ctx = self._pipeline_parser.parse(obj, source_filename=filename, ctx=ctx)
            return ParsedArtifact(
                source_filename=filename,
                kind=kind,
                name=pipeline.name,
                content_sha256=content_hash,
                pipeline=pipeline,
                warnings=ctx.warnings,
            )

        if kind is ArtifactKind.MAPPING_DATA_FLOW:
            data_flow, ctx = self._data_flow_parser.parse(obj, source_filename=filename, ctx=ctx)
            return ParsedArtifact(
                source_filename=filename,
                kind=kind,
                name=data_flow.name,
                content_sha256=content_hash,
                data_flow=data_flow,
                warnings=ctx.warnings,
            )

        raise UnsupportedArtifactError(
            f"File '{filename}' is valid JSON but is not a recognised ADF Pipeline "
            "or Mapping Data Flow definition."
        )

    def ingest(self, filename: str, data: bytes, session: AnalysisSession) -> ParsedArtifact | None:
        """Analyze and add to ``session``; record failures instead of raising."""
        try:
            artifact = self.analyze_upload(filename, data)
        except ADFIntelligenceError as exc:
            logger.warning("upload failed filename=%s error=%s", filename, type(exc).__name__)
            session.failures[filename] = str(exc)
            return None
        session.add(artifact)
        session.failures.pop(filename, None)
        return artifact

    @staticmethod
    def summarize_pipeline(pipeline: Pipeline) -> PipelineSummary:
        return PipelineSummary.from_pipeline(pipeline)

    @staticmethod
    def summarize_data_flow(data_flow: DataFlow) -> DataFlowSummary:
        return DataFlowSummary.from_data_flow(data_flow)

    @staticmethod
    def inventory(session: AnalysisSession) -> Inventory:
        """Cross-artifact source / sink / transformation / dataset inventory (F005-F007)."""
        data_flows = [a.data_flow for a in session.data_flow_artifacts() if a.data_flow is not None]
        return build_inventory(session.pipelines, data_flows)

    # ------------------------------------------------------------- lineage
    def data_flow_lineage(
        self,
        artifact: ParsedArtifact,
        *,
        session: AnalysisSession | None = None,
    ) -> DataFlowLineage:
        """Build (or fetch from ``session`` cache) the column lineage for a data flow."""
        if artifact.data_flow is None:
            raise ValueError(f"Artifact '{artifact.name}' is not a Mapping Data Flow.")
        if session is not None:
            cached = session.lineage_cache.get(artifact.content_sha256)
            if cached is not None:
                return cached
        result = build_data_flow_lineage(artifact.data_flow)
        if session is not None:
            session.lineage_cache[artifact.content_sha256] = result
        return result

    @staticmethod
    def pipeline_lineage(pipeline: Pipeline) -> DataFlowLineage:
        """Column lineage derived from a pipeline's Copy-activity mappings (F008)."""
        return build_pipeline_lineage(pipeline)

    def data_flow_chain_status(
        self, pipeline: Pipeline, session: AnalysisSession
    ) -> tuple[DataFlowChainStepStatus, ...]:
        """Execution-order chain of a pipeline's ``ExecuteDataFlow`` activities (F026).

        Answers "if this pipeline runs multiple data flows, what feeds what":
        each step reports whether its data flow was uploaded, and whether each
        upstream data flow it depends on is already linked to it through a
        shared dataset (i.e. the earlier data flow's sink is this one's source).
        """
        steps = build_data_flow_chain(pipeline)
        flows_by_name: dict[str, DataFlow] = {
            artifact.data_flow.name: artifact.data_flow
            for artifact in session.data_flow_artifacts()
            if artifact.data_flow is not None
        }

        statuses: list[DataFlowChainStepStatus] = []
        for step in steps:
            current_flow = flows_by_name.get(step.data_flow_reference)
            linked: list[str] = []
            unlinked: list[str] = []
            shared: list[tuple[str, tuple[str, ...]]] = []
            for upstream_ref in step.upstream_data_flows:
                upstream_flow = flows_by_name.get(upstream_ref)
                datasets = (
                    shared_datasets(upstream_flow, current_flow)
                    if upstream_flow is not None and current_flow is not None
                    else ()
                )
                if datasets:
                    linked.append(upstream_ref)
                    shared.append((upstream_ref, datasets))
                else:
                    unlinked.append(upstream_ref)
            statuses.append(
                DataFlowChainStepStatus(
                    activity_name=step.activity_name,
                    data_flow_reference=step.data_flow_reference,
                    order=step.order,
                    is_uploaded=current_flow is not None,
                    upstream_data_flows=step.upstream_data_flows,
                    linked_upstream=tuple(linked),
                    unlinked_upstream=tuple(unlinked),
                    shared_datasets=tuple(shared),
                )
            )
        return tuple(statuses)

    def combined_lineage(self, session: AnalysisSession) -> DataFlowLineage:
        """Merge every pipeline + data-flow graph and stitch by dataset identity (F008)."""
        lineages: list[DataFlowLineage] = [self.pipeline_lineage(p) for p in session.pipelines]
        for artifact in session.data_flow_artifacts():
            lineage = self.data_flow_lineage(artifact, session=session)
            if artifact.data_flow is not None:
                lineage = replace(lineage, bindings=data_flow_bindings(artifact.data_flow))
            lineages.append(lineage)
        return build_combined_lineage(lineages)

    @staticmethod
    def explain_column(lineage: DataFlowLineage, query: str) -> list[ColumnJourney]:
        """Return one :class:`ColumnJourney` per column node matching ``query``."""
        return [explain(lineage.graph, node_id) for node_id in lineage.graph.find(query)]

    @staticmethod
    def impact(lineage: DataFlowLineage, query: str) -> list[ImpactReport]:
        """Return one :class:`ImpactReport` per column node matching ``query``."""
        return [analyze_impact(lineage.graph, node_id) for node_id in lineage.graph.find(query)]

    @staticmethod
    def list_columns(lineage: DataFlowLineage) -> list[str]:
        """Distinct column names in the lineage graph (for a search picker)."""
        return sorted({node.column for node in lineage.graph.iter_nodes()})

    @staticmethod
    def search_columns(lineage: DataFlowLineage, query: str) -> list[str]:
        """Node ids matching a bare or qualified column name."""
        return lineage.graph.find(query)

    # ------------------------------------------------------- documentation
    @staticmethod
    def document_pipeline(pipeline: Pipeline) -> str:
        """Markdown documentation for a pipeline (F013)."""
        return pipeline_markdown(pipeline)

    def document_data_flow(
        self,
        artifact: ParsedArtifact,
        *,
        session: AnalysisSession | None = None,
        include_lineage: bool = True,
    ) -> str:
        """Markdown documentation for a Mapping Data Flow (F014)."""
        if artifact.data_flow is None:
            raise ValueError(f"Artifact '{artifact.name}' is not a Mapping Data Flow.")
        lineage = self.data_flow_lineage(artifact, session=session) if include_lineage else None
        return data_flow_markdown(artifact.data_flow, lineage)

    @staticmethod
    def export_html(markdown: str, *, title: str) -> str:
        """Wrap generated Markdown documentation in a standalone HTML page (F018)."""
        return render_html(markdown, title=title)

    @staticmethod
    def docx_export_available() -> bool:
        """Whether the optional 'docx' extra (python-docx) is installed."""
        return importlib.util.find_spec("docx") is not None

    @staticmethod
    def document_pipeline_docx(pipeline: Pipeline) -> bytes:
        """Styled Word (.docx) documentation for a pipeline. Needs the 'docx' extra."""
        return pipeline_docx(pipeline)

    def document_data_flow_docx(
        self,
        artifact: ParsedArtifact,
        *,
        session: AnalysisSession | None = None,
        include_lineage: bool = True,
    ) -> bytes:
        """Styled Word (.docx) documentation for a data flow. Needs the 'docx' extra."""
        if artifact.data_flow is None:
            raise ValueError(f"Artifact '{artifact.name}' is not a Mapping Data Flow.")
        lineage = self.data_flow_lineage(artifact, session=session) if include_lineage else None
        return data_flow_docx(artifact.data_flow, lineage)

    # ------------------------------------------------------------- insights
    @property
    def insight_provider_name(self) -> str:
        return self._insights.name

    def insights_for_pipeline(self, pipeline: Pipeline) -> InsightResult:
        return self._insights.generate(pipeline_request(self.summarize_pipeline(pipeline)))

    def insights_for_data_flow(
        self,
        artifact: ParsedArtifact,
        *,
        session: AnalysisSession | None = None,
    ) -> InsightResult:
        if artifact.data_flow is None:
            raise ValueError(f"Artifact '{artifact.name}' is not a Mapping Data Flow.")
        lineage = self.data_flow_lineage(artifact, session=session)
        return self._insights.generate(data_flow_request(artifact.data_flow, lineage))

    def insights_for_column(self, lineage: DataFlowLineage, node_id: str) -> InsightResult:
        journey = explain(lineage.graph, node_id)
        impact = analyze_impact(lineage.graph, node_id)
        return self._insights.generate(column_request(journey, impact))

    def analyze_error(
        self,
        error_text: str,
        session: AnalysisSession | None = None,
    ) -> ErrorAnalysis:
        """Analyze an ADF error and recommend lineage checkpoints to inspect.

        Args:
            error_text: The ADF error message or output
            session: Optional AnalysisSession for lineage context

        Returns:
            ErrorAnalysis with detected entities and checkpoints
        """
        from app.insights.error_analyzer import analyze_error

        # Try to build a combined graph from loaded artifacts (optional context)
        graph = None
        if session and session.artifacts:
            try:
                lineage = self.combined_lineage(session)
                graph = lineage.graph
            except Exception:
                # If lineage building fails, continue without graph context
                pass

        return analyze_error(error_text, graph=graph)

    @property
    def error_misses_enabled(self) -> bool:
        """Whether the opt-in "flag an unrecognized error" feature is configured."""
        return bool(self._settings.error_misses_path)

    def flag_unrecognized_error(self, error_text: str) -> None:
        """Record an unrecognized error for later pattern review (F025, opt-in).

        Only ever called on explicit user action from the UI. Raises if
        ``error_misses_path`` isn't configured or the write fails - the caller
        decides how to surface that.
        """
        if not self._settings.error_misses_path:
            raise ValueError("error_misses_path is not configured; flagging is disabled.")

        from app.insights.error_feedback import record_unrecognized_error

        record_unrecognized_error(error_text, Path(self._settings.error_misses_path))

    @staticmethod
    def lineage_dot(
        lineage: DataFlowLineage,
        *,
        focus: str | None = None,
        direction: Direction = "both",
        level: str | None = None,
        critical_path: tuple[str, ...] | None = None,
    ) -> str:
        """Graphviz DOT for the data flow, optionally focused on a column.

        ``focus`` may be a node id (``"stream::column"``) or a column name /
        qualified name; the first matching node is used. ``level`` colours the
        focus node by impact severity; ``critical_path`` highlights the
        riskiest route to a sink (see :class:`~app.models.impact.ImpactReport`).
        """
        resolved = focus
        if focus is not None and focus not in lineage.graph:
            matches = lineage.graph.find(focus)
            if not matches:
                raise KeyError(f"no column matching {focus!r}")
            resolved = matches[0]
        return build_dot(
            lineage.graph,
            focus=resolved,
            direction=direction,
            level=level,
            critical_path=critical_path,
        )
