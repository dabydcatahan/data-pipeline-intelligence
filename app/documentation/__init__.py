"""Documentation generation engine (F013 / F014).

Deterministic Markdown from the normalized models + lineage. HTML export is F018.
"""

from app.documentation.export import markdown_to_html_fragment, render_html
from app.documentation.generator import (
    data_flow_markdown,
    document_data_flow,
    document_pipeline,
    pipeline_markdown,
)

__all__ = [
    "data_flow_markdown",
    "document_data_flow",
    "document_pipeline",
    "markdown_to_html_fragment",
    "pipeline_markdown",
    "render_html",
]
