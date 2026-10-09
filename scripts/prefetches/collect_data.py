#!/usr/bin/env python3
"""Fill sweep for every ARM software-prefetch flavour, on all cores.

ARM port of DRAMHiT's scripts/eurosys_2026/collect_prefetches/collect_data.py,
plus the DRAM bandwidth of each phase from perf (see scripts/common.py).

    python3 scripts/prefetches/collect_data.py [--reps N]
    .venv/bin/python scripts/prefetches/plot_data.py scripts/prefetches/thor.json

Results go next to this script (thor.json) and are saved after every run.
Iteration counts shrink as the fill grows, so every phase runs for ~12 s
(a dozen 1 s perf samples). Passes after the first insert pass
update keys that are already there, as in DRAMHiT.
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

SIZE = 536870912  # slots (16 B each) -> 8 GiB
FILLS = list(range(10, 100, 10))
METRICS = ["set_mops", "get_mops",
           "set_rd_gbs", "set_wr_gbs", "set_total_gbs",
           "get_rd_gbs", "get_wr_gbs", "get_total_gbs"]


def factors(fill):
    """insert_factor, read_factor: ~12 s per phase on the Thor with DOUBLE.

    At 70% fill on 14 threads one pass takes ~0.75 s to insert and ~0.4 s to
    find, and a pass is proportional to the fill.
    """
    return math.ceil(1050 / fill), math.ceil(2100 / fill)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--threads", type=int, default=os.cpu_count())
    ap.add_argument("--prefetches", nargs="+", default=common.PREFETCHES,
                    choices=common.PREFETCHES)
    ap.add_argument("--out", type=Path, default=HERE / "thor.json")
    args = ap.parse_args()

    data = {
        "machine": "thor",
        "ht_size": SIZE,
        "ht_size_gib": SIZE * 16 / (1 << 30),
        "num_threads": args.threads,
        "reps": args.reps,
        "plot_order": args.prefetches,
        "series": {},
    }

    for prefetch in args.prefetches:
        common.build({
            "PREFETCH": prefetch,
            "BUCKETIZATION": "ON",
            "BRANCH": "simd",
            "UNIFORM_PROBING": "ON",
            "CPUFREQ_MHZ": "2600",  # Thor max is 2601
        })
        series = data["series"][prefetch] = {"fills": []}
        for m in METRICS:
            series[m] = []
            series[f"{m}_samples"] = []

        for fill in FILLS:
            insert_factor, read_factor = factors(fill)
            cmd = common.dramhit_args(args.threads, SIZE, fill, insert_factor, read_factor)
            runs = [r for r in (common.run(cmd, perf=True) for _ in range(args.reps)) if r]
            if not runs:
                continue
            series["fills"].append(fill)
            for m in METRICS:
                samples = [r[m] for r in runs if m in r]
                series[f"{m}_samples"].append(samples)
                series[m].append(statistics.median(samples) if samples else None)
            last = {m: series[m][-1] for m in METRICS}
            print(f"  {prefetch:>9} fill {fill:>2}: " + ", ".join(
                f"{p} {last[p + '_mops'] or 0:.0f} Mops {last[p + '_total_gbs'] or 0:.1f} GB/s"
                for p in common.PHASES), flush=True)
            args.out.write_text(json.dumps(data, indent=2))

    print(f"[OK] Saved {args.out}")


if __name__ == "__main__":
    main()
