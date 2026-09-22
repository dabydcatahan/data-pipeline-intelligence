"""Professional .docx documentation export (F013/F014 extension).

Renders the exact same underlying facts as ``app.documentation.generator``'s
Markdown (reusing its plain-English summary/narrative helpers as the single
source of truth for *what* to say) as a styled Word document instead of plain
text: a title page, an auto-updating table of contents, styled headings,
bordered/banded tables, code-styled blocks for raw expressions and technical
lineage traces, and a page-numbered footer.

Requires the optional ``docx`` extra (``pip install '.[docx]'``); python-docx
is imported lazily so the rest of the app runs without it - same pattern as
the ``viz``/pyvis extra in ``app.visualization.lineage``.

Colors match the Streamlit UI's own theme (``app/ui/app.py``) for a
consistent look between the app and its exported documents. Kept as local
constants rather than imported from ``app.ui`` - the UI is the outer layer
(DEC-002/§ architecture); nothing below it may depend on it.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

from app.lineage.column_lineage import DataFlowLineage
from app.lineage.plain_language import operator_plain
from app.lineage.traversal import explain
from app.models.dataflow import DataFlow
from app.models.pipeline import Pipeline

from .generator import (
    _data_flow_plain_summary,
    _joined,
    _pipeline_plain_summary,
    _plain_narrative,
)

# Matches the ING palette already hardcoded in app/ui/app.py, for visual
# consistency between the live app and its exported documents.
_ACCENT = "FF6200"  # heading rule / table header fill
_ACCENT_DARK = "D65200"
_NAVY = "00252E"  # heading text
_GREY = "F5F6F7"  # banded table rows / code block background
_BORDER = "E4E7EB"

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _require_docx() -> ModuleType:
    try:
        import docx
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Word export needs the optional 'docx' extra: "
            "pip install 'data-pipeline-intelligence[docx]'"
        ) from exc
    return docx


# --------------------------------------------------------------------- oxml helpers
def _shade_cell(cell: Any, hex_color: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    cell._tc.get_or_add_tcPr().append(shd)


def _shade_paragraph(paragraph: Any, hex_color: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    paragraph._p.get_or_add_pPr().append(shd)


def _add_bottom_rule(paragraph: Any, hex_color: str, size: int = 18) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "4")
    bottom.set(qn("w:color"), hex_color)
    pBdr.append(bottom)
    paragraph._p.get_or_add_pPr().append(pBdr)


def _add_toc(document: Any) -> None:
    """Insert a real Word TOC field, and mark the document to refresh it on open."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    paragraph = document.add_paragraph()
    run = paragraph.add_run()
    placeholder = "Right-click here and choose 'Update Field' to generate the table of contents."
    for tag, attrs, text in (
        ("w:fldChar", {"w:fldCharType": "begin"}, None),
        ("w:instrText", {"xml:space": "preserve"}, 'TOC \\o "1-3" \\h \\z \\u'),
        ("w:fldChar", {"w:fldCharType": "separate"}, None),
        ("w:t", {}, placeholder),
        ("w:fldChar", {"w:fldCharType": "end"}, None),
    ):
        el = OxmlElement(tag)
        for attr, value in attrs.items():
            el.set(qn(attr), value)
        if text is not None:
            el.text = text
        run._r.append(el)

    updatefields = OxmlElement("w:updateFields")
    updatefields.set(qn("w:val"), "true")
    document.settings.element.append(updatefields)


def _add_page_number_field(paragraph: Any) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(instr)
    run._r.append(end)


# ------------------------------------------------------------------ content helpers
def _add_markdown_runs(paragraph: Any, text: str) -> None:
    """Add ``text`` to ``paragraph``, honoring ``**bold**`` spans from the shared
    plain-English summary/narrative helpers (single source of truth with the
    Markdown exporter - only the rendering differs)."""
    pos = 0
    for match in _BOLD_RE.finditer(text):
        if match.start() > pos:
            paragraph.add_run(text[pos : match.start()])
        paragraph.add_run(match.group(1)).bold = True
        pos = match.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])


def _add_bullets(document: Any, lines: list[str]) -> None:
    for line in lines:
        text = line.lstrip("- ").strip()
        p = document.add_paragraph(style="List Bullet")
        _add_markdown_runs(p, text)


