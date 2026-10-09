#!/usr/bin/env python3
"""Thread sweep: Mops and DRAM bandwidth from 1 to all cores.

ARM version of DRAMHiT's scripts/eurosys_2026/collect_bw/collect_threads.sh:
one hashtable (casht++, the uniform test at 10% fill), a couple of prefetch
flavours, and perf's UCF counters for bandwidth (see scripts/common.py).
Every point runs twice: with hashed keys (uniform) and with --sequential
(key i in cache line i), the best case for the same table.

    python3 scripts/threads/collect_data.py [small|medium|large] [--reps N]
    .venv/bin/python scripts/threads/plot_data.py scripts/threads/thor-large.json

Results go next to this script (thor-<size>.json) and are saved after every
run, so an interrupted sweep still plots.

Each phase runs for ~TARGET_SECONDS (a dozen 1 s perf samples). The pass
counts come from the pass time measured at the previous thread count, scaled
by the thread ratio; one thread starts from a single-pass pilot run. Passes
after the first insert pass update keys that are already there, as in
DRAMHiT.
"""
import argparse
import json
import math
import os
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import common  # noqa: E402

# Slots (16 B each), as in collect_threads.sh plus a medium step.
SIZES = {
    "small": 524288,      # 8 MiB
    "medium": 16777216,   # 256 MiB
    "large": 536870912,   # 8 GiB
}
FILL = 10  # --sequential needs <= 25
PREFETCHES = ["DOUBLE", "L1"]
MODES = {"uniform": False, "sequential": True}  # name -> --sequential
TARGET_SECONDS = 12
METRICS = ["set_mops", "get_mops",
           "set_rd_gbs", "set_wr_gbs", "set_total_gbs",
           "get_rd_gbs", "get_wr_gbs", "get_total_gbs"]


def factors(pass_seconds):
    """insert_factor, read_factor that make each phase ~TARGET_SECONDS."""
    return tuple(max(1, math.ceil(TARGET_SECONDS / s)) for s in pass_seconds)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("size", nargs="?", default="large", choices=SIZES)
    # One run per point: each phase already gives a dozen 1 s samples, and
    # repeats barely differed.
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--max-threads", type=int, default=os.cpu_count())
    ap.add_argument("--prefetches", nargs="+", default=PREFETCHES,
                    choices=common.PREFETCHES)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    size = SIZES[args.size]
    out = args.out or HERE / f"thor-{args.size}.json"
    data = {
        "machine": "thor",
        "ht_size": size,
        "ht_size_gib": size * 16 / (1 << 30),
        "fill": FILL,
        "reps": args.reps,
        "perf_interval_ms": common.PERF_INTERVAL_MS,
        "plot_order": args.prefetches,
        "modes": {mode: {"series": {}} for mode in MODES},
    }

    for prefetch in args.prefetches:
        common.build({
            "PREFETCH": prefetch,
            "BUCKETIZATION": "ON",
            "BRANCH": "simd",
            "UNIFORM_PROBING": "ON",
            "CPUFREQ_MHZ": "2600",  # Thor max is 2601
        })
        for mode, sequential in MODES.items():
            series = data["modes"][mode]["series"][prefetch] = {
                "threads": [], "insert_factor": [], "read_factor": []}
            for m in METRICS:
                series[m] = []
                series[f"{m}_samples"] = []

            # Seconds per insert / find pass at the previous thread count.
            pilot = common.run(common.dramhit_args(1, size, FILL, 1, 1, sequential=sequential))
            pass_seconds, prev_threads = (pilot["insert_seconds"], pilot["find_seconds"]), 1

            for threads in range(1, args.max_threads + 1):
                scale = prev_threads / threads
                insert_factor, read_factor = factors([s * scale for s in pass_seconds])
                cmd = common.dramhit_args(threads, size, FILL, insert_factor, read_factor,
                                          sequential=sequential)
                runs = [r for r in (common.run(cmd, perf=True) for _ in range(args.reps)) if r]
                if not runs:
                    continue
                pass_seconds = (statistics.median(r["insert_seconds"] for r in runs) / insert_factor,
                                statistics.median(r["find_seconds"] for r in runs) / read_factor)
                prev_threads = threads

                series["threads"].append(threads)
                series["insert_factor"].append(insert_factor)
                series["read_factor"].append(read_factor)
                for m in METRICS:
                    samples = [r[m] for r in runs if m in r]
                    series[f"{m}_samples"].append(samples)
                    series[m].append(statistics.median(samples) if samples else None)
                last = {m: series[m][-1] for m in METRICS}
                print(f"  {prefetch:>6} {mode:>10} {threads:>2} thr: " + ", ".join(
                    f"{p} {last[p + '_mops'] or 0:.0f} Mops {last[p + '_total_gbs'] or 0:.1f} GB/s"
                    for p in common.PHASES), flush=True)
                out.write_text(json.dumps(data, indent=2))

    print(f"[OK] Saved {out}")


if __name__ == "__main__":
    main()
