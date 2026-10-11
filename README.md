# ARMHiT

Minimal ARM (aarch64) port of DRAMHiT's casht++ uniform benchmark
(`--mode 11 --ht-type 3`). C++20, built with g++ and make; no other
dependencies, no cmake, no nix.

## Build

```sh
make CPUFREQ_MHZ=2600 PREFETCH=DOUBLE BRANCH=simd UNIFORM_PROBING=ON
```

The binary is `build/dramhit`. The knobs are DRAMHiT's cmake `-D` options,
passed as make variables. There is no cache, so a knob you leave out goes back
to its default; the binary is rebuilt whenever the flags change.

| knob | values | notes |
|---|---|---|
| `CPUFREQ_MHZ` | required | only scales the printed "cycles"; Mops are wall-clock |
| `PREFETCH` | `NONE DOUBLE L1 L2 L3 L1STRM L2STRM L3STRM` and `X_L1` for each X but L1 | see below |
| `BRANCH` | `simd`, `branched` | `simd` compares a whole bucket at once (NEON) |
| `UNIFORM_PROBING` | `ON`/`OFF` | rehash vs. next bucket when a bucket is full |
| `BUCKETIZATION` | `ON`/`OFF` | implied by `simd`, as in DRAMHiT |

`READ_BEFORE_CAS` is always on and the hasher is always CRC32C. Only
DRAMHiT's plain `2025` batch loops are ported (no `DRAMHiT_VARIANT` knob).

### Prefetch schemes (ARM `PRFM`)

Each scheme prefetches a bucket when its key is queued and, for the doubles,
again 8 entries before it is dequeued.

| PREFETCH | find path | insert path | x86 counterpart |
|---|---|---|---|
| `L1` / `L2` / `L3` | `PLDL{1,2,3}KEEP` | `PSTL1KEEP` | t0 / t1 / t2 (+ prefetchw) |
| `L1STRM` / `L2STRM` / `L3STRM` | `PLDL{1,2,3}STRM` | `PSTL1KEEP` | nta (L1STRM) |
| `X_L1` (X = `L2 L3 L1STRM L2STRM L3STRM`) | `PLD X` + `PLDL1KEEP` | `PLD X` + `PSTL1KEEP` | |
| `DOUBLE` | `PLDL3KEEP` + `PLDL1KEEP` | `PLDL2KEEP` + `PSTL1KEEP` | DRAMHiT's double (t2 + t0) |
| `NONE` | nothing | nothing | |

`DOUBLE` keeps DRAMHiT's split (L3 on finds, L2 on inserts); `L3_L1` uses L3
on both. The `STRM` hints still allocate in the (coherent) caches, as a
"used once" replacement hint, so they work as a first prefetch too.

## Run

The arguments match DRAMHiT's, and unique prefixes work the way they do with
boost (`--find_queue`):

```sh
build/dramhit --mode 11 --ht-type 3 --num-threads 14 --ht-size 536870912 --ht-fill 70 \
  --insert-factor 1 --read-factor 100 --batch-len 16 --find_queue 64 [--test 1]
```

`--test 1` checks that fill equals the number of keys and that found equals
keys × read-factor. `--sequential 1` replaces the hash: key `i + 1` goes to
cache line `i` (one key per 4-slot line), counted from the line the first
hashed key would land in, so each thread walks consecutive lines; it needs
`--ht-fill <= 25`. `--numa-split`, `--skew`, `--seed`, `--hw-pref`,
`--no-prefetch` and `--drop-caches` are accepted but ignored. Statistics go to
stdout in DRAMHiT's format (`set_mops : X, get_mops : Y`) and logs go to stderr.

Workload: thread `t` walks indexes `[t*N/T, (t+1)*N/T)` with
`N = ht_size*fill/100` and uses `key = knuth(idx + 1)` (`x * 11400714819323198485`).
That gives unique, non-zero keys without RNG, zipf or key arrays. Each thread
first-touches its own slice of the table (THP via madvise, or hugetlb if pages
are reserved) and is pinned to cpu `t`.

## Setup

```sh
sudo sysctl kernel.perf_event_paranoid=-1   # uncore PMUs without sudo
sudo ./pin_ghz.sh on                        # pin CPU, memory (EMC) and GPU clocks; `off` restores
```

Collectors abort unless the clocks are pinned. Use the perf at
`/usr/lib/linux-tools/6.8.0-146-generic/perf` (the tegra kernel has no matching
distro linux-tools). DRAM bandwidth comes from
`nvidia_ucf_pmu_0/local_dram_byte_{rd,wr}/`, counted system wide at the memory
side. dramhit can gate perf itself: given perf's `--control` fifos in
`ARMHIT_PERF_CTL` / `ARMHIT_PERF_ACK`, it enables counting only while a phase
runs.

## Experiments

Each experiment is a folder under `scripts/` with its own collector
(`collect_*.py`), plotter (`plot_*.py`), results (json / png / log next to the
scripts) and a `progress.md` describing what it measures, how, and what it
found. Folders are self-contained: no shared helper code.

### Plotting

The plotters use `styling/paper_style.py` (adapted from DRAMHiT's
`scripts/eurosys_2026/paper_style.py`); read `styling/PLOTTING.md` before
writing a new one. They need seaborn, which the system python lacks:

```sh
uv venv --system-site-packages .venv && uv pip install --python .venv/bin/python seaborn
```

## Layout

```
src/dramhit.hpp               config, operation types, logging
src/hashtables/cas_ht.hpp     the hashtable (cas_kht.hpp trimmed to insert_batch/find_batch)
src/tests/uniform_test.cpp    the test: threads, workload, timing, stats, perf control
src/args.cpp                  DRAMHiT-compatible argument parsing
src/main.cpp                  validation + entry
scripts/<experiment>/         collector, plotter, results, progress.md
styling/                      paper_style.py + PLOTTING.md
pin_ghz.sh                    pin / restore clocks
progress.md                   project-wide notes
ideas.md                      research directions (TEEs on the Thor)
perf_list.txt                 `perf list` on the Thor (PMU events, incl. UCF)
```
