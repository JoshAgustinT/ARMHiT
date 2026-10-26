#!/usr/bin/env python3
"""Bar chart of CPU write DRAM bandwidth of every 2-core pair inside a core
group, same vs different cpufreq clock pair (collect_clock_pairs.py).

    .venv/bin/python scripts/cpu_bw/plot_clock_pairs.py scripts/cpu_bw/thor_clock_pairs.json

Two blocks of bars: every pair whose cores share a clock pair, then every pair
in the same group but different clock pairs (hatched). Bars are coloured by
group like plot_pairs.py and labelled with their GB/s. Dashed line: the reference pairs from different groups
(no sharing). y: perf's UCF DRAM wr GB/s. Saves a .png next to the json.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "styling"))

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
import paper_style as ps  # noqa: E402

GAP = 1.5  # empty bar slots between blocks


def main():
    path = Path(sys.argv[1])
    data = json.loads(path.read_text())

    ps.configure_style()
    fig, ax = ps.get_subplots(1, 1, plot_w=10, plot_h=4.5)

    x = 0
    ticks = []
    tick_labels = []
    groups = list(data["groups"])
    everything = [(g, p) for g, group in enumerate(groups) for p in data["groups"][group]]
    blocks = {"same clock pair": [(g, p) for g, p in everything if p["same_clock_pair"]],
              "same group, different clock pairs": [(g, p) for g, p in everything if not p["same_clock_pair"]]}
    for name, points in blocks.items():
        xs = [x + j for j in range(len(points))]
        ys = [p["perf_wr_gbs"] for _, p in points]
        ax.bar(xs, ys, 0.8, color=[plt.get_cmap("tab10")(g) for g, _ in points], edgecolor="black",
               linewidth=0.6, hatch="" if name == "same clock pair" else "//")
        for bx, y in zip(xs, ys):
            ax.text(bx, y + 1.5, f"{y:.1f}", ha="center", va="bottom", fontsize=7)
        ax.text(sum(xs) / len(xs), -0.2, name, transform=ax.get_xaxis_transform(),
                ha="center", va="top", fontsize=9, fontweight="bold")
        ticks += xs
        tick_labels += [f"{p['pair'][0]}\n{p['pair'][1]}" for _, p in points]  # stacked: fits the bar width
        x = xs[-1] + 1 + GAP

    reference = sum(p["perf_wr_gbs"] for p in data["reference"]) / len(data["reference"])
    pairs = ", ".join(f"{{{a},{b}}}" for a, b in (p["pair"] for p in data["reference"]))
    ax.axhline(reference, color="black", linestyle="--", linewidth=1, zorder=0)

    ax.set_xticks(ticks, tick_labels)
    ax.set_xlabel("pair of cores", labelpad=34)
    ax.set_ylabel("DRAM write bandwidth (GB/s)")
    ax.set_ylim(0, 120)
    ps.tidy(ax)
    ax.grid(False, axis="x")

    fig.suptitle("CPU writes, every 2-core pair inside a core group: same vs different cpufreq clock pair, "
                 f"{data['size_mib'] // 1024} GiB buffers,\n"
                 f"CPU {data['cpu_khz'] / 1e6:.2f} GHz, EMC {data['emc_hz'] / 1e6:.0f} MHz; "
                 "clock pairs {0,1}, {2,3}, ..., {12,13}",
                 y=0.99, fontsize=10)
    handles = [Patch(facecolor=plt.get_cmap("tab10")(g), edgecolor="black") for g in range(len(groups))]
    handles.append(plt.Line2D([0], [0], color="black", linestyle="--", linewidth=1))
    labels = groups + [f"different groups ({pairs}): {reference:.1f}"]
    fig.legend(handles, labels, fontsize=8, loc="upper center", ncol=5, bbox_to_anchor=(0.5, 0.9))
    ps.save(fig, str(path.with_suffix(".png")), legend_top=0.87)


if __name__ == "__main__":
    main()
