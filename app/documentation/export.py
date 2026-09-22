"""Markdown -> standalone HTML export (F018).

A small, dependency-free converter for exactly the Markdown dialect that
``app.documentation.generator`` emits: ``#``/``##``/``###`` headings, fenced
code blocks (``` ``` ```), pipe tables, ``- `` lists, ``> `` blockquotes, and
inline ``**bold**`` / ``_italic_``.

Security: every piece of text is HTML-escaped **before** any tag is added, so a
malicious column name or expression coming from an uploaded ADF file (e.g.
``<script>…``) cannot inject markup into the exported document.
"""

from __future__ import annotations

import re
from html import escape

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_SEP = re.compile(r"^\|[\s:|-]+\|?\s*$")
_LIST_ITEM = re.compile(r"^[-*]\s+(.*)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])_(.+?)_(?![\w*])")

_CSS = """
:root { color-scheme: light dark; }
body { font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       max-width: 960px; margin: 2rem auto; padding: 0 1rem; }
h1, h2, h3 { line-height: 1.25; margin-top: 1.6em; }
h1 { border-bottom: 2px solid currentColor; padding-bottom: .2em; }
h2 { border-bottom: 1px solid rgba(127,127,127,.4); padding-bottom: .15em; }
table { border-collapse: collapse; margin: .6rem 0; width: 100%; }
th, td { border: 1px solid rgba(127,127,127,.45); padding: .35rem .55rem; text-align: left;
         vertical-align: top; }
th { background: rgba(127,127,127,.12); }
pre { background: rgba(127,127,127,.12); padding: .8rem 1rem; overflow-x: auto;
      border-radius: 6px; }
code { font: 13px/1.5 "SF Mono", ui-monospace, Menlo, Consolas, monospace; }
blockquote { border-left: 4px solid rgba(127,127,127,.5); margin: .6rem 0; padding: .2rem 1rem;
             color: inherit; opacity: .85; }
""".strip()


def _inline(text: str) -> str:
    out = escape(text, quote=False)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC.sub(r"<em>\1</em>", out)
    return out


def _split_row(line: str) -> list[str]:
    parts = re.split(r"(?<!\\)\|", line)
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return [p.strip().replace("\\|", "|") for p in parts]


def _render_table(rows: list[str]) -> str:
    header = _split_row(rows[0])
    body = [_split_row(r) for r in rows[2:]]
    out = ["<table>", "<thead><tr>"]
    out += [f"<th>{_inline(c)}</th>" for c in header]
    out += ["</tr></thead>", "<tbody>"]
    for row in body:
        out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
    out += ["</tbody>", "</table>"]
    return "".join(out)


def markdown_to_html_fragment(markdown: str) -> str:
    lines = markdown.replace("\r\n", "\n").split("\n")
    html: list[str] = []
    i = 0
    n = len(lines)
    list_open = False

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            html.append("</ul>")
            list_open = False

    while i < n:
        line = lines[i]

        if line.strip() == "```":
            close_list()
            block: list[str] = []
            i += 1
            while i < n and lines[i].strip() != "```":
                block.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            html.append("<pre><code>" + escape("\n".join(block), quote=False) + "</code></pre>")
            continue

        heading = _HEADING.match(line)
        if heading:
            close_list()
            level = len(heading.group(1))
            html.append(f"<h{level}>{_inline(heading.group(2).strip())}</h{level}>")
            i += 1
            continue

        if line.startswith("|") and i + 1 < n and _TABLE_SEP.match(lines[i + 1]):
            close_list()
            table_rows = [line, lines[i + 1]]
            i += 2
            while i < n and lines[i].startswith("|"):
                table_rows.append(lines[i])
                i += 1
            html.append(_render_table(table_rows))
            continue

        item = _LIST_ITEM.match(line)
        if item:
            if not list_open:
                html.append("<ul>")
                list_open = True
            html.append(f"<li>{_inline(item.group(1).strip())}</li>")
            i += 1
            continue

        if line.startswith("> "):
            close_list()
            html.append(f"<blockquote>{_inline(line[2:].strip())}</blockquote>")
            i += 1
            continue

        if line.strip() == "":
            close_list()
            i += 1
            continue

        close_list()
        html.append(f"<p>{_inline(line.strip())}</p>")
        i += 1

    close_list()
    return "\n".join(html)


def render_html(markdown: str, *, title: str) -> str:
    """Wrap ``markdown`` in a complete, self-contained HTML document."""
    body = markdown_to_html_fragment(markdown)
    safe_title = escape(title, quote=True)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{safe_title}</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head>\n<body>\n"
        f"{body}\n"
        "</body>\n</html>\n"
    )
