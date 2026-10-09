# ARMHiT progress

## Setup (needed for every run)
- `sudo sysctl kernel.perf_event_paranoid=-1` so perf can read the uncore PMUs without sudo.
- `./pin_ghz.sh on`: CPUs at 2.601 GHz and memory clock (EMC, devfreq `bwmgr`) at 4266 MHz; `./pin_ghz.sh off` restores both.
  Unpinned, the EMC drops to 2750 MHz when idle and takes ~1 s to ramp; pinning raised L1 uniform lookups ~15-20%.
- Table on 2 MiB THP (no hugetlb reserved; checked in smaps, `thp_fault_fallback` 0). 1 GiB pages would need a sudo reservation + MAP_HUGE_1GB.
- Plotting needs the `.venv` (seaborn).

## How bandwidth is measured
- perf stat, system wide, `nvidia_ucf_pmu_0/local_dram_byte_{rd,wr}/`, 1 s samples.
- perf starts with counting off; dramhit turns it on only inside each timed phase (perf --control fifos) and pauses 2 samples between phases, so samples split cleanly into insert and find. GB/s per phase = median of samples (bytes / counted ns).
- Phases are sized to ~12 s (a dozen samples). Samples are flat in steady state; 1 run per point is enough except uniform DOUBLE lookups at 9-14 thr, which are noisy run to run.
- Sanity: Mops (binary's wall clock) and GB/s (perf) are independent, yet give exactly 64 B per sequential lookup and 128 B per sequential insert (read + writeback).

## Log
- 23:44 started: exploring DRAMHiT
- env finding: no g++/cc1plus, no cmake, no perf, no sudo. Only gcc 13 (C) + python3/matplotlib (no pandas/seaborn).
  -> plan: port in C11 (zero deps), CMakeLists.txt w/ same -D flags + Makefile fallback; PMU counters via perf_event_open in-process.
- 23:44 read CMakeLists + cas_kht.hpp; reading Application/zipfian/uniform/kvtypes
- 23:45 reading print_stats/types/numa
- user: no profiling/PMU; timing only. dropped perf counters entirely.
- design: C11, src/{dramhit.h,cas_ht.h,args.c,uniform_test.c,main.c}; Makefile (+CMakeLists.txt, same -D names); scripts/{collect_data.py,plot_data.py}
- ARM prefetch: PRFM PLDL{1,2,3}{KEEP,STRM}, PSTL1KEEP for inserts. Bucket compare via NEON (BRANCH=simd) or scalar.
- workload: index approach, key = knuth(tid*(N/T) + i + 1), no arrays, no zipf/RNG.
- $(date +%H:%M) writing sources
- 23:51 sources+Makefile+CMakeLists written; first build
- 23:52 first build OK, --test passes (14 thr, 16M slots, 70%). checking all variants
- 23:53 scripts written (collect_data.py, plot_data.py stdlib-only SVG); smoke testing
- 23:53 smoke test OK; launched full sweep -> results/thor.json (8GiB, 14 thr, rf10, 9 prefetches x 9 fills)
- 23:53 README written; sweep running (~5s/run, ~81 runs)
- DONE: full sweep complete, 81/81 runs OK, found==find_ops on every run. results/thor.json + results/thor.svg
  find Mops @30% fill (14 thr, 8GiB): L2 ~1400, DOUBLE_L2 ~1390, L2STRM ~1370, DOUBLE ~1260, L3/L3STRM ~770, L1/NTA ~560, NONE ~290

- We call sudo sysctl kernel.perf_event_paranoid=-1 -- to set perf



## Rewrite in C++ (2026-10-02)
- g++ 13 now available. Plan: src/*.c -> src/*.cpp/.hpp, built with g++ -std=c++20; drop CMakeLists.txt.
- cas_ht.hpp = DRAMHiT's cas_kht.hpp trimmed to: types, ctor/dtor, insert_batch, find_batch (+ the queue/probe helpers they call, flushes, get_fill). AVX-512 bucket compare -> NEON with the same mask layout so the code reads the same.
- uniform_test.cpp rewritten for readability; Makefile trimmed to the knobs collect_data.py uses.
- DONE: C++ rewrite builds with g++ (make only, CMakeLists.txt removed). All 24 variant combos (2025/2025_INLINE x simd/branched x UNIFORM on/off x DOUBLE/L2/NONE) pass --test. 8GiB @30%: L2 find ~1340-1390 Mops (was ~1400), DOUBLE ~1220; collect_data.py + plot_data.py smoke-tested.
- 12:16 removed DRAMHiT_2025_INLINED fast paths: insert_batch/find_batch are plain pop+add loops; DRAMHiT_VARIANT knob dropped (Makefile, script, README). 12 combos pass --test.
- 13:02 collect_data.py back to DRAMHiT's interface: hardcoded build_cfgs/run_cfgs, no flags; optional out path (default results/thor.json).

## Restructure + thread/bandwidth sweep (2026-10-08, from JOSH_TODO.md)
- src/ split: src/hashtables/cas_ht.hpp, src/tests/uniform_test.cpp (Makefile adds -Isrc).
- uniform_test.cpp simplified: one linear worker (pin, first-touch, timed insert, timed find), no RunStats/KeyRange/timed_phase helpers.
- BW: perf stat -I 100 -D -1 --control fifo; dramhit enables counting only inside each timed phase (300 ms gap between phases), so samples split cleanly per phase. Events: nvidia_ucf_pmu_0/local_dram_byte_{rd,wr}/. GB/s = median over samples of bytes / counted ns.
- scripts/<experiment>/ folders with their own results; scripts/common.py shared. results/ removed (old thor.json moved to scripts/prefetches/, plotter reads it as legacy).
- styling/paper_style.py + PLOTTING.md copied from DRAMHiT eurosys_2026 (palette = prefetch flavours). seaborn via uv venv in .venv/.
- Calibration (8GiB, 70%, DOUBLE): 1 pass @1 thr = 7.1 s insert, 4.2 s find; @14 thr 0.74 / 0.38 s. Factors: insert ceil(T/2), read T (x table-size scale).
- DONE: thread sweep (large, 3 reps, 9 prefetches x 1..14 thr, ~2 h) -> scripts/threads/thor-large.{json,png,log}. 0 failed runs.
  @13 thr: get DOUBLE_L2 ~1240 Mops / 91 GB/s, L2 ~1130, DOUBLE ~1050, L3 ~720, L1/NTA ~560, NONE ~340; set DOUBLE* ~800, others ~490, NONE ~250.
  Every flavour drops 10-20% Mops from 13 -> 14 threads while GB/s stays flat; likely the 14th thread sharing a core with perf/OS (unverified).

## Smoke tests: long phases, 1 s perf samples (2026-10-08)
- Huge pages: no hugetlb reserved (HugePages_Total 0, would need sudo), so calloc_ht falls back to THP; smaps shows the whole 8 GiB table as AnonHugePages (2 MiB). 1 GiB pages would need a reservation + MAP_HUGE_1GB.
- Bug found + fixed: with 1 s samples the 300 ms pause between phases was shorter than a sample, so perf saw one merged phase. Pause is now 2x the interval (ARMHIT_PERF_GAP_MS from common.py).
- 13 thr, 8 GiB, simd/bucketized/uniform probing, --test on (all ok). Passes sized for ~25 s per phase, 1 s samples:
  | run | passes ins/rd | set Mops | set GB/s (rd+wr) | n | get Mops | get GB/s | n |
  |---|---|---|---|---|---|---|---|
  | DOUBLE 70% | 56/71   | 842 | 123.0 (65.9+57.1) | 26 | 1016 | 84.3 | 27 |
  | DOUBLE 10% | 458/650 | 993 | 129.1 (64.9+64.2) | 25 | 1228 | 91.2 | 30 |
  | L3 70%     | 34/48   | 489 | 73.6 (41.3+32.2)  | 27 | 731  | 88.7 | 25 |
  | L3 10%     | 259/441 | 555 | 76.4 (40.7+35.7)  | 26 | 787  | 93.8 | 31 |
- Samples are flat in steady state (e.g. DOUBLE 70% get: 84-86 GB/s for 22 samples), then a 2-6 s tail at 7-40 GB/s: some threads finish early and the phase waits for the slowest. Median ignores the tail; Mops (ops / slowest thread) includes it.
- Earlier, shorter runs (same config): 100 ms vs 1 s samples change GB/s by <3; 10% fill vs 70% gives +8 GB/s DOUBLE get.

## Why L3 moves more bytes per lookup (2026-10-08)
- Partial 10%-fill thread sweep (DOUBLE 1-14, L3 1 thr; ~10 s phases, 1 s samples), stopped by request. DOUBLE 14 thr: set 912 Mops 130.5 GB/s, get 1160 Mops 93.6 GB/s.
  DOUBLE 13 thr lost its BW: perf printed disabled intervals as 0 with 0 ns run time, which the parser did not treat as a gap -> phases merged. Fixed in common.parse_perf (run time 0 = not counted).
- Code check: find path for L3 is one PRFM pldl3keep per key; DOUBLE is the same pldl3keep + pldl1keep. L3 issues fewer SW prefetches, so the extra bytes are not from our code.
- HW prefetchers can't be turned off here: Neoverse V3AE (0xd83) controls them in IMP_CPUECTLR_EL1, EL1-only -> needs a kernel module + sudo (sudo needs a password).
- PMU instead, find phase only, 1 thr, 10% fill, 8 GiB, per lookup:
  | event | DOUBLE | L3 | NONE |
  |---|---|---|---|
  | get Mops | 113 | 76 | 27 |
  | l2d_cache_refill | 1.11 | 1.63 | 1.64 |
  | l2d_cache_lmiss_rd (demand L2 miss) | 0.05 | 1.05 | 1.04 |
  | ll_cache_miss_rd | 0.02 | 0.02 | 1.01 |
  | slc_refill_rd | 0.09 | 0.62 | 1.65 |
  | local_dram_access_rd | 1.07 | 1.60 | 1.65 |
  | local_dram_byte_rd | 68 | 102 | 106 |
- Reading: L3/NONE miss L2 on every demand load (pldl3 does not fill L2; for L3 the line is then found in the last-level cache, ll miss ~0). Both get ~0.6 extra L2 refills per lookup that are neither demand misses nor SW prefetches, and each is an extra DRAM read. DOUBLE's pldl1 makes the demand load hit, there are no demand L2 misses, and the extra refills vanish. Fits an L2 HW prefetcher trained by demand L2 misses; not proven until the prefetcher can be disabled.
- Distance isn't the issue: L3's line is already in the LLC at use (ll miss 0.02). pldl3 fills the SLC, not L2, so every demand load still misses L2 -> SLC latency + ~0.55 wasted DRAM lines/lookup (likely an L2 HW prefetcher trained by demand L2 misses; unproven).
- Disabling HW prefetchers not done: bit for V3AE unconfirmed (N1's PF_DIS is IMP_CPUECTLR_EL1[15]; V3 TRM needed), needs a sudo-loaded kernel module. Restore = write back the saved register value, or reboot (it resets).
- Same PMU run for prefetches that fill L2/L1, per lookup (1 thr, 10% fill): extra traffic gone.
  | | L2 | DOUBLE_L2 | DOUBLE |
  |---|---|---|---|
  | get Mops | 122 | 128 | 114 |
  | l2d_cache_refill | 1.06 | 1.05 | 1.11 |
  | l2d_cache_lmiss_rd | 0.04 | 0.03 | 0.05 |
  | local_dram_access_rd | 1.05 | 1.05 | 1.08 |
  -> use L2-filling prefetches (DOUBLE_L2 best here); L3/L3STRM/NONE pay ~1.6 DRAM lines per lookup.

## Smoke: L2 vs DOUBLE, 10% fill, ~12 s phases, 1 s samples (2026-10-08)
- 8 GiB, simd/bucketized/uniform probing, --test on (all ok), 10-14 samples per phase. B/op = median GB/s / Mops.
  | prefetch | thr | set Mops | set GB/s | set B/op | get Mops | get GB/s | get B/op |
  |---|---|---|---|---|---|---|---|
  | L2     | 1  | 46  | 6.6   | 143 | 123  | 8.2   | 67 |
  | L2     | 6  | 255 | 35.3  | 139 | 719  | 47.0  | 65 |
  | L2     | 13 | 553 | 76.5  | 138 | 1645 | 108.6 | 66 |
  | L2     | 14 | 484 | 79.1  | 164 | 1444 | 111.5 | 77 |
  | DOUBLE | 1  | 114 | 15.4  | 135 | 113  | 7.5   | 67 |
  | DOUBLE | 6  | 632 | 82.6  | 131 | 635  | 42.5  | 67 |
  | DOUBLE | 13 | 986 | 129.1 | 131 | 1242 | 92.5  | 74 |
  | DOUBLE | 14 | 922 | 129.9 | 141 | 1097 | 94.7  | 86 |
- Finds stay ~1 line/lookup for both. L2 find beats DOUBLE at 13-14 thr (1645 vs 1242 Mops, 109 vs 93 GB/s).
- Inserts: L2's insert path is pstl1keep only (PREFETCHW) -> 2-2.5x slower than DOUBLE's pldl2keep+pstl1keep, same ~135 B/op (1 read + 1 writeback).
- 14 thr is worse than 13 for everything, and B/op rises (Mops counts the slow tail; GB/s median doesn't).

## Thread sweep: hashed vs --sequential, DOUBLE vs L1 (2026-10-08)
- Added --sequential: key i+1 -> cache line i (one key per line) from where the first hashed key lands; needs fill <= 25. Both modes pass --test.
- Sweep: 8 GiB, 10% fill, 1-14 thr, 3 reps, 1 s perf samples, ~12 s phases (pass counts sized from the previous thread count). 0 failed runs. -> scripts/threads/thor-large.{json,png,log} (old 70%-fill results deleted).
  | @13 thr | uniform set | uniform get | seq set | seq get |
  |---|---|---|---|---|
  | DOUBLE | 996 Mops 129 GB/s | 1343 Mops 91 GB/s | 1354 Mops 175 GB/s | 2850 Mops 185 GB/s |
  | L1     | 544 Mops 75 GB/s  | 634 Mops 47 GB/s  | 1836 Mops 236 GB/s | 2312 Mops 151 GB/s |
- Sequential lookups scale ~linearly to 13 thr (~65 B/op, 1 line); DOUBLE reaches 195 GB/s at 14 thr. Sequential inserts flatten at ~236 GB/s (L1, ~128 B/op = read + writeback) from ~10 thr: looks like the DRAM ceiling.
- Hashed: DOUBLE insert flattens at ~130 GB/s from ~10 thr; hashed lookups top out at ~96 GB/s, half of what sequential lookups reach, at ~1 line/op in both. So hashed finds are latency/MLP bound, not bandwidth bound.
- 14 thr < 13 thr in Mops for every curve while GB/s holds.

## Why 14 threads is slower than 13 (2026-10-08)
- Idle system is quiet (<2.5% busy per core). Mops = ops / slowest thread, so one delayed worker slows the whole phase (barrier).
- Per-thread poll, DOUBLE uniform 10%, no perf: at 14 thr the workers on cpu1 / cpu13 had 14788 / 6112 involuntary context switches (others 24-1029); cpu1's worker finished 3.2 s after the first with the same CPU time -> it sat preempted. At 13 thr the max was 327 and the 14th core is free for everything else.
- Who runs during a 14-thread find phase (6 s window): kworker/u28:0-devfreq_wq ~1.5 s (~25% of a core, unbound, moves between cpus), claude ~0.16 s (~3%), plus small stuff. devfreq_wq serves the bwmgr (memory clock) and GPU governors; it is busy because of our memory traffic. Claude is a minor contributor.
- Memory clock (devfreq bwmgr, 665/2750/3200/4266 MHz) stays at 4266 through both phases once loaded; it ramps up over ~1 s at the start of a phase from idle and drops when the run ends. Not the 14-thread cause.
- Also seen at 13 thr: 1-2 workers finish ~2 s late with ~20% more CPU time (slower, not preempted). Cause unknown.
- Options: report up to 13 threads (one core left for the OS), or `sudo jetson_clocks --store; sudo jetson_clocks` (pins clocks, should quiet the governors) and rerun 14; restore with `sudo jetson_clocks --restore`.

## 12 vs 13 vs 14 with CPUs + EMC pinned (pin_ghz.sh on: EMC min=max=4266 MHz) (2026-10-08)
- pin_ghz.sh now also pins the EMC (bwmgr min_freq = max_freq, like jetson_clocks) and restores it on `off`.
- DOUBLE, uniform, 10% fill, 8 GiB, no perf, 1 run each (insert phase is a single pass, so set Mops is noise):
  | thr | get Mops | most preempted worker (involuntary switches, finished late by) |
  |---|---|---|
  | 12 | 1129 | 216 |
  | 13 | 1251 | 369 |
  | 14 | 1278 | cpu0 15784 (+2.2 s, ~2 s off-cpu), cpu13 7294 |
  Unpinned EMC before: 13 -> 1195, 14 -> 1194. So 14 is no longer below 13, but still gains little.
- devfreq_wq kworker still takes ~1.5 s per 6 s (~25% of a core) with the EMC pinned: pinning min_freq does not stop the governor work. claude ~0.23 s (~4%).
- devfreq_wq = workqueue of the devfreq framework (clock governors for non-CPU devices: bwmgr/EMC, gpu-gpc-0 + gpu-nvd-0 (nvhost_podgov, 10 ms polling), vic). kworker/u28 = unbound kernel worker, any cpu.
  It uses ~27% of a core even when idle (2.72 s per 10 s idle, 2.57 s under load) -> constant background, not caused by us; tegra-hsp (BPMP mailbox) IRQ ~1.5k/s on cpu0 either way. Invisible per-core at idle (~2% each), but at 14 thr it must preempt a worker.
  Likely the GPU governors' 10 ms polling (unverified, needs root). Possible fix (root): raise gpu-*/polling_interval or a fixed governor, then re-measure.

### Plain explanation: devfreq_wq and the 14th thread
- What it is: besides the CPUs, the GPU, memory controller (EMC) and video engine have adjustable clocks. Linux runs a "governor" per device that, every few ms (10 ms for the GPU), checks how busy the device is and raises/lowers its clock. On Thor those checks and changes go through the BPMP (a separate power-management processor) via a mailbox (the tegra-hsp IRQ).
- Each check is a small kernel job queued on the devfreq_wq workqueue; a kernel helper thread (kworker/u28, "u" = unbound, may run on any cpu) runs it. Together it is ~25% of one core, always, even idle with the GPU unused. Why it is that expensive (e.g. waiting on BPMP replies) is a guess; needs root to see.
- Nothing reserves a core for it. When it wakes, the scheduler puts it on an idle core.
  - 13 threads: one core is idle, it runs there, workers are untouched.
  - 14 threads: no idle core, so the scheduler time-slices: it pauses a worker, runs the kworker, resumes the worker. Workers are pinned, so a paused worker just waits.
- Over a ~10 s phase it needs ~2.5 s of CPU, taken mostly from 1-2 cores (cpu0/cpu13 in the run above; cpu0's worker lost ~2 s). A phase ends only when its slowest worker finishes, so that one late worker holds back all 14 -> 14 threads gains ~nothing over 13.

### Conclusion: 14-thread drop
- Confirmed: the devfreq_wq kworker takes ~25% of a core all the time (idle or loaded). At 14 thr it has to preempt a worker (~2 s lost per phase), and the barrier makes every thread wait for that one. At 13 thr it runs on the free core and the problem goes away.
- Still a guess: which governor inside it is expensive (likely the GPU governors' 10 ms polling) and why it costs that much. Check (root, read-only): trace workqueue:workqueue_execute_start/end for 10 s and sum time per function. Fix to try (root): gpu-gpc-0 / gpu-nvd-0 polling_interval 10 -> 1000 ms (undo: back to 10).
- Separate, unexplained: even at 13 thr, 1-2 workers sometimes finish ~2 s late without being preempted (same work, ~20% more CPU time). Not the kworker.
- For now: treat 13 threads as the top of the scaling curve.

## Thread sweep rerun with EMC pinned at 4266 MHz (2026-10-08)
- Same sweep as above (8 GiB, 10% fill, DOUBLE + L1, uniform + sequential, ~12 s phases, 1 s samples) but 1 run per point (default --reps 1 now). 25 min (22:52-23:17), 0 failures. Plot: all 4 panels share the Mops and GB/s scales. Replaces the previous thor-large.{json,log,png}.
  | @13 thr | uniform set | uniform get | seq set | seq get |
  |---|---|---|---|---|
  | DOUBLE | 994 Mops 129 GB/s | 1186 Mops 89 GB/s | 1360 Mops 175 GB/s | 2872 Mops 185 GB/s |
  | L1     | 544 Mops 75 GB/s  | 748 Mops 55 GB/s  | 1825 Mops 236 GB/s | 2292 Mops 151 GB/s |
- vs unpinned EMC: sequential and inserts unchanged (within ~1%); L1 uniform lookups up ~15-20% (1 thr 51 -> 61, 13 thr 634 -> 748); DOUBLE 1-thr lookup 109 -> 117. DOUBLE uniform lookups at 9-14 thr are jagged (1003, 973, 1014, 1159, 1186, 1109) and 13 thr came out lower than before (1343): that curve is noisy run to run (it also had the widest band with 3 reps), so 1 rep is not enough for it.
- 14 thr still below 13 everywhere (devfreq kworker, see above).
