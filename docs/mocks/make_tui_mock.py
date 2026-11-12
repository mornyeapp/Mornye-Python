"""Writes docs/mocks/tui-download.html: the TUI's Download panel, direction A.

A 120×40 character grid drawn with the same palette as console.css, so the
two materials of the one interface can be judged side by side. Run it again
after changing the layout below:  python docs/mocks/make_tui_mock.py
"""

from __future__ import annotations

import html
from pathlib import Path

W, H = 120, 40
SIDE = 22  # sidebar box width

Seg = tuple[str, str]  # (text, css class)


def line(*segs: Seg, width: int = W, fill: str = " ") -> list[Seg]:
    used = sum(len(t) for t, _ in segs)
    assert used <= width, (used, width, segs)
    return [*segs, (fill * (width - used), "")]


def box(
    width: int,
    title: str,
    right: str,
    body: list[list[Seg]],
    height: int,
    focus: bool = False,
) -> list[list[Seg]]:
    """Square single-line border; the title sits in the top rule."""
    edge = "s" if focus else "r"
    top_l = [("┌─ ", edge), (title, "s" if focus else "t"), (" ", edge)]
    top_r = [(" ", edge), (right, "m"), (" ─┐", edge)] if right else [("─┐", edge)]
    used = sum(len(t) for t, _ in top_l + top_r)
    rows = [top_l + [("─" * (width - used), edge)] + top_r]
    inner = width - 4
    for i in range(height - 2):
        content = body[i] if i < len(body) else []
        rows.append([("│ ", edge), *line(*content, width=inner), (" │", edge)])
    rows.append([("└" + "─" * (width - 2) + "┘", edge)])
    return rows


def meter(value: float, n: int, cls: str = "s") -> list[Seg]:
    lit = round(value * n)
    return [("▮" * lit, cls), ("▯" * (n - lit), "r")]


def field(name: str, *value: Seg) -> list[Seg]:
    return [(f"{name:<9}", "m"), ("│ ", "r"), *value]


sidebar = box(
    SIDE,
    "MODES",
    "",
    [
        [("▸ Download", "s")],
        [("  Search", "t")],
        [("  Tracks", "t")],
        [("  Queue", "t"), ("      9", "s")],
        [("  Live", "t")],
        [("  History", "t")],
        [("  Session", "t")],
        [("  Library", "t")],
        [("  Server", "t")],
        [("  Extensions", "t")],
        [("  Health", "t")],
        [("  Signed", "t")],
        [("  Command", "t")],
    ],
    height=35,
)

MAIN = W - SIDE - 1
config = box(
    MAIN,
    "CONFIGURATION",
    "[ctrl+r] run",
    [
        [],
        field("URL", ("https://open.spotify.com/album/5d1Kc0Q7pX3vXa", "t")),
        field("CSV", ("—", "m")),
        field(
            "SOURCES",
            ("tidal", "t"),
            (" › ", "m"),
            ("qobuz", "t"),
            (" › ", "m"),
            ("deezer", "t"),
        ),
        field("QUALITY", ("[24/96 FLAC]", "s"), ("  hi-res, else lossless", "m")),
        field("OUTPUT", ("~/Music/Mornye/{artist}/{album}", "t")),
        field("LYRICS", ("apple › binilyrics › lrclib › ext:jellyfin", "t")),
        field("TAGS", ("enrich: deezer › apple · cover 3000px", "t")),
        [],
    ],
    height=11,
    focus=True,
)

