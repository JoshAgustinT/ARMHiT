# gpu_bw progress

## 2026-10-08 — CUDA hello world
- Added `hello.cu` (1 block, 4 threads, device `printf`) and a `Makefile` (`make`, `make run`, `make clean`).
- Build: `nvcc hello.cu -o hello` (nvcc at `/usr/local/cuda/bin/nvcc`, CUDA 13.2, driver 595.78).
- GPU: NVIDIA Thor, compute capability 11.0 (sm_110). The default nvcc target runs fine; no `-arch` flag needed.
- Ran `./hello`: all 4 threads printed. The toolchain works.

## Next
- Write the actual GPU memory bandwidth test.

## 2026-10-08 — hello as thread enumeration
- `hello.cu` now launches enough threads to fill every SM (`SMs * maxThreadsPerSM / 256` blocks of 256 threads). Each thread prints its global thread id, SM (`%smid`), block, warp and lane.
- Thor: 20 SMs x 1536 threads/SM -> `<<<120, 256>>>` = 30720 threads. Verified: 30720 distinct thread ids, spread over all 20 SMs.
- The printf FIFO is raised to 64MB because the default 1MB buffer silently drops lines.
- Print order is arbitrary; pipe through `sort -n -k2` for an ordered list.

## 2026-10-09 — mem.cu: GPU DRAM bandwidth
- `mem.cu`: streams 1 GiB buffers with every thread slot busy (`<<<120, 256>>>`, grid-stride loop, 16 B `uint4` loads/stores, so each warp touches 512 contiguous bytes per step). Patterns: `read` (XOR-reduce; result only stored if it equals a magic value so loads are not optimised out), `write` (fill), `copy` (src -> dst, counted as 2x bytes). Each repeats for ~N s; timed with CUDA events. `./mem [read|write|copy|all] [MiB] [seconds]`, output is `name : value` like dramhit.
- Makefile now builds with `-O3 -arch=sm_110`. Every CUDA call goes through `CHECK()` so a failure exits with a message.
- `collect_data.py`: aborts unless pin_ghz.sh has pinned the CPUs + EMC (rechecks at the end). Runs each pattern once (8 s) under perf (UCF `local_dram_byte_rd/wr`, 1 s samples, first/last sample dropped). Polls the GPU clock every 100 ms. Peak = EMC clock x 2 x 32 B = 4266 MHz -> 273.0 GB/s. Output `thor.json`; `plot_data.py` -> `thor.png` (bar chart, timed vs perf vs peak).
- **The UCF PMU counts GPU traffic too**: perf and the kernel timer agree to within 0.5 GB/s.
- Results (EMC 4266 MHz, CPUs 2.601 GHz, GPU clock 1386 MHz = gpu-gpc-0 max):
  | pattern | timed GB/s | perf GB/s (rd / wr) | % of 273 |
  |---|---|---|---|
  | read  | 253.9 | 254.1 (254.1 / 0.1) | 93% |
  | write | 252.2 | 252.7 (0.2 / 252.5) | 92% |
  | copy  | 239.0 | 239.2 (119.7 / 119.5) | 88% |
- Writes cause no read-for-ownership traffic (perf rd ~0). Copy is lower, probably from read/write bus turnaround.
- Compared with the CPU: the best CPU number in the thread sweep was ~236 GB/s (L1 sequential insert, 13 threads), so 14 CPU cores reach roughly what the GPU does on copy. For pure reads the GPU gets ~254 GB/s.
- The GPU clock is not pinned (governor nvhost_podgov, 315-1386 MHz). It ramps to max within the warm-up pass and stays there; `read` logged 315 MHz only in its first 100 ms.

## 2026-10-09 — pin_ghz.sh pins the GPU clocks too
- `pin_ghz.sh on` now sets min_freq = max_freq for the devfreq clocks `bwmgr` (EMC), `gpu-gpc-0` and `gpu-nvd-0`, the way jetson_clocks does. Each gets a saved-state file `/tmp/pin_ghz.<emc|gpc|nvd>.state` (the EMC one keeps its old name). `off` restores them; `status` lists them.
- max_freq is the cap the power mode sets (`nvpmodel -q`: 120W): gpc 1386 MHz, nvd 1557 MHz. The hardware lists up to 1575 / 1692 MHz. We pin to the cap and don't raise it.
- `collect_data.py` now aborts unless gpu-gpc-0 is pinned too, and records `gpu_hz`.
- Verified the restore on a fake sysfs copy (scratchpad): on/off, a double on, and the old-script state (EMC state only) all restore min/max/governor exactly.

## 2026-10-09 — rerun with the GPU clock pinned (gpc 1386, nvd 1557, EMC 4266 MHz)
  | pattern | timed GB/s | perf GB/s (rd / wr) | % of 273 |
  |---|---|---|---|
  | read  | 253.4 | 253.8 (253.7 / 0.1) | 93% |
  | write | 251.1 | 251.6 (0.2 / 251.4) | 92% |
  | copy  | 239.0 | 239.3 (119.8 / 119.6) | 88% |
- Same as unpinned to within ~1 GB/s; the GPU clock now reads 1386 MHz for the whole run, including the first 100 ms. thor.json / thor.png replaced.

