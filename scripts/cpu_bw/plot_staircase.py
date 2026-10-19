#!/usr/bin/env python3
"""Bar chart of CPU write DRAM bandwidth as threads fill one core group and
spill into the next (collect_staircase.py).

    .venv/bin/python scripts/cpu_bw/plot_staircase.py scripts/cpu_bw/thor_staircase.json

One block of bars per sequence; N threads run on the first N cores of the
sequence, so each block has one bar per thread count (1, 2, ...), labelled
with its GB/s and the sequence named under the block. Hatched bars: the
threads have reached cores outside the sequence's first group. y: perf's UCF
DRAM wr GB/s. If the cores sit in groups
{0-3}, {4-7}, {8-11}, {12-13} capped at ~93 GB/s each, every block climbs,
goes flat once its first group is full, then jumps when it reaches the next
group. Theoretical DRAM peak as a dashed line. Saves a .png next to the json.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "styling"))

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
import paper_style as ps  # noqa: E402

GAP = 1.5  # empty bar slots between blocks


def group(core):
    """Core group {0-3}, {4-7}, {8-11}, {12-13} -> 0, 1, 2, 3."""
    return min(core // 4, 3)


def main():
    path = Path(sys.argv[1])
    data = json.loads(path.read_text())
    peak = data["peak_gbs"]

    ps.configure_style()
    fig, ax = ps.get_subplots(1, 1, plot_w=10, plot_h=4.5)

    x = 0
    ticks = []
    tick_labels = []
    for i, (sequence, points) in enumerate(data["sequences"].items()):
        xs = [x + j for j in range(len(points))]
        ys = [p["perf_wr_gbs"] for p in points]
        cores = [[int(c) for c in p["cores"].strip(",").split(",")] for p in points]
        first = group(cores[0][0])
        spilled = [any(group(c) != first for c in cs) for cs in cores]
        ax.bar(xs, ys, 0.8, color=plt.get_cmap("tab10")(i), edgecolor="black", linewidth=0.6,
               hatch=["//" if s else "" for s in spilled])
        ax.text(sum(xs) / len(xs), -0.12, sequence, transform=ax.get_xaxis_transform(),
                ha="center", va="top", fontsize=9, fontweight="bold")
        for bx, y in zip(xs, ys):
            ax.text(bx, y + 3, f"{y:.0f}", ha="center", va="bottom", fontsize=7)
        ticks += xs
        tick_labels += [str(p["threads"]) for p in points]
        x = xs[-1] + 1 + GAP

    ax.axhline(peak, color="black", linestyle="--", linewidth=1, zorder=0,
               label=f"theoretical DRAM peak ({peak:.0f})")
    ax.set_xticks(ticks, tick_labels)
    ax.set_xlabel("threads (on the first N cores of each sequence)", labelpad=24)
    ax.set_ylabel("DRAM write bandwidth (GB/s)")
    ax.set_ylim(0, peak * 1.1)
    ps.tidy(ax)
    ax.grid(False, axis="x")

    fig.suptitle("CPU writes as threads fill one core group, then the next, "
                 f"{data['size_mib'] // 1024} GiB buffers, "
                 f"CPU {data['cpu_khz'] / 1e6:.2f} GHz, EMC {data['emc_hz'] / 1e6:.0f} MHz\n"
                 "core groups {0-3}, {4-7}, {8-11}, {12-13}",
                 y=0.99, fontsize=10)
    handles, labels = ax.get_legend_handles_labels()
    handles.append(Patch(facecolor="white", edgecolor="black", hatch="//"))
    labels.append("threads reach a second core group")
    fig.legend(handles, labels, fontsize=8, loc="upper center", ncol=2, bbox_to_anchor=(0.5, 0.9))
    ps.save(fig, str(path.with_suffix(".png")), legend_top=0.87)


if __name__ == "__main__":
    main()
