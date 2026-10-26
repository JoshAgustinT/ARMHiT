# cpu_bw progress

## Current state
- `mem.cpp`: size arg is **MiB per buffer** (default 8192); src and dst are always both allocated and touched, whatever the pattern (so `huge_page_mib` ~ 2 x MiB). mixed_bw passes `GIB * 1024`. Pattern loops live in `run_test()`, one explicit `if` per pattern; unknown pattern -> error. mmap + `MADV_HUGEPAGE`. One pinned thread per core, thread i on core i, each walking its own contiguous slice over and over. Patterns: `read` (sum src, kept alive by a magic-value check), `write` (fill dst), `copy` (src -> dst, counted as 2x bytes). Plain loops vectorised by `-O3 -mcpu=native`. Each thread first-touches its slice, waits for `MEM_START_AT` (if set), does one untimed warm-up pass, then counts its own bytes / own time; the threads' GB/s are summed (a preempted thread adds less instead of stalling a barrier).
  `./mem read|write|copy [MiB] [seconds] [threads]`, prints `gbs : X`.
- `collect_data.py`: buffer sizes 2 MiB .. 32 GiB (each of src, dst) in 4x steps x 1..14 threads x 3 patterns, 10 s each (336 runs, ~90 min) -> `thor.json` + `thor.log` (every run's mem command/output and perf command + raw 1 s samples, `perf stat -I 1000`, ~9 samples per run; GB/s = sum bytes / sum run_ns). `plot_threads.py` -> `thor_threads.png` (one panel per pattern, DRAM rd + wr vs threads, a line per size). `plot_data.py` -> `thor.png` (14-thread runs only; one panel per pattern, a bar per size split into perf DRAM rd / wr, total above the peak line; timed stays in the json only). Replaced the old fixed-8-GiB collect_data / collect_threads pair (2026-10-18).
- `collect_groups.py`: 5 patterns (`read`, `read_l3` = PRFM PLDL3KEEP only, `write`, `write_nt` = STNP, `write_pf` = stores + PSTL1KEEP 64 lines ahead) x 4 threads-per-group limits x every thread count from 1: 1 per group (cores 0,4,8,12; 1-4 thr), 2 per group (0,1,4,5,8,9,12,13; 1-8), 3 per group (0,1,2,4,5,6,8,9,10,12,13; 1-11), 4 per group = all (0..13; 1-14); each fills a group up to the limit before the next. 2 GiB buffers, 10 s each (185 runs, ~51 min) -> `thor_groups.json` (per run: per_group, threads, cores, timed / perf rd / wr GB/s) + `thor_groups.log`. `plot_groups.py` -> `thor_groups.png` (one panel per pattern; x = threads 1..14; a line per limit, all starting at 1 thread). mem.cpp's last arg also takes a core list (`0,4,8,12`, trailing comma OK for one core); a plain number still means cores 0..N-1.
- Measurement: system-wide `perf stat -a -D -I 1000` on `nvidia_ucf_pmu_0/local_dram_byte_rd/` and `_wr/`, counting from 0.5 s after `MEM_START_AT` to 0.5 s before the end; GB/s = bytes / counted ns. Aborts unless pin_ghz.sh has pinned CPU and EMC clocks. Peak = EMC 4266 MHz x 2 x 32 B = 273.0 GB/s.
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

- Size sweep (14 thr, 10 s; size = each buffer; timed / perf DRAM GB/s):
  | size | 2M | 8M | 32M | 128M | 512M | 2G | 8G | 32G |
  |---|---|---|---|---|---|---|---|---|
  | read  | 288 / 0 | 286 / 0 | 276 / 68 | 243 / 203 | 227 / 218 | 222 / 221 | 219 / 220 | 215 / 216 |
  | write | 1146 / 0 | 1145 / 0 | 567 / 67 | 326 / 254 | 269 / 256 | 258 / 256 | 255 / 255 | 254 / 254 |
  | copy  | 1100 / 0 | 990 / 2 | 342 / 208 | 245 / 228 | 232 / 230 | 229 / 230 | 228 / 228 | 226 / 226 |

## Findings
- **Threads per group (collect_groups, 2026-10-18; perf DRAM GB/s at the line's thread counts 1, 2, 3, ...)**:
  | pattern | 1 per group (1-4) | 2 per group (1-8) | 3 per group (1-11) | 4 per group / all (1-14) |
  |---|---|---|---|---|
  | read | 21 41 61 81 | 20 .. 154 | 20 .. 196 | 20 .. 222 |
  | read_l3 | 48 95 142 187 | 48 87 133 170 212 241 246 246 | 48 87 92 132 171 176 215 241 .. 242 | 48 87 92 92 120 160 173 173 206 235 239 235 242 241 |
  | write | 48 95 143 190 | 48 84 132 169 213 241 256 256 | 48 84 91 139 175 182 223 248 253 257 257 | 48 84 91 93 140 177 183 184 223 245 256 255 257 256 |
  | write_nt | = write (within 1) | | | |
  | write_pf | 45 81 110 134 | 45 .. 188 | 45 .. 190 | 45 .. 190 (8-11), 182 at 14 |
  Plain reads: the four lines coincide (~20 GB/s per core, under the group cap). read_l3 / write / write_nt: each line rises ~48 per added core while its current group has room and flattens at ~93 per full group, so the steps sit where each limit fills a group: 1 per group never steps (190 at 4 threads), 2 per group bends at 2 per group (84-87 for 2 cores), 4 per group shows the full 4-core plateaus (92-93 at 3-4 threads, 173-184 at 7-8). Writes reach DRAM's ~256 at 7 threads with 2 per group, 10-11 with 3-4 per group; read_l3 tops out ~242-246. read_l3 with 4 per group runs a bit under write at 5-8 threads (120 vs 140 at 5, 173 vs 184 at 8). write_pf: all lines coincide (rd = wr, every line read before written); placement-independent, ~45 GB/s for 1 core, flat ~188-190 from 8 threads, 182 at 14.
- **Threads x sizes (2026-10-18 sweep)**: for every size >= 128 MiB the thread curves sit on top of each other: read ~20 GB/s per core, near-linear, 216-222 at 14; write keeps its steps (flat 2-4 and 6-8 threads, jumps at 5 and 9; 255 from 11 on); copy saturates ~230 from 12. 128 MiB is a bit lower (part still cached). 32 MiB read/write tops out at 65-90 GB/s (mostly cached). Copy at 8 MiB: DRAM traffic rises to ~26 GB/s at 8 threads then falls back to ~2 at 14: with more cores the per-core slice shrinks into L2.
- **Units check (2026-10-18)**: GB/s everywhere is 1e9 B/s, same base as the 273.0 peak (4266e6 x 2 x 32 B). tegrastats `EMC_FREQ` during `./mem write|read 8192 12`: write 93% (mem 255.5 GB/s = 93.6% of 273), read 80% (220.4 = 80.7%). In GiB/s, write would be 274 GB/s = 100.5% of peak, which is impossible, so base 10 is right. The 1024 in plot_data.py only formats the buffer-size tick labels.
- **Cache cliff between 16 and 64 MiB of memory actually walked** (read/write walk one buffer, copy two; L2 is 14 x 1 MiB, something past it, likely the SLC, still holds ~16 MiB). So at 32 MiB per buffer read/write are only partly in DRAM (~68 GB/s) while copy (64 MiB walked) is mostly DRAM. From 512 MiB up timed == perf within ~1%; DRAM GB/s drifts down ~3% from 512 MiB to 32 GiB.
- **In-cache read is only ~286 GB/s** vs ~1100 for write/copy: the `sum +=` reduction is a dependency chain, so read measures the add loop, not the cache. Doesn't matter for DRAM sizes; would need several accumulators to measure cache read bandwidth.
- **Read is limited per core**: ~20 GB/s per core (misses each core can keep in flight), near-linear to 8 threads, still rising at 14. The 85% read ceiling is 14 cores' worth, not a shared limit. (GPU reads reach 93% from the same DRAM.)
- **Write steps = 4-core groups, each capped at ~93 GB/s of writes** (measured 2026-10-18 with a scratch mem taking a core list). Cores are grouped {0-3}, {4-7}, {8-11}, {12-13}: triples {a, a+1, j} write 92 GB/s when j is in a,a+1's group and 132 (= 84 + 48) otherwise, for every j. One core writes 48, two in a group 84, three or four 92-94; 4 threads on 0,1,2,3 = 93.6 but on 0,4,8,12 = 191. Since mem pins thread i to core i, the sweep fills group 0 (flat at 3-4 threads), then group 1 (jump at 5, flat at 7-8), then group 2, where DRAM (~256) caps it at 11 threads before the group cap could show; that's why there's no step at 12. Spreading the threads (0,4,8,12,1,5,9) reaches 251 GB/s at 6 threads and 258 at 7. Not memory channels: channels are address-interleaved, so every thread hits all 16; the cap follows which cores, not how many threads or which addresses. Reads show no group effect (4 cores packed 80.8 vs spread 81.2; 8 cores 153.8 vs 154.6), but a core only reads ~20 GB/s, so 4 x 20 = 80 never reaches ~93. Copy: 0-3 = 119, spread = 132. Linux topology (lscpu, sysfs cluster_id) and the device tree's cpu-map list 14 one-core clusters, so the grouping is only visible by measurement; likely 4 cores sharing one port into the on-chip fabric (unverified).
- **The groups cap reads too; it's not write-specific** (2026-10-18, scratch mem + perf UCF, 2 GiB). A core with PRFM PLDL3KEEP-only reads 47.8 GB/s from DRAM (plain loads 20.6, LDP 24.5), so 4 cores can exceed 93: pf_l3 on 0,1,2,3 = 92.1 vs 0,4,8,12 = 187.4 (write: 93.3 vs 190.2). Plain reads never showed it since 4 x ~20 < 93. In one group, 2 cores reading + 2 writing = 61 rd + 61 wr = 122 (vs 191 across groups): directions partly share the cap. The cap also applies to SLC hits but is higher: LDP from an 8 MiB buffer 0,1,2,3 = 141 vs 0,4,8,12 = 235; write 8 MiB 170 vs 284. A cap that rises with faster targets looks like a limit on outstanding requests per group (Little's law: 93 GB/s x ~178 ns DRAM chase latency / 64 B = ~260 lines in flight per group; 1 core at 48 GB/s = ~130) rather than a fixed link width (hypothesis). What the groups are: NVIDIA's Jetson Thor dev guide (r38.2, Platform Power and Performance) says "14 CPU clusters in 7 cluster pairs" sharing a clock unit, and BWMGR has 7 CPU_CLUSTER0-6 clients; the datasheet says one core per cluster. So a group of 4 = 2 cluster pairs ({0,1}+{2,3}, ...), and {12,13} is a lone pair. Pairs inside a group show a faint extra effect (write 0,1 = 84.4 vs 0,2 = 86.5, 0,4 = 95.4). Which fabric node the groups hang off isn't documented publicly. No way to route around it in software (single NUMA node, address-interleaved; every access goes through the core's own group port). The only lever is thread placement: spreading threads over groups helps below ~11 threads; with all 14 cores the 4 groups (~4 x 93) exceed DRAM's ~256, so the full-chip numbers are DRAM-bound anyway.
- Writes cause no read-for-ownership (perf rd ~0.2 GB/s): full-line streaming stores skip the read.
- ~5% of CPU writes are allocated in the SLC and written back later (`slc_allocate` 630 M per run); GPU writes bypass the SLC (see gpu_bw).
- **Why the hash table (scripts/threads) is below these numbers** (13 thr, sequential keys): inserts already reach `copy` (~118 rd + ~118 wr). Finds are limited by CPU work per key, not memory: 69 instructions / 10.7 cycles per find even in cache (IPC ~6.5) -> ~245 M finds/s per core. Software prefetch hurts sequential finds (NONE 3115 vs L1 2270 Mops); the hardware prefetcher already follows the stream. Relaxed CAS / no fence changed nothing.

## Open
- Which fabric node / port the 4-core groups (= 2 cluster pairs) attach to, and whether the cap is outstanding-request credits (SLC-hit cap > DRAM cap suggests so) or link width. The chase latencies under ~16 MiB (~7-9 ns, even at 1 MiB) look prefetcher-assisted, so SLC latency is still unmeasured.

## History
- 2026-10-09: mem.cpp + collector + bar chart; MEM_START_AT for mixed_bw; made self-contained (scripts/common.py removed); hash-table gap analysis.
- 2026-10-10: 30 s runs; plot replaced by the rd/wr chart (timer bar dropped, it matched perf); thread sweep added.
- 2026-10-18: mem.cpp readability pass (`pass` -> `run_test`, explicit per-pattern ifs, braces), size arg -> MiB per buffer, both buffers always touched; size sweep collector + plot + raw perf .log; then threads x sizes sweep, which replaced the old collect_data / collect_threads (new scripts took their names).
- 2026-10-18: write steps traced to 4-core groups {0-3},{4-7},{8-11},{12-13} (~93 GB/s write cap each).
- 2026-10-18: groups cap reads too (prefetch reads 92 packed vs 187 spread), SLC hits capped higher (~141-170); groups = 2 of NVIDIA's 7 cluster pairs.
- 2026-10-18: collect_groups / plot_groups (read, read_l3, write, write_nt, write_pf x 6 core sets); mem.cpp takes a core list.
- 2026-10-18: plot_groups -> line chart (packed vs spread over threads); collect_groups tags each core set with its order. Re-ran the 30 runs (the old json/log were overwritten by an aborted 1..14-thread sweep).
- 2026-10-18: collect_groups now 4, 8, 11, 14 threads for both orders (40 runs) so the two lines share x points.
- 2026-10-18: collect_groups redone as threads-per-group limits (1/2/3/4 per group, every thread count from 1; agreed on a fake-data mock first), 185 runs.

## 2026-10-18: how the 4 core groups were found (recap)
- Nothing in the system describes them: lscpu, sysfs (cluster_id, cache shared_cpu_list) and the device tree's cpu-map / MPIDRs all list 14 one-core clusters. Found by measurement only.
- 1) Thread sweep: write flattened at 3-4 threads (~93 GB/s) and 7-8, jumped at 5 and 9, which suggested cores sharing something in blocks of 4.
- 2) Placement, not thread count: 4 writers on cores 0,1,2,3 = 93 GB/s; on 0,4,8,12 = 191 (~4 x 48). Same thread count, different cores.
- 3) Mapping: 3 writers on {a, a+1, j} for every j. Same group as a, a+1: the 3rd core adds nothing (92). Other group: it adds its full ~48 (132). That split every core cleanly into {0-3}, {4-7}, {8-11}, {12-13}.
- Why 4 groups from 14 cores: they're unequal, 4 + 4 + 4 + 2. NVIDIA's Jetson Thor dev guide says the 14 cores form 7 "cluster pairs" sharing a clock; a group of 4 looks like 2 pairs, and {12,13} is the 7th pair alone. That's also why the "3 per group" line in collect_groups stops at 11 threads.

