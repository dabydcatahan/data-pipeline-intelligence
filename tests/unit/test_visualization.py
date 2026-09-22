import json

import pytest
from app.lineage import build_data_flow_lineage
from app.parsers.dataflow_parser import DataFlowParser
from app.visualization import build_dot, focus_node_set
from app.visualization.lineage import _escape


@pytest.fixture
def customer_graph(read_fixture_bytes):
    df, _ = DataFlowParser().parse(json.loads(read_fixture_bytes("dataflow_customer_full.json")))
    return build_data_flow_lineage(df).graph


def test_build_dot_is_wellformed(customer_graph):
    dot = build_dot(customer_graph)
    assert dot.startswith("digraph lineage {")
    assert dot.rstrip().endswith("}")
    assert 'subgraph "cluster_CustomerSource"' in dot
    assert 'subgraph "cluster_CustomerSink"' in dot
    assert '"CustomerSource::customer_id" [label="customer_id"' in dot


def test_edges_are_coloured_by_relationship(customer_graph):
    dot = build_dot(customer_graph)
    # derived edge carries its expression as a label + the derived colour
    assert (
        '"CustomerSource::customer_id" -> "DeriveCustomerKey::customer_key" [color="#8430CE"' in dot
    )
    assert "concat('CUST-', toString(customer_id))" in dot
    # passthrough edges are dashed
    assert "style=dashed" in dot
    # rename edge colour
    assert '"DeriveCustomerKey::customer_key" -> "RenameColumns::cust_key" [color="#1A73E8"' in dot


def test_focus_mode_restricts_to_lineage_of_one_column(customer_graph):
    dot = build_dot(customer_graph, focus="RenameColumns::cust_key")
    assert "CustomerSource::customer_id" in dot  # an ancestor
    assert "CustomerSink::cust_key" in dot  # a descendant
    assert "RegionRef::region_name" not in dot  # unrelated column
    assert "#FDE7E9" in dot  # focus node fill
    assert "focus: RenameColumns.cust_key (both)" in dot


def test_direction_downstream_drops_ancestors(customer_graph):
    dot = build_dot(customer_graph, focus="RenameColumns::cust_key", direction="downstream")
    assert "DeriveCustomerKey::customer_key" not in dot
    assert "CustomerSink::cust_key" in dot


def test_focus_node_set_helper(customer_graph):
    both = focus_node_set(customer_graph, "RenameColumns::cust_key", "both")
    up = focus_node_set(customer_graph, "RenameColumns::cust_key", "upstream")
    down = focus_node_set(customer_graph, "RenameColumns::cust_key", "downstream")
    assert up | down == both
    assert "RenameColumns::cust_key" in up and "RenameColumns::cust_key" in down


def test_max_nodes_truncates_with_caption(customer_graph):
    dot = build_dot(customer_graph, max_nodes=5)
    assert "showing 5 of" in dot


def test_build_dot_is_deterministic(customer_graph):
    assert build_dot(customer_graph) == build_dot(customer_graph)


def test_escape_handles_quotes_and_backslashes():
    assert _escape('a"b\\c') == 'a\\"b\\\\c'


def test_unknown_focus_raises(customer_graph):
    with pytest.raises(KeyError):
        build_dot(customer_graph, focus="Nope::missing")


def test_pyvis_html_when_available(customer_graph):
    pytest.importorskip("pyvis")
    from app.visualization import to_pyvis_html

    html = to_pyvis_html(customer_graph, focus="RenameColumns::cust_key")
    assert "<html" in html.lower()
    assert "RenameColumns::cust_key" in html
