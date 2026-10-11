#!/usr/bin/env python3
"""Fill sweep for every ARM software-prefetch scheme: Mops and DRAM bandwidth.

ARM port of DRAMHiT's scripts/eurosys_2026/collect_prefetches/collect_data.py,
plus the DRAM bandwidth of each phase from perf.

    python3 scripts/prefetches/collect_data.py
    .venv/bin/python scripts/prefetches/plot_data.py scripts/prefetches/thor.json

Results go next to this script: thor.json, saved after every point, and
thor.log, everything the sweep did: each command, dramhit's full output and
perf's raw samples, so any GB/s in the json can be checked by hand
(bytes / counted ns).

Schemes (PREFETCH=, see the Makefile): every single-level prefetch
(L1/L2/L3, keep and strm), each of those except L1 followed by an L1
prefetch before dequeue (X_L1), DRAMHiT's DOUBLE, and NONE.

One run per point. Each phase is sized to run ~TARGET_SECONDS (~60 s), so
its GB/s is the median of ~TARGET_SECONDS 1 s perf samples, and all of those samples
are kept for the plot's band. The pass counts come from the pass time at
the previous fill, scaled by the fill ratio; the first fill starts from a
single-pass pilot run. Passes after the first insert pass update keys that
are already there, as in DRAMHiT.
"""
import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

SOURCE_DIR = Path(__file__).resolve().parents[2]
BUILD_DIR = SOURCE_DIR / "build"
BINARY = BUILD_DIR / "dramhit"

PREFETCHES = ["NONE", "DOUBLE", "L1", "L2", "L3", "L1STRM", "L2STRM", "L3STRM",
              "L2_L1", "L3_L1", "L1STRM_L1", "L2STRM_L1", "L3STRM_L1"]  # see Makefile

# The Thor runs NVIDIA's tegra kernel, so the distro `perf` wrapper finds no
# matching linux-tools; this one works with it.
PERF = "/usr/lib/linux-tools/6.8.0-146-generic/perf"
PERF_INTERVAL_MS = 1000
# Bytes read from / written to local DRAM. any_byte_* also counts accesses
# the SLC serves.
UCF = "nvidia_ucf_pmu_0"
PERF_EVENTS = {"rd": f"{UCF}/local_dram_byte_rd/", "wr": f"{UCF}/local_dram_byte_wr/"}
# Samples that counted for less than this are mostly phase edge; skip them.
MIN_SAMPLE_NS = 20e6

PHASES = ["set", "get"]  # insert, find: the order dramhit runs them


LOG = None  # open log file, set in main()


def log(text=""):
    """Print and append to the log."""
    print(text, flush=True)
    LOG.write(text + "\n")
    LOG.flush()


