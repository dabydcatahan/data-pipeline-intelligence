import json

import pytest
from app.lineage import build_data_flow_lineage
from app.models.lineage import NodeKind, RelationshipType
from app.parsers.dataflow_parser import DataFlowParser


@pytest.fixture
def lineage(read_fixture_bytes):
    def _build(name):
        df, _ = DataFlowParser().parse(json.loads(read_fixture_bytes(name)), source_filename=name)
        return build_data_flow_lineage(df)

    return _build


def _rel(graph, src, dst):
    return graph.edge(src, dst).relationship


def test_simple_select_flow_is_connected(lineage):
    lin = lineage("dataflow_select.json")
    g = lin.graph
    assert g.node("CustomerSource::customer_id").kind is NodeKind.SOURCE_COLUMN
    assert g.node("CustomerSink::customer_id").kind is NodeKind.SINK_COLUMN
    assert _rel(g, "CustomerSource::customer_id", "SelectCustomerColumns::customer_id") is (
        RelationshipType.DIRECT
    )
    assert "CustomerSink::customer_id" in g.downstream("CustomerSource::customer_id")


def test_derive_creates_derived_and_passthrough_edges(lineage):
    g = lineage("dataflow_customer_full.json").graph
    assert _rel(g, "CustomerSource::customer_id", "DeriveCustomerKey::customer_key") is (
        RelationshipType.DERIVED
    )
    assert _rel(g, "CustomerSource::first_name", "DeriveCustomerKey::full_name") is (
        RelationshipType.DERIVED
    )
    assert _rel(g, "CustomerSource::last_name", "DeriveCustomerKey::full_name") is (
        RelationshipType.DERIVED
    )
    # derive keeps input columns
    assert (
        g.edge("CustomerSource::customer_id", "DeriveCustomerKey::customer_id").passthrough is True
    )


def test_select_rename_is_detected(lineage):
    g = lineage("dataflow_customer_full.json").graph
    assert _rel(g, "DeriveCustomerKey::customer_key", "RenameColumns::cust_key") is (
        RelationshipType.RENAME
    )
    assert _rel(g, "DeriveCustomerKey::customer_id", "RenameColumns::customer_id") is (
        RelationshipType.DIRECT
    )


def test_join_key_columns_are_marked(lineage):
    g = lineage("dataflow_customer_full.json").graph
    assert _rel(g, "RenameColumns::region_code", "JoinRegion::region_code") is (
        RelationshipType.JOIN_KEY
    )
    assert _rel(g, "RegionRef::region_code", "JoinRegion::region_code") is (
        RelationshipType.JOIN_KEY
    )
    # non-key columns from both sides still flow through
    assert "JoinRegion::region_name" in g.downstream("RegionRef::region_name")


def test_filter_records_participating_columns(lineage):
    lin = lineage("dataflow_customer_full.json")
    g = lin.graph
    assert g.stream_meta["FilterActive"]["filter_columns"] == ("status",)
    assert _rel(g, "JoinRegion::status", "FilterActive::status") is RelationshipType.FILTER


def test_aggregate_group_keys_and_measures(lineage):
    g = lineage("dataflow_aggregate_union.json").graph
    assert _rel(g, "UnionSales::sale_date", "AggregateDaily::sale_date") is RelationshipType.DIRECT
    assert _rel(g, "UnionSales::amount", "AggregateDaily::total_amount") is (
        RelationshipType.AGGREGATE
    )
    # the raw measure column is not carried past the aggregate
    assert not g.find("AggregateDaily.amount")


def test_union_maps_like_named_columns_from_every_input(lineage):
    g = lineage("dataflow_aggregate_union.json").graph
    assert _rel(g, "OnlineSales::amount", "UnionSales::amount") is RelationshipType.UNION
    assert _rel(g, "StoreSales::amount", "UnionSales::amount") is RelationshipType.UNION


def test_conditional_split_branches_and_conditions(lineage):
    lin = lineage("dataflow_split.json")
    g = lin.graph
    assert g.stream_meta["RouteByValue"]["split_conditions"] == ("amount > 1000",)
    assert g.stream_meta["RouteByValue"]["split_columns"] == ("amount",)
    assert g.streams["highValue"] == "transformation"
    assert "HighValueSink::amount" in g.downstream("Orders::amount")
    assert "StandardSink::region" in g.downstream("Orders::region")


def test_unknown_operator_falls_back_to_passthrough_with_warning(lineage):
    lin = lineage("dataflow_unknown_op.json")
    g = lin.graph
    assert "Snk::a" in g.downstream("Src::a")
    assert g.edge("Src::a", "DoMagic::a").passthrough is True
    assert any("approximate" in w for w in lin.warnings)


def test_lineage_build_is_deterministic(lineage):
    a = lineage("dataflow_customer_full.json").graph.to_dict()
    b = lineage("dataflow_customer_full.json").graph.to_dict()
    assert a == b


def test_graph_is_acyclic(lineage):
    for name in (
        "dataflow_select.json",
        "dataflow_customer_full.json",
        "dataflow_aggregate_union.json",
        "dataflow_split.json",
    ):
        assert lineage(name).graph.has_cycle() is False


def test_flatten_folddown_is_decomposed(lineage):
    lin = lineage("dataflow_flatten.json")
    assert lin.warnings == ()
    g = lin.graph
    assert _rel(g, "UserData::orders", "unrollUserData::order_id") is RelationshipType.DERIVED
    assert _rel(g, "UserData::user_id", "unrollUserData::user_id") is RelationshipType.DIRECT
    assert "FlatOrders::order_id" in g.downstream("UserData::orders")
