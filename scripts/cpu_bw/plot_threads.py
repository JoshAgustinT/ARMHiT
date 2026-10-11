#!/usr/bin/env python3
"""Line chart of CPU DRAM bandwidth vs thread count (collect_threads.py).

    .venv/bin/python scripts/cpu_bw/plot_threads.py scripts/cpu_bw/thor_threads.json

One line per pattern: perf's UCF DRAM rd + wr GB/s at each thread count. The
last point is labelled with its GB/s and % of the theoretical peak (dashed).
Saves a .png next to the json.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "styling"))

import paper_style as ps  # noqa: E402

MARKERS = {"read": "o", "write": "s", "copy": "^"}


def main():
    path = Path(sys.argv[1])
    data = json.loads(path.read_text())
    peak = data["peak_gbs"]

    ps.configure_style()
    palette = ps.configure_palette(n=4)  # not prefetch flavours: own palette
    fig, ax = ps.get_subplots(1, 1, plot_w=6)

    ends = []
    for i, (pattern, points) in enumerate(data["patterns"].items()):
        xs = [p["threads"] for p in points]
        ys = [p["perf_gbs"] for p in points]
        ax.plot(xs, ys, marker=MARKERS.get(pattern, "o"), color=palette[i + 1], label=pattern)
        ends.append((ys[-1], xs[-1], palette[i + 1]))

    # Last point's GB/s and % of peak, nudged apart so close lines don't collide.
    gap = peak * 0.05
    placed = []
    for y, x, colour in sorted(ends):
        y_text = max([y] + [p + gap for p in placed])
        placed.append(y_text)
        ax.annotate(f"{y:.0f} GB/s ({y / peak:.0%})", (x, y_text), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=7, color=colour)

    ax.axhline(peak, color="black", linestyle="--", linewidth=1, zorder=0,
               label=f"theoretical peak ({peak:.0f})")
    threads = sorted({p["threads"] for pts in data["patterns"].values() for p in pts})
    ax.set_xticks(threads)
    ax.set_xlim(0.5, max(threads) + 3)  # room for the end labels
    ax.set_xlabel("threads (one per core)")
    ax.set_ylabel("DRAM bandwidth (GB/s)")
    ax.set_title(f"CPU streaming vs threads, {data['buffer_gib']} GiB buffers,\n"
                 f"CPU {data['cpu_khz'] / 1e6:.2f} GHz, EMC {data['emc_hz'] / 1e6:.0f} MHz")
    ax.set_ylim(0, peak * 1.15)
    ps.tidy(ax)
    fig.legend(fontsize=8, loc="upper center", ncol=4)
    ps.save(fig, str(path.with_suffix(".png")), legend_top=0.88)


if __name__ == "__main__":
    main()