def build(defines):
    cmd = ["make", "-s", "-C", str(SOURCE_DIR), f"BUILD_DIR={BUILD_DIR}"]
    cmd += [f"{k}={v}" for k, v in defines.items()]
    log("$ " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def dramhit_args(threads, size, fill, insert_factor, read_factor, test=False,
                 sequential=False):
    """DRAMHiT's uniform-test command line."""
    return [
        str(BINARY),
        "--mode", "11",
        "--ht-type", "3",
        "--num-threads", str(threads),
        "--ht-size", str(size),
        "--ht-fill", str(fill),
        "--insert-factor", str(insert_factor),
        "--read-factor", str(read_factor),
        "--batch-len", "16",
        "--find_queue", "64",
        "--test", "1" if test else "0",
        "--sequential", "1" if sequential else "0",
    ]


def run(args, perf=False):
    """Runs dramhit; returns its stats (and `<phase>_<rd|wr|total>_gbs` with
    perf), or None if it failed."""
    if not perf:
        log("$ " + " ".join(args))
        proc = subprocess.run(args, capture_output=True, text=True)
        log(proc.stdout.rstrip())
        return parse_stats(proc)

    with tempfile.TemporaryDirectory() as tmp:
        ctl, ack, out = (os.path.join(tmp, n) for n in ("ctl", "ack", "perf.csv"))
        os.mkfifo(ctl)
        os.mkfifo(ack)
        events = ",".join(PERF_EVENTS.values())
        cmd = [PERF, "stat", "-a", "-x,", "-I", str(PERF_INTERVAL_MS), "-D", "-1",
               "--control", f"fifo:{ctl},{ack}", "-e", events, "-o", out, "--"] + args
        # The pause between phases spans a whole sample, so at least one
        # sample counts nothing and splits the phases.
        env = dict(os.environ, ARMHIT_PERF_CTL=ctl, ARMHIT_PERF_ACK=ack,
                   ARMHIT_PERF_GAP_MS=str(2 * PERF_INTERVAL_MS))
        log("$ " + " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
        log(proc.stdout.rstrip())
        perf_text = Path(out).read_text()
        log("# perf samples: time_s,bytes,unit,event,counted_ns,pct  (GB/s = bytes / counted_ns)")
        log(perf_text.rstrip())
        stats = parse_stats(proc)
        if stats is not None:
            stats.update(parse_perf(perf_text))
        return stats


def parse_stats(proc):
    if proc.returncode != 0:
        log("Error: " + proc.stderr)
        return None
    # e.g. "set_mops : 479, get_mops : 844"
    return {k: float(v) for k, v in re.findall(r"(\w+) : ([\d.]+)", proc.stdout)}


def parse_perf(text):
    """perf stat -x, -I output -> median GB/s per phase.

    Rows are "time,count,unit,event,run_ns,pct,...". A sample is every row
    with the same time; a run of counted samples is one phase.
    """
    samples = {}  # time -> {"rd"/"wr": (bytes, run_ns)}
    names = {v: k for k, v in PERF_EVENTS.items()}
    for line in text.splitlines():
        f = line.split(",")
        if len(f) < 5 or f[3] not in names:
            continue
        # A disabled interval reads "<not counted>", or 0 with 0 ns run time.
        counted = f[1] not in ("<not counted>", "<not supported>") and float(f[4] or 0) > 0
        samples.setdefault(f[0], {})[names[f[3]]] = (
            (float(f[1]), float(f[4])) if counted else None)

    phases, current = [], None
    for t in sorted(samples, key=float):
        s = samples[t]
        if any(v is None for v in s.values()):
            current = None
            continue
        if current is None:
            current = []
            phases.append(current)
        run_ns = s["rd"][1]
        if run_ns >= MIN_SAMPLE_NS:
            current.append({k: b / run_ns for k, (b, _) in s.items()})  # B/ns = GB/s

    stats = {}
    for name, phase in zip(PHASES, phases):
        if not phase:
            continue
        for k in ("rd", "wr"):
            stats[f"{name}_{k}_gbs"] = statistics.median(p[k] for p in phase)
        stats[f"{name}_total_gbs"] = statistics.median(p["rd"] + p["wr"] for p in phase)
        stats[f"{name}_bw_samples"] = len(phase)
        stats[f"{name}_total_gbs_series"] = [p["rd"] + p["wr"] for p in phase]
    if len(phases) != len(PHASES):
        log(f"[!] perf: expected {len(PHASES)} counted phases, found {len(phases)}; "
              "no bandwidth recorded")
        return {}
    return stats


SIZE = 536870912  # slots (16 B each) -> 8 GiB
FILLS = list(range(10, 100, 10))
THREADS = 14  # all cores
TARGET_SECONDS = 60
MIN_BW_SAMPLES = 5  # warn below this many 1 s samples in a phase
METRICS = ["set_mops", "get_mops",
           "set_rd_gbs", "set_wr_gbs", "set_total_gbs",
           "get_rd_gbs", "get_wr_gbs", "get_total_gbs"]

CPUFREQ = Path("/sys/devices/system/cpu/cpufreq")
EMC = Path("/sys/class/devfreq/bwmgr")


def pinned(lo_file, hi_file):
    """The pinned frequency, or exit if pin_ghz.sh has not pinned it."""
    lo, hi = int(lo_file.read_text()), int(hi_file.read_text())
    if lo != hi:
        sys.exit(f"[!] {lo_file.parent.name} not pinned ({lo}..{hi}); run sudo ./pin_ghz.sh on")
    return hi


def factors(pass_seconds):
    """insert_factor, read_factor that make each phase ~TARGET_SECONDS."""
    return tuple(max(1, math.ceil(TARGET_SECONDS / s)) for s in pass_seconds)


def main():
    global LOG
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--threads", type=int, default=THREADS)
    ap.add_argument("--prefetches", nargs="+", default=PREFETCHES,
                    choices=PREFETCHES)
    ap.add_argument("--out", type=Path, default=HERE / "thor.json")
    args = ap.parse_args()

    cpu_khz = {pinned(p / "scaling_min_freq", p / "scaling_max_freq")
               for p in CPUFREQ.glob("policy*")}
    emc_hz = pinned(EMC / "min_freq", EMC / "max_freq")
    LOG = open(args.out.with_suffix(".log"), "w")
    log(f"# CPU {max(cpu_khz)} kHz, EMC {emc_hz} Hz, {TARGET_SECONDS} s phases")

    data = {
        "machine": "thor",
        "ht_size": SIZE,
        "ht_size_gib": SIZE * 16 / (1 << 30),
        "num_threads": args.threads,
        "target_seconds": TARGET_SECONDS,
        "perf_interval_ms": PERF_INTERVAL_MS,
        "cpu_khz": max(cpu_khz),
        "emc_hz": emc_hz,
        "plot_order": args.prefetches,
        "series": {},
    }

    for prefetch in args.prefetches:
        build({
            "PREFETCH": prefetch,
            "BUCKETIZATION": "ON",
            "BRANCH": "simd",
            "UNIFORM_PROBING": "ON",
            "CPUFREQ_MHZ": "2600",  # Thor max is 2601
        })
        series = data["series"][prefetch] = {
            "fills": [], "insert_factor": [], "read_factor": [],
            "insert_seconds": [], "find_seconds": [],
            # per point: the 1 s GB/s samples of each phase (the band)
            "set_total_gbs_samples": [], "get_total_gbs_samples": []}
        for m in METRICS:
            series[m] = []

        # Seconds per insert / find pass at the previous fill.
        log(f"\n## {prefetch}: pilot")
        pilot = run(dramhit_args(args.threads, SIZE, FILLS[0], 1, 1))
        pass_seconds, prev_fill = (pilot["insert_seconds"], pilot["find_seconds"]), FILLS[0]

        for fill in FILLS:
            scale = fill / prev_fill
            insert_factor, read_factor = factors([s * scale for s in pass_seconds])
            cmd = dramhit_args(args.threads, SIZE, fill, insert_factor, read_factor)
            log(f"\n## {prefetch} fill {fill}")
            r = run(cmd, perf=True)
            if not r:
                continue
            pass_seconds = (r["insert_seconds"] / insert_factor, r["find_seconds"] / read_factor)
            prev_fill = fill

            series["fills"].append(fill)
            series["insert_factor"].append(insert_factor)
            series["read_factor"].append(read_factor)
            series["insert_seconds"].append(r["insert_seconds"])
            series["find_seconds"].append(r["find_seconds"])
            for m in METRICS:
                series[m].append(r.get(m))
            for p in PHASES:
                series[f"{p}_total_gbs_samples"].append(r.get(f"{p}_total_gbs_series", []))
                n = int(r.get(f"{p}_bw_samples", 0))
                if n < MIN_BW_SAMPLES:
                    log(f"[!] {prefetch} fill {fill} {p}: only {n} bandwidth samples")
            log(f"=> {prefetch:>9} fill {fill:>2}: " + ", ".join(
                f"{p} {r.get(p + '_mops', 0):.0f} Mops {r.get(p + '_total_gbs', 0):.1f} GB/s"
                f" ({int(r.get(p + '_bw_samples', 0))} samples)"
                for p in PHASES))
            args.out.write_text(json.dumps(data, indent=2))

    log(f"[OK] Saved {args.out}")


if __name__ == "__main__":
    main()
