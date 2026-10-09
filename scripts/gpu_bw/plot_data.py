#!/usr/bin/env python3
"""Bar chart of the GPU bandwidth collected by collect_data.py.

    .venv/bin/python scripts/gpu_bw/plot_data.py scripts/gpu_bw/thor.json

Style comes from styling/paper_style.py (see styling/PLOTTING.md), which
needs seaborn, hence the venv. Saves a .png next to the json: per pattern a
bar for mem.cu's own GB/s and one for perf's, then the theoretical peak.
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

    ps.configure_style()
    palette = ps.configure_palette(n=3)  # not prefetch flavours: own palette
    fig, ax = ps.get_subplots(1, 1, plot_w=5)

    for i, (key, label) in enumerate([("timed_gbs", "mem.cu timer"),
                                      ("perf_gbs", "perf (UCF DRAM rd + wr)")]):
        xs = [x + (i - 0.5) * WIDTH for x in range(len(patterns))]
        ys = [data["patterns"][p][key] for p in patterns]
        bars = ax.bar(xs, ys, WIDTH, label=label, color=palette[i + 1],
                      edgecolor="black", linewidth=0.6)
        ax.bar_label(bars, labels=[f"{y:.0f}\n{y / peak:.0%}" for y in ys],
                     label_type="center", color="white", fontsize=7)

    bar = ax.bar([len(patterns)], [peak], WIDTH * 2, label="theoretical peak",
                 color="lightgrey", edgecolor="black", linewidth=0.6, hatch="//")
    ax.bar_label(bar, labels=[f"{peak:.0f}"], fontsize=6, padding=2)
    ax.axhline(peak, color="black", linestyle="--", linewidth=1, zorder=0)

    ax.set_xticks(range(len(patterns) + 1), patterns + ["peak"])
    ax.set_ylabel("DRAM bandwidth (GB/s)")
    ax.set_title(f"GPU streaming, {data['buffer_mib'] // 1024} GiB buffers,\n"
                 f"GPU {data['gpu_hz'] / 1e6:.0f} MHz, EMC {data['emc_hz'] / 1e6:.0f} MHz")
    ax.set_ylim(0, peak * 1.15)
    ps.tidy(ax)
    ax.grid(False, axis="x")
    fig.legend(fontsize=8, loc="upper center", ncol=3)
    ps.save(fig, str(path.with_suffix(".png")), legend_top=0.93)


if __name__ == "__main__":
    main()