## 2026-10-18: staircase test (direct check of the core groups)
- `collect_staircase.py` / `plot_staircase.py` -> `thor_staircase.{json,log,png}`. `write` only, 2 GiB buffers, 10 s each, on the first 1..6 cores of 4 sequences: 0-3 then 4,5; 4-7 then 8,9; 8-11 then 12,13; 12-13 then 0-3 (24 runs, ~7 min). Plot: one block of bars per sequence (1-6 threads), GB/s on each bar, hatched once the threads reach a second group. Agreed on a fake-data mock first.
- Results (perf DRAM wr GB/s, 1..6 threads):
  | sequence | 1 | 2 | 3 | 4 | 5 | 6 |
  |---|---|---|---|---|---|---|
  | 0-3, then 4, 5 | 47.7 | 84.1 | 91.6 | 93.4 | 140.9 | 176.6 |
  | 4-7, then 8, 9 | 48.0 | 84.6 | 91.6 | 93.3 | 140.7 | 176.7 |
  | 8-11, then 12, 13 | 47.7 | 84.5 | 91.3 | 93.1 | 140.3 | 176.0 |
  | 12-13, then 0-3 | 48.0 | 84.2 | 131.8 | 168.3 | 175.2 | 176.5 |
- Confirms the groups: the three 4-core groups give the same staircase to within ~0.5 GB/s (48, 84, 92, 93: flat once 3-4 cores of a group are busy), and every jump lands exactly when the threads reach the next group (5th thread: +47; 6th: +36, like a 2nd core in a group). {12,13} behaves as its own 2-core group: full at 84 with 2 threads, then core 0 adds a full 48 (132), core 1 +36 (168), and cores 2-3 add ~8 between them (175, 176.5) as group 0 fills up. Every sequence ends at ~176-177 = two full groups.

