import json

import pytest
from app.core.config import AppSettings
from app.insights import (
    DeterministicInsightProvider,
    column_request,
    data_flow_request,
    get_provider,
    pipeline_request,
)
from app.insights.interface import InsightRequest
from app.lineage import analyze_impact, build_data_flow_lineage, explain
from app.parsers.dataflow_parser import DataFlowParser
from app.parsers.pipeline_parser import PipelineParser
from app.services.analysis_service import AnalysisService

DET = DeterministicInsightProvider()


def _pipeline_summary(read_fixture_bytes, name):
    p, _ = PipelineParser().parse(json.loads(read_fixture_bytes(name)))
    return AnalysisService.summarize_pipeline(p)


def _data_flow(read_fixture_bytes, name):
    df, _ = DataFlowParser().parse(json.loads(read_fixture_bytes(name)))
    return df


# ------------------------------------------------------------------ sanitize
def test_requests_carry_only_structural_facts(read_fixture_bytes):
    df = _data_flow(read_fixture_bytes, "dataflow_customer_full.json")
    req = data_flow_request(df, build_data_flow_lineage(df))
    blob = json.dumps(req.facts, default=str)
    assert "***redacted***" not in blob
    assert req.facts["kind"] == "data_flow"
    assert req.facts["source_count"] == 2
    assert "edge_relationship_counts" in req.facts


def test_column_request_shape(read_fixture_bytes):
    df = _data_flow(read_fixture_bytes, "dataflow_customer_full.json")
    graph = build_data_flow_lineage(df).graph
    (node_id,) = graph.find("CustomerSink.cust_key")
    req = column_request(explain(graph, node_id), analyze_impact(graph, node_id))
    assert req.subject == "column:CustomerSink.cust_key"
    assert req.facts["impact_level"] == "low"  # terminal sink column
    assert req.facts["origins"] == ["CustomerSource.customer_id"]
    assert req.facts["narrative"].startswith("SOURCE  CustomerSource.customer_id")


# -------------------------------------------------------------- deterministic
def test_deterministic_pipeline_insight_flags_cycle(read_fixture_bytes):
    req = pipeline_request(_pipeline_summary(read_fixture_bytes, "pipeline_cyclic.json"))
    result = DET.generate(req)
    assert result.provider == "deterministic"
    assert "CyclicPipeline" in result.summary
    assert any("Circular" in r for r in result.risks)


def test_deterministic_data_flow_insight_flags_unknown_operator(read_fixture_bytes):
    df = _data_flow(read_fixture_bytes, "dataflow_unknown_op.json")
    result = DET.generate(data_flow_request(df, build_data_flow_lineage(df)))
    assert any("approximate" in r for r in result.risks)
    assert "ExoticFlow" in result.summary


def test_deterministic_column_insight_is_specific(read_fixture_bytes):
    df = _data_flow(read_fixture_bytes, "dataflow_customer_full.json")
    graph = build_data_flow_lineage(df).graph
    (node_id,) = graph.find("CustomerSource.customer_id")
    result = DET.generate(column_request(explain(graph, node_id), analyze_impact(graph, node_id)))
    assert "HIGH" in result.summary
    assert result.recommendations
    assert any("join" in r.lower() for r in result.risks)


def test_deterministic_is_deterministic(read_fixture_bytes):
    req = pipeline_request(_pipeline_summary(read_fixture_bytes, "pipeline_multi_activity.json"))
    assert DET.generate(req).model_dump() == DET.generate(req).model_dump()


def test_deterministic_unknown_subject():
    result = DET.generate(InsightRequest(subject="mystery:x", facts={"kind": "mystery"}))
    assert "No deterministic template" in result.summary


# ------------------------------------------------------------------ wiring
def test_get_provider_is_deterministic():
    provider = get_provider(AppSettings(_env_file=None))
    assert isinstance(provider, DeterministicInsightProvider)


def test_service_insight_methods(read_fixture_bytes, settings):
    service = AnalysisService(settings)
    p = service.analyze_upload("p.json", read_fixture_bytes("pipeline_multi_activity.json"))
    assert service.insights_for_pipeline(p.pipeline).summary
    df = service.analyze_upload("df.json", read_fixture_bytes("dataflow_customer_full.json"))
    assert "Data flow" in service.insights_for_data_flow(df).summary
    with pytest.raises(ValueError, match="not a Mapping Data Flow"):
        service.insights_for_data_flow(p)
