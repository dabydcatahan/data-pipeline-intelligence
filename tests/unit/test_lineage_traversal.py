import json

import pytest
from app.lineage import build_data_flow_lineage, explain
from app.lineage.traversal import origins, sink_columns_reached
from app.parsers.dataflow_parser import DataFlowParser


@pytest.fixture
def customer_graph(read_fixture_bytes):
    df, _ = DataFlowParser().parse(json.loads(read_fixture_bytes("dataflow_customer_full.json")))
    return build_data_flow_lineage(df).graph


def test_find_resolves_bare_and_qualified_names(customer_graph):
    assert set(customer_graph.find("region_code")) == {
        "CustomerSource::region_code",
        "RegionRef::region_code",
        "DeriveCustomerKey::region_code",
        "RenameColumns::region_code",
        "JoinRegion::region_code",
        "FilterActive::region_code",
        "CustomerSink::region_code",
    }
    assert customer_graph.find("RegionRef.region_code") == ["RegionRef::region_code"]
    assert customer_graph.find("RegionRef::region_code") == ["RegionRef::region_code"]


def test_upstream_and_downstream(customer_graph):
    sink_key = "CustomerSink::cust_key"
    ups = customer_graph.upstream(sink_key)
    assert "CustomerSource::customer_id" in ups
    assert "DeriveCustomerKey::customer_key" in ups

    src_id = "CustomerSource::customer_id"
    downs = customer_graph.downstream(src_id)
    assert "CustomerSink::cust_key" in downs
    assert "CustomerSink::customer_id" in downs


def test_origins_and_sinks(customer_graph):
    assert origins(customer_graph, "CustomerSink::cust_key") == ["CustomerSource::customer_id"]
    assert sink_columns_reached(customer_graph, "CustomerSource::first_name") == [
        "CustomerSink::full_name"
    ]


def test_paths_between_columns(customer_graph):
    paths = customer_graph.paths("CustomerSource::customer_id", "CustomerSink::cust_key")
    assert len(paths) == 1
    assert paths[0] == [
        "CustomerSource::customer_id",
        "DeriveCustomerKey::customer_key",
        "RenameColumns::cust_key",
        "JoinRegion::cust_key",
        "FilterActive::cust_key",
        "CustomerSink::cust_key",
    ]


def test_explain_builds_ordered_journey_and_narrative(customer_graph):
    journey = explain(customer_graph, "CustomerSink::cust_key")
    assert journey.column == "cust_key"
    assert journey.origins == ("CustomerSource::customer_id",)
    assert journey.upstream_steps[0].is_origin
    assert journey.upstream_steps[0].column == "customer_id"
    assert journey.upstream_steps[-1].node_id == "CustomerSink::cust_key"

    # the rename step records the relationship + the transformation type
    rename_step = next(s for s in journey.upstream_steps if s.node_id == "RenameColumns::cust_key")
    assert rename_step.inputs[0].relationship.value == "rename"
    assert rename_step.inputs[0].transformation_type == "select"

    narrative = journey.narrative()
    assert "SOURCE  CustomerSource.customer_id" in narrative
    assert "concat('CUST-', toString(customer_id))" in narrative
    assert "renamed" in narrative


def test_explain_downstream_from_source_column(customer_graph):
    journey = explain(customer_graph, "CustomerSource::customer_id")
    assert "CustomerSink::cust_key" in journey.downstream_columns
    assert "RenameColumns" in journey.transformations
    assert journey.upstream_steps[0].is_origin  # itself, no ancestors
