#!/usr/bin/env python3
"""GPU DRAM bandwidth: read / write / copy, against the theoretical peak.

    python3 scripts/gpu_bw/collect_data.py
    .venv/bin/python scripts/gpu_bw/plot_data.py scripts/gpu_bw/thor.json

Runs ./mem once per pattern (1 GiB buffers, SECONDS each). Every pattern gets
two numbers:
  timed  bytes mem.cu moved / wall time (its own number)
  perf   DRAM bytes / time from perf's UCF counters (the Thor's memory-side
         fabric; system wide, so they see GPU traffic too), read + write

To line the two up, mem gets MEM_START_AT: it allocates, then waits until
that moment to start. perf watches the whole system (`-a ... -- sleep`) and
starts counting (-D) half a second after that moment and stops half a second
before the end, so it only sees the steady part.

Aborts unless pin_ghz.sh has pinned the EMC (memory clock) and GPU clock: the
peak is computed from the EMC clock.
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
BUFFER_MIB = 1024
SECONDS = 30
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


def run(pattern):
    cmd = [str(HERE / "mem"), pattern, str(BUFFER_MIB), str(SECONDS)]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "perf.csv"
        perf = [PERF, "stat", "-a", "-x,", "-e", ",".join(EVENTS.values()), "-o", str(out),
                "-D", str(int((SETUP_SECONDS + 0.5) * 1000)),
                "--", "sleep", str(SETUP_SECONDS + SECONDS - 0.5)]
        print("Running:", " ".join(cmd), flush=True)
        start_at = time.time() + SETUP_SECONDS
        perf_proc = subprocess.Popen(perf, stderr=subprocess.DEVNULL)  # "Events enabled" chatter
        mem = subprocess.run(cmd, capture_output=True, text=True,
                             env=dict(os.environ, MEM_START_AT=f"{start_at:.3f}"))
        perf_proc.wait()
        if mem.returncode != 0 or mem.stderr:
            print("Error:", mem.stderr)
            return None
        # Rows are "count,unit,event,run_ns,...".
        gbs = {}
        for line in out.read_text().splitlines():
            f = line.split(",")
            for key, event in EVENTS.items():
                if len(f) > 3 and f[2] == event:
                    gbs[key] = float(f[0]) / float(f[3])  # bytes/ns = GB/s

    result = {
        "timed_gbs": float(re.search(r"gbs : ([\d.]+)", mem.stdout).group(1)),
        "perf_rd_gbs": gbs["rd"],
        "perf_wr_gbs": gbs["wr"],
        "perf_gbs": gbs["rd"] + gbs["wr"],
    }
    print(f"  {pattern:>5}: timed {result['timed_gbs']:.1f} GB/s, perf {result['perf_gbs']:.1f} "
          f"GB/s (rd {gbs['rd']:.1f}, wr {gbs['wr']:.1f})", flush=True)
    return result


def main():
    emc_hz, gpu_hz = pinned_hz(EMC), pinned_hz(GPU)
    subprocess.run(["make", "-s", "-C", str(HERE), "mem"], check=True)

    out = HERE / "thor.json"
    data = {
        "buffer_mib": BUFFER_MIB,
        "seconds": SECONDS,
        "emc_hz": emc_hz,
        "gpu_hz": gpu_hz,
        "peak_gbs": emc_hz * 2 * BUS_BYTES / 1e9,
        "patterns": {},
    }
    for pattern in PATTERNS:
        result = run(pattern)
        if result:
            data["patterns"][pattern] = result
            out.write_text(json.dumps(data, indent=2))
    print(f"[OK] Saved {out} (peak {data['peak_gbs']:.1f} GB/s)")


if __name__ == "__main__":
    main()
