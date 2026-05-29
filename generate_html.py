#!/usr/bin/env python3
"""Generate a single HTML document from the numbered Markdown files."""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

DEFAULT_EXCLUDES = {"95-notes.md", "99-log.md"}


def discover_markdown_files(root: Path, include_log: bool) -> list[Path]:
    files = sorted(root.glob("[0-9][0-9]-*.md"))
    if not include_log:
        files = [path for path in files if path.name not in DEFAULT_EXCLUDES]
    return files


def inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    return escaped


def render_table(lines: list[str]) -> str:
    rows = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows.append(cells)

    header = rows[0]
    body = rows[2:] if len(rows) > 2 else []

    parts = ["<table>", "<thead><tr>"]
    parts.extend(f"<th>{inline_markdown(cell)}</th>" for cell in header)
    parts.append("</tr></thead>")
    if body:
        parts.append("<tbody>")
        for row in body:
            parts.append("<tr>")
            parts.extend(f"<td>{inline_markdown(cell)}</td>" for cell in row)
            parts.append("</tr>")
        parts.append("</tbody>")
    parts.append("</table>")
    return "\n".join(parts)


def fallback_markdown_to_html(markdown_text: str) -> str:
    """Small Markdown renderer for this repo's docs.

    It supports headings, paragraphs, fenced code blocks, lists, blockquotes,
    and simple pipe tables. If the optional `markdown` package is installed,
    the script uses that instead.
    """

    lines = markdown_text.splitlines()
    output: list[str] = []
    paragraph: list[str] = []
    list_type: str | None = None
    in_code = False
    code_lang = ""
    code_lines: list[str] = []
    i = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            output.append(f"<p>{inline_markdown(' '.join(paragraph))}</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal list_type
        if list_type:
            output.append(f"</{list_type}>")
            list_type = None

    while i < len(lines):
        line = lines[i]

        if in_code:
            if line.startswith("```"):
                language_class = (
                    f' class="language-{html.escape(code_lang)}"' if code_lang else ""
                )
                code = html.escape("\n".join(code_lines))
                output.append(f"<pre><code{language_class}>{code}</code></pre>")
                in_code = False
                code_lang = ""
                code_lines = []
            else:
                code_lines.append(line)
            i += 1
            continue

        if line.startswith("```"):
            flush_paragraph()
            close_list()
            in_code = True
            code_lang = line[3:].strip()
            i += 1
            continue

        if not line.strip():
            flush_paragraph()
            close_list()
            i += 1
            continue

        if (
            line.startswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\|\s*:?-{3,}", lines[i + 1])
        ):
            flush_paragraph()
            close_list()
            table_lines = [line, lines[i + 1]]
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                table_lines.append(lines[i])
                i += 1
            output.append(render_table(table_lines))
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            output.append(f"<h{level}>{inline_markdown(heading.group(2))}</h{level}>")
            i += 1
            continue

        if line.startswith("> "):
            flush_paragraph()
            close_list()
            output.append(f"<blockquote>{inline_markdown(line[2:])}</blockquote>")
            i += 1
            continue

        unordered = re.match(r"^\s*[-*]\s+(.+)$", line)
        ordered = re.match(r"^\s*\d+\.\s+(.+)$", line)
        if unordered or ordered:
            flush_paragraph()
            desired = "ul" if unordered else "ol"
            if list_type != desired:
                close_list()
                output.append(f"<{desired}>")
                list_type = desired
            item = unordered.group(1) if unordered else ordered.group(1)
            output.append(f"<li>{inline_markdown(item)}</li>")
            i += 1
            continue

        close_list()
        paragraph.append(line.strip())
        i += 1

    flush_paragraph()
    close_list()
    return "\n".join(output)


def markdown_to_html(markdown_text: str) -> str:
    try:
        import markdown  # type: ignore
    except ImportError:
        return fallback_markdown_to_html(markdown_text)

    return markdown.markdown(
        markdown_text,
        extensions=["fenced_code", "tables", "toc"],
        output_format="html5",
    )


def build_document(files: list[Path], title: str) -> str:
    sections = []
    nav_items = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        anchor = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")
        nav_items.append(f'<li><a href="#{anchor}">{html.escape(path.name)}</a></li>')
        sections.append(
            f'<section id="{anchor}">\n'
            f'<p class="source-file">{html.escape(path.name)}</p>\n'
            f"{markdown_to_html(text)}\n"
            "</section>"
        )

    nav = "\n".join(nav_items)
    content = "\n".join(sections)
    escaped_title = html.escape(title)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>
    body {{
      color: #17202a;
      font-family: Georgia, 'Times New Roman', serif;
      line-height: 1.55;
      margin: 0 auto;
      max-width: 980px;
      padding: 32px 24px 64px;
    }}
    nav {{
      background: #f6f1e8;
      border: 1px solid #e0d3bf;
      border-radius: 10px;
      margin: 24px 0 36px;
      padding: 16px 20px;
    }}
    nav ul {{ margin: 0; padding-left: 22px; }}
    section {{ border-top: 1px solid #dedede; padding-top: 28px; }}
    h1, h2, h3 {{ line-height: 1.2; }}
    code {{
      background: #f3f4f4;
      border-radius: 4px;
      font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
      padding: 0.1em 0.25em;
    }}
    pre {{
      background: #111827;
      border-radius: 10px;
      color: #f9fafb;
      overflow-x: auto;
      padding: 16px;
    }}
    pre code {{ background: transparent; color: inherit; padding: 0; }}
    table {{ border-collapse: collapse; display: block; overflow-x: auto; width: 100%; }}
    th, td {{ border: 1px solid #d3d7dc; padding: 8px 10px; text-align: left; }}
    th {{ background: #eef2f6; }}
    blockquote {{ border-left: 4px solid #bcc7d3; color: #475569; margin-left: 0; padding-left: 16px; }}
    .source-file {{ color: #64748b; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }}
  </style>
</head>
<body>
  <header>
    <h1>{escaped_title}</h1>
    <p>Generated from numbered Markdown files.</p>
  </header>
  <nav>
    <strong>Contents</strong>
    <ul>
      {nav}
    </ul>
  </nav>
  {content}
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o", "--output", default="solution.html", help="Output HTML path."
    )
    parser.add_argument(
        "--title", default="Stock and Materials Management", help="HTML document title."
    )
    parser.add_argument(
        "--include-log", action="store_true", help="Include 99-log.md in the output."
    )
    args = parser.parse_args()

    root = Path.cwd()
    files = discover_markdown_files(root, args.include_log)
    if not files:
        raise SystemExit("No numbered Markdown files found.")

    output = Path(args.output)
    output.write_text(build_document(files, args.title), encoding="utf-8")
    print(f"Wrote {output} from {len(files)} Markdown files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