tracks = [
    ("01", "Carrier Wave", "[24/96]", 1.0, "done", "ok"),
    ("02", "Static Bloom", "[24/96]", 1.0, "done", "ok"),
    ("03", "Low Hours", "[24/96]", 0.62, " 62%", "s"),
    ("04", "Second Antenna", "[24/96]", 0.0, "queued", "m"),
    ("05", "Glass Harbour", "[24/96]", 0.0, "queued", "m"),
    ("06", "Interference (feat. Kō)", "[16/44]", 0.0, "queued", "m"),
    ("08", "Dial Tone", "[  —  ]", 0.0, "not found", "e"),
]
dl_body: list[list[Seg]] = [[]]
for num, title, q, v, state, cls in tracks:
    dl_body.append(
        [
            (num + "  ", "m"),
            (f"{title:<30}", "t"),
            (q + "  ", "m" if q == "[  —  ]" else "k"),
            *meter(v, 20, {"ok": "ok", "e": "e"}.get(cls, "s")),
            ("  ", ""),
            (state, {"ok": "ok", "e": "e", "s": "s", "m": "m"}[cls]),
        ]
    )
dl_body += [[], [("tidal-web · 4.2 MB/s · 18.4 / 29.6 MB · 0:03 left", "m")]]
downloads = box(MAIN, "DOWNLOADING", "3 / 12", dl_body, height=12)

overall = box(
    MAIN,
    "RUN",
    "",
    [
        [],
        [("all  ", "m"), *meter(0.25, 48), ("  3 / 12", "s"), ("   1 failed", "e")],
        [],
        [("last  ", "m"), ("Harbour Lights — Mira Sol · 5 / 5 · 212.6 MB", "t")],
    ],
    height=12,
)

main_rows = config + downloads + overall
assert len(main_rows) == len(sidebar) == 35, (len(main_rows), len(sidebar))

rows: list[list[Seg]] = []
rows.append(
    line(
        ("MORNYE", "tb"),
        (" 2.0.6 ", "m"),
        ("─" * 78, "r"),
        (" ● downloading 3/12 ", "s"),
        ("──", "r"),
        (" 0:42 ", "m"),
        ("─", "r"),
    )
)
rows.append(line())
for left, right in zip(sidebar, main_rows):
    rows.append([*left, (" ", ""), *right])
rows.append(line())
rows.append(line(("▌", "s"), (" Downloading Night Signals — track 3 of 12", "t")))
hints = [
    ("[ctrl+r]", "k"),
    (" run   ", "m"),
    ("[ctrl+c]", "k"),
    (" stop   ", "m"),
    ("[/]", "k"),
    (" search   ", "m"),
    ("[ctrl+y]", "k"),
    (" copy cli   ", "m"),
    ("[t]", "k"),
    (" theme   ", "m"),
    ("[?]", "k"),
    (" help", "m"),
]
rows.append(line(*hints))
assert len(rows) == H, len(rows)
for r in rows:
    assert sum(len(t) for t, _ in r) == W, (sum(len(t) for t, _ in r), r[:3])


def render(rows: list[list[Seg]]) -> str:
    out = []
    for r in rows:
        out.append(
            "".join(
                f'<span class="{c}">{html.escape(t)}</span>' if c else html.escape(t)
                for t, c in r
            )
        )
    return "\n".join(out)


PAGE = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mock · TUI Download</title>
<link rel="stylesheet" href="console.css">
<style>
  body {{ display: grid; place-items: center; min-height: 100vh; padding: 24px; }}
  .term {{ background: var(--g-0); border: 1px solid var(--rule-strong); padding: 10px 12px; }}
  pre {{ margin: 0; font: 400 13px/1.2 var(--font); color: var(--text); font-variant-ligatures: none; }}
  .r {{ color: var(--rule-strong); }} .m {{ color: var(--muted); }} .t {{ color: var(--text); }}
  .tb {{ color: var(--text); font-weight: 700; }}
  .s {{ color: var(--signal); }} .ok {{ color: var(--ok); }} .e {{ color: var(--err); }}
  .k {{ color: var(--text-2); }}
  .cap {{ color: var(--muted); font-size: 11px; letter-spacing: .14em; text-transform: uppercase; margin: 0 0 8px; }}
</style>
</head>
<body>
<div>
  <p class="cap">TUI · Download panel · {W}×{H}</p>
  <div class="term"><pre>{render(rows)}</pre></div>
</div>
</body>
</html>
"""

Path(__file__).with_name("tui-download.html").write_text(PAGE, encoding="utf-8")
print("wrote tui-download.html")