def _add_code_block(document: Any, text: str) -> None:
    from docx.shared import Pt

    p = document.add_paragraph()
    _shade_paragraph(p, _GREY)
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(8)
    for i, line in enumerate(text.splitlines() or [text]):
        if i:
            p.add_run().add_break()
        run = p.add_run(line)
        run.font.name = "Consolas"
        run.font.size = Pt(9.5)


def _add_table(
    document: Any,
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
    *,
    note: str = "_none_",
) -> None:
    from docx.shared import RGBColor

    rows = list(rows)
    if not rows:
        document.add_paragraph(note.strip("_") or "None.").italic = True
        return
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.autofit = True
    for cell, header in zip(table.rows[0].cells, headers, strict=True):
        run = cell.paragraphs[0].add_run(header)
        run.bold = True
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        _shade_cell(cell, _ACCENT)
    for row_idx, row in enumerate(rows):
        cells = table.add_row().cells
        for cell, value in zip(cells, row, strict=True):
            text = "" if value is None else str(value)
            cell.text = text.strip() or "—"
        if row_idx % 2 == 1:
            for cell in cells:
                _shade_cell(cell, _GREY)


def _style_document(document: Any) -> None:
    from docx.shared import Pt, RGBColor

    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)

    title_style = document.styles["Title"]
    title_style.font.color.rgb = RGBColor.from_string(_NAVY)
    title_style.font.size = Pt(28)

    for level, size in ((1, 18), (2, 14), (3, 12)):
        style = document.styles[f"Heading {level}"]
        style.font.color.rgb = RGBColor.from_string(_NAVY)
        style.font.size = Pt(size)
        style.font.name = "Calibri"


def _add_title_page(document: Any, kind_label: str, name: str, description: str | None) -> None:
    from docx.shared import Pt, RGBColor

    title = document.add_heading(name, level=0)
    _add_bottom_rule(title, _ACCENT)
    subtitle = document.add_paragraph(f"{kind_label} Documentation")
    subtitle.runs[0].font.size = Pt(14)
    subtitle.runs[0].font.color.rgb = RGBColor.from_string(_ACCENT_DARK)
    generated = document.add_paragraph(
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · "
        "Deterministic, offline documentation — see PROJECT_PLAN.md §12"
    )
    generated.runs[0].font.size = Pt(9)
    generated.runs[0].font.italic = True
    if description:
        document.add_paragraph(description)


def _add_footer_page_numbers(document: Any) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    section = document.sections[0]
    footer = section.footer
    paragraph = footer.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.add_run("Page ")
    _add_page_number_field(paragraph)


def _finish(document: Any) -> bytes:
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- pipeline
def pipeline_docx(pipeline: Pipeline) -> bytes:
    """Render a :class:`Pipeline` as a styled, downloadable .docx document."""
    docx = _require_docx()
    document = docx.Document()
    _style_document(document)
    _add_footer_page_numbers(document)

    order = pipeline.execution_order()
    _add_title_page(document, "Pipeline", pipeline.name, pipeline.description)
    _add_toc(document)
    document.add_page_break()

    document.add_heading("Plain-English Summary", level=1)
    _add_bullets(document, _pipeline_plain_summary(pipeline, order))

    document.add_heading("Overview", level=1)
    _add_bullets(
        document,
        [
            f"**Folder:** {pipeline.folder or '_none_'}",
            f"**Concurrency:** "
            f"{pipeline.concurrency if pipeline.concurrency is not None else '_none_'}",
            f"**Annotations:** {_joined(pipeline.annotations)}",
            f"**Activities:** {pipeline.activity_count}",
        ],
    )

    document.add_heading("Parameters", level=2)
    _add_table(
        document,
        ["Name", "Type", "Default"],
        [(p.name, p.type, p.default_value) for p in pipeline.parameters],
    )
    document.add_heading("Variables", level=2)
    _add_table(
        document,
        ["Name", "Type", "Default"],
        [(v.name, v.type, v.default_value) for v in pipeline.variables],
    )

    document.add_heading("Execution Flow", level=1)
    if order.order:
        document.add_paragraph(" → ".join(order.order))
    if order.has_cycle:
        warn = document.add_paragraph(f"⚠ Circular dependency among: {', '.join(order.cyclic)}")
        _shade_paragraph(warn, "FFF3E0")
    document.add_heading("Dependencies", level=2)
    _add_table(document, ["Upstream", "Downstream"], list(pipeline.dependency_edges()))

    document.add_heading("Activities", level=1)
    _add_table(
        document,
        ["Name", "Type", "Container", "Depends on", "Inputs", "Outputs", "Data flow"],
        [
            (
                a.name,
                a.type + ("" if a.is_recognized else " (unrecognized)"),
                f"{a.parent_activity} ({a.container_branch})" if a.is_nested else "—",
                _joined(a.depends_on_names),
                _joined(r.name for r in a.inputs),
                _joined(r.name for r in a.outputs),
                a.data_flow_reference or "—",
            )
            for a in pipeline.activities
        ],
    )

    document.add_heading("Data Sources", level=1)
    document.add_paragraph(_joined(pipeline.input_datasets))
    document.add_heading("Data Targets", level=1)
    document.add_paragraph(_joined(pipeline.output_datasets))
    document.add_heading("Referenced Data Flows", level=1)
    document.add_paragraph(_joined(pipeline.data_flow_references))

    return _finish(document)


