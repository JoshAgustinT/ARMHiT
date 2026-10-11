#!/usr/bin/env python3
"""GPU DRAM bandwidth vs number of SMs, at 24 and 32 full warps per SM.

    python3 scripts/gpu_bw/collect_sms.py
    .venv/bin/python scripts/gpu_bw/plot_sms.py scripts/gpu_bw/thor_sms.json

Same measurement as collect_data.py (./mem's own timer + perf's UCF DRAM
rd / wr bytes over the steady middle of the run); only ./mem's grid changes,
the kernels are the same. k blocks of 768 (24 warps) or 1024 threads
(32 warps, the largest block) for k = 1..20: blocks go to the emptiest SM
first (checked with %smid), so that is exactly k SMs with one block each.
48 warps / SM would need two blocks per SM, which would spread over 2k SMs.
write uses mem.cu's store kernel here (cudaMemset has no thread count).

Aborts unless pin_ghz.sh has pinned the EMC (memory clock) and GPU clock.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

PATTERNS = ["read", "write", "copy"]
SMS = 20
WARPS_PER_SM = [24, 32]
# (blocks, threads_per_block, SMs used, warps / SM, threads / warp)
GRIDS = [(k, 32 * w, k, w, 32) for w in WARPS_PER_SM for k in range(1, SMS + 1)]
BUFFER_MIB = 256
SECONDS = 6
SETUP_SECONDS = 2  # for mem to allocate and touch its buffers

# The Thor runs NVIDIA's tegra kernel, so use this perf, not the distro wrapper.
PERF = "/usr/lib/linux-tools/6.8.0-146-generic/perf"
EVENTS = {"rd": "nvidia_ucf_pmu_0/local_dram_byte_rd/",
          "wr": "nvidia_ucf_pmu_0/local_dram_byte_wr/"}

EMC = Path("/sys/class/devfreq/bwmgr")
GPU = Path("/sys/class/devfreq/gpu-gpc-0")
BUS_BYTES = 256 // 8  # LPDDR5X on Thor: 256-bit bus, two transfers per EMC clock


def pinned_hz(dev):
    """A devfreq clock's frequency, or exit if pin_ghz.sh has not pinned it."""
    lo, hi = (int((dev / f).read_text()) for f in ("min_freq", "max_freq"))
    if lo != hi:
        sys.exit(f"[!] {dev.name} not pinned ({lo}..{hi} Hz); run sudo ./pin_ghz.sh on")
    return hi


def run(pattern, grid):
    blocks, tpb, sms, warps_per_sm, threads_per_warp = grid
    cmd = [str(HERE / "mem"), pattern, str(BUFFER_MIB), str(SECONDS), str(blocks), str(tpb)]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "perf.csv"
        perf = [PERF, "stat", "-a", "-x,", "-e", ",".join(EVENTS.values()), "-o", str(out),
                "-D", str(int((SETUP_SECONDS + 0.5) * 1000)),
                "--", "sleep", str(SETUP_SECONDS + SECONDS - 0.5)]
        start_at = time.time() + SETUP_SECONDS
        perf_proc = subprocess.Popen(perf, stderr=subprocess.DEVNULL)  # "Events enabled" chatter
        mem = subprocess.run(cmd, capture_output=True, text=True,
                             env=dict(os.environ, MEM_START_AT=f"{start_at:.3f}"))
        perf_proc.wait()
        if mem.returncode != 0 or mem.stderr:
            print(f"Error ({' '.join(cmd)}):", mem.stderr)
            return None
        # Rows are "count,unit,event,run_ns,...".
        gbs = {}
        for line in out.read_text().splitlines():
            f = line.split(",")
            for key, event in EVENTS.items():
                if len(f) > 3 and f[2] == event:
                    gbs[key] = float(f[0]) / float(f[3])  # bytes/ns = GB/s

    result = {
        "blocks": blocks, "threads_per_block": tpb, "sms": sms,
        "warps_per_sm": warps_per_sm, "threads_per_warp": threads_per_warp,
        "threads": sms * warps_per_sm * threads_per_warp,
        "timed_gbs": float(re.search(r"gbs : ([\d.]+)", mem.stdout).group(1)),
        "perf_rd_gbs": gbs["rd"],
        "perf_wr_gbs": gbs["wr"],
        "perf_gbs": gbs["rd"] + gbs["wr"],
    }
    print(f"  {pattern:>5} {sms:>2} SMs x {warps_per_sm:>2} warps x {threads_per_warp:>2} threads "
          f"({blocks} x {tpb}): timed {result['timed_gbs']:.3f} GB/s, "
          f"perf {result['perf_gbs']:.3f} GB/s", flush=True)
    return result


def main():
    emc_hz, gpu_hz = pinned_hz(EMC), pinned_hz(GPU)
    subprocess.run(["make", "-s", "-C", str(HERE), "mem"], check=True)

    out = HERE / "thor_sms.json"
    data = {
        "buffer_mib": BUFFER_MIB,
        "seconds": SECONDS,
        "emc_hz": emc_hz,
        "gpu_hz": gpu_hz,
        "peak_gbs": emc_hz * 2 * BUS_BYTES / 1e9,
        "patterns": {p: [] for p in PATTERNS},
    }
    for pattern in PATTERNS:
        for grid in GRIDS:
            result = run(pattern, grid)
            if result:
                data["patterns"][pattern].append(result)
                out.write_text(json.dumps(data, indent=2))
    print(f"[OK] Saved {out} (peak {data['peak_gbs']:.1f} GB/s)")


if __name__ == "__main__":
    main()
