# cpu_bw progress

## Current state
- `mem.cpp`: two 8 GiB buffers (src, dst), mmap + `MADV_HUGEPAGE` (both fully on 2 MiB pages; `huge_page_mib` = 16384 is printed and saved). One pinned thread per core, thread i on core i, each streaming its own contiguous slice over and over. Patterns: `read` (sum, kept alive by a magic-value check), `write` (fill), `copy` (src -> dst, counted as 2x bytes). Plain loops vectorised by `-O3 -mcpu=native`. Each thread first-touches its slice, waits for `MEM_START_AT` (if set), does one untimed warm-up pass, then counts its own bytes / own time; the threads' GB/s are summed (a preempted thread adds less instead of stalling a barrier).
  `./mem read|write|copy [GiB] [seconds] [threads]`, prints `gbs : X`.
- `collect_data.py`: 14 threads, 30 s per pattern -> `thor.json`; `plot_data.py` -> `thor.png` (per pattern: rd + wr bar with % of peak, and the same total split into rd / wr).
- `collect_threads.py`: 1..14 threads x 3 patterns, 8 s each (~8 min) -> `thor_threads.json`; `plot_threads.py` -> `thor_threads.png` (GB/s vs threads).
- Measurement (both collectors): system-wide `perf stat -a -D` on `nvidia_ucf_pmu_0/local_dram_byte_rd/` and `_wr/`, counting from 0.5 s after `MEM_START_AT` to 0.5 s before the end; GB/s = bytes / counted ns. Aborts unless pin_ghz.sh has pinned CPU and EMC clocks. Peak = EMC 4266 MHz x 2 x 32 B = 273.0 GB/s.
- Self-contained directory (no shared helpers); mixed_bw runs this `mem` binary too.

## Latest results (CPU 2.601 GHz, EMC 4266 MHz)
- 14 threads, 30 s (perf GB/s; mem's timer agrees to < 0.5):
  | pattern | rd + wr | rd / wr | % of 273 |
  |---|---|---|---|
  | read  | 230.9 | 230.8 / 0.1 | 85% |
  | write | 258.5 | 0.2 / 258.2 | 95% |
  | copy  | 235.4 | 117.9 / 117.6 | 86% |
  Read was 235 in earlier 8 s runs; ~2% run-to-run / thermal (a 3 s run also gave 231).
- Thread sweep (perf GB/s):
  | threads | 1 | 2 | 4 | 6 | 8 | 10 | 12 | 14 |
  |---|---|---|---|---|---|---|---|---|
  | read  | 20.7 | 41.0 | 81.4 | 121.1 | 157.5 | 189.7 | 214.9 | 231.5 |
  | write | 47.8 | 84.5 | 93.4 | 177.0 | 184.8 | 246.7 | 259.2 | 259.8 |
  | copy  | 39.6 | 75.6 | 119.7 | 173.0 | 205.8 | 224.5 | 234.0 | 236.5 |

## Findings
- **Read is limited per core**: ~20 GB/s per core (misses each core can keep in flight), near-linear to 8 threads, still rising at 14. The 85% read ceiling is 14 cores' worth, not a shared limit. (GPU reads reach 93% from the same DRAM.)
- **Write saturates DRAM at ~11 threads** (95%). Steps: flat at 2-4 (84-93) and 6-8 (177-185) threads, jumps at 5 and 9: looks like groups of cores sharing a ~90 GB/s link (sysfs doesn't show the grouping; unverified).
- Writes cause no read-for-ownership (perf rd ~0.2 GB/s): full-line streaming stores skip the read.
- ~5% of CPU writes are allocated in the SLC and written back later (`slc_allocate` 630 M per run); GPU writes bypass the SLC (see gpu_bw).
- **Why the hash table (scripts/threads) is below these numbers** (13 thr, sequential keys): inserts already reach `copy` (~118 rd + ~118 wr). Finds are limited by CPU work per key, not memory: 69 instructions / 10.7 cycles per find even in cache (IPC ~6.5) -> ~245 M finds/s per core. Software prefetch hurts sequential finds (NONE 3115 vs L1 2270 Mops); the hardware prefetcher already follows the stream. Relaxed CAS / no fence changed nothing.

## Open
- What the core grouping behind the write steps is.

## History
- 2026-10-09: mem.cpp + collector + bar chart; MEM_START_AT for mixed_bw; made self-contained (scripts/common.py removed); hash-table gap analysis.
- 2026-10-10: 30 s runs; plot replaced by the rd/wr chart (timer bar dropped, it matched perf); thread sweep added.
