#!/usr/bin/env python3
"""Line chart of GPU DRAM bandwidth vs number of SMs (collect_sms.py).

    .venv/bin/python scripts/gpu_bw/plot_sms.py scripts/gpu_bw/thor_sms.json

x = SMs in use (1..20), y = perf's UCF DRAM rd + wr GB/s. Colour = pattern,
solid = 32 warps / SM, dashed = 24 warps / SM (all warps full, 32 threads).
Theoretical peak dashed black. Saves a .png next to the json.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "styling"))

import paper_style as ps  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

MARKERS = {"read": "o", "write": "s", "copy": "^"}
STYLES = {32: "-", 24: "--"}


def main():
    path = Path(sys.argv[1])
    data = json.loads(path.read_text())
    peak = data["peak_gbs"]

    ps.configure_style()
    palette = ps.configure_palette(n=4)  # not prefetch flavours: own palette
    fig, ax = ps.get_subplots(1, 1, plot_w=6)

    colours = {}
    for i, (pattern, points) in enumerate(data["patterns"].items()):
        colours[pattern] = palette[i + 1]
        for warps, style in STYLES.items():
            pts = [p for p in points if p["warps_per_sm"] == warps]
            ax.plot([p["sms"] for p in pts], [p["perf_gbs"] for p in pts], linestyle=style,
                    marker=MARKERS.get(pattern, "o"), markersize=4, color=colours[pattern])
    ax.axhline(peak, color="black", linestyle="--", linewidth=1, zorder=0)

    # Legend: colour = pattern, line style = warps / SM.
    handles = [Line2D([], [], color=c, marker=MARKERS.get(p, "o"), label=p)
               for p, c in colours.items()]
    handles += [Line2D([], [], color="grey", linestyle=s, label=f"{w} warps / SM")
                for w, s in STYLES.items()]
    handles.append(Line2D([], [], color="black", linestyle="--", linewidth=1,
                          label=f"theoretical peak ({peak:.0f})"))

    sms = sorted({p["sms"] for pts in data["patterns"].values() for p in pts})
    ax.set_xticks(sms)
    ax.set_xlabel("SMs in use (each full of 32-thread warps)")
    ax.set_ylabel("DRAM bandwidth (GB/s)")
    ax.set_title(f"GPU bandwidth vs SMs, {data['buffer_mib']} MiB buffers,\n"
                 f"GPU {data['gpu_hz'] / 1e6:.0f} MHz, EMC {data['emc_hz'] / 1e6:.0f} MHz")
    ax.set_ylim(0, peak * 1.15)
    ps.tidy(ax)
    fig.legend(handles=handles, fontsize=8, loc="upper center", ncol=3)
    ps.save(fig, str(path.with_suffix(".png")), legend_top=0.85)


if __name__ == "__main__":
    main()
