import json

import pytest
from app.lineage import analyze_impact, build_data_flow_lineage
from app.models.impact import ImpactLevel
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


def test_unknown_node_raises(read_fixture_bytes):
    df, _ = DataFlowParser().parse(json.loads(read_fixture_bytes("dataflow_select.json")))
    graph = build_data_flow_lineage(df).graph
    with pytest.raises(KeyError):
        analyze_impact(graph, "Nope::missing")
