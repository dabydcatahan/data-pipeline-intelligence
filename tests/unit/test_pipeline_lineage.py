import json

from app.lineage import build_combined_lineage, build_pipeline_lineage, explain
from app.lineage.pipeline_lineage import build_data_flow_chain
from app.lineage.stitch import data_flow_bindings, shared_datasets
from app.parsers.dataflow_parser import DataFlowParser
from app.parsers.pipeline_parser import PipelineParser


def _pipeline(read_fixture_bytes, name):
    p, _ = PipelineParser().parse(json.loads(read_fixture_bytes(name)))
    return p


def test_pipeline_lineage_from_copy_translator(read_fixture_bytes):
    lineage = build_pipeline_lineage(_pipeline(read_fixture_bytes, "pipeline_copy_translator.json"))
    assert lineage.kind == "pipeline"
    graph = lineage.graph
    edge = graph.edge("RawCustomer::CustomerId", "StageCustomer::customer_id")
    assert edge.relationship.value == "rename"
    assert edge.transformation == "CopyRawToStage"
    assert edge.transformation_type == "Copy"
    assert ("RawCustomer", "RawCustomer", "reader") in lineage.bindings
    assert ("StageCustomer", "StageCustomer", "writer") in lineage.bindings
    assert lineage.warnings == ()


def test_pipeline_lineage_warns_when_no_mapping(read_fixture_bytes):
    lineage = build_pipeline_lineage(_pipeline(read_fixture_bytes, "pipeline_multi_activity.json"))
    assert len(lineage.graph) == 0
    assert any("no explicit column mapping" in w for w in lineage.warnings)


def test_data_flow_bindings(read_fixture_bytes):
    df, _ = DataFlowParser().parse(json.loads(read_fixture_bytes("dataflow_customer_full.json")))
    bindings = data_flow_bindings(df)
    assert ("CustomerSource", "StageCustomer", "reader") in bindings
    assert ("CustomerSink", "DimCustomer", "writer") in bindings


def test_stitch_links_pipeline_to_data_flow_by_dataset(read_fixture_bytes):
    pl = build_pipeline_lineage(_pipeline(read_fixture_bytes, "pipeline_copy_translator.json"))
    df, _ = DataFlowParser().parse(json.loads(read_fixture_bytes("dataflow_customer_full.json")))
    from app.lineage import build_data_flow_lineage

    dl = build_data_flow_lineage(df)
    combined = build_combined_lineage([pl, dl], list(data_flow_bindings(df)))

    assert combined.kind == "combined"
    assert any("cross-artifact column link" in w for w in combined.warnings)
    # the dataset handoff edge exists
    handoff = combined.graph.edge("StageCustomer::customer_id", "CustomerSource::customer_id")
    assert handoff.transformation_type == "dataset"

    # end-to-end: a raw source column reaches the final data-flow sink column
    (node_id,) = combined.graph.find("RawCustomer.CustomerId")
    journey = explain(combined.graph, node_id)
    assert "CustomerSink::cust_key" in journey.downstream_columns


def test_data_flow_chain_orders_and_links_activities_through_a_gap(read_fixture_bytes):
    """Two ExecuteDataFlow activities with a stored-proc step in between."""
    pipeline = _pipeline(read_fixture_bytes, "pipeline_chained_data_flows.json")
    steps = build_data_flow_chain(pipeline)

    assert [s.activity_name for s in steps] == ["RunEnrichFlow", "RunPublishFlow"]
    assert [s.data_flow_reference for s in steps] == ["CustomerEnrichFlow", "PublishCustomerFlow"]

    first, second = steps
    assert first.upstream_data_flows == ()
    # NotifyStart (a stored proc) sits in between, but is not a data flow itself,
    # so RunPublishFlow's nearest upstream data flow is still CustomerEnrichFlow.
    assert second.upstream_data_flows == ("CustomerEnrichFlow",)


def test_shared_datasets_confirms_continuity_between_chained_data_flows(read_fixture_bytes):
    enrich, _ = DataFlowParser().parse(
        json.loads(read_fixture_bytes("dataflow_customer_full.json"))
    )
    publish, _ = DataFlowParser().parse(
        json.loads(read_fixture_bytes("dataflow_publish_customer.json"))
    )

    assert shared_datasets(enrich, publish) == ("DimCustomer",)
    assert shared_datasets(publish, enrich) == ()
