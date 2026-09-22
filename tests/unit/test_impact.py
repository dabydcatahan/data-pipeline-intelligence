import json

import pytest
from app.lineage import analyze_impact, build_data_flow_lineage
from app.lineage.graph import ColumnLineageGraph
from app.models.impact import ImpactLevel
from app.models.lineage import NodeKind, RelationshipType
from app.parsers.dataflow_parser import DataFlowParser


@pytest.fixture
def impact_for(read_fixture_bytes):
    def _impact(fixture, query):
        df, _ = DataFlowParser().parse(json.loads(read_fixture_bytes(fixture)))
        graph = build_data_flow_lineage(df).graph
        (node_id,) = graph.find(query)
        return analyze_impact(graph, node_id)

    return _impact


def test_source_column_through_join_and_filter_is_high(impact_for):
    report = impact_for("dataflow_customer_full.json", "CustomerSource.customer_id")
    assert report.level is ImpactLevel.HIGH
    assert set(report.sink_columns) == {
        "CustomerSink::cust_key",
        "CustomerSink::customer_id",
    }
    assert report.joins_affected == ("JoinRegion",)
    assert report.filters_affected == ("FilterActive",)
    assert "RenameColumns::cust_key" in report.computed_columns_affected
    assert not report.is_terminal


def test_join_key_column_drives_gate_and_is_high(impact_for):
    report = impact_for("dataflow_customer_full.json", "RegionRef.region_code")
    assert report.level is ImpactLevel.HIGH
    assert report.joins_affected == ("JoinRegion",)
    assert any("directly drives a join" in f for f in report.factors)


def test_terminal_sink_column_is_low_with_zero_score(impact_for):
    report = impact_for("dataflow_customer_full.json", "CustomerSink.cust_key")
    assert report.is_terminal
    assert report.level is ImpactLevel.LOW
    assert report.score == 0
    assert report.all_downstream == ()


def test_aggregate_measure_source_is_at_least_medium(impact_for):
    report = impact_for("dataflow_aggregate_union.json", "OnlineSales.amount")
    assert report.level in {ImpactLevel.MEDIUM, ImpactLevel.HIGH}
    assert report.aggregations_affected == ("AggregateDaily",)
    assert "AggregateDaily::total_amount" in report.computed_columns_affected
    assert "SalesSink::total_amount" in report.sink_columns


def test_split_condition_source_reaches_multiple_sinks_high(impact_for):
    report = impact_for("dataflow_split.json", "Orders.amount")
    assert report.level is ImpactLevel.HIGH
    assert report.splits_affected == ("RouteByValue",)
    assert set(report.sink_columns) == {"HighValueSink::amount", "StandardSink::amount"}


def test_direct_and_indirect_downstream_partition(impact_for):
    report = impact_for("dataflow_customer_full.json", "CustomerSource.customer_id")
    direct = set(report.direct_downstream)
    indirect = set(report.indirect_downstream)
    assert direct and indirect
    assert direct.isdisjoint(indirect)
    assert direct | indirect == set(report.all_downstream)


def test_factors_explain_the_score(impact_for):
    report = impact_for("dataflow_customer_full.json", "CustomerSource.customer_id")
    assert report.factors
    assert report.factors[-1].endswith(report.level.label)
    assert any("sink column" in f for f in report.factors)


def test_impact_is_deterministic(impact_for):
    a = impact_for("dataflow_customer_full.json", "CustomerSource.customer_id")
    b = impact_for("dataflow_customer_full.json", "CustomerSource.customer_id")
    assert a.model_dump() == b.model_dump()


def test_summary_text_for_terminal_and_non_terminal(impact_for):
    terminal = impact_for("dataflow_customer_full.json", "CustomerSink.cust_key")
    assert "no downstream dependencies" in terminal.summary()
    assert "LOW" in terminal.summary()

    active = impact_for("dataflow_customer_full.json", "CustomerSource.customer_id")
    text = active.summary()
    assert "HIGH" in text
    assert "sink column(s)" in text
    assert active.downstream_column_count == len(active.all_downstream)


def test_gate_elsewhere_in_blast_radius_does_not_inflate_a_clean_sink_path():
    """A join/split must sit ON the path to an affected sink to justify HIGH.

    Previously ``_level()`` only checked "is there a sink reached at all" and
    "is there a join/split transformation anywhere in the downstream set" as
    two independent facts, so a column with one clean, gate-free path to its
    sink was marked HIGH purely because it *also* fed an unrelated join that
    dead-ends without ever reaching a sink.
    """
    g = ColumnLineageGraph()
    g.register_stream("Src", "source")
    g.register_stream("Sink1", "sink")
    g.add_column("Src", "a", NodeKind.SOURCE_COLUMN)
    g.add_column("Sink1", "a", NodeKind.SINK_COLUMN)
    g.add_dependency("Src", "a", "Sink1", "a", relationship=RelationshipType.DIRECT)

    # Dead-end join: shares the source column but never reaches a sink.
    g.register_stream("JoinX", "transformation", operator="join")
    g.add_column("Src", "b", NodeKind.SOURCE_COLUMN)
    g.add_column("JoinX", "b", NodeKind.OUTPUT_COLUMN)
    g.add_dependency("Src", "a", "JoinX", "b", relationship=RelationshipType.JOIN_KEY)
    g.add_dependency("Src", "b", "JoinX", "b", relationship=RelationshipType.JOIN_KEY)

    report = analyze_impact(g, "Src::a")
    assert report.level is ImpactLevel.MEDIUM
    assert report.critical_path == ("Src::a", "Sink1::a")


def test_gate_actually_on_the_path_to_a_sink_is_high():
    g = ColumnLineageGraph()
    g.register_stream("Src", "source")
    g.register_stream("Select1", "transformation", operator="select")
    g.register_stream("JoinX", "transformation", operator="join")
    g.register_stream("Sink1", "sink")
    g.add_column("Src", "a", NodeKind.SOURCE_COLUMN)
    g.add_column("Select1", "a", NodeKind.OUTPUT_COLUMN)
    g.add_column("JoinX", "a", NodeKind.OUTPUT_COLUMN)
    g.add_column("Sink1", "joined_a", NodeKind.SINK_COLUMN)
    g.add_dependency("Src", "a", "Select1", "a", relationship=RelationshipType.DIRECT)
    g.add_dependency("Select1", "a", "JoinX", "a", relationship=RelationshipType.JOIN_KEY)
    g.add_dependency("JoinX", "a", "Sink1", "joined_a", relationship=RelationshipType.DIRECT)

    report = analyze_impact(g, "Src::a")
    assert report.level is ImpactLevel.HIGH


def test_unknown_node_raises(read_fixture_bytes):
    df, _ = DataFlowParser().parse(json.loads(read_fixture_bytes("dataflow_select.json")))
    graph = build_data_flow_lineage(df).graph
    with pytest.raises(KeyError):
        analyze_impact(graph, "Nope::missing")
