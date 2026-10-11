#!/usr/bin/env python3
"""CPU DRAM bandwidth vs thread count: read / write / copy, 1 to all cores.

    python3 scripts/cpu_bw/collect_threads.py
    .venv/bin/python scripts/cpu_bw/plot_threads.py scripts/cpu_bw/thor_threads.json

Same measurement as collect_data.py (./mem's own timer + perf's UCF DRAM
rd / wr bytes over the steady middle of the run), once per thread count.
mem pins thread i to core i and splits the 8 GiB buffers into one slice per
thread, so every thread streams its own slice.

Aborts unless pin_ghz.sh has pinned the CPUs and the EMC (memory clock).
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
THREADS = list(range(1, os.cpu_count() + 1))
BUFFER_GIB = 8
SECONDS = 8
SETUP_SECONDS = 3  # for mem to allocate and touch its 16 GiB (slower with few threads)

# The Thor runs NVIDIA's tegra kernel, so use this perf, not the distro wrapper.
PERF = "/usr/lib/linux-tools/6.8.0-146-generic/perf"
EVENTS = {"rd": "nvidia_ucf_pmu_0/local_dram_byte_rd/",
          "wr": "nvidia_ucf_pmu_0/local_dram_byte_wr/"}

CPUFREQ = Path("/sys/devices/system/cpu/cpufreq")
EMC = Path("/sys/class/devfreq/bwmgr")
BUS_BYTES = 256 // 8  # LPDDR5X on Thor: 256-bit bus, two transfers per EMC clock


def pinned(lo_file, hi_file):
    """The pinned frequency, or exit if pin_ghz.sh has not pinned it."""
    lo, hi = int(lo_file.read_text()), int(hi_file.read_text())
    if lo != hi:
        sys.exit(f"[!] {lo_file.parent.name} not pinned ({lo}..{hi}); run sudo ./pin_ghz.sh on")
    return hi


def run(pattern, threads):
    cmd = [str(HERE / "mem"), pattern, str(BUFFER_GIB), str(SECONDS), str(threads)]
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
        "threads": threads,
        "timed_gbs": float(re.search(r"gbs : ([\d.]+)", mem.stdout).group(1)),
        "perf_rd_gbs": gbs["rd"],
        "perf_wr_gbs": gbs["wr"],
        "perf_gbs": gbs["rd"] + gbs["wr"],
    }
    print(f"  {pattern:>5} {threads:>2} threads: timed {result['timed_gbs']:.1f} GB/s, "
          f"perf {result['perf_gbs']:.1f} GB/s", flush=True)
    return result


def main():
    cpu_khz = {pinned(p / "scaling_min_freq", p / "scaling_max_freq")
               for p in CPUFREQ.glob("policy*")}
    emc_hz = pinned(EMC / "min_freq", EMC / "max_freq")
    subprocess.run(["make", "-s", "-C", str(HERE), "mem"], check=True)

    out = HERE / "thor_threads.json"
    data = {
        "buffer_gib": BUFFER_GIB,
        "seconds": SECONDS,
        "cpu_khz": max(cpu_khz),
        "emc_hz": emc_hz,
        "peak_gbs": emc_hz * 2 * BUS_BYTES / 1e9,
        "patterns": {p: [] for p in PATTERNS},
    }
    for pattern in PATTERNS:
        for threads in THREADS:
            result = run(pattern, threads)
            if result:
                data["patterns"][pattern].append(result)
                out.write_text(json.dumps(data, indent=2))
    print(f"[OK] Saved {out} (peak {data['peak_gbs']:.1f} GB/s)")


if __name__ == "__main__":
    main()
