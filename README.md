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

## Experiments

Each experiment is a folder under `scripts/` with its own collector, plotter
and results (json + png next to the scripts). Each folder is self-contained:
its collector has its own build, run, perf and clock-check code.

| folder | sweep | series |
|---|---|---|
| `scripts/threads/` | 1 .. all cores, 10% fill, ~60 s phases, 1 run per point, full log in `thor-<size>.log`, `small`/`medium`/`large` table (8 MiB / 256 MiB / 8 GiB), hashed and `--sequential` keys | DOUBLE, L1 |
| `scripts/prefetches/` | fill 10..90%, 14 threads, 8 GiB, ~60 s phases, 1 run per point, full log in `thor.log` | every prefetch scheme |

```sh
python3 scripts/threads/collect_data.py large             # -> scripts/threads/thor-large.json
.venv/bin/python scripts/threads/plot_data.py scripts/threads/thor-large.json   # -> .png
.venv/bin/python scripts/threads/plot_finds.py scripts/threads/thor-large.json  # lookups only -> _finds.png
python3 scripts/prefetches/collect_data.py               # -> scripts/prefetches/thor.json
.venv/bin/python scripts/prefetches/plot_data.py scripts/prefetches/thor.json   # -> .png
```

Both record Mops and DRAM bandwidth (GB/s) for the insert and find phases.
Iteration counts are sized so every phase runs for ~10-12 s, i.e. ~10 one
second perf samples, which beats repeating short runs; passes after the first
insert pass update existing keys, as in DRAMHiT. threads keeps the median
over `--reps` runs (default 1); prefetches runs each point once, GB/s being
the median of the phase's 1 s samples (no band: the first and last samples
are partly warm-up / tail).

### Bandwidth

perf stat samples `nvidia_ucf_pmu_0/local_dram_byte_{rd,wr}/` every 1 s,
system wide, with the perf at `/usr/lib/linux-tools/6.8.0-146-generic/perf`
(the tegra kernel has no matching distro linux-tools). The collector starts
perf with counting off (`-D -1 --control fifo:...`) and passes the fifos to
dramhit in `ARMHIT_PERF_CTL` / `ARMHIT_PERF_ACK`; dramhit turns counting on
only while a phase runs. So setup is never counted, the samples split into an
insert run and a find run, and each phase's GB/s is the median of its samples
(each divided by the time it actually counted). Needs
`kernel.perf_event_paranoid` <= 0 for the uncore PMU without sudo.

### Plotting

The plotters use `styling/paper_style.py`, a copy of DRAMHiT's
`scripts/eurosys_2026/paper_style.py` with the prefetch flavours as the
palette; read `styling/PLOTTING.md` before writing a new one. They need
seaborn, which the system python lacks:

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
scripts/<experiment>/         collect_data.py, plot_data.py, results
styling/                      paper_style.py + PLOTTING.md
```
