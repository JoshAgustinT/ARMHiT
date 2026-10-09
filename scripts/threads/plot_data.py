#!/usr/bin/env python3
"""Plot the thread sweeps collected by collect_data.py.

Style comes from styling/paper_style.py; see styling/PLOTTING.md. Needs
seaborn, so run it with the repo's venv:

    .venv/bin/python scripts/threads/plot_data.py scripts/threads/thor-large.json

One figure per json, next to it: four panels in a row, uniform insertion /
lookup then sequential insertion / lookup. Each panel has throughput (Mops,
solid, left axis) and DRAM bandwidth (GB/s read + write, dashed, right axis)
per prefetch, plus black lines (same solid / dashed) that scale DOUBLE's
1-thread Mops and GB/s linearly with threads. All four panels share the Mops and the GB/s scale.
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import seaborn as sns
from matplotlib.lines import Line2D

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "styling"))

import paper_style as ps  # noqa: E402

# (mode, phase, title)
PANELS = [
    ("uniform", "set", "uniform insertion"),
    ("uniform", "get", "uniform lookup"),
    ("sequential", "set", "sequential insertion"),
    ("sequential", "get", "sequential lookup"),
]
# metric suffix -> how it is drawn: (axis, linestyle, marker)
METRICS = {
    "mops": ("left", "-", "o"),
    "total_gbs": ("right", "--", "s"),
}
# Linear-scaling reference in black, drawn like its metric (solid Mops, dashed GB/s).
LINEAR = {m: style for m, (_, style, _) in METRICS.items()}
LINEAR_FOR = "DOUBLE"  # only this prefetch gets the linear-scaling reference


# =============================================================================
# DATA
# =============================================================================


def frame(series, metric):
    """Long-form frame for one metric: x / y / lo / hi / linear.

    `y` is the per-point median, lo/hi the min and max over the repeats
    (the band), and `linear` the 1-thread median times the thread count.
    """
    rows = []
    samples = series.get(f"{metric}_samples") or []
    base = None
    for i, (threads, y) in enumerate(zip(series["threads"], series[metric])):
        if y is None:
            continue
        if threads == 1:
            base = y
        point = samples[i] if i < len(samples) else None
        rows.append({
            "x": threads,
            "y": y,
            "lo": min(point) if point else float("nan"),
            "hi": max(point) if point else float("nan"),
            "linear": base * threads if base is not None else float("nan"),
        })
    return pd.DataFrame(rows, columns=["x", "y", "lo", "hi", "linear"])


def order(data):
    """Prefetches that have points in any mode, in the plotting order."""
    have = {n for m in data["modes"].values() for n, e in m["series"].items()
            if e.get("threads")}
    listed = [n for n in data.get("plot_order", []) if n in have]
    return listed + ps.order_series(have - set(listed))


# =============================================================================
# PLOTTING
# =============================================================================


def draw(ax, ax_bw, data, mode, phase, names, palette, xticks, title):
    axes = {"left": ax, "right": ax_bw}
    for name in names:
        series = data["modes"].get(mode, {}).get("series", {}).get(name)
        if not series or not series.get("threads"):
            continue
        color = ps.series_style(name, palette)["color"]
        for metric, (side, linestyle, marker) in METRICS.items():
            df = frame(series, f"{phase}_{metric}")
            if df.empty:
                continue
            style = {"color": color, "linestyle": linestyle, "marker": marker}
            ps.draw_band(axes[side], df, style)
            sns.lineplot(data=df, x="x", y="y", ax=axes[side], legend=False, **style)
            if name == LINEAR_FOR:
                axes[side].plot(df["x"], df["linear"], color="black", linestyle=LINEAR[metric],
                                linewidth=1.5, zorder=1)

    xlabel, ylabel = ps.axis_labels("threads")
    _, ylabel_bw = ps.axis_labels("threads_bw")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax_bw.set_ylabel(ylabel_bw)
    ax.set_title(title)
    # Every thread count is a point; label 1 and the even ones so they fit.
    ax.set_xticks([t for t in xticks if t == 1 or t % 2 == 0])
    ax.set_xlim(min(xticks) - 0.5, max(xticks) + 0.5)
    for a in (ax, ax_bw):
        a.set_ylim(bottom=0)
        ps.tidy(a)
    ax_bw.grid(False)  # one grid per panel, the left axis's


def add_legend(fig, palette, names, ncol=None):
    """Prefetch colours, then what each line style means."""
    handles = [Line2D([0], [0], label=ps.display_name(n), marker="o",
                      color=ps.series_style(n, palette)["color"])
               for n in names]
    handles += [
        Line2D([0], [0], color="grey", linestyle="-", marker="o", label="Mops (left)"),
        Line2D([0], [0], color="grey", linestyle="--", marker="s", label="GB/s (right)"),
        Line2D([0], [0], color="black", linewidth=1.5,
               label=f"linear from 1 thread ({ps.display_name(LINEAR_FOR)})"),
    ]
    fig.legend(fontsize=8, handles=handles, loc="upper center", ncol=ncol or len(handles))


def title_for(data, label):
    gib = data.get("ht_size_gib")
    bits = [label]
    if gib:
        bits.append(f"{gib:g} GiB" if gib >= 1 else f"{gib * 1024:g} MiB")
    if data.get("fill"):
        bits.append(f"{data['fill']}% fill")
    return ", ".join(bits)


def plot(data, out_stem):
    ps.configure_style()
    palette = ps.configure_palette()

    names = order(data)
    if not names:
        print(f"[!] {out_stem}: no series has any points, nothing to plot")
        return
    xticks = sorted({t for m in data["modes"].values() for e in m["series"].values()
                     for t in e.get("threads", [])})

    fig, axes = ps.get_subplots(1, len(PANELS))
    bw_axes = [ax.twinx() for ax in axes]
    for ax, ax_bw, (mode, phase, label) in zip(axes, bw_axes, PANELS):
        draw(ax, ax_bw, data, mode, phase, names, palette, xticks,
             title_for(data, label))
    # Same Mops scale and same GB/s scale on every panel, so they compare by eye.
    for side in (axes, bw_axes):
        top = max(a.get_ylim()[1] for a in side)
        for a in side:
            a.set_ylim(0, top)
    add_legend(fig, palette, names)
    ps.save(fig, f"{out_stem}.png", legend_top=0.9)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jsons", nargs="+", help="collect_data.py output json(s)")
    args = ap.parse_args()

    for path in args.jsons:
        path = Path(path)
        plot(json.loads(path.read_text()), str(path.with_suffix("")))


if __name__ == "__main__":
    main()
