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
| `PREFETCH` | `DOUBLE DOUBLE_L2 L1 L2 L3 NTA L2STRM L3STRM NONE` | find path, see below |
| `BRANCH` | `simd`, `branched` | `simd` compares a whole bucket at once (NEON) |
| `UNIFORM_PROBING` | `ON`/`OFF` | rehash vs. next bucket when a bucket is full |
| `BUCKETIZATION` | `ON`/`OFF` | implied by `simd`, as in DRAMHiT |

`READ_BEFORE_CAS` is always on and the hasher is always CRC32C. Only
DRAMHiT's plain `2025` batch loops are ported (no `DRAMHiT_VARIANT` knob).

### Prefetch flavours (ARM `PRFM`)

| PREFETCH | find path | x86 counterpart |
|---|---|---|
| `DOUBLE` | `PLDL3KEEP` when queued + `PLDL1KEEP` 8 entries before dequeue | t2 + t0 |
| `DOUBLE_L2` | `PLDL2KEEP` when queued + `PLDL1KEEP` 8 entries before dequeue | new |
| `L1` / `L2` / `L3` | `PLDL{1,2,3}KEEP` | t0 / t1 / t2 |
| `NTA` | `PLDL1STRM` | nta |
| `L2STRM` / `L3STRM` | `PLDL{2,3}STRM` | new |
| `NONE` | nothing | |

The insert path follows `PREFETCH`: `DOUBLE*` uses `PLDL2KEEP` when queued +
`PSTL1KEEP` before dequeue, `NONE` uses nothing, and everything else uses
`PSTL1KEEP` (prefetchw).

## Run

The arguments match DRAMHiT's, and unique prefixes work the way they do with
boost (`--find_queue`):

```sh
build/dramhit --mode 11 --ht-type 3 --num-threads 14 --ht-size 536870912 --ht-fill 70 \
  --insert-factor 1 --read-factor 100 --batch-len 16 --find_queue 64 [--test 1]
```

`--test 1` checks that fill equals the number of keys and that found equals
keys × read-factor. `--numa-split`, `--skew`, `--seed`, `--hw-pref`,
`--no-prefetch` and `--drop-caches` are accepted but ignored. Statistics go to
stdout in DRAMHiT's format (`set_mops : X, get_mops : Y`) and logs go to stderr.

Workload: thread `t` walks indexes `[t*N/T, (t+1)*N/T)` with
`N = ht_size*fill/100` and uses `key = knuth(idx + 1)` (`x * 11400714819323198485`).
That gives unique, non-zero keys without RNG, zipf or key arrays. Each thread
first-touches its own slice of the table (THP via madvise, or hugetlb if pages
are reserved) and is pinned to cpu `t`.

## Prefetch sweep

```sh
python3 scripts/collect_data.py   # -> results/thor.json; edit build_cfgs/run_cfgs to change the sweep
python3 scripts/plot_data.py results/thor.json results/thor.svg   # stdlib only
```

`collect_data.py` builds each variant with make and saves the JSON after
every run.

## Layout

```
src/dramhit.hpp       config, operation types, logging
src/cas_ht.hpp        the hashtable (cas_kht.hpp trimmed to insert_batch/find_batch)
src/uniform_test.cpp  the test: threads, workload, timing, stats
src/args.cpp          DRAMHiT-compatible argument parsing
src/main.cpp          validation + entry
```
