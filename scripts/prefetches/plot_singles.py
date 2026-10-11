#!/usr/bin/env python3
"""Plot L1 / L2 / L3 / none + L2 + L1 from collect_data.py's fill sweep.

Same figure as plot_data.py, minus the doubles (hash-table tricks) and the
STRM variants (within noise of their KEEP counterparts), so the per-level
comparison is readable. L2 + L1 (L2_L1) stays, as the reference used
elsewhere. Needs seaborn, so run it with the repo's venv:

    .venv/bin/python scripts/prefetches/plot_singles.py scripts/prefetches/thor.json
    .venv/bin/python scripts/prefetches/plot_singles.py scripts/prefetches/thor.json --split

Writes <json stem>_singles.png next to the json (or _singles_<phase>_<row>.png
with --split). Colours match plot_data.py.
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
# The schemes plotted, in legend order (one row).
SCHEMES = ["L1", "L2", "L3", "NONE", "L2_L1"]

# tab20 index per scheme: the same colours as plot_data.py.
_TAB20 = colormaps["tab20"].colors
COLORS = {"L2": 0, "L2_L1": 1, "L3": 2, "L1": 10, "NONE": 14}

# Legend text per PREFETCH= name.
NAMES = {
    "NONE": "none", "L2_L1": "L2 + L1", "L1": "L1", "L2": "L2", "L3": "L3",
}


def style(name):
    """Solid line; L2 + L1 gets square markers, as in plot_data.py."""
    return {"color": _TAB20[COLORS[name]], "linestyle": "-",
            "marker": "s" if name == "L2_L1" else "o"}


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
    """Plotted schemes that have points, in the legend's order."""
    return [n for n in SCHEMES if data["series"].get(n, {}).get("fills")]


# =============================================================================
# PLOTTING
# =============================================================================


def draw(ax, data, metric, names, title, labels, xticks):
    for name in names:
        df = frame(data["series"][name], metric)
        if df.empty:
            continue
        sns.lineplot(data=df, x="x", y="y", ax=ax, legend=False, **style(name))

    xlabel, ylabel = ps.axis_labels(labels)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xticks(xticks)
    ax.set_xlim(min(xticks) - 5, max(xticks) + 5)
    ax.set_ylim(bottom=0)
    ps.tidy(ax)


def add_legend(fig, names):
    handles = [Line2D([0], [0], label=NAMES[n], **style(n)) for n in names]
    fig.legend(fontsize=8, handles=handles, loc="upper center", ncol=len(names))


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
        print(f"[!] {out_stem}: no plotted scheme has any points, nothing to plot")
        return
    xticks = sorted({f for n in names for f in data["series"][n]["fills"]})

    if split:
        for metric, labels, suffix in ROWS:
            for phase, label in PHASES:
                fig, ax = ps.get_subplots(1, 1, plot_w=8, plot_h=5)
                draw(ax, data, f"{phase}_{metric}", names,
                     title_for(data, label), labels, xticks)
                add_legend(fig, names)
                ps.save(fig, f"{out_stem}_singles_{phase}_{suffix}.png",
                        legend_top=0.92)
        return

    fig, axes = ps.get_subplots(len(ROWS), len(PHASES), plot_w=5, plot_h=4)
    for row, (metric, labels, _) in zip(axes, ROWS):
        for ax, (phase, label) in zip(row, PHASES):
            draw(ax, data, f"{phase}_{metric}", names,
                 title_for(data, label), labels, xticks)
    add_legend(fig, names)
    ps.save(fig, f"{out_stem}_singles.png", legend_top=0.95)


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
