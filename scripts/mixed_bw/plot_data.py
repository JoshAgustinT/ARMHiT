#!/usr/bin/env python3
"""Bar chart of the CPU + GPU bandwidth collected by collect_data.py.

    .venv/bin/python scripts/mixed_bw/plot_data.py scripts/mixed_bw/thor.json

Style comes from styling/paper_style.py (see styling/PLOTTING.md), which
needs seaborn, hence the venv. Same layout as cpu_bw / gpu_bw, plus a bar for
who got the bandwidth. Three bars per pattern:
  rd + wr    perf's UCF DRAM total (GB/s, % of the theoretical peak)
  CPU / GPU  each binary's own timer (GB/s, share of their sum)
  rd / wr    perf's total split into local_dram_byte_rd / _wr (GB/s, share)
A ~0 part is labelled at the bar's foot. Peak as a dashed line. Saves a .png
next to the json.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "styling"))

import paper_style as ps  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

WIDTH = 0.28
MIN_LABEL_GBS = 15  # thinner segments are labelled at the bar's foot instead


def stacked(ax, x, parts):
    """One bar of (name, GB/s, colour) parts stacked bottom-up, each labelled
    with its GB/s and share of the bar."""
    total = sum(v for _, v, _ in parts)
    bottom = 0
    for name, v, colour in parts:
        ax.bar(x, v, WIDTH, bottom=bottom, color=colour, edgecolor="black", linewidth=0.6)
        text = f"{name}\n{v:.0f} GB/s\n{v / total:.0%}"
        if v >= MIN_LABEL_GBS:  # thin segments (e.g. GPU on `write`): smaller, tighter
            small = v < 3 * MIN_LABEL_GBS
            ax.text(x, bottom + v / 2, text, ha="center", va="center", color="white",
                    fontsize=5.5 if small else 7, linespacing=0.9 if small else 1.2)
        else:  # too thin to hold it: at the foot of the bar
            ax.text(x, 4, text, ha="center", va="bottom", color="white", fontsize=7)
        bottom += v


def main():
    path = Path(sys.argv[1])
    data = json.loads(path.read_text())
    peak = data["peak_gbs"]
    patterns = list(data["patterns"])

    ps.configure_style()
    palette = ps.configure_palette(n=6)  # not prefetch flavours: own palette
    total_c, cpu_c, gpu_c, rd_c, wr_c = palette[1:6]
    fig, ax = ps.get_subplots(1, 1, plot_w=8)

    for x, p in enumerate(patterns):
        r = data["patterns"][p]
        t = r["perf_rd_gbs"] + r["perf_wr_gbs"]
        ax.bar(x - WIDTH, t, WIDTH, color=total_c, edgecolor="black", linewidth=0.6)
        ax.text(x - WIDTH, t / 2, f"rd + wr\n{t:.0f} GB/s\n{t / peak:.0%}",
                ha="center", va="center", color="white", fontsize=7)
        stacked(ax, x, [("CPU", r["cpu_gbs"], cpu_c), ("GPU", r["gpu_gbs"], gpu_c)])
        stacked(ax, x + WIDTH, [("rd", r["perf_rd_gbs"], rd_c),
                                ("wr", r["perf_wr_gbs"], wr_c)])

    ax.axhline(peak, color="black", linestyle="--", linewidth=1, zorder=0)

    # Legend from proxies (the bars are drawn one segment at a time).
    handles = [Patch(facecolor=c, edgecolor="black", linewidth=0.6, label=l)
               for c, l in [(total_c, "DRAM rd + wr (perf)"),
                            (cpu_c, f"CPU ({data['cpu_threads']} threads, own timer)"),
                            (gpu_c, "GPU (own timer)"),
                            (rd_c, "DRAM rd (perf)"), (wr_c, "DRAM wr (perf)")]]
    handles.append(Line2D([], [], color="black", linestyle="--", linewidth=1,
                          label=f"theoretical peak ({peak:.0f})"))

    ax.set_xticks(range(len(patterns)), patterns)
    ax.set_ylabel("DRAM bandwidth (GB/s)")
    ax.set_title(f"CPU + GPU streaming at once,\nCPU {data['cpu_khz'] / 1e6:.2f} GHz, "
                 f"GPU {data['gpu_hz'] / 1e6:.0f} MHz, EMC {data['emc_hz'] / 1e6:.0f} MHz")
    ax.set_ylim(0, peak * 1.15)
    ps.tidy(ax)
    ax.grid(False, axis="x")
    fig.legend(handles=handles, fontsize=8, loc="upper center", ncol=3)
    ps.save(fig, str(path.with_suffix(".png")), legend_top=0.85)


if __name__ == "__main__":
    main()
