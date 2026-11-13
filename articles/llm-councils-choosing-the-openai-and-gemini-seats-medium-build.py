"""Build the Medium-ready HTML for the LLM Councils seat-selection article.

Medium takes no Markdown, has no table element, and keeps a pasted code block only in a narrow markup. So:
- pandoc converts the Markdown to HTML;
- every table becomes a 2x PNG (headless Chrome), referenced as an image;
- `##` maps to Medium's large heading (h3) and `###` to its small heading (h4);
- code blocks become a bare <pre> with <br> line breaks, figure spaces (U+2007) for indentation and &nbsp; for blank
  lines, which is the markup that survived Medium in published tests (2026-09/10).

Usage:  python3 <this file> [--base-url https://.../articles/]
Without --base-url, images use relative paths (for a local preview). To paste into Medium, the images must be at
public URLs, so Medium fetches and re-hosts them; pass the URL of the folder that holds them.
"""

import html
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SLUG = "llm-councils-choosing-the-openai-and-gemini-seats"
SRC = HERE / f"{SLUG}.md"
OUT = HERE / f"{SLUG}-medium.html"
TABLES = HERE / "medium-tables"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FIGURE_SPACE, WORD_JOINER = " ", "⁠"

TABLE_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html, body {{ margin: 0; background: #ffffff; }}
body {{ padding: 16px; font-family: -apple-system, 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #0b0b0b; }}
table {{ border-collapse: collapse; width: {width}px; font-size: 17px; line-height: 1.4; }}
th {{ text-align: left; font-weight: 700; border-bottom: 2px solid #c9c8c3; padding: 10px 12px; }}
td {{ border-bottom: 1px solid #e7e6e2; padding: 10px 12px; vertical-align: top; }}
code {{ font-family: Menlo, monospace; font-size: 15px; background: #f2f1ed; padding: 1px 4px; border-radius: 4px; }}
</style></head><body>{table}
<script>document.body.dataset.h = Math.ceil(document.body.getBoundingClientRect().height);</script>
</body></html>"""


def chrome(*args):
    return subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def render_table(index, table_html):
    """Render one table to PNG at 2x; return the image file name."""
    cols = len(re.findall(r"<th[ >]", table_html))
    width = 760 if cols <= 3 else 1000 if cols <= 5 else 1200
    page = TABLES / f"table-{index:02d}.html"
    page.write_text(TABLE_PAGE.format(width=width, table=table_html))
    dom = chrome("--virtual-time-budget=2000", f"--window-size={width + 32},2000", "--dump-dom", page.as_uri())
    height = int(re.search(r'data-h="(\d+)"', dom).group(1))
    png = TABLES / f"{SLUG}-table-{index:02d}.png"
    chrome(
        "--force-device-scale-factor=2", f"--window-size={width + 32},{height}", f"--screenshot={png}", page.as_uri()
    )
    page.unlink()
    return png.name


def table_alt(table_html):
    """Alt text: the header row, so a screen reader still learns what the table compares."""
    heads = [re.sub(r"<[^>]+>", "", h).strip() for h in re.findall(r"<th[^>]*>(.*?)</th>", table_html, re.S)]
    return "Table: " + ", ".join(heads) + ". The same data is in the article's source on GitHub."


def medium_pre(match):
    lang = match.group(1) or ""
    code = html.unescape(match.group(2)).rstrip("\n")
    lines = []
    for line in code.split("\n"):
        if not line.strip():
            lines.append("&nbsp;")
            continue
        stripped = line.lstrip(" \t")
        lead = (
            line[: len(line) - len(stripped)].replace("\t", WORD_JOINER + FIGURE_SPACE * 4).replace(" ", FIGURE_SPACE)
        )
        inner = re.sub(r"  +", lambda m: FIGURE_SPACE * len(m.group(0)), html.escape(stripped, quote=False))
        lines.append(lead + inner)
    cls = f' class="{lang}"' if lang else ""
    return f"<pre{cls}>" + "<br>".join(lines) + "</pre>"


def main():
    base = sys.argv[sys.argv.index("--base-url") + 1] if "--base-url" in sys.argv else ""
    TABLES.mkdir(exist_ok=True)
    body = subprocess.run(
        ["pandoc", str(SRC), "-f", "gfm", "-t", "html5", "--wrap=none", "--syntax-highlighting=none"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    # Title and headings: h1 stays the story title; ## -> h3 (large), ### -> h4 (small).
    body = re.sub(r"<h3([^>]*)>(.*?)</h3>", r"<h4>\2</h4>", body)
    body = re.sub(r"<h2([^>]*)>(.*?)</h2>", r"<h3>\2</h3>", body)
    body = re.sub(r"<h1([^>]*)>(.*?)</h1>", r"<h1>\2</h1>", body)

    # Tables -> images.
    tables = re.findall(r"<table>.*?</table>", body, re.S)
    for i, t in enumerate(tables, 1):
        name = render_table(i, t)
        body = body.replace(
            t, f'<figure><img src="{base}medium-tables/{name}" alt="{html.escape(table_alt(t))}"></figure>', 1
        )

    # Images: pandoc wraps them in <figure> with a caption from the alt text; keep the alt, drop the visible caption.
    body = re.sub(r"<figcaption.*?</figcaption>", "", body, flags=re.S)
    if base:
        body = re.sub(r'src="(?!https?:)([^"]+)"', lambda m: f'src="{base}{m.group(1)}"', body)

    # In-page links ("#slug") are GitHub anchors; Medium assigns its own, so keep only the link text.
    body = re.sub(r'<a href="#[^"]*">(.*?)</a>', r"\1", body, flags=re.S)

    # Code blocks -> Medium markup.
    body = re.sub(
        r'<pre(?: class="(?:sourceCode )?([\w-]+)")?[^>]*><code[^>]*>(.*?)</code></pre>', medium_pre, body, flags=re.S
    )
    body = re.sub(r"</pre>\s+", "</pre>", body)

    OUT.write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        "<title>LLM Councils: Choosing the OpenAI and Gemini Seats</title>"
        "<style>body{max-width:720px;margin:40px auto;padding:0 16px;font-family:Georgia,serif;font-size:20px;"
        "line-height:1.6;color:#242424}img{max-width:100%}pre{font-family:Menlo,monospace;font-size:15px;"
        "background:#f2f2f2;padding:16px;white-space:pre-wrap}h1,h3,h4{font-family:-apple-system,Helvetica,"
        "Arial,sans-serif}code{font-size:17px;background:#f2f2f2;padding:1px 4px}</style></head><body>\n"
        + body
        + "</body></html>\n"
    )
    print(f"wrote {OUT.name}: {len(tables)} tables as images, base URL {base or '(relative)'}")


if __name__ == "__main__":
    main()