## 2026-10-18: folder layout
- Each test now has its own subfolder with its collector, plotter and json / log / png; `mem.cpp`, `Makefile`, the `mem` binary and this progress.md stay at the top (collectors use `MEM_DIR = HERE.parent`; mixed_bw still runs `cpu_bw/mem`). File names unchanged:
  - `sizes/`: collect_data.py, plot_data.py, plot_threads.py, thor.{json,log,png}, thor_threads.png (buffer size x threads sweep)
  - `groups/`: collect_groups.py, plot_groups.py, thor_groups.* (threads per group limits)
  - `staircase/`: collect_staircase.py, plot_staircase.py, thor_staircase.* (fill a group, spill into the next)
  - `slices/`: plot_slices.py (+ fake_slices.* mock); next test, not run yet: one core per group (slice i = i-th core of groups 0-2, 1..3 threads), then a 4th thread from {12,13} once as 12 and once as 13; 20 runs, ~6 min. Expect 48 / 96 / 144 / ~190, all eight 4-thread bars equal (+12 = +13) if nothing couples across groups.
- Correction to the slices plan above: each slice is two full runs, A = cores i, 4+i, 8+i, 12 and B = cores i, 4+i, 8+i, 13, each at 1..4 threads (the 1-3 thread runs are repeated, which doubles as a repeatability check). 8 sequences x 4 = 32 runs, ~9 min. Mock plot: A and B blocks side by side per slice, B hatched.

