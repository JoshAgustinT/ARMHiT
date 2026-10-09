#!/usr/bin/env python3
"""Bar chart of the CPU + GPU bandwidth collected by collect_data.py.

    .venv/bin/python scripts/mixed_bw/plot_data.py scripts/mixed_bw/thor.json

Style comes from styling/paper_style.py (see styling/PLOTTING.md), which
needs seaborn, hence the venv. Saves a .png next to the json: per pattern a
stacked bar of what the CPU and the GPU each got (their own timers) beside
perf's combined GB/s, then the theoretical peak.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "styling"))

import paper_style as ps  # noqa: E402

WIDTH = 0.38


def main():
    path = Path(sys.argv[1])
    data = json.loads(path.read_text())
    peak = data["peak_gbs"]
    patterns = list(data["patterns"])
    results = [data["patterns"][p] for p in patterns]
    left = [x - WIDTH / 2 for x in range(len(patterns))]
    right = [x + WIDTH / 2 for x in range(len(patterns))]

    ps.configure_style()
    palette = ps.configure_palette(n=4)  # not prefetch flavours: own palette
    fig, ax = ps.get_subplots(1, 1, plot_w=5)

    # Left: CPU at the bottom, GPU stacked on top.
    cpu = [r["cpu_gbs"] for r in results]
    gpu = [r["gpu_gbs"] for r in results]
    for ys, bottom, label, colour in [(cpu, None, f"CPU ({data['cpu_threads']} threads)", palette[1]),
                                      (gpu, cpu, "GPU", palette[3])]:
        bars = ax.bar(left, ys, WIDTH, bottom=bottom, label=label, color=colour,
                      edgecolor="black", linewidth=0.6)
        ax.bar_label(bars, labels=[f"{y:.0f}" for y in ys], label_type="center",
                     color="white", fontsize=7)

    # Right: perf's combined total.
    perf = [r["perf_gbs"] for r in results]
    bars = ax.bar(right, perf, WIDTH, label="perf (UCF DRAM rd + wr)", color=palette[2],
                  edgecolor="black", linewidth=0.6)
    ax.bar_label(bars, labels=[f"{y:.0f}\n{y / peak:.0%}" for y in perf],
                 label_type="center", color="white", fontsize=7)

    bar = ax.bar([len(patterns)], [peak], WIDTH * 2, label="theoretical peak",
                 color="lightgrey", edgecolor="black", linewidth=0.6, hatch="//")
    ax.bar_label(bar, labels=[f"{peak:.0f}"], fontsize=6, padding=2)
    ax.axhline(peak, color="black", linestyle="--", linewidth=1, zorder=0)

    ax.set_xticks(range(len(patterns) + 1), patterns + ["peak"])
    ax.set_ylabel("DRAM bandwidth (GB/s)")
    ax.set_title(f"CPU + GPU streaming at once,\nCPU {data['cpu_khz'] / 1e6:.2f} GHz, "
                 f"GPU {data['gpu_hz'] / 1e6:.0f} MHz, EMC {data['emc_hz'] / 1e6:.0f} MHz")
    ax.set_ylim(0, peak * 1.15)
    ps.tidy(ax)
    ax.grid(False, axis="x")
    fig.legend(fontsize=8, loc="upper center", ncol=2)
    ps.save(fig, str(path.with_suffix(".png")), legend_top=0.9)


if __name__ == "__main__":
    main()
