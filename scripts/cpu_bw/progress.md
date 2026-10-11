# cpu_bw progress

## 2026-10-09 — mem.cpp: CPU DRAM bandwidth
- `mem.cpp`, the CPU twin of `scripts/gpu_bw/mem.cu`. Two 8 GiB buffers (src, dst), mmap + `MADV_HUGEPAGE` (2 MiB THP, as cas_ht.hpp does; no hugetlb reserved). The binary prints `huge_page_mib` from `/proc/self/smaps_rollup`: 16384 MiB, i.e. both buffers fully on 2 MiB pages.
- One thread per core (14), pinned. Each owns a contiguous 1/14 slice and walks it sequentially, over and over, for ~5 s. Patterns: `read` (sum; result stored only if it equals a magic value so loads stay), `write` (fill with a nonzero constant), `copy` (src -> dst, counted as 2x bytes). Plain loops, vectorised by `-O3 -mcpu=native`. Pages are first-touched in parallel by their owning thread before timing; one untimed warm-up pass per pattern.
- Time-based, not work-based: each thread counts its own bytes / own time, and the GB/s are summed. A thread the OS preempts (the devfreq kworker issue at 14 threads, see the top-level progress.md) just adds less instead of stalling everyone at a barrier.
- `./mem [read|write|copy|all] [GiB] [seconds] [threads]`; output is `name : value`.
- `collect_data.py` (whole run ~25 s) -> `thor.json`; `plot_data.py` -> `thor.png`, the same bar chart as gpu_bw. Aborts unless pin_ghz.sh has pinned the CPU and EMC clocks.
- Results (CPUs 2.601 GHz, EMC 4266 MHz -> peak 273.0 GB/s, 14 threads):
  | pattern | timed GB/s | perf GB/s (rd / wr) | % of 273 |
  |---|---|---|---|
  | read  | 235.3 | 235.9 (235.8 / 0.1) | 86% |
  | write | 258.5 | 258.3 (0.4 / 257.9) | 95% |
  | copy  | 237.6 | 237.9 (119.1 / 118.8) | 87% |
- vs GPU (gpu_bw): read 254 / write 253 / copy 239. The CPU writes slightly faster than the GPU, reads ~7% slower, and copies about the same.
- Writes cause no read-for-ownership traffic (perf rd 0.4 GB/s). Most likely the cores detect full-line streaming stores and skip the read, without any special instructions (not verified).
- Reads are the CPU's weak spot, probably because each core can only keep a limited number of cache misses in flight (memory-level parallelism, MLP), so 14 cores can't quite cover DRAM latency. The GPU has thousands of loads in flight. Untested; one check is the read bandwidth vs thread count.

## 2026-10-09 — MEM_START_AT
- `mem.cpp` waits until `MEM_START_AT` (unix time, env) before measuring, if set; used by scripts/mixed_bw to start alongside the GPU test. No effect when unset.

## 2026-10-09 — simplified; self-contained directory (current state)
- **Each test directory is self-contained**: no shared helpers in `scripts/common.py` (tried, reverted). Its collector has its own perf call, clock check and peak calculation, so it can be rerun and read on its own. Only `mixed_bw` reaches into the other two, for their `mem` binaries.
- `mem` runs one pattern per call and prints `gbs : X`. Usage: `./mem read|write|copy [size] [seconds] ...`.
- Measuring window: the collector sets `MEM_START_AT` = now + setup time; mem sets up (alloc, first touch), sleeps until then, does one untimed warm-up pass, and times itself for `seconds`. perf runs system-wide alongside (`perf stat -a -D <ms> -- sleep <s>`), counting from 0.5 s after the start to 0.5 s before the end. One total, no 1 s interval sampling, so no whiskers on the plot.
- `plot_data.py`: one bar chart, timer vs perf vs peak; ~55 lines.
- `mem.cpp`: no `all` mode and no separate `measure()`. Each worker first-touches its own slice, waits for the start, warms up and loops until `stop`. `huge_page_mib` is read just before stopping and saved in the json per pattern (16384 = both 8 GiB buffers on 2 MiB pages).
- Rerun (8 s, 14 threads, CPU 2.601 GHz / EMC 4266 MHz): read 235.1 / write 259.4 / copy 237.5 GB/s timed, perf 235.5 / 259.5 / 237.8. Same as before.

## 2026-10-09 — why the hash table doesn't reach these numbers
Smoke tests (scratch copy, 13 threads, CPU 2.601 GHz, `--sequential 1`, 10% fill, nothing in the repo changed). Question: why is the sequential hash table (scripts/threads) below this test's ~235 GB/s?
- **Inserts already reach it.** Sequential inserts move ~118 GB/s rd + ~118 GB/s wr ≈ 236 GB/s, same as `copy`. Only finds lag.
- **Not the fence or CAS ordering.** Relaxed `casp` instead of `caspal` and no fence in `flush_insert_queue` changed nothing (inserts 986 vs 980 Mops; uniform keys, DOUBLE, 8 GiB table).
- **Finds are limited by CPU work per key.** On an 8 MiB table (fits in cache, no DRAM), finds still run ~247 M/s per thread, the same as on the 8 GiB table with PREFETCH=NONE (245 M/s). perf (`instructions,cycles`, 1 thread, in-cache): **69.2 instructions and 10.7 cycles per find** (IPC ≈ 6.5, so the core is already busy). The count includes the test's batch building. 10.7 cycles at 2.6 GHz ≈ 243 M/s, which matches the measured rate.
- One 64 B line per key → ~16 GB/s per core at most. The bw-style loop (one word per line, no hash table logic) streams 390 M lines/s = 25 GB/s per core and reaches 240 GB/s at 13 threads.
- **Software prefetch hurts in sequential mode**: finds with PREFETCH=NONE 3115 Mops (≈200 GB/s, ~85% of `read`) vs L1 2270 Mops. The hardware prefetcher already follows the stream. The threads sweep only ran DOUBLE and L1.
- To get closer: fewer instructions per key.

## 2026-10-10 — 30 s runs, rd/wr plot
- `SECONDS` 8 -> 30. `plot_data.py` replaced by the rd/wr chart (same as gpu_bw): per pattern, an rd + wr bar (GB/s, % of peak) and the same total split into rd / wr (GB/s, share); a ~0 part is labelled at the bar's foot. The mem.cpp timer bar is gone (matches perf to < 1 GB/s). Old thor.json / thor.png deleted.
- Results (30 s, 14 threads, CPU 2.601 GHz / EMC 4266 MHz):
  | pattern | timed GB/s | perf GB/s (rd / wr) | % of 273 |
  |---|---|---|---|
  | read  | 230.4 | 230.9 (230.8 / 0.1) | 85% |
  | write | 258.4 | 258.5 (0.2 / 258.2) | 95% |
  | copy  | 235.0 | 235.4 (117.9 / 117.6) | 86% |
- Read is ~2% below the 8 s runs (235); a 3 s smoke run earlier today also gave 231, so it may be run-to-run / thermal rather than the longer window. Write and copy within ~1%.
- Write scaling (smoke, `./mem write 8 3 N`): 1 thr 48, 2: 84, 3: 92, 4: 94, 5: 141, 6: 177, 8: 185, 10: 247, 12: 257, 14: 259 GB/s. Plateaus at 2-4 and 6-8 threads, then jumps: looks like groups of cores sharing a ~90 GB/s link (sysfs cluster_id doesn't show the grouping; unverified).
