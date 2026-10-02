#!/usr/bin/env python3
"""Sweep fill factor for every ARM software-prefetch flavour.

ARM port of DRAMHiT's scripts/eurosys_2026/collect_prefetches/collect_data.py.
No perf counters: only the throughput dramhit prints itself.

    python3 scripts/collect_data.py [out.json]   # default: results/thor.json
    python3 scripts/plot_data.py results/thor.json results/thor.svg

Edit build_cfgs / run_cfgs at the bottom to change the sweep.

Builds with the Makefile (DRAMHiT's cmake -D knobs, passed as make variables).
"""
import json
import os
import re
import subprocess
import sys

SOURCE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_DIR = os.path.join(SOURCE_DIR, "build")

PREFETCHES = ["DOUBLE", "DOUBLE_L2", "L1", "L2", "L3", "NTA", "L2STRM", "L3STRM", "NONE"]


def build(defines):
    cmd = ["make", "-C", SOURCE_DIR, f"BUILD_DIR={BUILD_DIR}"]
    cmd += [f"{k}={v}" for k, v in defines.items()]
    print("Running:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def run(run_cfg):
    args = [
        os.path.join(BUILD_DIR, "dramhit"),
        "--find_queue", "64",
        "--ht-fill", str(run_cfg["fill_factor"]),
        "--ht-type", "3",
        "--insert-factor", str(run_cfg["insertFactor"]),
        "--read-factor", str(run_cfg["readFactor"]),
        "--num-threads", str(run_cfg["numThreads"]),
        "--numa-split", str(run_cfg["numa_policy"]),
        "--no-prefetch", "0",
        "--mode", "11",
        "--ht-size", str(run_cfg["size"]),
        "--skew", "0.01",
        "--hw-pref", "0",
        "--batch-len", "16",
        "--seed", "1775762440565610239",
    ]
    print("Running:", " ".join(args), flush=True)
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        print("Error:", proc.stderr)
        return None
    return proc.stdout, proc.stderr


def parse_results(result, run_cfg, build_cfg, identifier):
    def dict_to_str(d):
        return "-".join(f"{k}={d[k]}" for k in sorted(d.keys()))

    row = {
        "build_cfg": build_cfg,
        "build_cfg_str": dict_to_str(build_cfg),
        "run_cfg": run_cfg,
        "run_cfg_str": dict_to_str(run_cfg),
        "identifier": identifier,
    }
    if result is None:
        return row
    stdout, _ = result
    # stats line looks like: set_mops : 479, get_mops : 844
    kv_pattern = re.compile(r"(\w+)\s:\s([\d\.]+)")
    row.update({k: float(v) for k, v in kv_pattern.findall(stdout)})
    return row


def save_json(data, filename):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)


if __name__ == "__main__":
    out_file = sys.argv[1] if len(sys.argv) > 1 else os.path.join(SOURCE_DIR, "results", "thor.json")
    os.makedirs(os.path.dirname(os.path.abspath(out_file)), exist_ok=True)

    # Build configurations: one per prefetch flavour
    build_cfgs = [
        {
            "PREFETCH": p,
            "BUCKETIZATION": "ON",
            "BRANCH": "simd",
            "UNIFORM_PROBING": "ON",
            "CPUFREQ_MHZ": "2600",  # Thor max is 2601
        }
        for p in PREFETCHES
    ]

    # Run configurations: vary fill_factor, others fixed
    run_cfgs = [
        {
            "insertFactor": 1,
            "readFactor": 2,
            "numThreads": os.cpu_count(),
            "numa_policy": 1,
            "size": 536870912,  # slots (16 B each) -> 8 GiB
            "fill_factor": f,
        }
        for f in range(10, 100, 10)
    ]

    all_results = []
    for bcfg in build_cfgs:
        build(bcfg)
        for rcfg in run_cfgs:
            row = parse_results(run(rcfg), rcfg, bcfg, bcfg["PREFETCH"])
            print(f"  {bcfg['PREFETCH']:>9} fill {rcfg['fill_factor']:>2}: "
                  f"get_mops {row.get('get_mops')}  set_mops {row.get('set_mops')}", flush=True)
            all_results.append(row)
            save_json(all_results, out_file)  # keep partial results if interrupted

    print(f"[OK] Saved {out_file}")
