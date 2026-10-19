#!/usr/bin/env python3
"""Bar chart of CPU DRAM bandwidth vs buffer size (collect_data.py).

    .venv/bin/python scripts/cpu_bw/plot_data.py scripts/cpu_bw/thor.json

Only the runs with all threads (one per core). One panel per pattern, one
bar per buffer size. Each bar is perf's UCF DRAM
traffic split into local_dram_byte_rd (bottom) and local_dram_byte_wr (top),
every bar labelled with its rd and wr GB/s (inside the segment, or at the
bar's foot / just above it when the segment is too thin to hold the label),
and the rd + wr total in a row above the peak line. ~0 while the buffer fits
in cache. mem.cpp's own timer is in the json but left out here: it matches perf once the buffer
is past the caches. Theoretical DRAM peak as a dashed line. Saves a .png next
to the json.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "styling"))

import paper_style as ps  # noqa: E402

MIN_LABEL_GBS = 15  # thinner segments are labelled outside the segment instead
LABEL_GBS = 30  # roughly one label's height, in GB/s


def size_label(mib):
    if mib >= 1024:
        return f"{mib // 1024} GiB"
    return f"{mib} MiB"


def main():
    path = Path(sys.argv[1])
    data = json.loads(path.read_text())
    peak = data["peak_gbs"]
    patterns = list(data["patterns"])
    threads = max(data["threads"])

    ps.configure_style()
    palette = ps.configure_palette(n=4)  # not prefetch flavours: own palette
    fig, axes = ps.get_subplots(len(patterns), 1, plot_w=7, plot_h=2.6)

    for ax, pattern in zip(axes, patterns):
        points = [p for p in data["patterns"][pattern] if p["threads"] == threads]
        xs = list(range(len(points)))
        rd = [p["perf_rd_gbs"] for p in points]
        wr = [p["perf_wr_gbs"] for p in points]

        ax.bar(xs, rd, 0.6, label="DRAM rd", color=palette[2],
               edgecolor="black", linewidth=0.6)
        ax.bar(xs, wr, 0.6, bottom=rd, label="DRAM wr", color=palette[3],
               edgecolor="black", linewidth=0.6)
        for x, r, w in zip(xs, rd, wr):
            # rd label: inside its segment, else at the bar's foot.
            if r >= MIN_LABEL_GBS:
                ax.text(x, r / 2, f"rd {r:.0f}", ha="center", va="center", color="white", fontsize=7)
            elif w >= MIN_LABEL_GBS:  # foot sits inside the wr segment
                ax.text(x, 4, f"rd {r:.0f}", ha="center", va="bottom", color="white", fontsize=7)
            else:  # no bar to speak of
                ax.text(x, 4, f"rd {r:.0f}", ha="center", va="bottom", color="black", fontsize=7)

            # wr label: inside its segment, else just above the bar
            # (and above the rd label, when that one is at the foot too).
            if w >= MIN_LABEL_GBS and r >= MIN_LABEL_GBS:
                ax.text(x, r + w / 2, f"wr {w:.0f}", ha="center", va="center", color="white", fontsize=7)
            elif w >= MIN_LABEL_GBS:  # centred in the space above the rd label at the foot
                ax.text(x, (LABEL_GBS + w) / 2, f"wr {w:.0f}", ha="center", va="center", color="white", fontsize=7)
            elif r >= MIN_LABEL_GBS:
                ax.text(x, r + w + 4, f"wr {w:.0f}", ha="center", va="bottom", color="black", fontsize=7)
            else:
                ax.text(x, 4 + LABEL_GBS, f"wr {w:.0f}", ha="center", va="bottom", color="black", fontsize=7)

            ax.text(x, peak + 6, f"{r + w:.0f}", ha="center", va="bottom", fontsize=7)

        ax.axhline(peak, color="black", linestyle="--", linewidth=1, zorder=0,
                   label=f"theoretical peak ({peak:.0f})")
        ax.set_xticks(xs, [size_label(p["size_mib"]) for p in points])
        ax.set_ylabel("GB/s")
        ax.set_title(pattern, fontsize=11, fontweight="bold")
        ax.set_ylim(0, peak * 1.2)
        ps.tidy(ax)
        ax.grid(False, axis="x")

    axes[-1].set_xlabel("buffer size (each of src, dst)")
    fig.suptitle(f"CPU sequential access, DRAM bandwidth vs buffer size, {threads} threads,\n"
                 f"CPU {data['cpu_khz'] / 1e6:.2f} GHz, EMC {data['emc_hz'] / 1e6:.0f} MHz",
                 y=0.955, fontsize=10)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, fontsize=8, loc="upper center", ncol=3)
    ps.save(fig, str(path.with_suffix(".png")), legend_top=0.975)


if __name__ == "__main__":
    main()
