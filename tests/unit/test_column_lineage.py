import json

import pytest
from app.lineage import build_data_flow_lineage
from app.lineage.column_lineage import DataFlowLineage
from app.lineage.traversal import explain
from app.models.lineage import NodeKind, RelationshipType
from app.parsers.dataflow_parser import DataFlowParser


@pytest.fixture
def lineage(read_fixture_bytes):
    def _build(name):
        df, _ = DataFlowParser().parse(json.loads(read_fixture_bytes(name)), source_filename=name)
        return build_data_flow_lineage(df)

    return _build


def _build_from_script(*script_lines: str) -> DataFlowLineage:
    flow = {
        "name": "InlineFlow",
        "properties": {
            "type": "MappingDataFlow",
            "typeProperties": {
                "sources": [
                    {
                        "name": "Src",
                        "dataset": {"referenceName": "SrcDs", "type": "DatasetReference"},
                    }
                ],
                "sinks": [
                    {
                        "name": "Snk",
                        "dataset": {"referenceName": "SnkDs", "type": "DatasetReference"},
                    }
                ],
                "transformations": [{"name": "Step1"}],
                "scriptLines": list(script_lines),
            },
        },
    }
    df, _ = DataFlowParser().parse(flow)
    return build_data_flow_lineage(df)


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


def test_window_partition_sort_and_expression_columns_are_linked(lineage):
    lin = lineage("dataflow_window.json")
    assert lin.warnings == ()
    g = lin.graph

    # windowColumns() targets depend on the window expression's own columns...
    assert _rel(g, "OrdersSource::amount", "RankByCustomer::running_total") is (
        RelationshipType.WINDOW
    )
    # ...and on the over()/asc() partition + sort columns, even for a column
    # like rank() that references nothing directly.
    assert _rel(g, "OrdersSource::customer_id", "RankByCustomer::order_rank") is (
        RelationshipType.WINDOW
    )
    assert _rel(g, "OrdersSource::order_date", "RankByCustomer::order_rank") is (
        RelationshipType.WINDOW
    )
    assert g.stream_meta["RankByCustomer"]["window_partition_by"] == ("customer_id",)
    assert g.stream_meta["RankByCustomer"]["window_order_by"] == ("order_date",)

    # Existing columns not produced by windowColumns() still pass through.
    assert g.edge("OrdersSource::customer_id", "RankByCustomer::customer_id").passthrough is True
    assert "RankedOrdersSink::running_total" in g.downstream("OrdersSource::amount")


def test_pivot_groupby_is_direct_and_values_approximated(lineage):
    lin = lineage("dataflow_pivot.json")
    g = lin.graph

    assert _rel(g, "SalesSource::customer_id", "PivotByQuarter::customer_id") is (
        RelationshipType.DIRECT
    )
    # Literal pivotBy() values let us approximate the output column names.
    for quarter in ("Q1", "Q2", "Q3", "Q4"):
        assert _rel(g, "SalesSource::amount", f"PivotByQuarter::amount_{quarter}") is (
            RelationshipType.AGGREGATE
        )
        assert _rel(g, "SalesSource::quarter", f"PivotByQuarter::amount_{quarter}") is (
            RelationshipType.AGGREGATE
        )
    assert any("approximated" in w for w in lin.warnings)

    # A pivot must NOT claim unrelated columns pass through unchanged: "quarter"
    # (the pivot key itself) is consumed, not carried through as its own output.
    produced = {g.node(n).column for n in g.node_ids() if n.startswith("PivotByQuarter::")}
    assert produced == {"customer_id", "amount_Q1", "amount_Q2", "amount_Q3", "amount_Q4"}


def test_parse_introduces_one_derived_struct_column(lineage):
    lin = lineage("dataflow_parse.json")
    assert lin.warnings == ()
    g = lin.graph

    # parse() adds exactly one new struct column, derived from the source string.
    assert _rel(g, "EventSource::jsonString", "ParseTradeJson::json") is (RelationshipType.DERIVED)
    # Existing columns (including the original string column parse() read from)
    # still pass through unchanged - parse() doesn't consume its source.
    assert g.edge("EventSource::jsonString", "ParseTradeJson::jsonString").passthrough is True
    assert g.edge("EventSource::event_id", "ParseTradeJson::event_id").passthrough is True

    # A later select() unpacking a struct field (json.trade) correctly resolves
    # the dependency back to the struct column parse() produced.
    assert _rel(g, "ParseTradeJson::json", "SelectTradeFields::trade") is (RelationshipType.DERIVED)
    assert "TradeSink::trade" in g.downstream("EventSource::jsonString")


