# Prefetch fill sweep -- progress

## Prefetch fill sweep rewrite: every scheme + "X then L1" doubles (2026-10-09)
- Build: PREFETCH= now just picks PRFM ops (Makefile -> FIND_PRFM / FIND_PRFM_NEXT / INSERT_PRFM / INSERT_PRFM_NEXT); cas_ht.hpp has no per-scheme #ifdefs any more.
  Schemes: NONE, L1 L2 L3 L1STRM L2STRM L3STRM (find PLD X, insert PSTL1KEEP), X_L1 for X in L2 L3 L1STRM L2STRM L3STRM (find PLD X + PLDL1KEEP 8 before dequeue, insert PLD X + PSTL1KEEP), and DOUBLE (DRAMHiT's: find L3+L1, insert L2+PSTL1KEEP). Renamed NTA -> L1STRM, DOUBLE_L2 -> L2_L1 (same ops). L1_L1 skipped (the 2nd prefetch would be redundant).
  STRM hints are included: on ARM they are only a replacement hint ("used once"), the line is still allocated in the coherent caches.
- Collector: 1 run per point (no --reps); each phase is sized to ~10 s from the previous fill's pass time (pilot pass at 10%), so every phase gets 7-16 1 s perf samples (median = GB/s; all samples kept). 13 threads (see the 14-thread kworker note). Aborts unless CPU and EMC clocks are pinned. 8 GiB, fills 10..90. 13 schemes x 9 fills ~50 min, 0 failures. Old thor.json/png (old flat format, 14 thr, 3 reps) deleted. -> scripts/prefetches/thor.{json,log,png}
- Plot: 2x2 (Mops / GB/s x insert / lookup), all solid, no band (1 run; the 1 s samples' spread is mostly warm-up / tail, not noise). 13 schemes don't fit the shared rocket palette, so this plot uses tab20 pairs: X dark, X + L1 light of the same hue, doubles with square markers.
- Results @10% fill (B/op = GB/s / Mops):
  | scheme | set Mops | set B/op | get Mops | get B/op | get Mops @70% |
  |---|---|---|---|---|---|
  | NONE | 281 | 167 | 389 | 101 | 335 |
  | L1 / L1STRM | 545 | 137 | 744 / 737 | 73 | 650 |
  | L2 / L2STRM | 543 / 545 | 138 | 1654 / 1644 | 66 | 1175 / 1153 |
  | L3 / L3STRM | 544 / 539 | 138 | 765 / 910 | 124 / 106 | 716 / 756 |
  | L2_L1 / L2STRM_L1 | 995 / 989 | 130 | 1674 | 65 | 1225 / 1132 |
  | L3_L1 / L3STRM_L1 | 830 / 795 | 148 / 157 | 1259 / 1178 | 71 / 75 | 904 / 941 |
  | L1STRM_L1 | 570 | 134 | 790 | 69 | 695 |
  | DOUBLE | 993 | 130 | 1348 | 68 | 1011 |
- Takeaways:
  - Lookups: L2 is the level that matters. L2, L2STRM and their +L1 doubles are all ~1650 Mops at 10% (1 line/op); the extra L1 prefetch adds ~1-5%. DOUBLE (L3 first) is ~20% below them.
  - L3 alone moves ~2 lines per lookup (124 B/op vs 66 for L2) at half L2's Mops: lines prefetched "to L3" seem to be dropped before use and fetched again. Adding the L1 prefetch brings it back to ~1 line/op.
  - Inserts: every single scheme is ~545 Mops (they share the PSTL1KEEP insert path); the doubles with an L2 first prefetch (L2_L1, L2STRM_L1, DOUBLE) reach ~990. L3_L1 / L3STRM_L1 are jagged (780-960).
  - KEEP vs STRM makes ~no difference at L1/L2.
  - Best overall: L2_L1 (= old DOUBLE_L2), equal-best inserts and best lookups.
- Straggler tails again: in some runs 1-2 workers finish 2-4 s after the rest, and the last 1 s samples drop to ~10 GB/s (e.g. L3_L1 insert @70%: 118 GB/s for 9 s, then 25, 13, 10, 10). Mops = ops / slowest thread, so those points dip (the jagged L3_L1 / L3STRM_L1 / DOUBLE lines and the wide bands). Mostly in schemes with an L3 first prefetch; cause unknown (same as the unexplained 13-thread late workers above).

## Rerun: 14 threads, ~60 s phases, full log (2026-10-10)
- Same as threads/: the collector is self-contained (no scripts/common.py), ~60 s phases (TARGET_SECONDS 10 -> 60), 1 run per point, 14 threads (13 dropped). `thor.log` has each command, dramhit's output and perf's raw 1 s samples (GB/s = bytes / counted_ns) under `## <scheme> fill N`. 117 runs, ~4 h, 0 failures, 0 perf warnings. CPU 2.601 GHz, EMC 4266 MHz. Fill-10 inserts run ~40 s (the pilot's one pass is a cold insert, slower than the update passes).
- Results @10% fill (B/op = GB/s / Mops):
  | scheme | set Mops | set B/op | get Mops | get B/op | get Mops @70% |
  |---|---|---|---|---|---|
  | NONE | 275 | 178 | 376 | 110 | 334 |
  | L1 / L1STRM | 542 / 535 | 144 / 146 | 753 / 754 | 77 / 76 | 651 / 680 |
  | L2 / L2STRM | 547 / 555 | 143 / 141 | 1601 / 1591 | 70 | 1183 / 1169 |
  | L3 / L3STRM | 527 / 560 | 148 / 140 | 941 / 931 | 110 | 742 / 750 |
  | L2_L1 / L2STRM_L1 | 950 / 947 | 137 | 1610 / 1673 | 70 / 67 | 1203 / 1124 |
  | L3_L1 / L3STRM_L1 | 960 / 879 | 133 / 145 | 1290 / 1188 | 74 / 77 | 1045 / 1036 |
  | L1STRM_L1 | 584 | 136 | 795 | 72 | 721 |
  | DOUBLE | 988 | 132 | 1308 | 74 | 1113 |
- Takeaways unchanged: L2 (+/- L1) is best for lookups (~1600 Mops @10%); inserts ~545 single, ~950-990 with an L2/L3 first prefetch; KEEP vs STRM ~no difference. L3 alone now ~935 lookups (was 765-910 at 13 thr / 10 s) but still ~2 lines/op (110 B).
- B/op is GB/s (median, all threads running) / Mops (includes the tail), so it reads ~5-10% high wherever the tail is long.
- **Straggler tails** (scratchpad tails.py: trailing samples < 90% of the phase median; lost = sum(1 - GB/s/median) / phase s): median **7-9% of each phase lost**, >5% in 199 of 234 phases. Worse at high fill (median 7% @10-70%, 11% @80, 15% @90) and for L3-first doubles (L3_L1 median 13%, L3STRM_L1 11%; others 6-10%). Worst: L3_L1 @80 insert, 35%: ~119 GB/s for 45 s, then ~38 GB/s (~4 threads) for 10 s, then ~10 GB/s (1 thread) for ~22 s.
- So the jagged Mops lines (L3_L1 / L3STRM_L1 / DOUBLE inserts especially) come from the tails, not from throughput: the GB/s panels, which are medians over the all-threads-running part, are smooth. A 22 s single-thread tail is far more than the devfreq kworker's ~25% of a core can explain; some workers really do much more slowly. Cause still unknown (same open question as threads/).

## Singles-only plot (2026-10-10)
- `plot_singles.py`: same 2x2 as plot_data.py but only L1, L2, L3, NONE plus L2_L1 (L2 + L1, kept as the reference used elsewhere). The other doubles (X + L1, DRAMHiT's DOUBLE) are dropped since they're hash-table tricks, and the STRM variants since they're within noise of their KEEP counterparts. Note: in the insert panels all singles run the same insert code (INSERT_PRFM=pstl1keep), so their insert GB/s lines overlap exactly (within 0.1 GB/s). Same tab20 colours as plot_data.py. -> scripts/prefetches/thor_singles.png
