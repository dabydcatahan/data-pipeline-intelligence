"""End-to-end: upload a data flow -> build lineage -> answer the core question.

'Given a column name, explain where it originated, what happened to it, and where
it ended up.'
"""

import pytest
from app.services.analysis_service import AnalysisService, AnalysisSession

pytestmark = pytest.mark.integration


def test_customer_id_origin_journey_and_impact(read_fixture_bytes, settings):
    service = AnalysisService(settings)
    session = AnalysisSession()
    artifact = service.ingest(
        "customer_enrich.json", read_fixture_bytes("dataflow_customer_full.json"), session
    )
    assert artifact is not None

    lineage = service.data_flow_lineage(artifact, session=session)

    # Search: the derived business key resolves to exactly one node.
    (journey,) = service.explain_column(lineage, "CustomerSink.cust_key")

    # Origin
    assert journey.origins == ("CustomerSource::customer_id",)
    # It ended up in the sink
    assert journey.node_id == "CustomerSink::cust_key"
    # The transformations it passed through
    assert set(journey.transformations) >= {
        "DeriveCustomerKey",
        "RenameColumns",
        "JoinRegion",
        "FilterActive",
    }
    # The narrative mentions the derivation expression and the rename
    narrative = journey.narrative()
    assert "concat('CUST-', toString(customer_id))" in narrative
    assert "renamed" in narrative

    # Impact: changing the source customer_id affects these downstream columns
    (source_journey,) = service.explain_column(lineage, "CustomerSource.customer_id")
    assert "CustomerSink::cust_key" in source_journey.downstream_columns
    assert "CustomerSink::customer_id" in source_journey.downstream_columns
    assert "CustomerSink::cust_key" in source_journey.sink_columns


def test_cross_artifact_stitched_lineage(read_fixture_bytes, settings):
    service = AnalysisService(settings)
    session = AnalysisSession()
    service.ingest("p.json", read_fixture_bytes("pipeline_copy_translator.json"), session)
    service.ingest("df.json", read_fixture_bytes("dataflow_customer_full.json"), session)

    combined = service.combined_lineage(session)
    (raw,) = service.explain_column(combined, "RawCustomer.CustomerId")
    # traced across the pipeline Copy, the dataset handoff, and the data flow.
    # The data flow's own streams are namespaced by its name (CustomerEnrichFlow)
    # so they can never collide with another artifact's identically-named stream.
    assert "CustomerEnrichFlow/CustomerSink::cust_key" in raw.downstream_columns
    assert {"CopyRawToStage", "StageCustomer", "DeriveCustomerKey", "RenameColumns"} <= set(
        raw.transformations
    )
    (impact_report,) = service.impact(combined, "RawCustomer.CustomerId")
    assert impact_report.level.value in {"medium", "high"}


def test_lineage_survives_partial_information(read_fixture_bytes, settings):
    service = AnalysisService(settings)
    artifact = service.analyze_upload("exotic.json", read_fixture_bytes("dataflow_unknown_op.json"))
    lineage = service.data_flow_lineage(artifact)
    # Even through an unknown operator, the column is traced end to end...
    (journey,) = service.explain_column(lineage, "Snk.a")
    assert journey.origins == ("Src::a",)
    # ...but the engine is honest that the middle step is approximate.
    assert any("approximate" in w for w in lineage.warnings)
