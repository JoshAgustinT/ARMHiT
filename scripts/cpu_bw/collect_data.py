#!/usr/bin/env python3
"""CPU memory bandwidth vs buffer size and threads: read / write / copy,
2 MiB to 32 GiB, 1 to all cores.

    python3 scripts/cpu_bw/collect_data.py
    .venv/bin/python scripts/cpu_bw/plot_data.py scripts/cpu_bw/thor.json
    .venv/bin/python scripts/cpu_bw/plot_threads.py scripts/cpu_bw/thor.json

Runs ./mem once per pattern, size and thread count (SECONDS each; ~90 min in
all). mem pins thread i to core i and gives every thread its own slice of the
buffers. The size is the size of each of mem's two buffers (src and dst, both
always allocated and touched). Sizes go up by 4x: 2 x 2 MiB fits in the
cores' L2 caches, 2 x 32 GiB is half of the Thor's DRAM. Every run gets two
numbers:
  timed  sum over threads of bytes moved / time (mem.cpp's own number)
  perf   DRAM bytes / time from perf's UCF counters (the Thor's memory-side
         fabric, system wide), read + write. ~0 when the buffer fits in cache.

To line the two up, mem gets MEM_START_AT: it allocates and touches its
buffers, then waits until that moment to start. perf watches the whole system
(`-a ... -- sleep`), starts counting (-D) half a second after that moment and
stops half a second before the end, so it only sees the steady part. perf
prints one sample per second (-I 1000); perf GB/s = all samples' bytes / all
samples' ns.

Writes thor.json and thor.log. The log has, for every run, the
mem command and its output, and the perf command and its raw samples, so the
numbers in the json can be checked by hand.

Aborts unless pin_ghz.sh has pinned the CPUs and the EMC (memory clock): the
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
SIZES_MIB = [2, 8, 32, 128, 512, 2048, 8192, 32768]
THREADS = list(range(1, os.cpu_count() + 1))
SECONDS = 10
SETUP_SECONDS = 6  # for mem to allocate and touch its buffers (~4 s at 2 x 32 GiB, 1 thread)

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


def run(pattern, mib, threads, log):
    """Runs mem and perf side by side; returns the run's numbers, or None."""
    mem_cmd = [str(HERE / "mem"), pattern, str(mib), str(SECONDS), str(threads)]
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

    log.write(f"=== {pattern} {mib} MiB {threads} threads ===\n")
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
    samples = 0
    for line in perf_text.splitlines():
        f = line.split(",")
        if len(f) < 5:
            continue
        if f[3] == RD_EVENT:
            rd_bytes += float(f[1])
            rd_ns += float(f[4])
            samples += 1
        if f[3] == WR_EVENT:
            wr_bytes += float(f[1])
            wr_ns += float(f[4])

    result = {
        "size_mib": mib,
        "threads": threads,
        "timed_gbs": float(re.search(r"gbs : ([\d.]+)", mem.stdout).group(1)),
        "huge_page_mib": int(re.search(r"huge_page_mib : (\d+)", mem.stdout).group(1)),
        "perf_rd_gbs": rd_bytes / rd_ns,  # bytes/ns = GB/s
        "perf_wr_gbs": wr_bytes / wr_ns,
        "perf_samples": samples,
    }
    result["perf_gbs"] = result["perf_rd_gbs"] + result["perf_wr_gbs"]
    print(f"  timed {result['timed_gbs']:.1f} GB/s, perf {result['perf_gbs']:.1f} GB/s "
          f"(rd {result['perf_rd_gbs']:.1f}, wr {result['perf_wr_gbs']:.1f}, "
          f"{samples} samples), {result['huge_page_mib']} MiB on huge pages", flush=True)
    return result


def main():
    cpu_khz = set()
    for policy in CPUFREQ.glob("policy*"):
        cpu_khz.add(pinned(policy / "scaling_min_freq", policy / "scaling_max_freq"))
    emc_hz = pinned(EMC / "min_freq", EMC / "max_freq")
    subprocess.run(["make", "-s", "-C", str(HERE), "mem"], check=True)

    out = HERE / "thor.json"
    data = {
        "threads": THREADS,
        "seconds": SECONDS,
        "cpu_khz": max(cpu_khz),
        "emc_hz": emc_hz,
        "peak_gbs": emc_hz * 2 * BUS_BYTES / 1e9,
        "patterns": {},
    }
    with open(out.with_suffix(".log"), "w") as log:
        for pattern in PATTERNS:
            data["patterns"][pattern] = []
            for mib in SIZES_MIB:
                for threads in THREADS:
                    result = run(pattern, mib, threads, log)
                    if result:
                        data["patterns"][pattern].append(result)
                        out.write_text(json.dumps(data, indent=2))
    print(f"[OK] Saved {out} and {out.with_suffix('.log')} (peak {data['peak_gbs']:.1f} GB/s)")


if __name__ == "__main__":
    main()
