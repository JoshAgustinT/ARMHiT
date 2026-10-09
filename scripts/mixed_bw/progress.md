# mixed_bw progress

## 2026-10-09 — CPU + GPU streaming at the same time
- Only Python: `collect_data.py` builds `../cpu_bw/mem` and `../gpu_bw/mem` and, per pattern (read / write / copy), runs both at once on the same pattern. CPU: 8 GiB buffers on 2 MiB pages, 13 threads. GPU: 1 GiB buffers. 8 s window each, whole run ~45 s. `plot_data.py` -> `thor.png`.
- **Same window**: both binaries take `MEM_START_AT` (unix time) from the env, set up first (alloc, first touch), then sleep until that moment, so they measure over the same 8 s. Setup gets 4 s (the CPU's 16 GiB first touch takes ~1-2 s).
- **perf**: the UCF counters are system-wide, so a single `perf stat -a ... -- sleep` session sees the combined DRAM traffic of both. Only samples wholly inside the shared window are kept (minus its first second). Each binary's own timer gives the split. Check: cpu + gpu matches perf to within 0.6 GB/s on every pattern, so the windows line up.
- **13 vs 14 CPU threads**: the GPU process needs a CPU thread to launch kernels, so the default leaves one core free. Tried 14 too: same within ~2 GB/s (read cpu 105.9 / gpu 133.1 vs 104.3 / 135.2), so it doesn't matter here.
- Changes to the other two dirs for this: both `mem` binaries got `wait_for_start()` (MEM_START_AT). `gpu_bw/mem.cu` now stops after a fixed time (launch batches of 8 passes until `seconds` pass) instead of a pass count guessed from one pilot pass, so it can't drift past the window when slowed down. Its solo numbers are unchanged (252.4 / 252.5 / 239.2 GB/s in a 2 s check).
- Results (EMC 4266, CPU 2.601 GHz, GPU 1386 MHz; peak 273.0 GB/s):
  | pattern | CPU GB/s | GPU GB/s | sum | perf GB/s | % of 273 | best solo (cpu_bw / gpu_bw) |
  |---|---|---|---|---|---|---|
  | read  | 104.3 | 135.2 | 239.4 | 239.7 | 88% | GPU 253.8 |
  | write | 212.3 | 40.7  | 252.9 | 252.4 | 92% | CPU 258.5 |
  | copy  | 137.8 | 87.4  | 225.3 | 224.8 | 82% | GPU 239.3 |
- Observations:
  - Together they never beat the best one alone: DRAM is the bottleneck, and mixing costs 2-6% of total (read 254 -> 240, copy 239 -> 225). Probably more independent streams -> more bank/row conflicts. Untested.
  - The split is far from even and depends on the pattern. Writes: the CPU takes 84% (212 vs 41). Reads: the GPU gets more (135 vs 104). The memory controller doesn't share fairly by requester.

## 2026-10-09 — simplified, rerun with all 14 CPU threads (current state)
- Same structure as cpu_bw / gpu_bw: its own perf call, clock check and peak; no `scripts/common.py`. Builds and runs `../cpu_bw/mem` and `../gpu_bw/mem` with the same `MEM_START_AT`. One system-wide `perf stat -a -D` counts from 0.5 s after the start to 0.5 s before the end of the 8 s window.
- CPU threads now default to all 14. The 13-vs-14 check earlier showed no difference.
  | pattern | CPU GB/s | GPU GB/s | sum | perf GB/s | % of 273 |
  |---|---|---|---|---|---|
  | read  | 105.8 | 133.0 | 238.9 | 239.2 | 88% |
  | write | 212.4 | 39.7  | 252.1 | 252.0 | 92% |
  | copy  | 137.9 | 87.3  | 225.2 | 224.8 | 82% |
- cpu + gpu still matches perf to within 0.4 GB/s. Results are the same as with 13 threads.

## Where the limit is (discussion, 2026-10-09)
- Run-to-run noise is ~1% (≤ 2.5 GB/s between reruns).
- Practical ceiling per pattern: read ~93% (GPU alone; CPU alone 86%), write ~95% (CPU alone, 259 GB/s), copy ~87-88% (either alone).
- Two independent requesters together never beat the best single one; they land 1-6% below it. So the limit is on the DRAM side, not the CPU or GPU. Not strictly proven: the gpu_bw/progress.md tests (lower GPU clock, more requests in flight) are still untested.
- The read-only drop when mixed (254 -> 239 GB/s) is **not** read/write bus turnaround: there are no writes. Likely two independent sets of read streams hitting the banks at once -> more row switches / bank conflicts, and the controller can't merge them as neatly. Untested.

## Next idea: TEE (OP-TEE) cost on bandwidth (not started)
### What's on the board (checked read-only, 2026-10-09)
- OP-TEE (ARM TrustZone) is installed and running: `/dev/tee0`, `/dev/teepriv0`, `nv-tee-supplicant.service` active (OP-TEE Client Supplicant), `/usr/sbin/tee-supplicant`.
- ~9 trusted applications (TAs) in `/lib/optee_armtz/*.ta`, plus `/usr/sbin/nvluks-srv-app` (NVIDIA LUKS disk-key TA client).
- `/usr/bin/xtest` (OP-TEE's test suite) is installed; may have benchmark cases worth checking first.
- `/dev/tee0` is `crw------- root`, so calls need root (or a permissions change).

### How a call works / expectations
- Call path: user space -> ioctl on `/dev/tee0` (libteec) -> kernel -> `SMC` -> secure monitor (EL3) -> OP-TEE (S-EL1) -> TA (S-EL0) and back. A world switch on **one core**; other cores keep running.
- TrustZone by itself doesn't encrypt DRAM (it is access control: a memory firewall), so there should be no per-byte bandwidth tax like encrypted-memory TEEs have. The cost is mostly the calling core's time in the secure world, plus cache/TLB pollution around the switch.
- Prediction (untested): aggregate GB/s may barely move. DRAM is already saturated (write 95% with 14 cores), so losing part of one core's time doesn't matter much. The calling thread's own throughput drops by roughly its share of time in the TEE. Reads may be more sensitive (CPU reads look limited by misses in flight, not DRAM).
- Where it could hurt a lot: frequent calls with little work between them (per-operation entry); a TEE operation that itself streams secure memory; all cores calling at once and serializing inside OP-TEE.

### Plan
1. **Baseline call cost**: a do-nothing TEE call in a loop -> round-trip latency. This sets the scale: at ~10 µs/call, it takes ~100k calls/s machine-wide before it matters. Check xtest's benchmarks first.
2. **Periodic phase**: every X passes (or X µs) a thread does one TEE call. Sweep X from rare to very frequent; plot GB/s vs the share of time spent in the TEE.
3. **Who calls**: one thread vs all 14. Separates "one core lost" from "OP-TEE serializes everything".
4. **Batched vs per-operation**: one small call at the start of each chunk of work vs one per item (the classic enclave-entry tradeoff).

### Hurdle to check early
- A custom do-nothing TA needs the OP-TEE TA dev kit, and on Jetson TAs typically must be signed with the platform's TA key. That decides whether we write our own TA or reuse an existing one (an installed TA or an xtest case).
