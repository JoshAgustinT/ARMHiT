#!/usr/bin/env python3
"""Line chart of CPU DRAM bandwidth vs threads, with at most 1, 2, 3 or 4
threads in each 4-core group (collect_groups.py).

    .venv/bin/python scripts/cpu_bw/plot_groups.py scripts/cpu_bw/thor_groups.json

One panel per pattern. x: threads 1..14, y: perf's UCF DRAM rd + wr GB/s.
One line per threads-per-group limit (each group is filled up to it before
the next one starts), with a point at every thread count from 1. The rd / wr split is in the
json. Theoretical
DRAM peak as a dashed line. Saves a .png next to the json.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "styling"))

import matplotlib.pyplot as plt  # noqa: E402
import paper_style as ps  # noqa: E402

TITLES = {
    "read": "read\n(plain loads)",
    "read_l3": "read_l3\n(PRFM PLDL3KEEP only, no loads)",
    "write": "write\n(plain stores)",
    "write_nt": "write_nt\n(STNP non-temporal stores)",
    "write_pf": "write_pf\n(stores + PRFM PSTL1KEEP ahead)",
}
LABELS = {
    1: "1 per group: cores 0, 4, 8, 12",
    2: "2 per group: cores 0, 1, 4, 5, 8, 9, 12, 13",
    3: "3 per group: cores 0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13",
    4: "4 per group (all): cores 0, 1, 2, ..., 13",
}
DASHES = {1: (0, (1, 1)), 2: (0, (3, 1, 1, 1)), 3: (0, (5, 2)), 4: (0, (8, 2))}  # none solid, so overlaps show
COLORS = {k: plt.get_cmap("tab10")(k - 1) for k in LABELS}  # distinct colours, own palette for this plot


def main():
    path = Path(sys.argv[1])
    data = json.loads(path.read_text())
    peak = data["peak_gbs"]
    patterns = list(data["patterns"])

    ps.configure_style()
    fig, axes = ps.get_subplots(1, len(patterns), plot_w=3.6, plot_h=5)
    if len(patterns) == 1:  # a single panel comes back as one Axes, not a list
        axes = [axes]

    for ax, pattern in zip(axes, patterns):
        for per_group, label in LABELS.items():
            points = [p for p in data["patterns"][pattern] if p["per_group"] == per_group]
            xs = [p["threads"] for p in points]
            ys = [p["perf_gbs"] for p in points]
            ax.plot(xs, ys, marker="o", markersize=3, color=COLORS[per_group],
                    linestyle=DASHES[per_group], zorder=10 - per_group, label=label)  # shorter lines on top

        ax.axhline(peak, color="black", linestyle="--", linewidth=1, zorder=0,
                   label=f"theoretical DRAM peak ({peak:.0f})")
        ax.set_xticks(range(1, 15))
        ax.set_xlim(0.5, 14.5)
        ax.tick_params(axis="x", labelsize=7)
        ax.set_xlabel("threads")
        ax.set_title(TITLES.get(pattern, pattern), fontsize=10, fontweight="bold")
        ax.set_ylim(0, peak * 1.1)
        ps.tidy(ax)

    axes[0].set_ylabel("DRAM bandwidth (GB/s)")
    fig.suptitle("CPU sequential access, DRAM bandwidth vs threads, by threads per core group, "
                 f"{data['size_mib'] // 1024} GiB buffers, CPU {data['cpu_khz'] / 1e6:.2f} GHz, "
                 f"EMC {data['emc_hz'] / 1e6:.0f} MHz\n"
                 "Core groups: {0-3}, {4-7}, {8-11}, {12-13}; each group reaches DRAM at ~93 GB/s at most.",
                 y=0.99, fontsize=10)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, fontsize=8, loc="upper center", ncol=2,
               bbox_to_anchor=(0.5, 0.92))  # under the title
    ps.save(fig, str(path.with_suffix(".png")), legend_top=0.86)


if __name__ == "__main__":
    main()
