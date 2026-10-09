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
