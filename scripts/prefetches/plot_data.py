#!/usr/bin/env python3
"""Plot the fill sweeps collected by collect_data.py.

Style comes from styling/paper_style.py; see styling/PLOTTING.md. Needs
seaborn, so run it with the repo's venv:

    .venv/bin/python scripts/prefetches/plot_data.py scripts/prefetches/thor.json
    .venv/bin/python scripts/prefetches/plot_data.py scripts/prefetches/thor.json --split

One figure per json, next to it: throughput (Mops) on the top row and DRAM
bandwidth (GB/s, read + write) on the bottom row, insertion on the left and
lookup on the right. A json without bandwidth gets the top row only.
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import seaborn as sns

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "styling"))

import paper_style as ps  # noqa: E402

# The two phases mode 11 times, and what each is called on the page.
PHASES = [
    ("set", "insertion"),
    ("get", "lookup"),
]
# (metric suffix, axis_labels() key, file suffix)
ROWS = [
    ("mops", "fill", "mops"),
    ("total_gbs", "fill_bw", "bw"),
]


# =============================================================================
# DATA
# =============================================================================


def load(path):
    data = json.loads(Path(path).read_text())
    return data if "series" in data else from_legacy(data, path)


def from_legacy(rows, path):
    """Lift the first collector's flat list of runs into the current shape.

    That list held one row per (prefetch, fill) run, with no repeats and no
    bandwidth, so the figure has no band and no bandwidth row.
    """
    series = {}
    for r in rows:
        entry = series.setdefault(r["identifier"], {"fills": [], "set_mops": [], "get_mops": []})
        entry["fills"].append(r["run_cfg"]["fill_factor"])
        entry["set_mops"].append(r.get("set_mops"))
        entry["get_mops"].append(r.get("get_mops"))
    cfg = rows[0]["run_cfg"] if rows else {}
    print(f"[!] {path}: flat json from the first collector, one run per point -- no band drawn")
    return {
        "ht_size_gib": cfg["size"] * 16 / (1 << 30) if cfg else None,
        "num_threads": cfg.get("numThreads"),
        "series": series,
    }


def frame(data, metric):
    """Long-form frame for one metric: series / x / y / lo / hi.

    `y` is the per-point median and lo/hi the min and max over the repeats,
    which is what the band shows.
    """
    rows = []
    for name in order(data):
        entry = data["series"][name]
        samples = entry.get(f"{metric}_samples") or []
        for i, (fill, y) in enumerate(zip(entry["fills"], entry.get(metric, []))):
            if y is None:
                continue
            point = samples[i] if i < len(samples) else None
            rows.append({
                "series": name,
                "x": fill,
                "y": y,
                "lo": min(point) if point else float("nan"),
                "hi": max(point) if point else float("nan"),
            })
    return pd.DataFrame(rows, columns=["series", "x", "y", "lo", "hi"])


def order(data):
    """Series that actually have points, in the plotting order."""
    have = [n for n, e in data["series"].items() if e.get("fills")]
    listed = [n for n in data.get("plot_order", []) if n in have]
    return listed + ps.order_series(set(have) - set(listed))


# =============================================================================
# PLOTTING
# =============================================================================


def draw(ax, df, series, title, labels, xticks, styles):
    for name in series:
        sub = df[df["series"] == name].sort_values("x")
        if sub.empty:
            continue
        style = styles[name]
        ps.draw_band(ax, sub, style)
        sns.lineplot(data=sub, x="x", y="y", ax=ax, legend=False, **style)

    xlabel, ylabel = ps.axis_labels(labels)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xticks(xticks)
    ax.set_xlim(min(xticks) - 5, max(xticks) + 5)
    ax.set_ylim(bottom=0)
    ps.tidy(ax)


def title_for(data, label):
    gib = data.get("ht_size_gib")
    threads = data.get("num_threads")
    bits = [label]
    if gib:
        bits.append(f"{gib:g} GiB table")
    if threads:
        bits.append(f"{threads} threads")
    return ", ".join(bits)


def plot(data, out_stem, split):
    ps.configure_style()
    palette = ps.configure_palette()

    series = order(data)
    if not series:
        print(f"[!] {out_stem}: no series has any points, nothing to plot")
        return

    styles = ps.styles_for(series, palette)
    xticks = sorted({f for n in series for f in data["series"][n]["fills"]})
    rows = [r for r in ROWS
            if not frame(data, f"{PHASES[0][0]}_{r[0]}").empty]
    # The legend has one entry per prefetch; wrap it rather than run off the page.
    ncol = min(len(series), 5)

    if split:
        for metric, labels, suffix in rows:
            for phase, label in PHASES:
                fig, ax = ps.get_subplots(1, 1)
                draw(ax, frame(data, f"{phase}_{metric}"), series,
                     title_for(data, label), labels, xticks, styles)
                ps.add_legend(fig, palette, series, ncol=ncol, styles=styles)
                ps.save(fig, f"{out_stem}_{phase}_{suffix}.png", legend_top=0.86)
        return

    fig, axes = ps.get_subplots(len(rows), len(PHASES))
    axes = axes.reshape(len(rows), len(PHASES))
    for row, (metric, labels, _) in zip(axes, rows):
        for ax, (phase, label) in zip(row, PHASES):
            draw(ax, frame(data, f"{phase}_{metric}"), series,
                 title_for(data, label), labels, xticks, styles)
    ps.add_legend(fig, palette, series, ncol=ncol, styles=styles)
    ps.save(fig, f"{out_stem}.png", legend_top=0.93 if len(rows) > 1 else 0.88)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jsons", nargs="+", help="collect_data.py output json(s)")
    ap.add_argument("--split", action="store_true",
                    help="one figure per panel instead of one grid")
    args = ap.parse_args()

    for path in args.jsons:
        path = Path(path)
        plot(load(path), str(path.with_suffix("")), args.split)


if __name__ == "__main__":
    main()
