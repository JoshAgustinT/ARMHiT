#!/usr/bin/env python3
"""Bar chart of the GPU bandwidth: total DRAM traffic and its rd / wr split.

    .venv/bin/python scripts/gpu_bw/plot_data.py scripts/gpu_bw/thor.json

Two bars per pattern, both from perf's UCF DRAM counters: the rd + wr total
(GB/s and % of the theoretical peak), and the same total split into
local_dram_byte_rd (bottom) and local_dram_byte_wr (top), each labelled with
its GB/s and share of the total; a ~0 segment is labelled at the bar's foot.
Peak as a dashed line. mem.cu's own timer is left out: it matches perf to
< 1 GB/s. Saves a .png next to the json.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "styling"))

import paper_style as ps  # noqa: E402

WIDTH = 0.38
MIN_LABEL_GBS = 15  # thinner segments are labelled at the bar's foot instead


def main():
    path = Path(sys.argv[1])
    data = json.loads(path.read_text())
    peak = data["peak_gbs"]
    patterns = list(data["patterns"])
    rd = [data["patterns"][p]["perf_rd_gbs"] for p in patterns]
    wr = [data["patterns"][p]["perf_wr_gbs"] for p in patterns]
    total = [r + w for r, w in zip(rd, wr)]

    ps.configure_style()
    palette = ps.configure_palette(n=4)  # not prefetch flavours: own palette
    fig, ax = ps.get_subplots(1, 1, plot_w=6)

    # Left bar per pattern: rd + wr total, with % of peak.
    xs = [x - 0.5 * WIDTH for x in range(len(patterns))]
    ax.bar(xs, total, WIDTH, label="DRAM rd + wr", color=palette[1],
           edgecolor="black", linewidth=0.6)
    for x, t in zip(xs, total):
        ax.text(x, t / 2, f"rd + wr\n{t:.0f} GB/s\n{t / peak:.0%}", ha="center",
                va="center", color="white", fontsize=7)

    # Right bar per pattern: the same total split into rd (bottom) / wr (top),
    # each with its share of the total.
    xs = [x + 0.5 * WIDTH for x in range(len(patterns))]
    for vals, bottom, label, color in [(rd, [0] * len(rd), "DRAM rd", palette[2]),
                                       (wr, rd, "DRAM wr", palette[3])]:
        ax.bar(xs, vals, WIDTH, bottom=bottom, label=label, color=color,
               edgecolor="black", linewidth=0.6)
        name = label.split()[-1]
        for x, v, b, t in zip(xs, vals, bottom, total):
            text = f"{name}\n{v:.0f} GB/s\n{v / t:.0%}"
            if v >= MIN_LABEL_GBS:
                ax.text(x, b + v / 2, text, ha="center", va="center",
                        color="white", fontsize=7)
            else:  # too thin to hold it: at the foot of the bar
                ax.text(x, 4, text, ha="center", va="bottom",
                        color="white", fontsize=7)

    ax.axhline(peak, color="black", linestyle="--", linewidth=1, zorder=0,
               label=f"theoretical peak ({peak:.0f})")

    ax.set_xticks(range(len(patterns)), patterns)
    ax.set_ylabel("DRAM bandwidth (GB/s)")
    ax.set_title(f"GPU streaming, {data['buffer_mib'] // 1024} GiB buffers,\n"
                 f"GPU {data['gpu_hz'] / 1e6:.0f} MHz, EMC {data['emc_hz'] / 1e6:.0f} MHz")
    ax.set_ylim(0, peak * 1.15)
    ps.tidy(ax)
    ax.grid(False, axis="x")
    fig.legend(fontsize=8, loc="upper center", ncol=2)
    ps.save(fig, str(path.with_suffix(".png")), legend_top=0.85)


if __name__ == "__main__":
    main()
