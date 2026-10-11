# gpu_bw progress

## GPU in brief (Thor)
- 20 SMs (the GPU's cores). Each SM holds up to 48 warps (1536 threads) and has 4 schedulers: each cycle it issues from up to 4 ready warps; the rest wait their turn or on memory.
- Warp = 32 threads running the same instruction in lockstep. One warp load of 16 B per thread = 512 contiguous bytes.
- Full GPU = 20 x 48 = 960 warps = 30,720 threads.
- Launch: grid -> blocks (each runs on one SM) -> split into warps of 32. A block is at most 1024 threads; an SM holds at most 24 blocks. Blocks go to the emptiest SM first (checked with `%smid`).
- Memory path: SM (own L1 / shared memory) -> GPU L2 (shared by all SMs) -> SLC (shared with the CPU) -> DRAM.
- Toolchain: CUDA 13.2 (`/usr/local/cuda/bin/nvcc`), sm_110, Makefile builds with `-O3 -arch=sm_110`. `hello.cu` prints each thread's id / SM / block / warp / lane.

## Current state
- `mem.cu`: 1 GiB buffers, grid-stride kernels with 16 B `uint4` accesses (each warp step = 512 contiguous bytes). Patterns: `read` (XOR-reduce, kept alive by a magic-value check), `write`, `copy` (counted as 2x bytes). Waits for `MEM_START_AT` (if set), one warm-up pass, then launches batches of passes until `seconds` of wall time pass, timed with the wall clock.
  `./mem read|write|copy [MiB] [seconds] [blocks threads_per_block]`
  - Default (no grid): every thread slot (`<<<120, 256>>>`), and **`write` = `cudaMemsetAsync`** (fastest GPU write, ~255 vs ~251 GB/s for the store kernel; nsys shows no kernel for it, so likely a dedicated path such as the copy engine, unverified).
  - With a grid: that grid, `write` = `write_kernel` (uint4 stores, since cudaMemset has no thread count), and 1-pass batches.
- `collect_data.py`: default grid, 30 s per pattern -> `thor.json`; `plot_data.py` -> `thor.png` (per pattern: rd + wr bar with % of peak, and the same total split into rd / wr).
- `collect_sms.py`: k = 1..20 SMs x 24 or 32 warps / SM (k blocks of 768 or 1024 threads = one full block per SM), 3 patterns, 256 MiB, 6 s each (~18 min) -> `thor_sms.json`; `plot_sms.py` -> `thor_sms.png` (GB/s vs SMs; solid 32, dashed 24 warps / SM). 48 warps / SM on k SMs would need a kernel change (2 blocks per SM spread to 2k SMs).
- Measurement: system-wide `perf stat -a -D` on `nvidia_ucf_pmu_0/local_dram_byte_rd/` and `_wr/` (the UCF counts GPU traffic too; agrees with mem's timer to < 0.5 GB/s), from 0.5 s after the start to 0.5 s before the end. Aborts unless pin_ghz.sh has pinned EMC and GPU clocks (gpu-gpc-0 1386 MHz, gpu-nvd-0 1557 MHz = the 120 W power mode caps). Peak 273.0 GB/s.

## Latest results (GPU 1386 MHz, EMC 4266 MHz)
- Full GPU, 30 s (perf GB/s):
  | pattern | rd + wr | rd / wr | % of 273 |
  |---|---|---|---|
  | read  | 253.7 | 253.6 / 0.1 | 93% |
  | write | 255.3 | 0.1 / 255.1 | 94% (cudaMemset) |
  | copy  | 238.9 | 119.5 / 119.5 | 88% |
- SM sweep (perf GB/s):
  | SMs | 1 | 2 | 4 | 6 | 8 | 10 | 12 | 16 | 20 |
  |---|---|---|---|---|---|---|---|---|---|
  | read 24 w/SM | 21 | 42 | 81 | 118 | 158 | 189 | 218 | 243 | 243 |
  | read 32 w/SM | 28 | 55 | 108 | 158 | 198 | 230 | 243 | 248 | 247 |
  | write 24 / 32 | 44 / 44 | 88 / 87 | 173 / 174 | 252 / 252 | 230 / 232 | 242 / 249 | 239 / 248 | 251 / 239 | 234 / 250 |
  | copy 32 w/SM | 51 | 98 | 178 | 221 | 237 | 240 | 237 | 241 | 239 |

## Findings
- **Writes are capped per SM at ~44 GB/s** (= 32 B per GPU cycle), same at 24 or 32 warps; they scale linearly with SMs and saturate DRAM at **6 SMs** (7-9 SMs dip slightly to ~230). A full warp writes ~18 GB/s, so ~16 full warps anywhere saturate.
- **Reads are latency-bound per warp**: one 512 B load in flight per warp, ~0.87 GB/s per warp (~590 ns round trip), linear in warps up to ~256 warps; saturate at ~12 SMs x 32 warps (~384 warps). Copy in between (~10 SMs).
- **Coalescing is the multiplier**: with 1 thread per warp each access is 16 B; doubling threads per warp doubles read bandwidth (same latency, bigger access). Uncoalesced writes cap at ~70 GB/s for the whole GPU (one 16 B store request per ~6 cycles per SM); only lane 0 storing gives 77-80.
- **Nothing happens at 4 warps / SM**: memory-bound warps barely use the schedulers; bandwidth stays linear in warps.
- **One GPU thread is very slow** (read 0.05, write 0.57 GB/s; same at 64 MiB-1 GiB buffers): in-order, one 16 B access at a time, no other warps to hide latency. A CPU core does 20 / 48.
- Reads on 1-20 SMs with 1 thread each step up in pairs of SMs (1 -> 2 SMs barely helps); writes don't. Maybe SM pairs share part of the load path; unverified, values tiny.
- **CPU writes (259) beat GPU writes (~251 kernel / 255 memset)**. Ruled out: request size (64 B for both), thread count, launch gaps, store width / `__stcs`, address pattern (1-64 streams), allocation (`cudaMalloc` = managed; GPU writing CPU THP memory is worse, 212, coherence). Differences seen: CPU writes partly go through the SLC, GPU writes bypass it; and CPU + GPU writing together (mixed_bw) total less than the CPU alone.
- ~93% rather than 100% is the usual streaming ceiling: DRAM refresh, row open/close, read/write turnaround (copy 88% vs read/write 93%), command overhead. Background traffic ~0.1-0.2 GB/s.
- At 1-3 warps perf sees ~0.4-0.6 GB/s more than the timer (constant, invisible above ~12 warps); not host spin-waiting (`cudaDeviceScheduleBlockingSync` same). Unexplained.

## Open
- Lower the GPU clock (needs root): if bandwidth stays ~253, the limit is past the GPU.
- 48 warps / SM line in the SM sweep (needs the kernel change above).
- ECC on? Cold vs warm (refresh rate) effect on the ~93%.

## History
- 2026-10-08: hello.cu, toolchain, thread enumeration (120 x 256 = 30720 threads on 20 SMs).
- 2026-10-09: mem.cu + collector; pin_ghz.sh pins GPU clocks too; time-based loop + MEM_START_AT (for mixed_bw); self-contained directory.
- 2026-10-10: write investigation -> `write` uses cudaMemset; 30 s runs; rd/wr plot; optional grid in mem.cu; warp sweep, occupancy sweep (1 thread .. full GPU) and SM sweep. Warp and occupancy sweeps removed afterwards (their findings are above); SM sweep kept.
