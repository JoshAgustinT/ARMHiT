# mixed_bw progress

## Current state
- `collect_data.py` builds and runs `../cpu_bw/mem` (14 threads, 8 GiB buffers on 2 MiB pages) and `../gpu_bw/mem` (1 GiB, full GPU, `write` = cudaMemset) **at the same time on the same pattern** (read / write / copy), 30 s each. Both get the same `MEM_START_AT`: they set up first, then measure over the same window. `plot_data.py` -> `thor.png`: per pattern an rd + wr bar (perf, % of peak), a CPU / GPU bar (own timers, share), and an rd / wr bar (perf, share).
- Self-contained: own perf call, clock check (CPU, EMC, GPU must be pinned) and peak (273.0 GB/s).

## How bandwidth is measured, and how CPU vs GPU is split
- **perf** (one system-wide `perf stat -a -D`, 0.5 s after the start to 0.5 s before the end): `nvidia_ucf_pmu_0/local_dram_byte_rd/` and `_wr/`, bytes at the memory-side fabric (UCF) from **every** requester. GB/s = bytes / counted ns. So the rd / wr bar is CPU + GPU combined.
- **The CPU / GPU split comes from each binary's own timer** (ops x bytes per op / wall time). Check: cpu + gpu matches perf's total to within 0.4 GB/s on every pattern, so the windows line up and nothing else moves memory. Each binary also matches perf on its own in cpu_bw / gpu_bw. The split itself is derived, not measured.
- Per-requester rd / wr isn't measured; it follows from the pattern (read = all rd, write = all wr, copy ~half each).
- **Possible measured split, not used yet**: the GPU's own `nvidia_ucf_gpu_pmu_0` (`wr_bytes_loc`, `rd_bytes_loc_rem`, ...). `wr_bytes_loc` matched DRAM writes in a solo GPU write run (758.3 vs 758.6 GB) and stayed ~0 in a CPU-only run, so GPU = these, CPU = total - GPU. GPU read counters not checked yet.

## Latest results (30 s; CPU 2.601 GHz, GPU 1386 MHz, EMC 4266 MHz)
  | pattern | CPU GB/s | GPU GB/s | perf GB/s (rd / wr) | % of 273 | best solo |
  |---|---|---|---|---|---|
  | read  | 105.2 | 134.1 | 239.7 (239.6 / 0.1) | 88% | GPU 253.7 |
  | write | 211.8 | 40.1  | 252.0 (0.2 / 251.7) | 92% | CPU 258.5 |
  | copy  | 138.2 | 86.3  | 224.7 (112.5 / 112.2) | 82% | GPU 238.9 |

## Findings
- **Together never beats the best one alone**: DRAM is the limit, and mixing costs 2-6% (read 254 -> 240 with no writes, so not bus turnaround; likely more independent streams -> more row / bank conflicts, untested).
- **The split is uneven but not random**: two extra 8 s smoke reruns gave the same split to within ~1 GB/s. Writes: CPU ~84%. Reads: GPU ~56%. Copy: CPU ~61%. Guess (untested): reads split roughly by requests kept in flight (GPU has more); writes favour the CPU in the shared part of the memory system (CPU writes use the SLC, GPU writes bypass it). A check: limit the GPU to a few SMs and see whether the write split changes.
- 13 vs 14 CPU threads made no difference (~2 GB/s); cudaMemset vs the store kernel for the GPU write made none either (GPU 39.7 -> 40.1).

## Next idea: TEE (OP-TEE) cost on bandwidth (not started)
- OP-TEE is running (`/dev/tee0` root-only, `nv-tee-supplicant`, ~9 TAs in `/lib/optee_armtz`, `xtest` installed). A call is a world switch on one core; TrustZone doesn't encrypt DRAM, so no per-byte tax is expected, only the calling core's time.
- Plan: (1) do-nothing call latency (check xtest benchmarks first); (2) one call every X passes, sweep X, GB/s vs time in the TEE; (3) one thread vs all 14 calling (serialization inside OP-TEE?); (4) batched vs per-operation calls.
- Hurdle: a custom TA needs the TA dev kit. The shipped TAs are signed with OP-TEE 4.6's public default key, so no NVIDIA key is needed (see ../TEE/progress.md).

## History
- 2026-10-09: first version (13 threads, 8 s); both mem binaries got MEM_START_AT and gpu_bw a time-based loop; made self-contained, 14 threads.
- 2026-10-10: 30 s runs with the current binaries (GPU write = cudaMemset); 3-bar plot; measurement method written up.
