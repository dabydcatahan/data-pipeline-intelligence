import pytest
from app.core.exceptions import InvalidJsonError, UnsupportedArtifactError
from app.models.artifact import ArtifactKind
from app.services.analysis_service import AnalysisService, AnalysisSession


@pytest.fixture
def service(settings):
    return AnalysisService(settings)


def test_analyze_pipeline_upload(service, read_fixture_bytes):
    artifact = service.analyze_upload(
        "pipeline_multi_activity.json", read_fixture_bytes("pipeline_multi_activity.json")
    )
    assert artifact.kind is ArtifactKind.PIPELINE
    assert artifact.name == "CustomerLoad"
    assert artifact.pipeline is not None
    assert artifact.content_sha256


def test_analyze_dataflow_upload(service, read_fixture_bytes):
    artifact = service.analyze_upload(
        "dataflow_select.json", read_fixture_bytes("dataflow_select.json")
    )
    assert artifact.kind is ArtifactKind.MAPPING_DATA_FLOW
    assert artifact.data_flow is not None
    assert artifact.data_flow.source_count == 1
    assert artifact.data_flow.sink_count == 1


def test_unknown_artifact_raises(service, read_fixture_bytes):
    with pytest.raises(UnsupportedArtifactError):
        service.analyze_upload("unknown_artifact.json", read_fixture_bytes("unknown_artifact.json"))


def test_ingest_records_failures_without_raising(service, read_fixture_bytes):
    session = AnalysisSession()
    ok = service.ingest(
        "pipeline_copy_direct.json", read_fixture_bytes("pipeline_copy_direct.json"), session
    )
    bad = service.ingest("not_json.json", b"nonsense", session)
    assert ok is not None
    assert bad is None
    assert "not_json.json" in session.failures
    assert len(session.artifacts) == 1


def test_session_dedupes_by_content_hash(service, read_fixture_bytes):
    session = AnalysisSession()
    data = read_fixture_bytes("pipeline_copy_direct.json")
    service.ingest("a.json", data, session)
    service.ingest("a-copy.json", data, session)
    assert len(session.artifacts) == 1


def test_session_remove_and_clear(service, read_fixture_bytes):
    session = AnalysisSession()
    p = service.ingest("p.json", read_fixture_bytes("pipeline_copy_direct.json"), session)
    df = service.ingest("df.json", read_fixture_bytes("dataflow_customer_full.json"), session)
    service.data_flow_lineage(df, session=session)  # populate lineage cache
    session.failures["bad.json"] = "boom"

    assert session.remove(p.content_sha256) is True
    assert session.remove("nope") is False
    assert [a.name for a in session.artifacts] == [df.name]
    assert df.content_sha256 in session.lineage_cache

    session.clear()
    assert session.artifacts == []
    assert session.failures == {}
    assert session.lineage_cache == {}


def test_dashboard_stats_aggregate(service, read_fixture_bytes):
    session = AnalysisSession()
    service.ingest("p.json", read_fixture_bytes("pipeline_multi_activity.json"), session)
    service.ingest("df.json", read_fixture_bytes("dataflow_select.json"), session)
    stats = session.dashboard_stats()
    assert stats["files_analyzed"] == 2
    assert stats["pipelines"] == 1
    assert stats["data_flows"] == 1
    assert stats["activities"] == 3
    assert stats["source_datasets"] >= 1
    assert stats["sink_datasets"] >= 1


def test_summarize_pipeline(service, read_fixture_bytes):
    artifact = service.analyze_upload(
        "pipeline_multi_activity.json", read_fixture_bytes("pipeline_multi_activity.json")
    )
    summary = service.summarize_pipeline(artifact.pipeline)
    assert summary.name == "CustomerLoad"
    assert summary.execution_order == ("CopyRaw", "TransformCustomer", "RefreshStats")
    assert summary.data_flow_references == ("CustomerDataFlow",)
    assert "Copy" in summary.activity_types


def test_invalid_json_bubbles_as_upload_error(service):
    with pytest.raises(InvalidJsonError):
        service.analyze_upload("x.json", b"{not json")


def test_data_flow_lineage_is_cached_on_session(service, read_fixture_bytes):
    session = AnalysisSession()
    artifact = service.ingest("df.json", read_fixture_bytes("dataflow_customer_full.json"), session)
    first = service.data_flow_lineage(artifact, session=session)
    second = service.data_flow_lineage(artifact, session=session)
    assert first is second
    assert artifact.content_sha256 in session.lineage_cache


