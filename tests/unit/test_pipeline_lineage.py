import json
from dataclasses import replace

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

    dl = replace(build_data_flow_lineage(df), bindings=data_flow_bindings(df))
    combined = build_combined_lineage([pl, dl])

    assert combined.kind == "combined"
    assert any("cross-artifact column link" in w for w in combined.warnings)
    # the dataset handoff edge exists - the data flow's own streams are namespaced
    # by its name (CustomerEnrichFlow) so they can never collide with another
    # artifact's identically-named stream; the pipeline side is left as the
    # dataset name it already is (stitching keys off shared dataset identity).
    handoff = combined.graph.edge(
        "StageCustomer::customer_id", "CustomerEnrichFlow/CustomerSource::customer_id"
    )
    assert handoff.transformation_type == "dataset"

    # end-to-end: a raw source column reaches the final data-flow sink column
    (node_id,) = combined.graph.find("RawCustomer.CustomerId")
    journey = explain(combined.graph, node_id)
    assert "CustomerEnrichFlow/CustomerSink::cust_key" in journey.downstream_columns


def test_combined_lineage_does_not_merge_unrelated_data_flows_with_the_same_stream_names():
    """Two unrelated data flows reusing ADF's own default stream names.

    ADF Studio assigns generic defaults (``source1``, ``sink1``, ``Derive1``,
    ...) whenever a user doesn't rename a transformation, so two completely
    unrelated data flows sharing those names is common, not a contrived edge
    case. Before namespacing streams by their owning data flow, stitching two
    such flows together silently merged their columns into one node - a
    customer id and an unrelated order id would both land on a transformation
    literally named ``sink1`` and look like the same column.
    """
    flow_a = {
        "name": "CustomerFlow",
        "properties": {
            "type": "MappingDataFlow",
            "typeProperties": {
                "sources": [
                    {
                        "name": "source1",
                        "dataset": {"referenceName": "DsA", "type": "DatasetReference"},
                    }
                ],
                "sinks": [
                    {
                        "name": "sink1",
                        "dataset": {"referenceName": "OutA", "type": "DatasetReference"},
                    }
                ],
                "transformations": [],
                "scriptLines": [
                    "source(output(id as string)) ~> source1",
                    "source1 sink(allowSchemaDrift: true) ~> sink1",
                ],
            },
        },
    }
    flow_b = {
        "name": "OrderFlow",
        "properties": {
            "type": "MappingDataFlow",
            "typeProperties": {
                "sources": [
                    {
                        "name": "source1",
                        "dataset": {"referenceName": "DsB", "type": "DatasetReference"},
                    }
                ],
                "sinks": [
                    {
                        "name": "sink1",
                        "dataset": {"referenceName": "OutB", "type": "DatasetReference"},
                    }
                ],
                "transformations": [{"name": "Derive1"}],
                "scriptLines": [
                    "source(output(order_num as string)) ~> source1",
                    "source1 derive(id = toString(order_num)) ~> Derive1",
                    "Derive1 sink(allowSchemaDrift: true) ~> sink1",
                ],
            },
        },
    }
    from app.lineage import build_data_flow_lineage

    df_a, _ = DataFlowParser().parse(flow_a)
    df_b, _ = DataFlowParser().parse(flow_b)
    lin_a = replace(build_data_flow_lineage(df_a), bindings=data_flow_bindings(df_a))
    lin_b = replace(build_data_flow_lineage(df_b), bindings=data_flow_bindings(df_b))

    combined = build_combined_lineage([lin_a, lin_b])
    g = combined.graph

    # Each flow's "sink1::id" is kept distinct, namespaced by its own data flow.
    assert list(g.networkx.predecessors("CustomerFlow/sink1::id")) == ["CustomerFlow/source1::id"]
    assert list(g.networkx.predecessors("OrderFlow/sink1::id")) == ["OrderFlow/Derive1::id"]
    # The unqualified names from before the fix must not appear at all.
    assert "sink1::id" not in g.node_ids()
    assert "source1::id" not in g.node_ids()


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
