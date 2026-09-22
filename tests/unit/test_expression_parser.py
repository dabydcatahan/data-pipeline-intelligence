import pytest
from app.models.expression import ExpressionDialect
from app.parsers.expression_parser import ExpressionParser, analyze_expression


def cols(expr):
    return set(analyze_expression(expr).referenced_columns)


@pytest.mark.parametrize(
    ("expr", "expected_cols", "expected_funcs"),
    [
        ("concat(first_name, last_name)", {"first_name", "last_name"}, {"concat"}),
        ("upper(customer_name)", {"customer_name"}, {"upper"}),
        ("toString(customer_id)", {"customer_id"}, {"toString"}),
        (
            "iif(isNull(email), alternate_email, email)",
            {"email", "alternate_email"},
            {"iif", "isNull"},
        ),
        ("substring(name, 1, 5)", {"name"}, {"substring"}),
        ("sum(amount)", {"amount"}, {"sum"}),
        ("avg(amount)", {"amount"}, {"avg"}),
        ("concat('CUST-', toString(customer_id))", {"customer_id"}, {"concat", "toString"}),
        ("base_price * quantity - discount", {"base_price", "quantity", "discount"}, set()),
    ],
)
def test_supported_expressions(expr, expected_cols, expected_funcs):
    analysis = analyze_expression(expr)
    assert analysis.confidence == "high"
    assert set(analysis.referenced_columns) == expected_cols
    assert set(analysis.functions) == expected_funcs


def test_numbers_and_string_literals_are_not_columns():
    analysis = analyze_expression("substring(full_name, 1, 5)")
    assert analysis.referenced_columns == ("full_name",)


def test_escaped_quote_in_string_literal():
    analysis = analyze_expression(r"concat(note, ' it\'s fine ')")
    assert analysis.confidence == "high"
    assert analysis.referenced_columns == ("note",)


def test_braced_identifier_is_a_column():
    analysis = analyze_expression("upper({Customer Name})")
    assert analysis.referenced_columns == ("Customer Name",)


def test_parameter_reference_is_not_a_column():
    analysis = analyze_expression("$rate * net_amount")
    assert analysis.parameters == ("rate",)
    assert analysis.referenced_columns == ("net_amount",)


def test_stream_qualified_columns():
    analysis = analyze_expression("Left@customer_id == Right@customer_id")
    assert analysis.referenced_columns == ("customer_id",)
    assert set(analysis.qualified_columns) == {"Left@customer_id", "Right@customer_id"}
    assert analysis.functions == ()


def test_byname_literal_is_treated_as_column():
    analysis = analyze_expression("toString(byName('customer_ref'))")
    assert "customer_ref" in analysis.referenced_columns


def test_keywords_are_ignored():
    analysis = analyze_expression("iif(is_active == true, x, y)")
    assert set(analysis.referenced_columns) == {"is_active", "x", "y"}


def test_unbalanced_parens_is_unable_to_determine():
    analysis = analyze_expression("concat(a, b")
    assert analysis.confidence == "none"
    assert analysis.referenced_columns == ()
    assert analysis.summary == "Unable to determine automatically"


def test_unterminated_string_is_unable_to_determine():
    analysis = analyze_expression("concat(a, 'oops)")
    assert analysis.confidence == "none"


def test_higher_order_function_is_partial_not_fatal():
    analysis = analyze_expression("reduce(scores, 0, #acc + #item, #acc)")
    assert analysis.confidence == "partial"
    assert "scores" in analysis.referenced_columns
    assert any("iterator" in n for n in analysis.notes)


def test_unknown_function_still_extracts_arguments():
    analysis = analyze_expression("myCompanyUdf(col_a, col_b)")
    assert set(analysis.referenced_columns) == {"col_a", "col_b"}
    assert analysis.functions == ("myCompanyUdf",)
    assert analysis.confidence == "high"


def test_empty_expression():
    analysis = analyze_expression("   ")
    assert analysis.confidence == "none"


def test_summary_text():
    assert analyze_expression("concat(a, b)").summary == "depends on a, b"
    assert analyze_expression("currentTimestamp()").summary == "no column dependencies"


def test_pipeline_dialect_extracts_params_not_columns():
    parser = ExpressionParser(ExpressionDialect.PIPELINE)
    analysis = parser.analyze("@concat(pipeline().parameters.region, '-', variables('env'))")
    assert analysis.confidence == "high"
    assert set(analysis.parameters) == {"region", "env"}
    assert "concat" in analysis.functions
    assert analysis.referenced_columns == ()
