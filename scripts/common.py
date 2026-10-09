"""Build and run helpers shared by the scripts/<test>/collect_data.py collectors.

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # scripts/
    import common

build() runs make with DRAMHiT's cmake -D knobs. run() runs build/dramhit,
optionally under perf, and returns the "name : value" stats it prints plus,
with perf, the DRAM bandwidth of the insert and find phases.

Bandwidth: perf samples the NVIDIA UCF PMU (the Thor's memory-side fabric;
see perf_list.txt) every PERF_INTERVAL_MS, system wide. dramhit opens perf's
--control fifos (ARMHIT_PERF_CTL / ARMHIT_PERF_ACK) and enables counting only
while a phase runs, pausing two samples between the phases, so the samples
split into an insert run and a find run. Each sample also carries how long it
counted,
so a sample cut short by a phase edge still gives the right GB/s.
"""
import os
import re
import statistics
import subprocess
import tempfile
from pathlib import Path

SOURCE_DIR = Path(__file__).resolve().parent.parent
BUILD_DIR = SOURCE_DIR / "build"
BINARY = BUILD_DIR / "dramhit"

PREFETCHES = ["DOUBLE", "DOUBLE_L2", "L1", "L2", "L3", "NTA", "L2STRM", "L3STRM", "NONE"]

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


def build(defines):
    cmd = ["make", "-s", "-C", str(SOURCE_DIR), f"BUILD_DIR={BUILD_DIR}"]
    cmd += [f"{k}={v}" for k, v in defines.items()]
    print("Running:", " ".join(cmd), flush=True)
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
    print("Running:", " ".join(args), flush=True)
    if not perf:
        proc = subprocess.run(args, capture_output=True, text=True)
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
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
        stats = parse_stats(proc)
        if stats is not None:
            stats.update(parse_perf(Path(out).read_text()))
        return stats


def parse_stats(proc):
    if proc.returncode != 0:
        print("Error:", proc.stderr)
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
        print(f"[!] perf: expected {len(PHASES)} counted phases, found {len(phases)}; "
              "no bandwidth recorded")
        return {}
    return stats
