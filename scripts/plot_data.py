#!/usr/bin/env python3
"""Plot collect_data.py output: find and insert Mops vs fill factor, one line
per prefetch flavour. Standard library only; writes an SVG (open it in any
browser).

    python3 scripts/plot_data.py thor.json thor.svg
"""
import json
import sys
from collections import defaultdict

# Same order as collect_data.PREFETCHES so a flavour keeps its colour.
ORDER = ["DOUBLE", "DOUBLE_L2", "L1", "L2", "L3", "NTA", "L2STRM", "L3STRM", "NONE"]
COLORS = ["#1b2a49", "#3b5b92", "#d1495b", "#edae49", "#00798c",
          "#66a182", "#8d6a9f", "#c97b84", "#888888"]

PANELS = [("get_mops", "Find throughput"), ("set_mops", "Insert throughput")]
W, H = 460, 340               # one panel
ML, MR, MT, MB = 64, 16, 40, 52
LEGEND_H = 34


def nice_ceil(v):
    """Round up to 1/2/2.5/5 x 10^k for a tidy axis top."""
    if v <= 0:
        return 1
    mag = 10 ** (len(str(int(v))) - 1)
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag


def load(path):
    rows = json.load(open(path))
    series = defaultdict(dict)  # (metric, prefetch) -> {fill: [values]}
    for r in rows:
        pf = r["identifier"].split("-")[0]
        fill = int(r["run_cfg"]["fill_factor"])
        for metric, _ in PANELS:
            if r.get(metric) is not None:
                series[(metric, pf)].setdefault(fill, []).append(r[metric])
    names = {pf for _, pf in series}
    names = [n for n in ORDER if n in names] + sorted(names - set(ORDER))
    meta = rows[0]["run_cfg"] if rows else {}
    return series, names, meta


def panel(out, x0, metric, title, series, names, fills):
    pw, ph = W - ML - MR, H - MT - MB
    ox, oy = x0 + ML, LEGEND_H + MT
    vals = [v for (m, _), pts in series.items() if m == metric
            for vs in pts.values() for v in vs]
    top = nice_ceil(max(vals) if vals else 1)
    xmin, xmax = min(fills) - 5, max(fills) + 5

    def X(f):
        return ox + (f - xmin) / (xmax - xmin) * pw

    def Y(v):
        return oy + ph - v / top * ph

    out.append(f'<text x="{ox + pw / 2}" y="{oy - 14}" class="t">{title}</text>')
    for i in range(6):
        v = top * i / 5
        y = Y(v)
        out.append(f'<line x1="{ox}" y1="{y:.1f}" x2="{ox + pw}" y2="{y:.1f}" class="g"/>')
        out.append(f'<text x="{ox - 6}" y="{y + 4:.1f}" class="yl">{v:g}</text>')
    for f in fills:
        out.append(f'<text x="{X(f):.1f}" y="{oy + ph + 16}" class="xl">{f}</text>')
    out.append(f'<line x1="{ox}" y1="{oy + ph}" x2="{ox + pw}" y2="{oy + ph}" class="a"/>')
    out.append(f'<text x="{ox + pw / 2}" y="{oy + ph + 38}" class="xt">Fill factor (%)</text>')
    out.append(f'<text transform="translate({x0 + 16},{oy + ph / 2}) rotate(-90)" '
               f'class="xt">Mops</text>')

    for name in names:
        pts = series.get((metric, name))
        if not pts:
            continue
        color = COLORS[ORDER.index(name) % len(COLORS)] if name in ORDER else "#000"
        xy = [(X(f), Y(sorted(v)[len(v) // 2])) for f, v in sorted(pts.items())]
        path = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
        out.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="2"/>')
        for x, y in xy:
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"/>')


def plot(json_file, out_file):
    series, names, meta = load(json_file)
    fills = sorted({f for pts in series.values() for f in pts})
    total_w = W * len(PANELS)
    total_h = LEGEND_H + H + 20
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{total_h}" '
           f'viewBox="0 0 {total_w} {total_h}" font-family="sans-serif">',
           "<style>.t{font-size:14px;font-weight:bold;text-anchor:middle}"
           ".g{stroke:#e0e0e0;stroke-dasharray:3 3}.a{stroke:#444}"
           ".yl{font-size:11px;text-anchor:end;fill:#333}"
           ".xl{font-size:11px;text-anchor:middle;fill:#333}"
           ".xt{font-size:12px;text-anchor:middle;fill:#222}"
           ".lg{font-size:12px;fill:#222}</style>",
           f'<rect width="{total_w}" height="{total_h}" fill="white"/>']

    # legend across the top
    lx = 20
    for name in names:
        color = COLORS[ORDER.index(name) % len(COLORS)] if name in ORDER else "#000"
        out.append(f'<line x1="{lx}" y1="18" x2="{lx + 18}" y2="18" stroke="{color}" stroke-width="2"/>')
        out.append(f'<circle cx="{lx + 9}" cy="18" r="3" fill="{color}"/>')
        out.append(f'<text x="{lx + 24}" y="22" class="lg">{name}</text>')
        lx += 30 + 8 * len(name)
    if meta:
        gib = meta["size"] * 16 / (1 << 30)
        out.append(f'<text x="{total_w - 12}" y="22" class="lg" text-anchor="end">'
                   f'{gib:g} GiB table, {meta["numThreads"]} threads, '
                   f'read-factor {meta["readFactor"]}</text>')

    for i, (metric, title) in enumerate(PANELS):
        panel(out, i * W, metric, title, series, names, fills)
    out.append("</svg>")
    with open(out_file, "w") as f:
        f.write("\n".join(out))
    print(f"[OK] Plot saved to {out_file}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 plot_data.py <input.json> <output.svg>")
        sys.exit(1)
    plot(sys.argv[1], sys.argv[2])