# --------------------------------------------------------------------------- data flow
def data_flow_docx(data_flow: DataFlow, lineage: DataFlowLineage | None = None) -> bytes:
    """Render a :class:`DataFlow` as a styled, downloadable .docx document."""
    docx = _require_docx()
    document = docx.Document()
    _style_document(document)
    _add_footer_page_numbers(document)

    _add_title_page(document, "Mapping Data Flow", data_flow.name, data_flow.description)
    _add_toc(document)
    document.add_page_break()

    document.add_heading("Plain-English Summary", level=1)
    _add_bullets(document, _data_flow_plain_summary(data_flow))

    document.add_heading("Sources", level=1)
    _add_table(
        document,
        ["Source", "Dataset", "Schema columns"],
        [
            (s.name, s.dataset.name if s.dataset else "—", _joined(s.schema_columns))
            for s in data_flow.sources
        ],
    )

    document.add_heading("Sinks", level=1)
    _add_table(
        document,
        ["Sink", "Dataset", "Fed by"],
        [
            (s.name, s.dataset.name if s.dataset else "—", s.input_stream or "—")
            for s in data_flow.sinks
        ],
    )

    document.add_heading("Transformations", level=1)
    for transformation in data_flow.transformations:
        title = f"{transformation.name} ({operator_plain(transformation.kind)})"
        if not transformation.is_recognized:
            title += " — unrecognized operator"
        document.add_heading(title, level=2)
        _add_bullets(document, [f"**Inputs:** {_joined(transformation.input_streams)}"])
        if transformation.group_by:
            _add_bullets(document, [f"**Group by:** {_joined(transformation.group_by)}"])
        if transformation.raw_arguments:
            _add_code_block(document, transformation.raw_arguments)
        if transformation.columns:
            document.add_paragraph("Column mapping").runs[0].bold = True
            _add_table(
                document,
                ["Target", "Kind", "From", "Confidence", "Expression"],
                [
                    (
                        c.target,
                        c.kind,
                        _joined(c.referenced_columns),
                        c.analysis_confidence,
                        c.expression or "—",
                    )
                    for c in transformation.columns
                ],
            )

    if lineage is not None:
        _add_lineage_section(document, lineage)
        if lineage.warnings:
            document.add_heading("Warnings", level=1)
            _add_bullets(document, list(lineage.warnings))

    return _finish(document)


def _add_lineage_section(document: Any, lineage: DataFlowLineage) -> None:
    graph = lineage.graph
    sink_nodes = [n.id for n in graph.iter_nodes() if n.kind.value == "sink_column"]
    document.add_heading("Column Lineage", level=1)
    if not sink_nodes:
        document.add_paragraph("No sink columns resolved.").italic = True
        return

    document.add_paragraph(
        "This shows where the data in each final output column came from, and what "
        "happened to it along the way."
    )
    rows = []
    for node_id in sink_nodes:
        journey = explain(graph, node_id)
        rows.append(
            (
                f"{journey.stream}.{journey.column}",
                _joined(o.replace("::", ".") for o in journey.origins),
                _joined(journey.transformations),
            )
        )
    _add_table(document, ["Sink column", "Originating source column(s)", "Transformations"], rows)

    document.add_heading("Detailed paths", level=2)
    for node_id in sink_nodes:
        journey = explain(graph, node_id)
        document.add_heading(f"{journey.stream}.{journey.column}", level=3)
        _add_bullets(document, _plain_narrative(journey).splitlines())
        p = document.add_paragraph("Technical trace")
        p.runs[0].italic = True
        _add_code_block(document, journey.narrative())
