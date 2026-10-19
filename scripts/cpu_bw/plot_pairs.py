#!/usr/bin/env python3
"""Bar chart of CPU write DRAM bandwidth of 2-core pairs: each of cores 0-11
with core 12, then with core 13 (collect_pairs.py).

    .venv/bin/python scripts/cpu_bw/plot_pairs.py scripts/cpu_bw/thor_pairs.json

Two blocks of bars, "+12" and "+13", one bar per core paired with it, then a
reference block of pairs from inside one group ({2,3}, {6,7}, {10,11},
{12,13}). Bars are coloured by the (first) core's group and labelled with
their GB/s. Two cores in
different groups should write ~95 GB/s; ~84 would mean the pair shares a
group-like limit. y: perf's UCF DRAM wr GB/s. Saves a .png next to the json.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "styling"))

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
import paper_style as ps  # noqa: E402

GAP = 1.5  # empty bar slots between blocks
GROUPS = ["group {0-3}", "group {4-7}", "group {8-11}", "group {12-13}"]


def main():
    path = Path(sys.argv[1])
    data = json.loads(path.read_text())

    ps.configure_style()
    fig, ax = ps.get_subplots(1, 1, plot_w=12, plot_h=4.5)

    x = 0
    ticks = []
    tick_labels = []
    for name, points in data["pairs"].items():
        xs = [x + j for j in range(len(points))]
        ys = [p["perf_wr_gbs"] for p in points]
        colors = [plt.get_cmap("tab10")(min(p["core"] // 4, 3)) for p in points]
        ax.bar(xs, ys, 0.8, color=colors, edgecolor="black", linewidth=0.6)
        for bx, y in zip(xs, ys):
            ax.text(bx, y + 1.5, f"{y:.1f}", ha="center", va="bottom", fontsize=6)
        title = "same group (reference)" if name == "same group" else f"paired with core {name[1:]}"
        ax.text(sum(xs) / len(xs), -0.22, title,
                transform=ax.get_xaxis_transform(), ha="center", va="top", fontsize=9,
                fontweight="bold")
        ticks += xs
        if name == "same group":
            tick_labels += [f"{p['core']}\n{p['partner']}" for p in points]  # stacked: fits the bar width
        else:
            tick_labels += [str(p["core"]) for p in points]
        x = xs[-1] + 1 + GAP

    ax.set_xticks(ticks, tick_labels)
    ax.set_xlabel("other core of the pair (reference block: both cores)", labelpad=42)
    ax.set_ylabel("DRAM write bandwidth (GB/s)")
    ax.set_ylim(0, 120)
    ps.tidy(ax)
    ax.grid(False, axis="x")

    fig.suptitle("CPU writes, 2-core pairs: each of cores 0-11 with core 12, then with core 13, plus same-group pairs, "
                 f"{data['size_mib'] // 1024} GiB buffers,\n"
                 f"CPU {data['cpu_khz'] / 1e6:.2f} GHz, EMC {data['emc_hz'] / 1e6:.0f} MHz; "
                 "two cores in different groups ~95 GB/s, in the same group ~84",
                 y=0.99, fontsize=10)
    handles = [Patch(facecolor=plt.get_cmap("tab10")(g), edgecolor="black") for g in range(4)]
    fig.legend(handles, GROUPS, fontsize=8, loc="upper center", ncol=4, bbox_to_anchor=(0.5, 0.9))
    ps.save(fig, str(path.with_suffix(".png")), legend_top=0.87)


if __name__ == "__main__":
    main()