def test_surrogate_key_adds_a_column_with_no_upstream_origin(lineage):
    lin = lineage("dataflow_surrogate_key.json")
    assert lin.warnings == ()
    g = lin.graph

    # The generated key column must exist in the sink schema (previously,
    # keyGenerate fell through to plain passthrough, which only carries EXISTING
    # input columns - the surrogate key column was invisible in lineage entirely).
    assert "DimCustomerSink::customer_key" in g.node_ids()
    # It's a synthetic sequence: no upstream column dependency at all.
    assert list(g.networkx.predecessors("SurrogateKey1::customer_key")) == []
    # Every existing column still passes through unchanged alongside it.
    assert g.edge("CustomerSource::customer_id", "SurrogateKey1::customer_id").passthrough is True

    from app.lineage.traversal import explain

    (node,) = g.find("DimCustomerSink.customer_key")
    journey = explain(g, node)
    assert journey.origins == ("SurrogateKey1::customer_key",)


def test_derive_chained_reference_to_an_earlier_derived_column_is_resolved():
    """`fullNameUpper = upper(fullName)` where `fullName` is derived one line above.

    Previously, a reference to a column produced earlier in the SAME derive
    block was reported as "not in any input stream" and the edge was dropped
    entirely, even though the referenced column plainly exists in the graph.
    """
    lin = _build_from_script(
        "source(output(first as string, last as string)) ~> Src",
        "Src derive(fullName = concat(first, last), fullNameUpper = upper(fullName)) ~> Step1",
        "Step1 sink(allowSchemaDrift: true) ~> Snk",
    )
    assert lin.warnings == ()
    g = lin.graph
    assert _rel(g, "Step1::fullName", "Step1::fullNameUpper") is RelationshipType.DERIVED
    (node,) = g.find("Snk.fullNameUpper")
    assert explain(g, node).origins == ("Src::first", "Src::last")


def test_select_chained_reference_to_an_earlier_mapped_column_is_resolved():
    lin = _build_from_script(
        "source(output(first as string, last as string)) ~> Src",
        "Src select(mapColumn(fullName = concat(first, last), "
        "initial = left(fullName, 1))) ~> Step1",
        "Step1 sink(allowSchemaDrift: true) ~> Snk",
    )
    assert lin.warnings == ()
    g = lin.graph
    assert _rel(g, "Step1::fullName", "Step1::initial") is RelationshipType.DERIVED
    (node,) = g.find("Snk.initial")
    assert explain(g, node).origins == ("Src::first", "Src::last")


def test_aggregate_chained_reference_to_an_earlier_aggregate_column_is_resolved():
    lin = _build_from_script(
        "source(output(customer_id as string, amount as double)) ~> Src",
        "Src aggregate(groupBy(customer_id), total = sum(amount), doubled = total * 2) ~> Step1",
        "Step1 sink(allowSchemaDrift: true) ~> Snk",
    )
    assert lin.warnings == ()
    g = lin.graph
    assert _rel(g, "Step1::total", "Step1::doubled") is RelationshipType.AGGREGATE
    (node,) = g.find("Snk.doubled")
    assert "Src::amount" in explain(g, node).origins


def test_window_chained_reference_to_an_earlier_window_column_is_resolved():
    lin = _build_from_script(
        "source(output(customer_id as string, amount as double)) ~> Src",
        "Src window(over(customer_id), "
        "windowColumns(running_total = sum(amount), pct = running_total / 100)) ~> Step1",
        "Step1 sink(allowSchemaDrift: true) ~> Snk",
    )
    assert lin.warnings == ()
    g = lin.graph
    assert _rel(g, "Step1::running_total", "Step1::pct") is RelationshipType.WINDOW
    (node,) = g.find("Snk.pct")
    assert "Src::amount" in explain(g, node).origins
