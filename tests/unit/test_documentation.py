import json

import pytest
from app.documentation import data_flow_markdown, pipeline_markdown
from app.lineage import build_data_flow_lineage
from app.parsers.dataflow_parser import DataFlowParser
from app.parsers.pipeline_parser import PipelineParser


def _pipeline(read_fixture_bytes, name):
    p, _ = PipelineParser().parse(json.loads(read_fixture_bytes(name)))
    return p


def _data_flow(read_fixture_bytes, name):
    df, _ = DataFlowParser().parse(json.loads(read_fixture_bytes(name)))
    return df


def test_pipeline_markdown_sections(read_fixture_bytes):
    md = pipeline_markdown(_pipeline(read_fixture_bytes, "pipeline_multi_activity.json"))
    assert md.startswith("# Pipeline: CustomerLoad")
    for heading in (
        "## Overview",
        "### Parameters",
        "## Execution Flow",
        "## Activities",
        "## Data Sources",
        "## Data Targets",
        "## Referenced Data Flows",
    ):
        assert heading in md
    assert "| runDate | String | 2026-01-01 |" in md
    assert "CopyRaw → TransformCustomer → RefreshStats" in md
    assert "CustomerDataFlow" in md


def test_pipeline_markdown_flags_cycles(read_fixture_bytes):
    md = pipeline_markdown(_pipeline(read_fixture_bytes, "pipeline_cyclic.json"))
    assert "Circular dependency" in md


def test_pipeline_markdown_does_not_leak_scrubbed_secrets(read_fixture_bytes):
    md = pipeline_markdown(_pipeline(read_fixture_bytes, "pipeline_multi_activity.json"))
    assert "Password" not in md
    assert "connectionString" not in md
    assert "redacted" not in md


def test_data_flow_markdown_without_lineage(read_fixture_bytes):
    md = data_flow_markdown(_data_flow(read_fixture_bytes, "dataflow_customer_full.json"))
    assert md.startswith("# Mapping Data Flow: CustomerEnrichFlow")
    assert "## Sources" in md and "## Sinks" in md and "## Transformations" in md
    assert "### DeriveCustomerKey (derive)" in md
    assert "| customer_key | derived | customer_id | high |" in md
    assert "## Column Lineage" not in md


def test_data_flow_markdown_with_lineage(read_fixture_bytes):
    df = _data_flow(read_fixture_bytes, "dataflow_customer_full.json")
    lineage = build_data_flow_lineage(df)
    md = data_flow_markdown(df, lineage)
    assert "## Column Lineage" in md
    assert "| CustomerSink.cust_key | CustomerSource.customer_id |" in md
    assert "### Detailed paths" in md
    assert "concat('CUST-', toString(customer_id))" in md


def test_documentation_is_deterministic(read_fixture_bytes):
    df = _data_flow(read_fixture_bytes, "dataflow_customer_full.json")
    lineage = build_data_flow_lineage(df)
    assert data_flow_markdown(df, lineage) == data_flow_markdown(df, lineage)


def test_unrecognized_operator_is_noted(read_fixture_bytes):
    md = data_flow_markdown(_data_flow(read_fixture_bytes, "dataflow_unknown_op.json"))
    assert "unrecognized operator" in md


@pytest.fixture
def service(settings):
    from app.services.analysis_service import AnalysisService

    return AnalysisService(settings)


def test_service_documents_pipeline_and_data_flow(service, read_fixture_bytes):
    p_artifact = service.analyze_upload(
        "p.json", read_fixture_bytes("pipeline_multi_activity.json")
    )
    assert service.document_pipeline(p_artifact.pipeline).startswith("# Pipeline:")

    df_artifact = service.analyze_upload(
        "df.json", read_fixture_bytes("dataflow_customer_full.json")
    )
    doc = service.document_data_flow(df_artifact, include_lineage=True)
    assert "## Column Lineage" in doc
    no_lin = service.document_data_flow(df_artifact, include_lineage=False)
    assert "## Column Lineage" not in no_lin

    with pytest.raises(ValueError, match="not a Mapping Data Flow"):
        service.document_data_flow(p_artifact)
