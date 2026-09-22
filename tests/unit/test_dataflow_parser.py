import json

from app.parsers.dataflow_parser import DataFlowParser


def _parse(read_fixture_bytes, name):
    return DataFlowParser().parse(json.loads(read_fixture_bytes(name)), source_filename=name)


def test_parse_simple_select_flow(read_fixture_bytes):
    df, _ctx = _parse(read_fixture_bytes, "dataflow_select.json")
    assert df.name == "CustomerDataFlow"
    assert [s.name for s in df.sources] == ["CustomerSource"]
    assert df.sources[0].schema_columns == ("customer_id", "first_name", "last_name")
    assert [s.name for s in df.sinks] == ["CustomerSink"]
    assert [t.name for t in df.transformations] == ["SelectCustomerColumns"]
    select = df.transformation("SelectCustomerColumns")
    assert select.kind == "select"
    assert {c.target for c in select.columns} == {"customer_id", "first_name", "last_name"}
    assert all(c.kind == "retained" for c in select.columns)
    assert df.sinks[0].input_stream == "SelectCustomerColumns"


def test_parse_full_customer_flow(read_fixture_bytes):
    df, _ctx = _parse(read_fixture_bytes, "dataflow_customer_full.json")

    assert {s.name for s in df.sources} == {"CustomerSource", "RegionRef"}
    src = next(s for s in df.sources if s.name == "CustomerSource")
    assert "status" in src.schema_columns and "customer_id" in src.schema_columns

    derive = df.transformation("DeriveCustomerKey")
    assert derive.kind == "derive"
    exprs = {c.target: c.expression for c in derive.columns}
    assert exprs["customer_key"] == "concat('CUST-', toString(customer_id))"
    assert exprs["full_name"] == "concat(first_name, ' ', last_name)"
    # F021: expression parser resolves the upstream column dependencies.
    deps = {c.target: c.referenced_columns for c in derive.columns}
    assert deps["customer_key"] == ("customer_id",)
    assert set(deps["full_name"]) == {"first_name", "last_name"}
    assert all(c.analysis_confidence == "high" for c in derive.columns)

    rename = df.transformation("RenameColumns")
    kinds = {c.target: c.kind for c in rename.columns}
    assert kinds["cust_key"] == "renamed"
    assert kinds["customer_id"] == "retained"
    rename_deps = {c.target: c.referenced_columns for c in rename.columns}
    assert rename_deps["cust_key"] == ("customer_key",)
    assert rename_deps["customer_id"] == ("customer_id",)

    join = df.transformation("JoinRegion")
    assert join.kind == "join"
    assert set(join.input_streams) == {"RenameColumns", "RegionRef"}
    assert "region_code == RegionRef@region_code" in join.raw_arguments

    filt = df.transformation("FilterActive")
    assert filt.kind == "filter"
    assert filt.raw_arguments == "status == 'ACTIVE'"

    edges = df.stream_edges()
    assert ("CustomerSource", "DeriveCustomerKey") in edges
    assert ("RegionRef", "JoinRegion") in edges
    assert ("JoinRegion", "FilterActive") in edges
    assert df.unrecognized_operators == ()


def test_parse_aggregate_union_flow(read_fixture_bytes):
    df, _ctx = _parse(read_fixture_bytes, "dataflow_aggregate_union.json")

    union = df.transformation("UnionSales")
    assert union.kind == "union"
    assert set(union.input_streams) == {"OnlineSales", "StoreSales"}

    agg = df.transformation("AggregateDaily")
    assert agg.kind == "aggregate"
    assert agg.group_by == ("sale_date", "customer_id")
    assert {c.target for c in agg.columns} == {"total_amount", "order_count"}
    assert {c.kind for c in agg.columns} == {"aggregate"}


def test_unknown_operator_is_reported_not_fatal(read_fixture_bytes):
    df, ctx = _parse(read_fixture_bytes, "dataflow_unknown_op.json")
    magic = df.transformation("DoMagic")
    assert magic is not None
    assert magic.is_recognized is False
    assert df.unrecognized_operators == ("superNovaTransform",)
    assert any("superNovaTransform" in w and "approximate" in w for w in ctx.warnings)
    # The rest of the flow still parsed.
    assert [s.name for s in df.sources] == ["Src"]
    assert [s.name for s in df.sinks] == ["Snk"]


def test_missing_scriptlines_is_non_fatal(read_fixture_bytes):
    obj = {
        "name": "NoScriptFlow",
        "properties": {
            "type": "MappingDataFlow",
            "typeProperties": {
                "sources": [{"name": "S", "dataset": {"referenceName": "Ds"}}],
                "sinks": [{"name": "K", "dataset": {"referenceName": "Dk"}}],
                "transformations": [{"name": "T1"}],
            },
        },
    }
    df, ctx = DataFlowParser().parse(obj, source_filename="n.json")
    assert df.transformation("T1") is not None
    assert any("no scriptLines" in w for w in ctx.warnings)
    assert any("recorded by name only" in w for w in ctx.warnings)


def test_secret_like_keys_in_type_properties_are_scrubbed_defensively():
    """ADF's own schema doesn't put credentials in data flow source/sink
    typeProperties (they live in a separately-referenced linked service this tool
    never parses), but a stray secret-shaped key must never survive parsing
    regardless - defense in depth, matching pipeline_parser.py's guarantee."""
    obj = {
        "name": "EvilFlow",
        "properties": {
            "typeProperties": {
                "sources": [
                    {
                        "name": "Src1",
                        "password": "hunter2",
                        "accountKey": "supersecretkey==",
                        "dataset": {"referenceName": "Ds1"},
                    }
                ],
                "sinks": [],
                "transformations": [],
                "scriptLines": ["source(output(a as string)) ~> Src1"],
            }
        },
    }
    df, _ctx = DataFlowParser().parse(obj, source_filename="evil.json")
    assert df.name == "EvilFlow"
    blob = json.dumps(df.model_dump(), default=str)
    assert "hunter2" not in blob
    assert "supersecretkey" not in blob