## 2026-10-18: slices test results (one core per group)
- `slices/collect_slices.py` -> `slices/thor_slices.{json,log,png}`, 32 runs, all succeeded. perf DRAM wr GB/s at 1..4 threads:
  | slice | run | 1 | 2 | 3 | 4 |
  |---|---|---|---|---|---|
  | 0: cores 0, 4, 8 | A: +12 | 47.7 | 95.3 | 143.0 | 190.1 |
  | | B: +13 | 47.6 | 95.3 | 143.0 | 189.9 |
  | 1: cores 1, 5, 9 | A: +12 | 47.8 | 95.6 | 143.1 | 190.2 |
  | | B: +13 | 47.7 | 95.5 | 143.1 | 189.9 |
  | 2: cores 2, 6, 10 | A: +12 | 47.7 | 95.4 | 143.0 | 190.1 |
  | | B: +13 | 47.7 | 95.4 | 143.0 | 189.8 |
  | 3: cores 3, 7, 11 | A: +12 | 47.9 | 95.5 | 143.0 | 190.2 |
  | | B: +13 | 47.9 | 95.4 | 143.0 | 189.7 |
- With one core per group, bandwidth is exactly linear: every added core gives 47.5-47.8 GB/s, for every slice and with both 12 and 13 as the 4th core. All 32 points are within 0.5 GB/s of 47.7 x threads. No coupling between any core of groups 0-2 and cores 12 / 13, and no slice behaves differently, so the cap really is per group, not something shared across groups.
- Only visible difference: +13 ends 0.2-0.5 GB/s below +12 in all four slices (189.7-189.9 vs 190.1-190.2). Consistent sign but ~0.2%, at run-to-run noise level; not meaningful without repeats.
- Repeatability: the 1-3 thread runs measured twice (in A and B) agree within 0.1 GB/s.

