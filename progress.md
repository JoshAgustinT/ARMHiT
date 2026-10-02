# ARMHiT progress
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

## Idea: TEE under high throughput (AMD SEV-SNP)
- Motivation: TEEs are usually evaluated on toy apps; we want to see what happens to a large, high-throughput, DRAM-latency-bound workload (DRAMHiT) inside one.
- Thor's OP-TEE (/dev/tee0) is a poor fit: TAs are typically single-threaded with a small secure carve-out, and plain TrustZone may not encrypt DRAM (unverified). ARM CCA/Realms is likely not enabled in Jetson firmware.
- Plan: use the AMD r6615 box (EPYC, if it supports SEV-SNP). Run DRAMHiT in a normal VM vs an SEV-SNP VM at the same thread count; same prefetch sweep (collect_data_amd.py), compare Mops vs fill.
- Questions: cost of memory encryption/integrity on miss latency; does it shift which prefetch flavour wins; does software prefetching hide the extra latency; any change in effective MLP.

## Rewrite in C++ (2026-10-02)
- g++ 13 now available. Plan: src/*.c -> src/*.cpp/.hpp, built with g++ -std=c++20; drop CMakeLists.txt.
- cas_ht.hpp = DRAMHiT's cas_kht.hpp trimmed to: types, ctor/dtor, insert_batch, find_batch (+ the queue/probe helpers they call, flushes, get_fill). AVX-512 bucket compare -> NEON with the same mask layout so the code reads the same.
- uniform_test.cpp rewritten for readability; Makefile trimmed to the knobs collect_data.py uses.
- DONE: C++ rewrite builds with g++ (make only, CMakeLists.txt removed). All 24 variant combos (2025/2025_INLINE x simd/branched x UNIFORM on/off x DOUBLE/L2/NONE) pass --test. 8GiB @30%: L2 find ~1340-1390 Mops (was ~1400), DOUBLE ~1220; collect_data.py + plot_data.py smoke-tested.
- 12:16 removed DRAMHiT_2025_INLINED fast paths: insert_batch/find_batch are plain pop+add loops; DRAMHiT_VARIANT knob dropped (Makefile, script, README). 12 combos pass --test.
- 13:02 collect_data.py back to DRAMHiT's interface: hardcoded build_cfgs/run_cfgs, no flags; optional out path (default results/thor.json).
