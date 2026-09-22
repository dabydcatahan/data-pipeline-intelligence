from app.parsers.dfs_script import (
    extract_columns,
    parse_statement,
    split_statements,
    split_top_level,
)


def test_split_statements_handles_multiline_and_terminators():
    script = "\n".join(
        [
            "source(output(",
            "\t\tid as string",
            "\t),",
            "\tallowSchemaDrift: true) ~> Src",
            "Src derive(x = 1) ~> D",
        ]
    )
    stmts = split_statements(script)
    assert len(stmts) == 2
    assert stmts[0].endswith("~> Src")
    assert stmts[1].endswith("~> D")


def test_split_top_level_respects_parens_and_quotes():
    assert split_top_level("a, b, c") == ["a", "b", "c"]
    assert split_top_level("concat(a, b), d = ','") == ["concat(a, b)", "d = ','"]


def test_parse_source_statement():
    st = parse_statement(
        "source(output(id as string, name as string), allowSchemaDrift: true) ~> Src"
    )
    assert st.operator == "source"
    assert st.input_streams == ()
    assert st.output_stream == "Src"
    cols, group_by = extract_columns(st.operator, st.raw_arguments)
    assert [c.target for c in cols] == ["id", "name"]
    assert cols[0].data_type == "string"
    assert group_by == ()


def test_parse_derive_statement_captures_raw_expressions():
    st = parse_statement(
        "CustomerSource derive(customer_key = concat('CUST-', toString(customer_id)), "
        "full_name = concat(first_name, ' ', last_name)) ~> DeriveKey"
    )
    assert st.operator == "derive"
    assert st.input_streams == ("CustomerSource",)
    cols, _ = extract_columns(st.operator, st.raw_arguments)
    by_target = {c.target: c for c in cols}
    assert by_target["customer_key"].expression == "concat('CUST-', toString(customer_id))"
    assert by_target["customer_key"].kind == "derived"
    assert by_target["full_name"].expression == "concat(first_name, ' ', last_name)"


def test_parse_select_distinguishes_retained_rename_derived():
    st = parse_statement(
        "D select(mapColumn(customer_id, cust_key = customer_key, "
        "full = first_name + ' ' + last_name)) ~> Sel"
    )
    cols, _ = extract_columns(st.operator, st.raw_arguments)
    kinds = {c.target: c.kind for c in cols}
    assert kinds["customer_id"] == "retained"
    assert kinds["cust_key"] == "renamed"
    assert kinds["full"] == "derived"


def test_parse_join_statement_has_two_inputs():
    st = parse_statement(
        "Left, Right join(Left@id == Right@id, joinType:'inner', broadcast: 'auto') ~> J"
    )
    assert st.operator == "join"
    assert st.input_streams == ("Left", "Right")
    assert "Left@id == Right@id" in st.raw_arguments
    assert st.is_recognized is True


def test_parse_filter_statement():
    st = parse_statement("J filter(status == 'ACTIVE') ~> F")
    assert st.operator == "filter"
    assert st.raw_arguments == "status == 'ACTIVE'"


def test_parse_aggregate_statement_groupby_and_aggs():
    st = parse_statement(
        "U aggregate(groupBy(sale_date, customer_id), total = sum(amount), n = count()) ~> Agg"
    )
    cols, group_by = extract_columns(st.operator, st.raw_arguments)
    assert group_by == ("sale_date", "customer_id")
    assert {c.target for c in cols} == {"total", "n"}
    assert all(c.kind == "aggregate" for c in cols)


def test_parse_union_statement():
    st = parse_statement("A, B union() ~> U")
    assert st.operator == "union"
    assert st.input_streams == ("A", "B")


def test_conditional_split_branches_captured():
    st = parse_statement(
        "X split(status == 'A', status == 'B', disjoint: false) ~> Split@(active, inactive)"
    )
    assert st.operator == "split"
    assert st.output_stream == "Split"
    assert st.output_branches == ("active", "inactive")


def test_unknown_operator_flagged_not_fatal():
    st = parse_statement("X superNovaTransform(mode: 'quantum') ~> Y")
    assert st.operator == "superNovaTransform"
    assert st.is_recognized is False
    assert st.output_stream == "Y"


def test_statement_without_terminator_is_unrecognized():
    st = parse_statement("this is not a real statement")
    assert st.is_recognized is False
    assert st.output_stream == ""
