import json

from app.parsers.pipeline_parser import PipelineParser
from app.parsers.translator import parse_copy_mappings


def test_tabular_translator_mappings_list():
    tp = {
        "translator": {
            "type": "TabularTranslator",
            "mappings": [
                {"source": {"name": "A"}, "sink": {"name": "a"}},
                {"source": {"name": "B"}, "sink": {"name": "b"}},
            ],
        }
    }
    assert parse_copy_mappings(tp) == (("A", "a"), ("B", "b"))


def test_legacy_column_mappings_string():
    tp = {"translator": {"type": "TabularTranslator", "columnMappings": "A: a, B : b"}}
    assert parse_copy_mappings(tp) == (("A", "a"), ("B", "b"))


def test_legacy_column_mappings_object():
    tp = {"translator": {"type": "TabularTranslator", "columnMappings": {"A": "a"}}}
    assert parse_copy_mappings(tp) == (("A", "a"),)


def test_no_translator_returns_empty():
    assert parse_copy_mappings({"source": {}, "sink": {}}) == ()
    assert parse_copy_mappings({"translator": {"type": "TabularTranslator"}}) == ()


def test_incomplete_mapping_entries_are_dropped():
    tp = {
        "translator": {
            "mappings": [
                {"source": {"name": "A"}},
                {"sink": {"name": "b"}},
                "not-a-dict",
                {"source": {"name": "C"}, "sink": {"name": "c"}},
            ]
        }
    }
    assert parse_copy_mappings(tp) == (("C", "c"),)


def test_pipeline_parser_attaches_copy_mappings(read_fixture_bytes):
    pipeline, _ = PipelineParser().parse(
        json.loads(read_fixture_bytes("pipeline_copy_translator.json"))
    )
    copy = pipeline.activity("CopyRawToStage")
    assert copy is not None
    assert ("CustomerId", "customer_id") in copy.column_mappings
    # non-Copy activities get no mappings
    assert pipeline.activity("RunCustomerFlow").column_mappings == ()