def test_explain_column_returns_journey_per_match(service, read_fixture_bytes):
    artifact = service.analyze_upload("df.json", read_fixture_bytes("dataflow_customer_full.json"))
    lineage = service.data_flow_lineage(artifact)
    journeys = service.explain_column(lineage, "CustomerSink.cust_key")
    assert len(journeys) == 1
    assert journeys[0].origins == ("CustomerSource::customer_id",)

    all_customer_id = service.explain_column(lineage, "customer_id")
    assert len(all_customer_id) > 1  # one per stream that has the column


def test_data_flow_lineage_rejects_pipeline(service, read_fixture_bytes):
    artifact = service.analyze_upload("p.json", read_fixture_bytes("pipeline_copy_direct.json"))
    with pytest.raises(ValueError, match="not a Mapping Data Flow"):
        service.data_flow_lineage(artifact)


def test_impact_returns_report_per_match(service, read_fixture_bytes):
    artifact = service.analyze_upload("df.json", read_fixture_bytes("dataflow_customer_full.json"))
    lineage = service.data_flow_lineage(artifact)
    (report,) = service.impact(lineage, "CustomerSource.customer_id")
    assert report.level.value == "high"
    assert report.sink_columns
    assert "raw score" in report.factors[-1]


def test_inventory_aggregates_the_session(service, read_fixture_bytes):
    session = AnalysisSession()
    service.ingest("p.json", read_fixture_bytes("pipeline_copy_translator.json"), session)
    service.ingest("df.json", read_fixture_bytes("dataflow_customer_full.json"), session)
    inv = service.inventory(session)
    assert not inv.is_empty
    assert any(d.name == "StageCustomer" for d in inv.datasets)
    assert any(s.name == "CustomerSink" for s in inv.sinks)


def test_lineage_dot_resolves_focus_query(service, read_fixture_bytes):
    artifact = service.analyze_upload("df.json", read_fixture_bytes("dataflow_customer_full.json"))
    lineage = service.data_flow_lineage(artifact)
    dot = service.lineage_dot(lineage, focus="RenameColumns.cust_key")
    assert dot.startswith("digraph lineage {")
    assert "focus: RenameColumns.cust_key" in dot

    whole = service.lineage_dot(lineage)
    assert 'subgraph "cluster_CustomerSource"' in whole


def test_data_flow_chain_status_flags_uploaded_and_linked_steps(service, read_fixture_bytes):
    session = AnalysisSession()
    pipeline_artifact = service.ingest(
        "p.json", read_fixture_bytes("pipeline_chained_data_flows.json"), session
    )
    service.ingest("enrich.json", read_fixture_bytes("dataflow_customer_full.json"), session)
    # PublishCustomerFlow is intentionally NOT uploaded, to exercise the "missing" path.

    statuses = service.data_flow_chain_status(pipeline_artifact.pipeline, session)
    assert [s.data_flow_reference for s in statuses] == [
        "CustomerEnrichFlow",
        "PublishCustomerFlow",
    ]

    first, second = statuses
    assert first.is_first
    assert first.is_uploaded

    assert second.is_uploaded is False
    assert second.upstream_data_flows == ("CustomerEnrichFlow",)
    # can't confirm the dataset link because PublishCustomerFlow was never uploaded
    assert second.linked_upstream == ()
    assert second.unlinked_upstream == ("CustomerEnrichFlow",)


def test_data_flow_chain_status_confirms_dataset_link_when_both_uploaded(
    service, read_fixture_bytes
):
    session = AnalysisSession()
    pipeline_artifact = service.ingest(
        "p.json", read_fixture_bytes("pipeline_chained_data_flows.json"), session
    )
    service.ingest("enrich.json", read_fixture_bytes("dataflow_customer_full.json"), session)
    service.ingest("publish.json", read_fixture_bytes("dataflow_publish_customer.json"), session)

    statuses = service.data_flow_chain_status(pipeline_artifact.pipeline, session)
    second = statuses[1]
    assert second.is_uploaded
    assert second.linked_upstream == ("CustomerEnrichFlow",)
    assert second.unlinked_upstream == ()
    assert second.shared_datasets == (("CustomerEnrichFlow", ("DimCustomer",)),)