## 2026-10-18: pairs test (each of cores 0-11 with core 12, then with 13)
- `pairs/collect_pairs.py` / `pairs/plot_pairs.py` -> `pairs/thor_pairs.{json,log,png}`. `write`, 2 threads: core c (0..11) + core 12, then c + 13; 2 GiB, 10 s; 24 runs, all succeeded. (This is the test the user meant by "slices"; the earlier slices run was my misreading: cumulative 1-4 threads instead of direct pairs. Lesson: write out the exact core sets of a few runs as lists and confirm before mocking.)
- Results (perf DRAM wr GB/s):
  | partner | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|
  | +12 | 95.4 | 95.8 | 95.6 | 95.7 | 95.8 | 95.6 | 95.8 | 95.7 | 95.7 | 95.8 | 95.6 | 95.5 |
  | +13 | 95.1 | 95.3 | 95.3 | 95.4 | 95.4 | 95.3 | 95.4 | 95.2 | 95.2 | 95.4 | 95.3 | 95.2 |
- Every pair writes 95.1-95.8 GB/s = two full cores (2 x ~47.7); none comes near the ~84 of two cores sharing a group. So no core of groups 0-2 shares anything with 12 or 13: {12, 13} is a separate group, confirmed pair by pair.
- +13 pairs are 0.2-0.5 GB/s below +12 pairs for every core (mean 95.3 vs 95.7), the same small offset as in the slices test. Consistent across 12 + 4 comparisons, so probably real, but ~0.4%: core 13 alone may write a hair slower than 12. Not meaningful for the group question.

