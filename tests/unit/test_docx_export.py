"""Tests for the styled .docx documentation export (F013/F014 extension).

Requires the optional 'docx' extra; skipped entirely if python-docx isn't
installed, matching the pyvis/'viz' extra pattern in test_visualization.py.
"""

import io
import json
import zipfile

import pytest

docx = pytest.importorskip("docx")

from app.documentation.docx_export import data_flow_docx, pipeline_docx  # noqa: E402
from app.lineage import build_data_flow_lineage  # noqa: E402
from app.parsers.dataflow_parser import DataFlowParser  # noqa: E402
from app.parsers.pipeline_parser import PipelineParser  # noqa: E402


def _pipeline(read_fixture_bytes, name):
    p, _ = PipelineParser().parse(json.loads(read_fixture_bytes(name)))
    return p


def _data_flow(read_fixture_bytes, name):
    df, _ = DataFlowParser().parse(json.loads(read_fixture_bytes(name)))
    return df


def _open(data: bytes):
    return docx.Document(io.BytesIO(data))


def _all_text(document) -> str:
    return "\n".join(p.text for p in document.paragraphs)


def test_pipeline_docx_is_a_valid_zip_package(read_fixture_bytes):
    data = pipeline_docx(_pipeline(read_fixture_bytes, "pipeline_multi_activity.json"))
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.testzip() is None
        assert "[Content_Types].xml" in z.namelist()
        assert "word/document.xml" in z.namelist()


def test_pipeline_docx_has_expected_sections_and_data(read_fixture_bytes):
    document = _open(pipeline_docx(_pipeline(read_fixture_bytes, "pipeline_multi_activity.json")))
    headings = [p.text for p in document.paragraphs if p.style.name in ("Title", *[f"Heading {i}" for i in range(1, 4)])]
    assert headings[0] == "CustomerLoad"
    for expected in (
        "Plain-English Summary",
        "Overview",
        "Execution Flow",
        "Activities",
        "Data Sources",
        "Data Targets",
        "Referenced Data Flows",
    ):
        assert expected in headings

    activities_table = next(
        t for t in document.tables if [c.text for c in t.rows[0].cells][:2] == ["Name", "Type"]
    )
    names = [row.cells[0].text for row in activities_table.rows[1:]]
    assert "CopyRaw" in names and "TransformCustomer" in names


def test_pipeline_docx_shows_nested_activity_container(read_fixture_bytes):
    document = _open(pipeline_docx(_pipeline(read_fixture_bytes, "pipeline_control_flow.json")))
    activities_table = next(
        t for t in document.tables if [c.text for c in t.rows[0].cells][:2] == ["Name", "Type"]
    )
    rows = {row.cells[0].text: [c.text for c in row.cells] for row in activities_table.rows[1:]}
    assert rows["CopyRegionRaw"][2] == "ForEachRegion (loop)"
    assert rows["CopyGoldTier"][2] == "RouteByTier (case:gold)"
    assert rows["ForEachRegion"][2] == "—"


def test_pipeline_docx_does_not_leak_scrubbed_secrets(read_fixture_bytes):
    text = _all_text(_open(pipeline_docx(_pipeline(read_fixture_bytes, "pipeline_multi_activity.json"))))
    assert "Password" not in text
    assert "connectionString" not in text


def test_data_flow_docx_without_lineage(read_fixture_bytes):
    document = _open(data_flow_docx(_data_flow(read_fixture_bytes, "dataflow_customer_full.json")))
    text = _all_text(document)
    assert "Sources" in text and "Sinks" in text and "Transformations" in text
    assert "Column Lineage" not in text


def test_data_flow_docx_with_lineage(read_fixture_bytes):
    df = _data_flow(read_fixture_bytes, "dataflow_customer_full.json")
    lineage = build_data_flow_lineage(df)
    document = _open(data_flow_docx(df, lineage))
    text = _all_text(document)
    assert "Column Lineage" in text
    assert "Detailed paths" in text
    assert "concat('CUST-', toString(customer_id))" in text

    lineage_table = next(
        t
        for t in document.tables
        if [c.text for c in t.rows[0].cells] == ["Sink column", "Originating source column(s)", "Transformations"]
    )
    rows = {row.cells[0].text: row.cells[1].text for row in lineage_table.rows[1:]}
    assert rows["CustomerSink.cust_key"] == "CustomerSource.customer_id"


def test_docx_table_header_row_is_styled(read_fixture_bytes):
    """The header row must be a single bold, white-on-accent-color run - not a
    stray empty run plus a styled one (regression: cell.text="" before add_run
    used to leave a leftover empty run in front of the real one)."""
    document = _open(pipeline_docx(_pipeline(read_fixture_bytes, "pipeline_multi_activity.json")))
    table = document.tables[0]
    header_runs = table.rows[0].cells[0].paragraphs[0].runs
    assert len(header_runs) == 1
    assert header_runs[0].bold is True


def test_docx_has_table_of_contents_field_and_page_numbers(read_fixture_bytes):
    data = pipeline_docx(_pipeline(read_fixture_bytes, "pipeline_multi_activity.json"))
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        document_xml = z.read("word/document.xml").decode("utf-8")
        settings_xml = z.read("word/settings.xml").decode("utf-8")
        footer_xml = z.read("word/footer1.xml").decode("utf-8")
    assert 'TOC \\o "1-3"' in document_xml
    assert "updateFields" in settings_xml
    assert '>PAGE<' in footer_xml or "PAGE" in footer_xml


def test_docx_is_deterministic(read_fixture_bytes):
    df = _data_flow(read_fixture_bytes, "dataflow_customer_full.json")
    lineage = build_data_flow_lineage(df)
    a = _all_text(_open(data_flow_docx(df, lineage)))
    b = _all_text(_open(data_flow_docx(df, lineage)))
    assert a == b


def test_pipeline_docx_raises_a_clear_error_without_the_docx_extra(monkeypatch, read_fixture_bytes):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "docx":
            raise ModuleNotFoundError("No module named 'docx'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="docx' extra"):
        pipeline_docx(_pipeline(read_fixture_bytes, "pipeline_multi_activity.json"))
