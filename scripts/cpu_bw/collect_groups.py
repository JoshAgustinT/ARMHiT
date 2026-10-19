#!/usr/bin/env python3
"""CPU DRAM bandwidth vs threads, with at most 1, 2, 3 or 4 threads in each
of the Thor's 4-core groups: plain / L3-prefetch reads and plain /
non-temporal / prefetched writes.

    python3 scripts/cpu_bw/collect_groups.py
    .venv/bin/python scripts/cpu_bw/plot_groups.py scripts/cpu_bw/thor_groups.json

The cores sit in groups {0-3}, {4-7}, {8-11}, {12-13} (see progress.md), and
each group tops out at ~93 GB/s to DRAM. For K = 1..4 threads per group,
the threads fill each group up to K cores before moving to the next group
(ORDERS), and every pattern runs at every thread count from 1 to all of them:
  1 per group  cores 0, 4, 8, 12                       1..4 threads
  2 per group  cores 0, 1, 4, 5, 8, 9, 12, 13          1..8 threads
  3 per group  cores 0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13  1..11 threads
               (the last group only has 2 cores)
  4 per group  cores 0, 1, 2, ..., 13 (all cores)      1..14 threads
N threads run on the first N cores of the order. 2 GiB buffers, SECONDS
each; 5 patterns x 37 runs = 185 runs, ~51 min.

Same measurement as collect_data.py: mem waits for MEM_START_AT, perf counts
the UCF DRAM rd / wr bytes system wide from 0.5 s after that to 0.5 s before
the end, one sample per second; GB/s = all samples' bytes / all samples' ns.
mem's own timer is kept too, but for read_l3 it means little: the core
doesn't wait for prefetches.

Writes thor_groups.json and thor_groups.log (every run's mem command and
output, perf command and raw samples).

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

PATTERNS = ["read", "read_l3", "write", "write_nt", "write_pf"]
ORDERS = {
    1: [0, 4, 8, 12],
    2: [0, 1, 4, 5, 8, 9, 12, 13],
    3: [0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13],
    4: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13],
}
SIZE_MIB = 2048
SECONDS = 10
SETUP_SECONDS = 6  # for mem to allocate and touch its buffers

# The Thor runs NVIDIA's tegra kernel, so use this perf, not the distro wrapper.
PERF = "/usr/lib/linux-tools/6.8.0-146-generic/perf"
RD_EVENT = "nvidia_ucf_pmu_0/local_dram_byte_rd/"
WR_EVENT = "nvidia_ucf_pmu_0/local_dram_byte_wr/"

CPUFREQ = Path("/sys/devices/system/cpu/cpufreq")
EMC = Path("/sys/class/devfreq/bwmgr")
BUS_BYTES = 256 // 8  # LPDDR5X on Thor: 256-bit bus, two transfers per EMC clock


def pinned(lo_file, hi_file):
    """The pinned frequency, or exit if pin_ghz.sh has not pinned it."""
    lo = int(lo_file.read_text())
    hi = int(hi_file.read_text())
    if lo != hi:
        sys.exit(f"[!] {lo_file.parent.name} not pinned ({lo}..{hi}); run sudo ./pin_ghz.sh on")
    return hi


def run(pattern, per_group, threads, cores, log):
    """Runs mem and perf side by side; returns the run's numbers, or None."""
    mem_cmd = [str(HERE / "mem"), pattern, str(SIZE_MIB), str(SECONDS), cores]
    with tempfile.TemporaryDirectory() as tmp:
        perf_out = Path(tmp) / "perf.csv"
        perf_cmd = [PERF, "stat", "-a", "-x,", "-I", "1000",
                    "-e", f"{RD_EVENT},{WR_EVENT}", "-o", str(perf_out),
                    "-D", str(int((SETUP_SECONDS + 0.5) * 1000)),
                    "--", "sleep", str(SETUP_SECONDS + SECONDS - 0.5)]
        print("Running:", " ".join(mem_cmd), flush=True)

        start_at = time.time() + SETUP_SECONDS
        mem_env = dict(os.environ, MEM_START_AT=f"{start_at:.3f}")
        perf = subprocess.Popen(perf_cmd, stderr=subprocess.DEVNULL)  # "Events enabled" chatter
        mem = subprocess.run(mem_cmd, capture_output=True, text=True, env=mem_env)
        perf.wait()
        perf_text = perf_out.read_text()

    log.write(f"=== {pattern} {per_group} per group {threads} threads ===\n")
    log.write("$ " + " ".join(mem_cmd) + "\n")
    log.write(mem.stdout + mem.stderr)
    log.write("$ " + " ".join(perf_cmd) + "\n")
    log.write(perf_text + "\n")
    log.flush()

    if mem.returncode != 0 or mem.stderr:
        print("Error:", mem.stderr)
        return None

    # Sample rows are "time,count,unit,event,run_ns,...", one per event per second.
    rd_bytes = 0
    rd_ns = 0
    wr_bytes = 0
    wr_ns = 0
    for line in perf_text.splitlines():
        f = line.split(",")
        if len(f) < 5:
            continue
        if f[3] == RD_EVENT:
            rd_bytes += float(f[1])
            rd_ns += float(f[4])
        if f[3] == WR_EVENT:
            wr_bytes += float(f[1])
            wr_ns += float(f[4])

    result = {
        "per_group": per_group,
        "threads": threads,
        "cores": cores,
        "timed_gbs": float(re.search(r"gbs : ([\d.]+)", mem.stdout).group(1)),
        "perf_rd_gbs": rd_bytes / rd_ns,  # bytes/ns = GB/s
        "perf_wr_gbs": wr_bytes / wr_ns,
    }
    result["perf_gbs"] = result["perf_rd_gbs"] + result["perf_wr_gbs"]
    print(f"  perf {result['perf_gbs']:.1f} GB/s (rd {result['perf_rd_gbs']:.1f}, "
          f"wr {result['perf_wr_gbs']:.1f}), timed {result['timed_gbs']:.1f}", flush=True)
    return result


def main():
    cpu_khz = set()
    for policy in CPUFREQ.glob("policy*"):
        cpu_khz.add(pinned(policy / "scaling_min_freq", policy / "scaling_max_freq"))
    emc_hz = pinned(EMC / "min_freq", EMC / "max_freq")
    subprocess.run(["make", "-s", "-C", str(HERE), "mem"], check=True)

    out = HERE / "thor_groups.json"
    data = {
        "size_mib": SIZE_MIB,
        "seconds": SECONDS,
        "cpu_khz": max(cpu_khz),
        "emc_hz": emc_hz,
        "peak_gbs": emc_hz * 2 * BUS_BYTES / 1e9,
        "patterns": {},
    }
    with open(out.with_suffix(".log"), "w") as log:
        for pattern in PATTERNS:
            data["patterns"][pattern] = []
            for per_group, order in ORDERS.items():
                for threads in range(1, len(order) + 1):
                    # Trailing comma: mem reads it as a core list, even for one core.
                    cores = ",".join(str(c) for c in order[:threads]) + ","
                    result = run(pattern, per_group, threads, cores, log)
                    if result:
                        data["patterns"][pattern].append(result)
                        out.write_text(json.dumps(data, indent=2))
    print(f"[OK] Saved {out} and {out.with_suffix('.log')} (peak {data['peak_gbs']:.1f} GB/s)")


if __name__ == "__main__":
    main()
