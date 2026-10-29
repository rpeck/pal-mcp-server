"""Draw the two plots for the LLM Councils seat-selection article as standalone HTML (SVG) files.

Data: Artificial Analysis Intelligence Index v5 leaderboard, read 2026-10-28. Render each HTML file to PNG with
headless Chrome, for example:
    chrome --headless=new --force-device-scale-factor=2 --window-size=1400,820 --screenshot=out.png file://.../x.html
"""

import math
from pathlib import Path

OUT = Path(__file__).resolve().parent
SLUG = "llm-councils-choosing-the-openai-and-gemini-seats"

# Validated categorical slots 1-3 (light mode, all pairs): blue, orange, aqua.
COLOR = {"Gemini": "#2a78d6", "Claude": "#eb6834", "GPT": "#1baf7a"}
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e7e6e2"
W, H = 1400, 820
LEFT, RIGHT, TOP, BOTTOM = 110, 60, 150, 100


def page(title, subtitle, body, legend):
    keys = "".join(
        f'<g transform="translate({LEFT + i * 150},112)"><circle r="7" fill="{COLOR[v]}"/>'
        f'<text x="16" y="6" class="t2">{v}</text></g>'
        for i, v in enumerate(legend)
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
  html, body {{ margin: 0; background: {SURFACE}; }}
  svg {{ display: block; font-family: -apple-system, 'Helvetica Neue', Helvetica, Arial, sans-serif; }}
  .t1 {{ fill: {INK}; font-size: 30px; font-weight: 700; }}
  .t2 {{ fill: {INK2}; font-size: 20px; }}
  .ax {{ fill: {INK2}; font-size: 18px; }}
  .lbl {{ fill: {INK}; font-size: 18px; font-weight: 600; }}
  .note {{ fill: {INK2}; font-size: 18px; }}
</style></head><body>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
<rect width="{W}" height="{H}" fill="{SURFACE}"/>
<text x="{LEFT}" y="52" class="t1">{title}</text>
<text x="{LEFT}" y="84" class="t2">{subtitle}</text>
{keys}
{body}
</svg></body></html>
"""


def dot(x, y, color):
    # 2px surface ring keeps overlapping markers apart.
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{color}" stroke="{SURFACE}" stroke-width="2"/>'


def label(x, y, text, anchor="start", cls="lbl"):
    return f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" text-anchor="{anchor}">{text}</text>'


def cost_vs_score():
    xmin, xmax, ymin, ymax = 0.1, 16.0, 25, 62
    px = lambda c: LEFT + (math.log10(c) - math.log10(xmin)) / (math.log10(xmax) - math.log10(xmin)) * (W - LEFT - RIGHT)
    py = lambda s: TOP + (ymax - s) / (ymax - ymin) * (H - TOP - BOTTOM)
    lines = {
        ("GPT", "GPT-6.1 Sol"): [(0.161, 34.5), (0.359, 46.1), (0.591, 50.2), (1.272, 54.5)],
        ("GPT", "GPT-6 Astra"): [(1.424, 43.0), (2.732, 51.5), (3.239, 54.8), (6.245, 56.9)],
        ("Claude", "Claude Opus 5.5"): [(0.976, 34.5), (2.451, 48.1), (3.465, 51.7), (6.047, 57.0), (10.036, 58.4)],
        ("Gemini", "Gemini 3.8 Flash"): [(6.259, 30.0), (7.766, 30.5)],
    }
    points = {("Gemini", "Gemini 4 Argon"): (7.683, 50.4), ("Gemini", "Gemini 4 Carbon"): (7.756, 59.4)}
    out = []
    for c in (0.1, 0.3, 1, 3, 10):
        out.append(f'<line x1="{px(c):.1f}" y1="{TOP}" x2="{px(c):.1f}" y2="{H - BOTTOM}" stroke="{GRID}" stroke-width="1"/>')
        out.append(label(px(c), H - BOTTOM + 30, f"${c:g}", "middle", "ax"))
    for s in (30, 40, 50, 60):
        out.append(f'<line x1="{LEFT}" y1="{py(s):.1f}" x2="{W - RIGHT}" y2="{py(s):.1f}" stroke="{GRID}" stroke-width="1"/>')
        out.append(label(LEFT - 14, py(s) + 6, str(s), "end", "ax"))
    out.append(label((LEFT + W - RIGHT) / 2, H - 30, "Cost per task of AA's Index run (USD, log scale)", "middle", "ax"))
    out.append(f'<text transform="translate(34,{(TOP + H - BOTTOM) / 2}) rotate(-90)" class="ax" text-anchor="middle">AA Intelligence Index v5</text>')
    for (vendor, name), pts in lines.items():
        d = " ".join(f"{'M' if i == 0 else 'L'}{px(c):.1f},{py(s):.1f}" for i, (c, s) in enumerate(pts))
        out.append(f'<path d="{d}" fill="none" stroke="{COLOR[vendor]}" stroke-width="2"/>')
        out += [dot(px(c), py(s), COLOR[vendor]) for c, s in pts]
    for (vendor, name), (c, s) in points.items():
        out.append(dot(px(c), py(s), COLOR[vendor]))
    # Direct labels at each line's cheapest (low effort) end, and beside the single points.
    out.append(label(px(0.161) + 14, py(34.5) + 26, "Sol (low → max)"))
    out.append(label(px(1.424) + 14, py(43.0) + 22, "Astra (low → max)"))
    out.append(label(px(0.976) + 14, py(34.5) + 24, "Opus 5.5 (low → max)"))
    out.append(label(px(7.766) + 16, py(30.5) + 6, "3.8 Flash"))
    out.append(label(px(7.683) + 16, py(50.4) + 6, "Argon"))
    out.append(label(px(7.756) + 16, py(59.4) + 6, "Carbon"))
    # Callout: Sol at max against Astra at high.
    y = py(54.6)
    out.append(f'<line x1="{px(1.272) + 10:.1f}" y1="{y:.1f}" x2="{px(3.239) - 10:.1f}" y2="{y:.1f}" stroke="{INK2}" stroke-width="1.5"/>')
    out.append(f'<line x1="{px(1.9):.1f}" y1="{y - 6:.1f}" x2="{px(1.0):.1f}" y2="{py(59.6):.1f}" stroke="{INK2}" stroke-width="1"/>')
    out.append(label(px(1.0) - 6, py(60.4), "Sol at max ≈ Astra at high,", "end", "note"))
    out.append(label(px(1.0) - 6, py(60.4) + 24, "for about 40% of the cost", "end", "note"))
    out.append(label(px(6.259) - 16, py(30.0) - 18, "same cost per task as Argon,", "end", "note"))
    out.append(label(px(6.259) - 16, py(30.0) + 6, "about 20 points lower", "end", "note"))
    html = page(
        "More effort or a bigger model: what each point costs",
        "Each line joins one model's effort rows, low to max. Read on 2026-10-28.",
        "\n".join(out),
        ["Gemini", "Claude", "GPT"],
    )
    (OUT / f"{SLUG}-plot-cost-vs-score.html").write_text(html)


def facts_vs_code():
    xmin, xmax, ymin, ymax = 20, 50, 20, 100
    px = lambda v: LEFT + (v - xmin) / (xmax - xmin) * (W - LEFT - RIGHT)
    py = lambda v: TOP + (ymax - v) / (ymax - ymin) * (H - TOP - BOTTOM)
    pts = [
        ("Gemini", "Gemini 4 Argon", 25.7, 95.7, (16, 6, "start")),
        ("Gemini", "Gemini 4 Carbon", 40.6, 88.6, (16, 6, "start")),
        ("GPT", "GPT-6 Astra", 40.1, 57.5, (16, 6, "start")),
        ("GPT", "GPT-6.1 Sol", 36.7, 51.0, (-16, 6, "end")),
        ("Claude", "Claude Opus 5.5", 35.9, 36.1, (16, 6, "start")),
        ("Claude", "Claude Fable 5.1", 30.3, 29.8, (-16, 6, "end")),
    ]
    out = []
    for v in (20, 30, 40, 50):
        out.append(f'<line x1="{px(v):.1f}" y1="{TOP}" x2="{px(v):.1f}" y2="{H - BOTTOM}" stroke="{GRID}" stroke-width="1"/>')
        out.append(label(px(v), H - BOTTOM + 30, f"{v}%", "middle", "ax"))
    for v in (20, 40, 60, 80, 100):
        out.append(f'<line x1="{LEFT}" y1="{py(v):.1f}" x2="{W - RIGHT}" y2="{py(v):.1f}" stroke="{GRID}" stroke-width="1"/>')
        out.append(label(LEFT - 14, py(v) + 6, f"{v}%", "end", "ax"))
    out.append(label((LEFT + W - RIGHT) / 2, H - 30, "FrontierCode 2.0: changes a maintainer would merge (code-quality judgment)", "middle", "ax"))
    out.append(f'<text transform="translate(34,{(TOP + H - BOTTOM) / 2}) rotate(-90)" class="ax" text-anchor="middle">Non-hallucination: says "I don\'t know" instead of guessing</text>')
    for vendor, name, x, y, (dx, dy, anchor) in pts:
        out.append(dot(px(x), py(y), COLOR[vendor]))
        out.append(label(px(x) + dx, py(y) + dy, name, anchor))
    out.append(label(px(21), py(84), "Rarely invents facts:", "start", "note"))
    out.append(label(px(21), py(84) + 24, "the contract and fact checker", "start", "note"))
    out.append(label(px(49.5), py(27), "Stronger code judgment,", "end", "note"))
    out.append(label(px(49.5), py(27) + 24, "more confident guesses", "end", "note"))
    html = page(
        "Why each seat gets a different job",
        "Every model at high effort, from AA Intelligence Index v5. Read on 2026-10-28.",
        "\n".join(out),
        ["Gemini", "Claude", "GPT"],
    )
    (OUT / f"{SLUG}-plot-facts-vs-code.html").write_text(html)


if __name__ == "__main__":
    cost_vs_score()
    facts_vs_code()
