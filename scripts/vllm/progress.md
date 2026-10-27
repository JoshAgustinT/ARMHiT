# vllm progress

## Plan
- Run Qwen3-8B (`Qwen/Qwen3-8B`, BF16, ~16 GB weights) under vLLM and run its built-in benchmarks (`vllm bench latency` / `throughput`). Qwen on vLLM is the usual setup in the literature, so it's easy to defend. Record the exact HF checkpoint/revision and precision.
- Refs: https://qwen.readthedocs.io/en/latest/deployment/vllm.html, https://docs.vllm.ai/en/latest/benchmarking/cli/#offline-throughput-benchmark

## Notes
- 2026-10-26: vLLM is not installed. The generic `pip install vllm` wheels likely don't target aarch64 + Thor's GPU. Use NVIDIA's NGC vLLM container (or dusty-nv `jetson-containers`) built for Thor; the image/tag that supports Thor still needs checking.
- 2026-10-26: our user can't reach the docker socket (`permission denied` on `/var/run/docker.sock`). Fix (then log out and back in):
  > sudo usermod -aG docker $USER
- 2026-10-26: unified memory: vLLM's default `--gpu-memory-utilization 0.9` is a fraction of system DRAM here (~110 of 122 GB). Set it explicitly (0.4-0.5), especially when running CPU bandwidth load (`mixed_bw`) alongside it.
- 2026-10-26: benchmark design:
  - `bench throughput` batches all prompts at once; `--num-prompts 10` mostly measures startup/ramp-up. Use 500-1000.
  - Also run `bench latency` at batch 1: decode reads all weights per token, so it's memory-bandwidth-bound and tokens/s x ~16 GB ≈ effective BW, which ties to cpu_bw/gpu_bw/mixed_bw. Large batches move toward compute-bound; sweeping batch size shows where that happens.
  - `random` dataset is fine for systems work (fixed in/out lengths, content irrelevant; Qwen3 thinking mode doesn't matter). Fix `--seed`.
- 2026-10-26: first commands to try (once vLLM runs):
  > vllm bench latency --model Qwen/Qwen3.8-27B --input-len 512 --output-len 128 --batch-size 1 --gpu-memory-utilization 0.5
  > vllm bench throughput --model Qwen/Qwen3.8-27B --dataset-name random --random-input-len 512 --random-output-len 128 --num-prompts 1000 --gpu-memory-utilization 0.5 --seed 0
- Next: find the Thor vLLM container, get Qwen3-8B loading, agree on output/plot format (mock first) before collecting.
- 2026-10-26: model switched to **Qwen3.6-27B** (`Qwen/Qwen3.6-27B`, dense, Apr 2026; the current 27B people use). Not a plain transformer: hybrid Gated DeltaNet + Gated Attention (3:1), so KV cache only grows in 1/4 of the layers. Needs a recent vLLM (the Thor container must be new enough). BF16 ≈ 54 GB weights -> `--gpu-memory-utilization` ~0.6-0.7 so KV cache fits. Batch-1 decode ceiling ≈ 273 GB/s / 54 GB ≈ 5 tok/s. Official FP8 variant `Qwen/Qwen3.6-27B-FP8` (~27 GB) if needed.
- 2026-10-26: **no container needed**: vLLM 0.32.0's stock aarch64 wheel installs natively; it pulls torch 2.13.0+cu132, whose arch list includes sm_110 (Thor = cc 11.0). Smoke test with `Qwen/Qwen3-0.6B` generates correctly ("The capital of France is" -> " Paris."). venv in `scripts/vllm/.venv` (gitignored). Install:
  > uv venv --python 3.12 ~/Desktop/josh/ARMHiT/scripts/vllm/.venv
  > VIRTUAL_ENV=~/Desktop/josh/ARMHiT/scripts/vllm/.venv uv pip install vllm==0.32.0 --torch-backend=auto
- 2026-10-26: using `--gpu-memory-utilization 0.5` for now (~61 GB; default 0.9 is ~110 GB of shared DRAM). With 54 GB of weights that leaves ~5 GB of KV cache, so cap `--max-model-len` (default 262K won't fit). Chat (server in one shell, client in another; first run downloads ~54 GB to ~/.cache/huggingface):
  > ~/Desktop/josh/ARMHiT/scripts/vllm/.venv/bin/vllm serve Qwen/Qwen3.8-27B --gpu-memory-utilization 0.7
  > ~/Desktop/josh/ARMHiT/scripts/vllm/.venv/bin/vllm chat
- 2026-10-26: 0.32.0 = latest GitHub release at install time, pinned for reproducibility (same as unpinned `uv pip install vllm` today); no other reason, older versions not compared.
- 2026-10-26: deleted the Qwen3-0.6B smoke-test model from `~/.cache/huggingface` (~1.5 GB, its blobs were the only ones there). The venv stays.
- 2026-10-26: Qwen's vLLM page (qwen.readthedocs.io/en/latest/deployment/vllm.html) only covers Qwen3, not 3.5/3.6. It's mostly CLI: `vllm serve` (OpenAI API on localhost:8000) + a client (`vllm chat`, curl, `openai` SDK). The Python `LLM` class is for offline batch only.
- 2026-10-26: downloading: `vllm serve` / `vllm bench --model <HF repo id>` fetches missing files via huggingface_hub into `~/.cache/huggingface/hub` (`HF_HOME` moves it); Qwen3.6-27B is Apache 2.0, not gated, so no token. Pre-downloading with `hf download Qwen/Qwen3.6-27B` (hf 2.3.0 in the venv) shows progress and resumes; serve/bench then hit the cache. `--local-dir <dir>` + passing the path as `--model` also works.
- 2026-10-26: `vllm bench throughput` is offline: starts its own engine in-process, runs all prompts as one batch, prints requests/s, total tok/s and output tok/s (`--output-json` saves them), exits. No server or chat needed. Notes:
  - no `vllm serve` running at the same time (two copies at 0.5 = all 122 GB).
  - same `--gpu-memory-utilization 0.5` and a `--max-model-len` >= in + out.
  - 10 prompts mostly measures ramp-up; fine as a first run.
  - `random` sends random tokens with fixed output length, so a broken load (bad weights / kernels) still gives plausible numbers. Check once by chatting that the output is coherent.
- 2026-10-26: paper-worthiness: `vllm bench` (formerly `benchmark_throughput.py` / `benchmark_serving.py`) is what many systems papers report; reproducible from command + version. Datasets: `random` for controlled sweeps, ShareGPT (`--dataset-name sharegpt`) as the usual realistic one; reporting both is common. A hand-written chat-prompt Python script is less defensible; write our own only for metrics bench doesn't give (e.g. per-token timing aligned with perf).
- 2026-10-26: commands: download Qwen3.8-27B BF16 (~55.6 GB), then the throughput benchmark:
  > ~/Desktop/josh/ARMHiT/scripts/vllm/.venv/bin/hf download Qwen/Qwen3.8-27B
  > ~/Desktop/josh/ARMHiT/scripts/vllm/.venv/bin/vllm bench throughput --model Qwen/Qwen3.8-27B --dataset-name random --random-input-len 500 --random-output-len 150 --num-prompts 10 --gpu-memory-utilization 0.7
- 2026-10-26: correction: newest is **Qwen3.8** (Aug 2026), not 3.6. Model switched to `Qwen/Qwen3.8-27B` (HF, 2026-08-05, not gated, 27.78B params BF16 ≈ 55.6 GB, same size as 3.6-27B). Same architecture as 3.5/3.6 (`Qwen3_5ForConditionalGeneration`), which vLLM 0.32.0 supports. FP8 variant: `Qwen/Qwen3.8-27B-FP8`. Commands (download optional, bench fetches it too):
  > ~/Desktop/josh/ARMHiT/scripts/vllm/.venv/bin/hf download Qwen/Qwen3.8-27B
  > ~/Desktop/josh/ARMHiT/scripts/vllm/.venv/bin/vllm bench throughput --model Qwen/Qwen3.8-27B --dataset-name random --random-input-len 500 --random-output-len 150 --num-prompts 10 --gpu-memory-utilization 0.7
- 2026-10-26: checked: vLLM 0.32.0 (2026-10-18) is the latest on PyPI and GitHub, no newer pre-release; supports Qwen3.8-27B's arch. Current commands (install already done; download optional, bench fetches the model too):
  > uv venv --python 3.12 ~/Desktop/josh/ARMHiT/scripts/vllm/.venv
  > VIRTUAL_ENV=~/Desktop/josh/ARMHiT/scripts/vllm/.venv uv pip install vllm==0.32.0 --torch-backend=auto
  > ~/Desktop/josh/ARMHiT/scripts/vllm/.venv/bin/hf download Qwen/Qwen3.8-27B
  > ~/Desktop/josh/ARMHiT/scripts/vllm/.venv/bin/vllm bench throughput --model Qwen/Qwen3.8-27B --dataset-name random --random-input-len 500 --random-output-len 150 --num-prompts 10 --gpu-memory-utilization 0.7
- 2026-10-26: ran bench throughput (Qwen3.8-27B, random 500/150, 10 prompts) at the default memory setting: **failed at startup**. vLLM 0.32.0's default `--gpu-memory-utilization` is **0.92** (not 0.9) = 112.98 GiB, but only 109.5 / 122.8 GiB is free (OS etc. ~13 GiB). Fix: 0.85 (~104 GiB; leaves ~48 GiB over the weights for KV, enough for the full 262K context, so no `--max-model-len` needed). Bench commands above updated to 0.85.
  Also warned: FlashInfer precompiled kernels missing (`flashinfer-cubin` / `flashinfer-jit-cache` not installed), so it JIT-compiles them at startup (several minutes). The vLLM install docs say to run this after install (skipped at install):
  > ~/Desktop/josh/ARMHiT/scripts/vllm/.venv/bin/vllm download-kernels
  > ~/Desktop/josh/ARMHiT/scripts/vllm/.venv/bin/vllm bench throughput --model Qwen/Qwen3.8-27B --dataset-name random --random-input-len 500 --random-output-len 150 --num-prompts 10 --gpu-memory-utilization 0.7

- 2026-10-26: run at 0.8 **hung** after "GPU KV cache size" (first run, incl. torch.compile): 13+ min with GPU idle (4.3 W) and 2 CPU cores spinning at 100%; Ctrl-C didn't work, needed `kill -9` on EngineCore (took a few s to release ~80 GB). Cause unknown (killed before a py-spy dump). In the working run below that same step took 73 s (18:04:31 -> 18:05:44), then JIT/FlashInfer warmup and a 2nd CUDA graph capture.
- 2026-10-26: **first result** at `--gpu-memory-utilization 0.7` (bench commands above updated to 0.7):
  > ~/Desktop/josh/ARMHiT/scripts/vllm/.venv/bin/vllm bench throughput --model Qwen/Qwen3.8-27B --dataset-name random --random-input-len 500 --random-output-len 150 --num-prompts 10 --gpu-memory-utilization 0.7
  `Throughput: 0.27 requests/s, 174.50 total tokens/s, 40.27 output tokens/s` (5000 prompt + 1500 output tokens, 37 s for the prompts).
  - startup ~5.5 min: weights 43 s (51.01 GiB), compile 0.55 s (cached from the hung run), init engine 235 s (mostly 2 rounds of CUDA graph capture, ~54 s + 61 s). KV cache 30.04 GiB = 482K tokens.
  - steady decode (engine log): 43.7 output tok/s at 10 running reqs = 4.37 steps/s = 0.229 s/step. Each step reads all weights (51.01 GiB = 54.8 GB): 54.8 / 0.229 ≈ **~239 GB/s ≈ 88% of the 273 GB/s peak (ESTIMATE, not measured**: assumes all weights read once per step; ignores KV cache (~0.4 GB/step), DeltaNet state, activations, and the unused vision-encoder weights; 10 s log averages. Measure with perf UCF counters like mixed_bw before using it).
  - `total tokens/s` = (prompt + output) / wall time, so it mixes prefill and decode; `output tokens/s` is the decode-relevant number.
- 2026-10-26: added `bw_trace.py` (not run yet; started as a .sh, switched to Python to add the clock check): aborts unless CPU, EMC and GPU clocks are pinned (`sudo ~/Desktop/josh/ARMHiT/pin_ghz.sh on`), then runs the bench command above under `perf stat -a -I 100` on the UCF `local_dram_byte_rd/wr` events (same perf binary/events as cpu_bw), then plots all / rd / wr GB/s over the whole run -> `bw.csv`, `bw.log`, `bw.png` in `scripts/vllm/`.
  > ~/Desktop/josh/ARMHiT/scripts/vllm/bw_trace.py
- 2026-10-26: ran `bw_trace.py` (clocks pinned, EMC 4266 MHz -> 273.0 GB/s peak). Ran with:
  > python3 ~/Desktop/josh/ARMHiT/scripts/vllm/bw_trace.py
  Bench: `Throughput: 0.27 requests/s, 177.81 total tokens/s, 41.03 output tokens/s` (steady decode 44.5 tok/s). Outputs `bw.png`, `bw.csv`, `bw.log`.
  - **measured decode plateau (238-268 s): rd 237.9 + wr 9.0 = 246.8 GB/s ≈ 90% of peak** (perf UCF, all requesters). The earlier ~239 GB/s weights-only estimate was close.
  - shape: ~0-83 s weight loading (low BW, disk-bound); ~83-108 s bursty ~80 GB/s then a ~240 GB/s spike; ~108-150 s and ~172-212 s two similar ~150-190 GB/s phases with rd ≈ wr (CUDA graph capture, twice as in the log); ~150-162 s / ~212-232 s rd-heavy bursts up to ~250 GB/s (warmup/profiling); ~235-270 s the benchmark itself (flat, read-dominated); ~270 s shutdown.
- 2026-10-26: `bw_trace.py` perf interval 100 ms -> 1 s (fewer points, smoother plot; the 100 ms run's bw.csv/png were overwritten). Reran:
  > python3 ~/Desktop/josh/ARMHiT/scripts/vllm/bw_trace.py
  Bench: `Throughput: 0.26 requests/s, 172.21 total tokens/s, 39.74 output tokens/s`. Same phase shape as the 100 ms run.
  - decode plateau (240-270 s): rd 229.8 + wr 8.6 = **238.4 GB/s ≈ 87% of peak**, vs 246.8 in the 100 ms run; output tok/s dropped by the same ~3% (41.03 -> 39.74), so it's run-to-run variation in the bench, not the interval. Need several runs before quoting one number.
- 2026-10-26: takeaways (Qwen3.8-27B BF16, vLLM 0.32.0, random 500/150, 10 prompts, clocks pinned):
  - decode is memory-bandwidth-bound and close to the ceiling: 238-247 GB/s = 87-90% of the 273 GB/s peak, ~94-97% of the best GPU read kernel (gpu_bw `read` 253.7 GB/s). Little headroom: decode speed scales with bytes per step (FP8 would roughly halve them), not kernel tuning.
  - read-heavy as expected. Per step (0.229 s), from the config, not measured separately:
    - reads: weights 54.8 GB (nearly all), DeltaNet state ~1.5 GB (48 linear-attention layers x 48 value heads x 128 x 128 x 4 B fp32 ≈ 151 MB / request x 10), KV cache ~0.4 GB (16 attention layers, 64 KiB / token).
    - writes ~2 GB: the DeltaNet state is rewritten every step (~1.5 GB ≈ 6.6 GB/s of the 8.6 GB/s measured); the rest is new KV entries (tiny), activations, logits (10 x 248K vocab), sampling, CPU side. A plain transformer would write almost nothing per step; the write line comes from the hybrid architecture.
  - batching is nearly free while bandwidth-bound: the weights are read once per step for all requests, so 10 reqs give ~40 tok/s total but ~4 tok/s each (batch 1 would also be ~4.4 tok/s). Ollama's 17.45 tok/s (4-bit, 1 prompt, unpinned) is not comparable (fewer bytes per token, single stream); compare bandwidth instead (~150 vs ~240 GB/s).
  - any other DRAM traffic (CPU load, TEE work) competes for the same ~250 GB/s, so decode tok/s should drop roughly in proportion: a realistic victim workload for the interference / mixed_bw experiments.
  - setup phases are not inference: weight load (~0-83 s, disk-bound), two CUDA-graph-capture / warmup rounds at ~150-190 GB/s with rd ≈ wr. Only the last ~35 s plateau is the bench.
  - run-to-run variation ~3% (41.03 vs 39.74 tok/s), so repeat before quoting a number.
- 2026-10-26: chat with the model: server in one terminal (startup ~4-5 min, wait for `Application startup complete`), client in another (type a message + Enter, Ctrl-C to quit). ~4 tok/s (single stream, bandwidth-bound). Thinks first by default (`/no_think` in a message may skip it; untested on 3.8). Ctrl-C the server afterwards to free ~85 GB; don't run a bench at the same time.
  > ~/Desktop/josh/ARMHiT/scripts/vllm/.venv/bin/vllm serve Qwen/Qwen3.8-27B --gpu-memory-utilization 0.7
  > ~/Desktop/josh/ARMHiT/scripts/vllm/.venv/bin/vllm chat


vllm qwen3.8 install + benchmark docs
https://qwen.readthedocs.io/en/latest/deployment/vllm.html
https://docs.vllm.ai/en/latest/benchmarking/cli/#offline-throughput-benchmark