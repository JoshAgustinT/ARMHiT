# threads — progress

Earlier history of this sweep (first runs, the 14-thread investigation, EMC pinning) is in the root `progress.md`.

## 2026-10-09 — rerun with ~60 s phases, self-contained collector, full log
- `collect_data.py` no longer uses `scripts/common.py` (deleted; `prefetches` got its own copy too). It now does its own build, run, perf call and parsing, and aborts unless CPU + EMC clocks are pinned.
- `thor-large.log` is everything the sweep did: each command, dramhit's full output and perf's raw 1 s samples (`time,bytes,unit,event,counted_ns,...`). Any GB/s in the json can be checked by hand: bytes / counted_ns, median over the phase.
- `TARGET_SECONDS` 12 -> 60, 1 run per point, no repeat band (removed from `plot_data.py`). 8 GiB, 10% fill, DOUBLE + L1, uniform + sequential. CPU 2.601 GHz, EMC 4266 MHz. ~2 h, 0 failures.
- Phases are 57-68 s, except the 1-thread inserts (19-30 s): the pilot's one insert pass is a cold insert, ~2x slower than the update passes after it, so it overestimates the pass time. Later points use measured pass times.
- Results @13 / @14 threads (Mops, median GB/s):
  | | uniform set | uniform get | seq set | seq get |
  |---|---|---|---|---|
  | DOUBLE 13 | 993 / 129 | 1167 / 88 | 1354 / 174 | 2849 / 183 |
  | DOUBLE 14 | 947 / 130 | 1230 / 94 | 1316 / 173 | 2819 / 195 |
  | L1 13 | 543 / 75 | 741 / 54 | 1818 / 234 | 2332 / 152 |
  | L1 14 | 534 / 78 | 728 / 58 | 1752 / 233 | 2270 / 160 |
  1-13 threads match the 12 s sweep to within a few %. At 14 the 60 s phases do better: DOUBLE uniform get 1109 -> 1230, seq get 2523 -> 2819.

## Straggler tails, read from the perf samples
- Mops = ops / time until the *last* thread finishes (barrier at each phase end). When one thread finishes late, the others idle and the 1 s GB/s samples drop at the end of the phase: a "tail". The median GB/s ignores the tail, so the json's GB/s is the all-threads-running rate while Mops includes the tail.
- Per run, tail = trailing samples below 90% of the phase median; lost = sum of (1 - GB/s / median) over them / phase seconds:
  - **1-13 threads: tails 1-2 s, <2% lost**, except DOUBLE uniform finds (below).
  - **14 threads: tails 3-14 s, 3-16% lost in every curve.** That's the devfreq kworker (~25% of a core) pausing workers when no core is free.
  - **DOUBLE uniform finds from 5 threads up: tails 7-12 s, 8-14% lost**, with free cores. Example, 13 thr: ~88 GB/s for ~50 s, then ~10 s at 14 GB/s (about two threads left). L1 uniform and both sequential find curves don't do this. Same as the earlier "1-2 workers finish late without being preempted" note; cause unknown.
- So **14 vs 13 is about even, and longer phases don't fix it**. The 14th core adds ~7% while all threads run (GB/s at 14 vs 13: finds +6-7%, L1 uniform insert +5%; DOUBLE uniform insert +1% and the sequential inserts -1%, already at their ceiling); the tail takes ~3-16% back. A 60 s smoke run had finds 14 > 13 by 5%; this sweep has 3 of 4 lookup curves and all inserts slightly below. Correction to the earlier guess that longer phases would spread the kworker evenly and remove the tail: it doesn't.
- If a number without tails is wanted: per-thread ops / own time summed (as `cpu_bw/mem.cpp`), or reserve a core for the OS (13 threads).

## Open
- Why some DOUBLE uniform find workers run slower (not preempted). Next check: per-thread finish times + which cpus, to see whether it's always the same cores.

## 2026-10-10 — rerun with NONE + L2 (instead of DOUBLE + L1)
- `PREFETCHES = ["NONE", "L2"]`; plot_data.py's linear reference now scales L2 (`LINEAR_FOR`). Otherwise same setup: 8 GiB, 10% fill, ~60 s phases, 1 run per point, CPU 2.601 GHz, EMC 4266 MHz. 56 runs, 14:30 -> 16:26 (~1 h 56 m), 0 failures.
- Old DOUBLE + L1 results kept as `thor-large-double-l1.{json,log,png}` and `thor-large-double-l1_finds.png`; new ones are `thor-large.*`.
- Results @13 / @14 threads (Mops / median GB/s):
  | | uniform set | uniform get | seq set | seq get |
  |---|---|---|---|---|
  | NONE 13 | 279 / 46 | 387 / 38 | 1747 / 227 | 3044 / 198 |
  | NONE 14 | 276 / 49 | 395 / 41 | 1716 / 228 | 3023 / 208 |
  | L2 13 | 544 / 75 | 1630 / 106 | 1820 / 235 | 2723 / 176 |
  | L2 14 | 530 / 78 | 1606 / 112 | 1741 / 233 | 2639 / 185 |
- Uniform: L2 lookups scale ~linearly to 13 threads (1630 Mops, ~4x NONE); L2 inserts ~2x NONE (same PSTL1KEEP insert path as L1, so ≈ old L1 insert curve). Sequential: NONE ≥ L2 for lookups (3044 vs 2723 @13), likely because the hardware prefetcher already covers sequential keys, so the software prefetches are only overhead (not verified); inserts saturate at ~230 GB/s for both from ~9 threads.
- 14 vs 13 again about even or slightly worse (tails, see above).
- One outlier: NONE uniform insert @7 threads = 116 Mops (expected ~160). Perf samples: 60 s at ~16 GB/s read, then ~25 s at ~2 GB/s (one straggler thread alone). Mops includes the tail, GB/s (median) doesn't, so the GB/s curve is smooth there. Same unexplained straggler pattern.