## 2026-10-18: pairs test re-run with same-group reference pairs
- `pairs/collect_pairs.py` now also runs SAME_GROUP = {2,3}, {6,7}, {10,11}, {12,13} after the 24 cross pairs (28 runs, ~8 min); `plot_pairs.py` draws them as a third block "same group (reference)" (stacked tick labels, 4th colour for group {12-13}). Re-ran the whole collector + plotter end to end from the command line as a proof test: both exit 0, 28 / 28 runs.
- Results (perf DRAM wr GB/s): +12 pairs 95.3-95.6, +13 pairs 95.0-95.4, same-group pairs 84.3 / 84.4 / 84.2 / 84.1. Cross-group pairs match the previous run within ~0.3 GB/s.
- The reference block makes the contrast direct: two cores in one group lose ~11 GB/s (84 vs 95), and {12,13} loses exactly as much as {2,3}, {6,7}, {10,11}, so the 2-core last group shares its port like the 4-core ones. +13 is again ~0.2-0.3 GB/s under +12 for every core (3rd time).

## 2026-10-18: analysis, what the group evidence shows (discussion, no new runs)
- Evidence for a per-group shared resource between the cores and memory:
  1. Depends on which cores, not how many: 4 writers packed in one group = 93 GB/s, one per group = 190 (same thread count, same addresses).
  2. Membership is exact and repeatable: triples, staircase, slices and pairs all give {0-3}, {4-7}, {8-11}, {12-13}, no exceptions across ~100 runs.
  3. Across groups it adds perfectly: one core per group = 47.7 GB/s per core (within 0.5); every cross-group pair ~95.
  4. Within a group it doesn't: 2 cores 84 (not 95), 3 cores 92, 4 cores 93; {12,13} loses exactly like {2,3}, {6,7}, {10,11}.
  5. Reads are capped too (prefetch reads 92 packed vs 187 spread), so it isn't write-specific (e.g. a write buffer).
- Ruled out: memory channels (addresses interleave over all 16; the cap follows cores, not addresses); DRAM itself (190+ across groups); clocks / power (pinned, 48 per core everywhere); OS / scheduling (pinned threads, identical across runs and patterns). What's left is something each group shares at the fabric / bus level.
- Probably not a plain bandwidth cap: a fixed 93 GB/s pipe would give ~93 for 2 cores asking for 96; they give 84, and the curve bends smoothly (48, 84, 92, 93). That looks like cores competing for a shared pool of outstanding requests. Consistent with: SLC hits capped per group too but higher (141 read / 170 write), and Little's law 93 GB/s x ~178 ns / 64 B = ~260 lines in flight per group vs ~130 for one core. Hypothesis: each group of 4 cores (= 2 of NVIDIA's 7 cluster pairs) shares one fabric port with a limited number of request slots.
- Still open: what the shared port physically is (not public); request slots vs link width (curve shape and SLC cap favour slots, not proven); core 13 ~0.3% slower than 12 (seen 3 times, tiny).
- Possible next test (not run): one core's pointer-chase latency while other cores write heavily, either in the same group or in other groups. A shared request pool should raise latency much more for same-group writers; a plain link should depend mostly on total traffic.
- Why contiguous groups with a short last one (interpretation, not documented): Linux core numbers follow the hardware IDs (MPIDR 0x0-0xd, no gaps), which usually go by position on the die, so neighbours share a port -> 0-3, 4-7, 8-11. 7 cluster pairs at 2 pairs per port = 2+2+2+1 = 4+4+4+2, which looks like a 16-core design (4 ports x 4 cores) with one pair disabled; the 12-core T4000 would be exactly 3 full groups. Can't tell whether the disabled pair was physically the last one or firmware renumbered the cores to put the gap at the end.

