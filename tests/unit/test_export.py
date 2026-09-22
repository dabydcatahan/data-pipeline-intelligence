import json

from app.documentation import data_flow_markdown, render_html
from app.documentation.export import markdown_to_html_fragment
from app.lineage import build_data_flow_lineage
from app.parsers.dataflow_parser import DataFlowParser

SAMPLE_MD = """\
# Title

## Section

- one
- two

| A | B |
| --- | --- |
| x | y\\|z |

> a note

```
code line 1
<not a tag>
```

Some **bold** and _em_ text.
"""


def test_render_html_is_a_standalone_document():
    html = render_html(SAMPLE_MD, title="Doc")
    assert html.startswith("<!doctype html>")
    assert "<title>Doc</title>" in html
    assert html.rstrip().endswith("</html>")
    assert "<style>" in html


def test_all_constructs_convert():
    frag = markdown_to_html_fragment(SAMPLE_MD)
    assert "<h1>Title</h1>" in frag
    assert "<h2>Section</h2>" in frag
    assert "<ul>\n<li>one</li>" in frag
    assert "<table>" in frag and "<th>A</th>" in frag and "<td>y|z</td>" in frag
    assert "<blockquote>a note</blockquote>" in frag
    assert "<pre><code>code line 1\n&lt;not a tag&gt;</code></pre>" in frag
    assert "<strong>bold</strong>" in frag and "<em>em</em>" in frag


def test_title_and_content_are_escaped():
    html = render_html("# <script>alert(1)</script>\n", title='a "&" <b>')
    assert "<title>a &quot;&amp;&quot; &lt;b&gt;</title>" in html
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_malicious_table_cell_cannot_inject_markup(read_fixture_bytes):
    md = "| Col |\n| --- |\n| <img src=x onerror=alert(1)> |\n"
    frag = markdown_to_html_fragment(md)
    assert "<img" not in frag
    assert "&lt;img src=x onerror=alert(1)&gt;" in frag


def test_export_is_deterministic(read_fixture_bytes):
    df, _ = DataFlowParser().parse(json.loads(read_fixture_bytes("dataflow_customer_full.json")))
    md = data_flow_markdown(df, build_data_flow_lineage(df))
    assert render_html(md, title="X") == render_html(md, title="X")


def test_generated_data_flow_doc_round_trips_to_html(read_fixture_bytes):
    df, _ = DataFlowParser().parse(json.loads(read_fixture_bytes("dataflow_customer_full.json")))
    html = render_html(data_flow_markdown(df, build_data_flow_lineage(df)), title=df.name)
    assert "<h1>Mapping Data Flow: CustomerEnrichFlow</h1>" in html
    assert "<table>" in html
    assert "concat(&#x27;CUST-&#x27;, toString(customer_id))" in html or (
        "concat('CUST-', toString(customer_id))" in html
    )
