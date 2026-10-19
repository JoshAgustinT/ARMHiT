#!/usr/bin/env python3
"""CPU write DRAM bandwidth as threads fill one core group and spill into the
next: a direct check of the 4-core groups {0-3}, {4-7}, {8-11}, {12-13}.

    python3 scripts/cpu_bw/collect_staircase.py
    .venv/bin/python scripts/cpu_bw/plot_staircase.py scripts/cpu_bw/thor_staircase.json

For each sequence in SEQUENCES, `write` runs on its first 1, 2, ..., 6 cores
(2 GiB buffers, SECONDS each; 4 x 6 = 24 runs, ~7 min). If the groups are
right, sequences 1-3 climb, go flat at 3-4 threads (one full group, ~93 GB/s)
and jump at 5; sequence 4 goes flat at 2 threads ({12,13} full) and jumps at 3.

Same measurement as collect_data.py: mem waits for MEM_START_AT, perf counts
the UCF DRAM rd / wr bytes system wide from 0.5 s after that to 0.5 s before
the end, one sample per second; GB/s = all samples' bytes / all samples' ns.

Writes thor_staircase.json and thor_staircase.log (every run's mem command and
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

PATTERN = "write"
SEQUENCES = {
    "cores 0-3, then 4, 5": [0, 1, 2, 3, 4, 5],
    "cores 4-7, then 8, 9": [4, 5, 6, 7, 8, 9],
    "cores 8-11, then 12, 13": [8, 9, 10, 11, 12, 13],
    "cores 12-13, then 0-3": [12, 13, 0, 1, 2, 3],
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


def run(pattern, sequence, threads, cores, log):
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

    log.write(f"=== {pattern} {sequence}: {threads} threads ===\n")
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

    out = HERE / "thor_staircase.json"
    data = {
        "size_mib": SIZE_MIB,
        "seconds": SECONDS,
        "cpu_khz": max(cpu_khz),
        "emc_hz": emc_hz,
        "peak_gbs": emc_hz * 2 * BUS_BYTES / 1e9,
        "sequences": {},
    }
    with open(out.with_suffix(".log"), "w") as log:
        for sequence, order in SEQUENCES.items():
            data["sequences"][sequence] = []
            for threads in range(1, len(order) + 1):
                # Trailing comma: mem reads it as a core list, even for one core.
                cores = ",".join(str(c) for c in order[:threads]) + ","
                result = run(PATTERN, sequence, threads, cores, log)
                if result:
                    data["sequences"][sequence].append(result)
                    out.write_text(json.dumps(data, indent=2))
    print(f"[OK] Saved {out} and {out.with_suffix('.log')} (peak {data['peak_gbs']:.1f} GB/s)")


if __name__ == "__main__":
    main()
