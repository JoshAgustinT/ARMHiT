#!/usr/bin/env python3
"""Line chart of CPU DRAM bandwidth vs threads, one line per buffer size
(collect_data.py).

    .venv/bin/python scripts/cpu_bw/plot_threads.py scripts/cpu_bw/thor.json

One panel per pattern. x: threads (one per core), y: perf's UCF DRAM rd + wr
GB/s, one line per buffer size (light = small, dark = big). Sizes that fit
in cache sit at ~0. The rd / wr split is in the json and in plot_data.py's
bars. Theoretical DRAM peak as a dashed line. Saves a _threads.png next to
the json.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "styling"))

import paper_style as ps  # noqa: E402


def size_label(mib):
    if mib >= 1024:
        return f"{mib // 1024} GiB"
    return f"{mib} MiB"


def main():
    path = Path(sys.argv[1])
    data = json.loads(path.read_text())
    peak = data["peak_gbs"]
    patterns = list(data["patterns"])
    sizes = sorted({p["size_mib"] for pts in data["patterns"].values() for p in pts})

    ps.configure_style()
    palette = ps.configure_palette(n=len(sizes) + 1)  # light -> dark; [0] is too pale
    fig, axes = ps.get_subplots(1, len(patterns), plot_w=4.5, plot_h=4)

    for ax, pattern in zip(axes, patterns):
        for i, mib in enumerate(sizes):
            points = [p for p in data["patterns"][pattern] if p["size_mib"] == mib]
            xs = [p["threads"] for p in points]
            ys = [p["perf_gbs"] for p in points]
            ax.plot(xs, ys, marker="o", markersize=3, color=palette[i + 1], label=size_label(mib))

        ax.axhline(peak, color="black", linestyle="--", linewidth=1, zorder=0,
                   label=f"theoretical peak ({peak:.0f})")
        ax.set_xticks(data["threads"])
        ax.set_xlabel("threads (one per core)")
        ax.set_title(pattern, fontsize=11, fontweight="bold")
        ax.set_ylim(0, peak * 1.1)
        ps.tidy(ax)

    axes[0].set_ylabel("DRAM bandwidth (GB/s)")
    fig.suptitle(f"CPU sequential access, DRAM bandwidth vs threads, per buffer size,\n"
                 f"CPU {data['cpu_khz'] / 1e6:.2f} GHz, EMC {data['emc_hz'] / 1e6:.0f} MHz",
                 y=0.905, fontsize=10)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, fontsize=8, loc="upper center", ncol=len(labels))
    ps.save(fig, str(path.with_name(path.stem + "_threads.png")), legend_top=0.9)


if __name__ == "__main__":
    main()
