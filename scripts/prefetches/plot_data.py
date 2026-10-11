#!/usr/bin/env python3
"""Plot the fill sweep collected by collect_data.py.

Style comes from styling/paper_style.py; see styling/PLOTTING.md. Needs
seaborn, so run it with the repo's venv:

    .venv/bin/python scripts/prefetches/plot_data.py scripts/prefetches/thor.json
    .venv/bin/python scripts/prefetches/plot_data.py scripts/prefetches/thor.json --split

One figure per json, next to it: throughput (Mops) on the top row and DRAM
bandwidth (GB/s, read + write) on the bottom row, insertion on the left and
lookup on the right. One run per point: Mops is the phase's, GB/s the median
of its 1 s perf samples (no band: the first and last samples are partly
warm-up / tail, not run-to-run spread).

Thirteen schemes do not fit the shared rocket palette, so this figure uses
its own: tab20's pairs, a scheme dark and its X + L1 double in the light
shade of the same hue, all solid.
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import seaborn as sns
from matplotlib import colormaps
from matplotlib.lines import Line2D

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "styling"))

import paper_style as ps  # noqa: E402

# The two phases dramhit runs, and what each is called on the page.
PHASES = [
    ("set", "insertion"),
    ("get", "lookup"),
]
# (metric suffix, axis_labels() key, file suffix)
ROWS = [
    ("mops", "fill", "mops"),
    ("total_gbs", "fill_bw", "bw"),
]
# Legend layout, 7 columns: singles on the first row, doubles on the second,
# each X + L1 under its X (DRAMHiT's double sits under L1, which has no + L1).
LEGEND = [
    "L1", "L2", "L3", "L1STRM", "L2STRM", "L3STRM", "NONE",
    "DOUBLE", "L2_L1", "L3_L1", "L1STRM_L1", "L2STRM_L1", "L3STRM_L1", None,
]

# tab20 index (pairs: 2k dark, 2k + 1 light) per scheme.
_TAB20 = colormaps["tab20"].colors
COLORS = {
    "L2": 0, "L2_L1": 1,              # blue
    "L3": 2, "L3_L1": 3,              # orange
    "L1STRM": 4, "L1STRM_L1": 5,      # green
    "L2STRM": 6, "L2STRM_L1": 7,      # red
    "L3STRM": 8, "L3STRM_L1": 9,      # purple
    "L1": 10,                         # brown
    "DOUBLE": 12,                     # pink
    "NONE": 14,                       # grey
}


# Legend text per PREFETCH= name.
NAMES = {
    "NONE": "none", "DOUBLE": "DRAMHiT double",
    "L1": "L1", "L2": "L2", "L3": "L3",
    "L1STRM": "L1 strm", "L2STRM": "L2 strm", "L3STRM": "L3 strm",
    "L2_L1": "L2 + L1", "L3_L1": "L3 + L1", "L1STRM_L1": "L1 strm + L1",
    "L2STRM_L1": "L2 strm + L1", "L3STRM_L1": "L3 strm + L1",
}


def style(name):
    """Solid line, colour from COLORS; the doubles get square markers."""
    return {"color": _TAB20[COLORS[name]] if name in COLORS else "black",
            "linestyle": "-",
            "marker": "s" if name.endswith("_L1") or name == "DOUBLE" else "o"}


# =============================================================================
# DATA
# =============================================================================


def frame(series, metric):
    """Long-form frame for one metric: x / y."""
    rows = [{"x": fill, "y": y}
            for fill, y in zip(series["fills"], series.get(metric, []))
            if y is not None]
    return pd.DataFrame(rows, columns=["x", "y"])


def order(data):
    """Schemes that have points, in the legend's order."""
    have = {n for n, e in data["series"].items() if e.get("fills")}
    return [n for n in LEGEND if n in have] + sorted(have - set(LEGEND))


# =============================================================================
# PLOTTING
# =============================================================================


def draw(ax, data, metric, names, styles, title, labels, xticks):
    for name in names:
        df = frame(data["series"][name], metric)
        if df.empty:
            continue
        sns.lineplot(data=df, x="x", y="y", ax=ax, legend=False, **styles[name])

    xlabel, ylabel = ps.axis_labels(labels)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xticks(xticks)
    ax.set_xlim(min(xticks) - 5, max(xticks) + 5)
    ax.set_ylim(bottom=0)
    ps.tidy(ax)


def add_legend(fig, names, styles):
    """Two rows (singles, doubles), blanks kept so each X + L1 sits under X."""
    blank = Line2D([0], [0], color="none", label=" ")
    handles = [Line2D([0], [0], label=NAMES.get(n, n), **styles[n]) if n in names
               else blank
               for n in LEGEND]
    # matplotlib fills a legend column by column, so interleave the two rows.
    handles = [h for pair in zip(handles[:7], handles[7:]) for h in pair]
    handles += [Line2D([0], [0], label=NAMES.get(n, n), **styles[n])
                for n in names if n not in LEGEND]
    fig.legend(fontsize=8, handles=handles, loc="upper center", ncol=7)


def title_for(data, label):
    bits = [label]
    if data.get("ht_size_gib"):
        bits.append(f"{data['ht_size_gib']:g} GiB")
    if data.get("num_threads"):
        bits.append(f"{data['num_threads']} threads")
    return ", ".join(bits)


def plot(data, out_stem, split):
    ps.configure_style()

    names = order(data)
    if not names:
        print(f"[!] {out_stem}: no series has any points, nothing to plot")
        return
    styles = {n: style(n) for n in names}
    xticks = sorted({f for n in names for f in data["series"][n]["fills"]})

    if split:
        for metric, labels, suffix in ROWS:
            for phase, label in PHASES:
                fig, ax = ps.get_subplots(1, 1, plot_w=8, plot_h=5)
                draw(ax, data, f"{phase}_{metric}", names, styles,
                     title_for(data, label), labels, xticks)
                add_legend(fig, names, styles)
                ps.save(fig, f"{out_stem}_{phase}_{suffix}.png", legend_top=0.88)
        return

    fig, axes = ps.get_subplots(len(ROWS), len(PHASES), plot_w=5, plot_h=4)
    for row, (metric, labels, _) in zip(axes, ROWS):
        for ax, (phase, label) in zip(row, PHASES):
            draw(ax, data, f"{phase}_{metric}", names, styles,
                 title_for(data, label), labels, xticks)
    add_legend(fig, names, styles)
    ps.save(fig, f"{out_stem}.png", legend_top=0.93)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jsons", nargs="+", help="collect_data.py output json(s)")
    ap.add_argument("--split", action="store_true",
                    help="one figure per panel instead of one grid")
    args = ap.parse_args()

    for path in args.jsons:
        path = Path(path)
        plot(json.loads(path.read_text()), str(path.with_suffix("")), args.split)


if __name__ == "__main__":
    main()