## 2026-10-18: folder layout reverted
- Subfolders removed again (user preference): every test's collector, plotter and json / log / png sit directly in cpu_bw/ next to mem.cpp / Makefile / mem. Collectors use `HERE / "mem"` again. Files: collect_data / plot_data / plot_threads / thor.* + thor_threads.png (sizes), collect_groups / plot_groups / thor_groups.*, collect_staircase / plot_staircase / thor_staircase.*, collect_slices / plot_slices / thor_slices.*, collect_pairs / plot_pairs / thor_pairs.*.
- Deleted the slices test (collect_slices.py, plot_slices.py, thor_slices.*): redundant with the groups test's 1-per-group line (linear scaling) and the pairs test (no coupling with 12 / 13). Its results stay above in this file.
- cpufreq policies show the cluster pairs directly: `pin_ghz.sh off` lists policy0 = cpus 0,1, policy2 = 2,3, ..., policy12 = 12,13. One cpufreq policy per clock domain (named after its first CPU); NVIDIA's dev guide: 14 clusters in 7 cluster pairs sharing a clock unit, DVFS per pair. So two levels: pairs share a clock, and each 4-core bandwidth group = 2 pairs ({0,1}+{2,3}, {4,5}+{6,7}, {8,9}+{10,11}), with {12,13} the lone 7th pair. The faint pair effect (write 0,1 = 84.4 vs 0,2 = 86.5) may be the clock-pair level showing through.
- Smoke test (clocks re-pinned, cpu_bw/mem write 2 GiB 10 s, perf UCF wr): {0,1} 84.5, {0,2} 86.6, {0,3} 86.7, {0,4} 95.6 GB/s (mem's timer agrees within 0.1). Matches the earlier scratch numbers (84.4 / 86.5 / 86.5 / 95.4). So the limit follows the 4-core group, not the cpufreq clock pair: {0,2} and {0,3} are in different clock pairs but still lose ~9 GB/s vs {0,4}; sharing the clock pair as well ({0,1}) costs only ~2 GB/s more.
- All 18 pairs inside groups 0-2 (same smoke-test setup, write 2 GiB 10 s, perf wr GB/s):
  | group | same clock pair | different clock pair |
  |---|---|---|
  | 0-3 | {0,1} 84.4, {2,3} 84.5 | {0,2} 86.5, {0,3} 86.7, {1,2} 86.7, {1,3} 86.7 |
  | 4-7 | {4,5} 84.6, {6,7} 84.6 | {4,6} 86.9, {4,7} 86.6, {5,6} 87.0, {5,7} 87.0 |
  | 8-11 | {8,9} 84.6, {10,11} 84.6 | {8,10} 86.5, {8,11} 86.5, {9,10} 86.8, {9,11} 86.8 |
  The clock-pair effect is real and uniform: same clock pair 84.4-84.6 (6/6), different clock pair 86.5-87.0 (12/12), no overlap, ~2.2 GB/s apart in every group. So two levels of sharing: the clock pair costs ~2 GB/s, the 4-core group ~9 GB/s more (vs ~95.5 across groups). Possibly the cluster pair shares something small (its own bridge / queue into the group port) on top of the group-level limit.

## 2026-10-18: clock_pairs test (every 2-core pair inside a group)
- `collect_clock_pairs.py` / `plot_clock_pairs.py` -> `thor_clock_pairs.{json,log,png}`. `write`, 2 GiB, 10 s; all 19 in-group pairs (6 per 4-core group + {12,13}; exhaustive for in-group pairs) + 3 cross-group reference pairs; 22 runs, all succeeded. Plot: block of same-clock-pair bars, block of same-group-different-clock-pair bars (hatched), coloured by group; dashed line = mean of the references. Agreed on a fake-data mock first.
- Results (perf DRAM wr GB/s):
  - same clock pair: {0,1} 84.3, {2,3} 84.5, {4,5} 84.6, {6,7} 84.4, {8,9} 84.5, {10,11} 84.3, {12,13} 83.9
  - same group, different clock pairs: {0,2} 86.4, {0,3} 86.4, {1,2} 86.5, {1,3} 86.8, {4,6} 86.7, {4,7} 86.6, {5,6} 86.8, {5,7} 86.7, {8,10} 86.7, {8,11} 86.4, {9,10} 86.5, {9,11} 86.7
  - different groups (reference): {0,4} 95.2, {4,8} 95.5, {8,12} 95.5 (mean 95.4)
- Three clean levels, no overlap: same clock pair 83.9-84.6, same group / different clock pair 86.4-86.8, different groups 95.2-95.5. Matches the smoke test within ~0.3. The clock pair costs ~2.2 GB/s on top of the ~9 GB/s group cost. {12,13} is the lowest same-clock-pair (83.9, ~0.5 below the rest), in line with core 13 being a hair slower in the pairs / slices tests.

## 2026-10-18: implications for TEEs / partitioning (discussion, no new runs)
- Interference is local and cheap: cores in other groups cost a victim nothing until total traffic nears DRAM's ~256 (>= ~6 heavy cores); 1 heavy neighbour in the victim's group -> victim ~42 instead of 48 (~12%, from 84 / 2), 3 neighbours -> ~23 (~50%, from 93 / 4). Even split assumed; per-core shares not measured. Partition schemes that hand out single cores as "isolated" miss this; whole-group assignment doesn't.
- Suggestions: partition at group granularity (clock pairs already force pair granularity via shared DVFS); {12,13} is a natural island (own port, own clock pair) for a TEE / real-time partition. Caveat: TrustZone TEEs (e.g. OP-TEE) usually run on the calling core, time-sliced, so core partitioning matters more for hypervisor partitions / dedicated-core TEEs. The shared port is also a group-local covert / side channel (a core can observe its neighbours' memory traffic through its own bandwidth).
- Optimization: below ~11 threads spread memory-heavy threads over groups (4 threads: 93 packed vs 190 spread); put a critical memory-bound thread where its group neighbours are compute-bound, or on {12,13}.
- If continuing (otherwise write up): (1) latency under interference: pointer-chase latency of one core with 1-3 heavy writers in its group vs in other groups (real-time cares about latency, not measured yet); (2) check whether the kernel exposes Arm MPAM (/sys/fs/resctrl) and whether it could regulate group-port contention or only DRAM.
- Does it matter in practice? Probably invisible for most real workloads: plain loads get ~20 GB/s per core, so even 4 reading cores in a group (~80) stay under the ~93 cap. Only write / copy-heavy streaming (memset, memcpy, buffer fills, hash-table inserts) or aggressive prefetching from 2+ cores of one group reaches it. Where it does matter: worst-case / adversarial analysis (an attacker picks streaming stores on purpose), benchmark interpretation (the default 0, 1, 2, ... pinning produced our sweep's steps), and partial-core write-heavy jobs (spreading over groups, up to ~2x bandwidth at 4-8 threads). A footnote with practical value for pinning, benchmarking and isolation claims, not a big performance story.
- Idea, squeezing performance via placement (not tested on a real workload): 3 cores already reach ~92 of a group's ~93 cap, and chip-wide DRAM (~256) saturates with only 2 memory-streaming cores per group (groups test, 2-per-group line: writes 256 at 7 threads, prefetch reads ~246 at 7-8). So for write / copy / prefetch-heavy work: memory threads on 2 cores per group (prefer different clock pairs, e.g. 0,2 / 4,6 / 8,10 / 12,13, ~2 GB/s better per group than 0,1), compute on the other ~6 cores at no bandwidth cost. Doesn't apply to plain-load reads (per-core limited, ~20 GB/s, need all 14 cores for ~220). Compute cores still share the group port, so they should be light on memory. Candidate to try on the hash table (insert threads on 2 per group).
