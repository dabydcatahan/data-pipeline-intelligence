"""Streamlit smoke test - the app boots and the new pages render without error."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
APP = str(Path(__file__).resolve().parents[2] / "app" / "ui" / "app.py")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _run(page: str | None = None):
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception
    if page is not None:
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception
    return at


def test_app_boots_with_all_pages():
    at = _run()
    assert set(at.sidebar.radio[0].options) == {
        "Dashboard",
        "Upload",
        "Pipeline Explorer",
        "Data Flow Explorer",
        "Column Lineage & Impact",
        "Inventory",
        "Documentation",
        "Error Analyzer",
        "AI Insights",
    }


def test_upload_page_paste_json_ingests_an_artifact():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value("Upload").run()

    text = (FIXTURES / "pipeline_copy_direct.json").read_text()
    at.text_area[0].set_value(text).run()
    next(b for b in at.button if b.label == "Analyze pasted JSON").click().run()

    assert not at.exception
    names = [a.name for a in at.session_state["session"].artifacts]
    assert "CopyCustomerPipeline" in names


def test_upload_page_paste_matches_file_upload_byte_for_byte():
    """Paste and file-upload share `_ingest_one` -> `AnalysisService.analyze_upload`;
    this pins that a pasted data flow parses identically to the same bytes uploaded
    as a file (same transformation count, same content hash)."""
    from app.services.analysis_service import AnalysisService

    text = (FIXTURES / "dataflow_customer_full.json").read_text()
    file_artifact = AnalysisService().analyze_upload(
        "dataflow_customer_full.json", text.encode("utf-8")
    )

    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value("Upload").run()
    at.text_area[0].set_value(text).run()
    next(b for b in at.button if b.label == "Analyze pasted JSON").click().run()

    assert not at.exception
    (pasted,) = at.session_state["session"].artifacts
    assert pasted.content_sha256 == file_artifact.content_sha256
    assert pasted.name == file_artifact.name == "CustomerEnrichFlow"
    assert len(pasted.data_flow.transformations) == len(file_artifact.data_flow.transformations)


def test_upload_page_paste_name_field_only_sets_source_filename():
    """The optional 'Name' field must not leak into the artifact's identity - that
    comes from the JSON content itself, same as a real ADF export would have it."""
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value("Upload").run()

    at.text_input[0].set_value("not-the-real-name").run()
    text = (FIXTURES / "pipeline_copy_direct.json").read_text()
    at.text_area[0].set_value(text).run()
    next(b for b in at.button if b.label == "Analyze pasted JSON").click().run()

    assert not at.exception
    (artifact,) = at.session_state["session"].artifacts
    assert artifact.name == "CopyCustomerPipeline"
    assert artifact.source_filename == "not-the-real-name.json"


def test_upload_page_paste_invalid_json_shows_error_not_crash():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value("Upload").run()

    at.text_area[0].set_value("{not valid json,,,").run()
    next(b for b in at.button if b.label == "Analyze pasted JSON").click().run()

    assert not at.exception
    assert at.session_state["session"].artifacts == []
    assert "pasted.json" in at.session_state["session"].failures
    assert any("not valid JSON" in e.value for e in at.error)


def test_upload_page_paste_unsupported_artifact_shows_error_not_crash():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value("Upload").run()

    at.text_area[0].set_value('{"name": "SomeDataset", "properties": {"type": "AzureBlob"}}').run()
    next(b for b in at.button if b.label == "Analyze pasted JSON").click().run()

    assert not at.exception
    assert at.session_state["session"].artifacts == []
    assert "not a recognised ADF Pipeline" in at.session_state["session"].failures["pasted.json"]


def test_upload_page_paste_empty_text_is_a_no_op():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value("Upload").run()

    next(b for b in at.button if b.label == "Analyze pasted JSON").click().run()

    assert not at.exception
    assert at.session_state["session"].artifacts == []
    assert any("paste some JSON" in w.value for w in at.warning)


def test_upload_page_can_remove_and_clear_artifacts():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    from app.services.analysis_service import AnalysisService

    svc = AnalysisService()
    for name in ("pipeline_copy_translator.json", "dataflow_customer_full.json"):
        at.session_state["session"].add(svc.analyze_upload(name, (FIXTURES / name).read_bytes()))

    at.sidebar.radio[0].set_value("Upload").run()
    assert len(at.session_state["session"].artifacts) == 2

    # Remove the first artifact
    remove_buttons = [b for b in at.button if b.label == "Remove"]
    assert len(remove_buttons) == 2
    remove_buttons[0].click().run()
    assert len(at.session_state["session"].artifacts) == 1

    # Clear all
    next(b for b in at.button if b.label == "Clear all").click().run()
    assert at.session_state["session"].artifacts == []


def test_error_analyzer_shows_real_column_lineage_for_a_loaded_data_flow():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    from app.services.analysis_service import AnalysisService

    svc = AnalysisService()
    at.session_state["session"].add(
        svc.analyze_upload(
            "dataflow_customer_full.json",
            (FIXTURES / "dataflow_customer_full.json").read_bytes(),
        )
    )

    at.sidebar.radio[0].set_value("Error Analyzer").run()
    at.text_area(key="error_analyzer_paste_input").set_value(
        "Column 'customer_id' not found in source"
    ).run()
    next(b for b in at.button if b.label == "Analyze error").click().run()

    assert not at.exception
    _error_text, analysis = at.session_state["error_analyzer_result"]
    assert analysis.inferred_error_type == "missing_column"
    # The backend found a real match in the uploaded data flow, not just a
    # generic recommendation - and the UI must actually render it.
    assert len(analysis.suggested_lineage) > 0
    body = "\n".join(str(v) for e in at.get("markdown") for v in [e.value])
    assert "Column Lineage" in body
    assert "customer_id" in body


def test_inventory_page_lists_artifacts():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    from app.services.analysis_service import AnalysisService

    svc = AnalysisService()
    for name in ("pipeline_copy_translator.json", "dataflow_customer_full.json"):
        at.session_state["session"].add(svc.analyze_upload(name, (FIXTURES / name).read_bytes()))

    at.sidebar.radio[0].set_value("Inventory").run()
    assert not at.exception
    assert len(at.tabs) == 4


def test_ai_insights_page_renders_for_a_data_flow():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    from app.services.analysis_service import AnalysisService

    artifact = AnalysisService().analyze_upload(
        "df.json", (FIXTURES / "dataflow_customer_full.json").read_bytes()
    )
    at.session_state["session"].add(artifact)
    at.sidebar.radio[0].set_value("AI Insights").run()
    assert not at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "Data flow" in body
    assert any("deterministic" in c.value for c in at.caption)


def test_empty_states_render():
    for page in ("Data Flow Explorer", "Column Lineage & Impact", "Pipeline Explorer"):
        at = _run(page)
        assert any("No " in info.value for info in at.info)


def test_documentation_page_renders_and_offers_download():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    from app.services.analysis_service import AnalysisService

    artifact = AnalysisService().analyze_upload(
        "df.json", (FIXTURES / "dataflow_customer_full.json").read_bytes()
    )
    at.session_state["session"].add(artifact)

    at.sidebar.radio[0].set_value("Documentation").run()
    assert not at.exception
    assert len(at.download_button) == 1
    body = " ".join(m.value for m in at.markdown)
    assert "Mapping Data Flow: CustomerEnrichFlow" in body

    # switch to HTML export
    fmt = next(r for r in at.radio if set(r.options) == {"Markdown", "HTML"})
    fmt.set_value("HTML").run()
    assert not at.exception
    assert any(b.label == "Download .html" for b in at.download_button)
    assert any("<!doctype html>" in c.value for c in at.code)


def test_column_lineage_page_with_a_loaded_data_flow():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    # Load a data flow directly into the session the app uses.
    from app.services.analysis_service import AnalysisService

    artifact = AnalysisService().analyze_upload(
        "df.json", (FIXTURES / "dataflow_customer_full.json").read_bytes()
    )
    at.session_state["session"].add(artifact)

    at.sidebar.radio[0].set_value("Column Lineage & Impact").run()
    assert not at.exception
    at.text_input[0].set_value("CustomerSink.cust_key").run()
    assert not at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "Lineage narrative" in body
    # the impact metric rendered
    assert any(m.label == "Impact" for m in at.metric)


def test_column_lineage_stitched_scope():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    from app.services.analysis_service import AnalysisService

    svc = AnalysisService()
    for name in ("pipeline_copy_translator.json", "dataflow_customer_full.json"):
        at.session_state["session"].add(svc.analyze_upload(name, (FIXTURES / name).read_bytes()))

    at.sidebar.radio[0].set_value("Column Lineage & Impact").run()
    scope = next(r for r in at.radio if "All artifacts (stitched)" in r.options)
    scope.set_value("All artifacts (stitched)").run()
    assert not at.exception
    at.text_input[0].set_value("RawCustomer.CustomerId").run()
    assert not at.exception
    assert any("Lineage narrative" in m.value for m in at.markdown)