## Why ~93% and not 100%? (theories, none tested yet)
Streaming tests usually top out at 90-95% of the theoretical peak, so 93% is near the practical ceiling. Likely causes, biggest first:
1. **DRAM refresh.** A bank can't serve reads or writes while it refreshes. On LPDDR5X that costs a few %, more when hot (refresh gets more frequent). May be most of the gap.
2. **Row open/close.** Each bank reads from an open row. Crossing into a new row costs idle cycles to close the old row and open the next. With thousands of warps streaming, some collide on the same bank and wait.
3. **Read/write turnaround.** Switching the bus direction costs dead cycles. The clearest signal in the data: copy (half rd, half wr) gets 88%, pure read / pure write get 92-93%.
4. **Command bus and controller scheduling overhead.** Small, never zero.
5. **Probably not the cause:**
   - Measurement: perf (memory-side bytes) agrees with the kernel timer.
   - Background traffic: ~0.1 GB/s.
   - The peak itself: 4266 MHz x 2 x 32 B = 273 GB/s is correct for a 256-bit bus.

### Tests to narrow it down
- **Lower the GPU clock** (pin gpu-gpc-0 below 1386 MHz). If bandwidth stays ~253 GB/s, memory is the limit and nothing on the GPU side will help. If it drops, the GPU isn't keeping enough requests in flight.
- **Vary requests in flight**: block size 128-1024, or 2-4 independent loads per thread per loop step (unroll). No change -> another sign memory is the limit.
- **ECC**: check whether the memory has ECC enabled (Thor docs / nvpmodel). If so, it costs bandwidth.
- **Refresh / temperature**: rerun cold vs warm (thermal zones in /sys/class/thermal) to see if bandwidth drops as refresh rate rises.

## 2026-10-09 — time-based loop, MEM_START_AT
- `mem.cu` now launches batches of 8 passes until `seconds` of wall time pass, instead of a pass count from one pilot pass. Its duration is exact even when slowed by another process (needed for scripts/mixed_bw). Solo numbers unchanged (2 s check: 252.4 / 252.5 / 239.2 GB/s).
- Waits until `MEM_START_AT` (unix time, env) before measuring, if set.

## 2026-10-09 — simplified; self-contained directory (current state)
- **Each test directory is self-contained**: no shared helpers in `scripts/common.py` (tried, reverted). Its collector has its own perf call, clock check and peak calculation, so it can be rerun and read on its own. Only `mixed_bw` reaches into the other two, for their `mem` binaries.
- `mem` runs one pattern per call and prints `gbs : X`. Usage: `./mem read|write|copy [size] [seconds] ...`.
- Measuring window: the collector sets `MEM_START_AT` = now + setup time; mem sets up (alloc, first touch), sleeps until then, does one untimed warm-up pass, and times itself for `seconds`. perf runs system-wide alongside (`perf stat -a -D <ms> -- sleep <s>`), counting from 0.5 s after the start to 0.5 s before the end. One total, no 1 s interval sampling, so no whiskers on the plot.
- `plot_data.py`: one bar chart, timer vs perf vs peak; ~55 lines.
- `mem.cu`: kernels unchanged. `main` is now linear: alloc, one `pass` lambda that launches the kernel for the pattern, wait_for_start, warm-up, then batches of 8 passes until time is up, timed with the wall clock (no CUDA events, no pilot pass). The GPU clock poller is gone (pin_ghz.sh pins the clock and the collector checks it).
- Rerun (8 s, GPU 1386 / EMC 4266 MHz): read 253.0 / write 249.9 / copy 238.8 GB/s timed, perf 253.3 / 250.4 / 239.0. Same as before.

## 2026-10-10 — write via cudaMemset, 30 s runs, rd/wr plot
- **Why CPU writes (259 GB/s) beat GPU writes (~251)**: smoke tests in a scratch copy, nothing in the repo changed. Ruled out:
  - request size: bytes / `local_dram_access_wr` = 64.0 B for both;
  - thread count: 6 blocks x 256 (5% of slots) already 254, 1 warp/SM 239, 2 warps/SM 248, oversubscribed 249;
  - launch gaps: one kernel doing 64 passes 249-253;
  - store width / hints: u32 244, uint2 250, uint4 251, `__stcs` 252;
  - address pattern: 1-64 independent sequential streams all 250-252;
  - allocation: `cudaMallocManaged` 251, same as `cudaMalloc`. GPU writing CPU `mmap`+THP memory is worse (212, coherence).
  - Breaking coalescing hurts: only lane 0 storing 77-80; 4 consecutive uint4 per thread 184-205.
  - Differences seen: ~5% of CPU writes are allocated in the SLC and written back later (`slc_allocate` 630 M vs 13 M for the GPU); GPU writes bypass it. And CPU + GPU writing together (mixed_bw) total 253, below the CPU alone, so GPU writes look slightly less efficient for the DRAM, not just capped by a slower port.
  - Not tested (needs root): lower the GPU clock. If writes stay ~251 GB/s, the limit is past the GPU.
- `cudaMemset` was the best GPU write (255.6 vs ~251 GB/s). nsys shows it as a "[CUDA memset]" memop with no kernel, so it likely runs on a dedicated path (copy engine?), unverified. **`write` now uses `cudaMemsetAsync`** instead of the uint4 store kernel (removed). Note scripts/mixed_bw runs this binary, so its GPU write changes too.
- `SECONDS` 8 -> 30.
- `plot_data.py` replaced by the rd/wr chart: per pattern, an rd + wr bar (GB/s, % of peak) and the same total split into rd / wr (GB/s, share); a ~0 part is labelled at the bar's foot. The mem.cu timer bar is gone (matches perf to < 1 GB/s). Old thor.json / thor.png deleted.
- Results (30 s, GPU 1386 / EMC 4266 MHz):
  | pattern | timed GB/s | perf GB/s (rd / wr) | % of 273 |
  |---|---|---|---|
  | read  | 253.4 | 253.7 (253.6 / 0.1) | 93% |
  | write | 255.1 | 255.3 (0.1 / 255.1) | 94% |
  | copy  | 238.8 | 238.9 (119.5 / 119.5) | 88% |
  Write up from ~250 (kernel) to 255 (memset); read and copy unchanged.
