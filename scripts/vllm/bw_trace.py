#!/usr/bin/env python3
"""DRAM bandwidth over a whole vLLM throughput bench run.

Runs the bench under system-wide perf counting of the UCF DRAM rd / wr bytes
(every requester, so CPU + GPU), read every 1 s, and plots all / rd / wr
GB/s against time. perf stat -I only reads the counters each interval (no
sampling interrupts), so it barely touches the run.

Aborts unless pin_ghz.sh has pinned the CPUs, the EMC (memory clock) and the
GPU. Writes bw.csv (raw perf), bw.log (bench output) and bw.png here.
"""
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
# The Thor runs NVIDIA's tegra kernel, so use this perf, not the distro wrapper.
PERF = "/usr/lib/linux-tools/6.8.0-146-generic/perf"
RD_EVENT = "nvidia_ucf_pmu_0/local_dram_byte_rd/"
WR_EVENT = "nvidia_ucf_pmu_0/local_dram_byte_wr/"
BENCH = [str(HERE / ".venv/bin/vllm"), "bench", "throughput", "--model", "Qwen/Qwen3.8-27B",
         "--dataset-name", "random", "--random-input-len", "500", "--random-output-len", "150",
         "--num-prompts", "10", "--gpu-memory-utilization", "0.7"]

CPUFREQ = Path("/sys/devices/system/cpu/cpufreq")
EMC = Path("/sys/class/devfreq/bwmgr")
GPU = Path("/sys/class/devfreq/gpu-gpc-0")


def pinned(lo_file, hi_file):
    """The pinned frequency, or exit if pin_ghz.sh has not pinned it."""
    lo, hi = int(lo_file.read_text()), int(hi_file.read_text())
    if lo != hi:
        sys.exit(f"[!] {lo_file.parent.name} not pinned ({lo}..{hi}); run sudo ./pin_ghz.sh on")
    return hi


def main():
    for p in CPUFREQ.glob("policy*"):
        pinned(p / "scaling_min_freq", p / "scaling_max_freq")
    pinned(EMC / "min_freq", EMC / "max_freq")
    pinned(GPU / "min_freq", GPU / "max_freq")

    perf_cmd = [PERF, "stat", "-a", "-x,", "-I", "1000", "-e", f"{RD_EVENT},{WR_EVENT}",
                "-o", str(HERE / "bw.csv"), "--", *BENCH]
    with open(HERE / "bw.log", "w") as log:  # tee: bench output to terminal and log
        proc = subprocess.Popen(perf_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            sys.stdout.write(line)
            log.write(line)
        if proc.wait() != 0:
            sys.exit(f"[!] bench failed (exit {proc.returncode}); see bw.log")

    # Rows are "time,count,unit,event,run_ns,...", one per event per interval.
    rd, wr = defaultdict(float), defaultdict(float)
    for line in (HERE / "bw.csv").read_text().splitlines():
        f = line.split(",")
        if len(f) < 5 or not f[4]:
            continue
        gbs = float(f[1]) / float(f[4])  # bytes/ns = GB/s
        if f[3] == RD_EVENT:
            rd[float(f[0])] = gbs
        if f[3] == WR_EVENT:
            wr[float(f[0])] = gbs
    t = sorted(rd)

    plt.figure(figsize=(12, 4))
    plt.plot(t, [rd[x] + wr[x] for x in t], label="all", lw=1)
    plt.plot(t, [rd[x] for x in t], label="rd", lw=1)
    plt.plot(t, [wr[x] for x in t], label="wr", lw=1)
    plt.xlabel("time (s)")
    plt.ylabel("DRAM bandwidth (GB/s)")
    plt.ylim(bottom=0)
    plt.legend()
    plt.tight_layout()
    plt.savefig(HERE / "bw.png", dpi=150)
    print(f"wrote {HERE / 'bw.png'}")


if __name__ == "__main__":
    main()
