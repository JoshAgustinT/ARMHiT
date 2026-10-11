#!/usr/bin/env python3
"""CPU and GPU streaming at the same time: how do they share DRAM?

    python3 scripts/mixed_bw/collect_data.py
    .venv/bin/python scripts/mixed_bw/plot_data.py scripts/mixed_bw/thor.json

Builds and runs the two other tests' binaries, scripts/cpu_bw/mem and
scripts/gpu_bw/mem, at the same time on the same pattern (read / write /
copy). Both get the same MEM_START_AT, so they set up first and then measure
over the same SECONDS. Bandwidth comes from two places:
  cpu / gpu  each binary's own timer: how the bandwidth splits
  perf       perf's UCF counters are system wide, so one perf session sees
             both together: the combined total. Counts only the middle of
             the window (-D), as in the other two tests.
cpu + gpu should add up to perf; if not, the windows did not line up.

Aborts unless pin_ghz.sh has pinned the CPUs, the EMC (memory clock) and the
GPU clock.
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
CPU_DIR = HERE.parent / "cpu_bw"
GPU_DIR = HERE.parent / "gpu_bw"

PATTERNS = ["read", "write", "copy"]
CPU_GIB = 8
GPU_MIB = 1024
CPU_THREADS = os.cpu_count()
SECONDS = 30
SETUP_SECONDS = 3  # for both to allocate and touch their buffers

# The Thor runs NVIDIA's tegra kernel, so use this perf, not the distro wrapper.
PERF = "/usr/lib/linux-tools/6.8.0-146-generic/perf"
EVENTS = {"rd": "nvidia_ucf_pmu_0/local_dram_byte_rd/",
          "wr": "nvidia_ucf_pmu_0/local_dram_byte_wr/"}

CPUFREQ = Path("/sys/devices/system/cpu/cpufreq")
EMC = Path("/sys/class/devfreq/bwmgr")
GPU = Path("/sys/class/devfreq/gpu-gpc-0")
BUS_BYTES = 256 // 8  # LPDDR5X on Thor: 256-bit bus, two transfers per EMC clock


def pinned(lo_file, hi_file):
    """The pinned frequency, or exit if pin_ghz.sh has not pinned it."""
    lo, hi = int(lo_file.read_text()), int(hi_file.read_text())
    if lo != hi:
        sys.exit(f"[!] {lo_file.parent.name} not pinned ({lo}..{hi}); run sudo ./pin_ghz.sh on")
    return hi


def run(pattern):
    cmds = {
        "cpu": [str(CPU_DIR / "mem"), pattern, str(CPU_GIB), str(SECONDS), str(CPU_THREADS)],
        "gpu": [str(GPU_DIR / "mem"), pattern, str(GPU_MIB), str(SECONDS)],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "perf.csv"
        perf = [PERF, "stat", "-a", "-x,", "-e", ",".join(EVENTS.values()), "-o", str(out),
                "-D", str(int((SETUP_SECONDS + 0.5) * 1000)),
                "--", "sleep", str(SETUP_SECONDS + SECONDS - 0.5)]
        print("Running:", " + ".join(" ".join(c) for c in cmds.values()), flush=True)
        start_at = time.time() + SETUP_SECONDS
        env = dict(os.environ, MEM_START_AT=f"{start_at:.3f}")
        perf_proc = subprocess.Popen(perf, stderr=subprocess.DEVNULL)  # "Events enabled" chatter
        procs = {name: subprocess.Popen(cmd, env=env, text=True, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
                 for name, cmd in cmds.items()}
        outputs = {name: p.communicate() for name, p in procs.items()}  # (stdout, stderr)
        perf_proc.wait()
        for name, p in procs.items():
            if p.returncode != 0 or outputs[name][1]:
                print(f"Error ({name}):", outputs[name][1])
                return None
        # Rows are "count,unit,event,run_ns,...".
        gbs = {}
        for line in out.read_text().splitlines():
            f = line.split(",")
            for key, event in EVENTS.items():
                if len(f) > 3 and f[2] == event:
                    gbs[key] = float(f[0]) / float(f[3])  # bytes/ns = GB/s

    result = {f"{name}_gbs": float(re.search(r"gbs : ([\d.]+)", stdout).group(1))
              for name, (stdout, _) in outputs.items()}
    result.update(perf_rd_gbs=gbs["rd"], perf_wr_gbs=gbs["wr"], perf_gbs=gbs["rd"] + gbs["wr"])
    print(f"  {pattern:>5}: cpu {result['cpu_gbs']:.1f} + gpu {result['gpu_gbs']:.1f} = "
          f"{result['cpu_gbs'] + result['gpu_gbs']:.1f} GB/s, perf {result['perf_gbs']:.1f} "
          f"GB/s (rd {gbs['rd']:.1f}, wr {gbs['wr']:.1f})", flush=True)
    return result


def main():
    cpu_khz = {pinned(p / "scaling_min_freq", p / "scaling_max_freq")
               for p in CPUFREQ.glob("policy*")}
    emc_hz = pinned(EMC / "min_freq", EMC / "max_freq")
    gpu_hz = pinned(GPU / "min_freq", GPU / "max_freq")
    for d in (CPU_DIR, GPU_DIR):
        subprocess.run(["make", "-s", "-C", str(d), "mem"], check=True)

    out = HERE / "thor.json"
    data = {
        "cpu_threads": CPU_THREADS,
        "cpu_buffer_gib": CPU_GIB,
        "gpu_buffer_mib": GPU_MIB,
        "seconds": SECONDS,
        "cpu_khz": max(cpu_khz),
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
