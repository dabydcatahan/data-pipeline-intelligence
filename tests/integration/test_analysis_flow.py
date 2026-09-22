"""End-to-end: raw bytes in -> analysis session -> UI-ready summary out."""

import pytest
from app.services.analysis_service import AnalysisService, AnalysisSession

pytestmark = pytest.mark.integration


def test_full_upload_flow(read_fixture_bytes, settings):
    service = AnalysisService(settings)
    session = AnalysisSession()

    for name in (
        "pipeline_copy_direct.json",
        "pipeline_multi_activity.json",
        "pipeline_arm_form.json",
        "dataflow_select.json",
        "unknown_artifact.json",
        "not_json.txt",
    ):
        filename = name.replace(".txt", ".json")
        service.ingest(filename, read_fixture_bytes(name), session)

    stats = session.dashboard_stats()
    assert stats["pipelines"] == 3
    assert stats["data_flows"] == 1
    assert stats["failed_uploads"] == 2  # unknown_artifact + not_json

    summaries = [service.summarize_pipeline(p) for p in session.pipelines]
    names = {s.name for s in summaries}
    assert names == {"CopyCustomerPipeline", "CustomerLoad", "OrdersPipeline"}

    customer_load = next(s for s in summaries if s.name == "CustomerLoad")
    assert customer_load.execution_order[0] == "CopyRaw"
    assert customer_load.data_flow_references == ("CustomerDataFlow",)


def test_multiple_pipelines_and_dataflows_are_independent(read_fixture_bytes, settings):
    service = AnalysisService(settings)
    session = AnalysisSession()
    service.ingest("a.json", read_fixture_bytes("pipeline_cyclic.json"), session)
    service.ingest("b.json", read_fixture_bytes("pipeline_unknown_activity.json"), session)

    assert len(session.pipelines) == 2
    cyclic = session.get_pipeline("CyclicPipeline")
    assert cyclic.execution_order().has_cycle is True
    # A cycle in one pipeline does not stop the other from being summarized.
    other = session.get_pipeline("ExoticPipeline")
    assert AnalysisService.summarize_pipeline(other).activity_count == 2
