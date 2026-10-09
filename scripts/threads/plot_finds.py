#!/usr/bin/env python3
"""Lookups only: the two find panels of plot_data.py, uniform and sequential.

Same data, style and helpers as plot_data.py (see there and
styling/PLOTTING.md); writes <json stem>_finds.png next to the json.

    .venv/bin/python scripts/threads/plot_finds.py scripts/threads/thor-large.json
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plot_data as pd_  # noqa: E402
from plot_data import ps  # noqa: E402

PANELS = [
    ("uniform", "get", "uniform lookup"),
    ("sequential", "get", "sequential lookup"),
]


def plot(data, out_stem):
    ps.configure_style()
    palette = ps.configure_palette()

    names = pd_.order(data)
    if not names:
        print(f"[!] {out_stem}: no series has any points, nothing to plot")
        return
    xticks = sorted({t for m in data["modes"].values() for e in m["series"].values()
                     for t in e.get("threads", [])})

    fig, axes = ps.get_subplots(1, len(PANELS))
    bw_axes = [ax.twinx() for ax in axes]
    for ax, ax_bw, (mode, phase, label) in zip(axes, bw_axes, PANELS):
        pd_.draw(ax, ax_bw, data, mode, phase, names, palette, xticks,
                 pd_.title_for(data, label))
    # Same Mops scale and same GB/s scale on both panels.
    for side in (axes, bw_axes):
        top = max(a.get_ylim()[1] for a in side)
        for a in side:
            a.set_ylim(0, top)
    pd_.add_legend(fig, palette, names, ncol=3)
    ps.save(fig, f"{out_stem}_finds.png", legend_top=0.86)


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
