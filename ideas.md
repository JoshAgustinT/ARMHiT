# Ideas: high-throughput workloads inside TEEs (on the Thor)

## Pitch

TEEs are mostly evaluated on toy applications: password managers, small KV
stores, single requests. We want to know what happens to a high-throughput,
DRAM-latency-bound workload (DRAMHiT: ~1.4 Gops on 14 cores, many misses in
flight per core) when it has to run inside a TEE. We want to measure it,
explain where the throughput goes, and propose mitigations.

Constraint: stay on this machine (Jetson Thor, 14x Neoverse V3AE, unified
LPDDR5X, Blackwell GPU, OP-TEE present at `/dev/tee0`).

## What the Thor gives us

| piece | status | note |
|---|---|---|
| CPU baseline | done | ARMHiT, `results/thor.json` |
| TrustZone / OP-TEE | present (`/dev/tee0`, `/dev/teepriv0`) | need the OP-TEE client lib + TA dev kit; check the secure carve-out size |
| ARM CCA (Realms) | unlikely | not known to be enabled in Jetson firmware; would be the SEV/TDX analogue |
| GPU | present, no CUDA toolkit | needs sudo to install; GPU confidential mode on Thor unknown |
| memory encryption for TZ DRAM | unknown | must check what Thor's memory controller does for the secure carve-out |

## Where throughput can go (cost model)

```
 normal world (REE)                        secure world (TEE)
 ------------------                        ------------------
 app thread ── ioctl ─► tee driver ── SMC ─► OP-TEE core ─► TA: ht_find_batch()
   ▲                                                         │
   └────────────── return, results in shared mem ◄───────────┘

 per call  : syscall + SMC world switch + OP-TEE dispatch + TA context  ~ µs
 per op    : ~10 ns per find on this box (1.4 Gops / 14 cores)
 => one call per op costs 100-1000x; the call must be amortised over a batch
```

| cost | when it is paid | expected impact at Gops |
|---|---|---|
| attestation | once per launch / session | ~none in steady state (ms, one-off) |
| world switch / TA invocation | per call | dominant without batching |
| copy in/out of shared memory | per byte crossing | bandwidth tax, grows with batch |
| secure memory capacity | table size | an 8 GiB table probably does not fit a TZ carve-out |
| memory encryption/integrity | per DRAM miss | latency added per miss -> Little's law -> lost throughput unless more misses are kept in flight |
| TA threading limits | always | OP-TEE TAs are usually single-threaded per session |

### On "attestation will make throughput drop extremely"

Not per the evidence so far. Misono et al. measure attestation primitives at
**6.19 ms (SEV-SNP) and 2.75 ms (TDX)** per report, and treat it as a
one-time cost dominated by boot/TLS. Attestation hurts throughput only if
it is done **per request or per short session**, e.g. re-attesting on every
client connection with high connection churn. That is a legitimate scenario
to test, but steady-state throughput is driven by the per-call and per-miss
costs above. Measure it rather than assume it.

## Related work found (verified by search; read before relying on numbers)

**CPU confidential VMs**
- Misono et al., *Confidential VMs Explained: An Empirical Analysis of AMD SEV-SNP and Intel TDX*, POMACS 8(3) / SIGMETRICS '25.
  Their takeaways:
  - memory overhead of 7.29% (SNP) and 4.06% (TDX);
  - VMEXIT-type instructions +240% (SNP) and +472% (TDX);
  - up to 431% slowdown from vCPU HLT sleep/wake (fixed by guest halt polling);
  - up to 60% drop on iperf TCP from bounce buffers;
  - attestation in milliseconds, one-time.
  https://dl.acm.org/doi/10.1145/3726854.3727280
- ConfBench (DSN '25): a tool for evaluating CVMs. http://www.diag.uniroma1.it/delia/papers/dsn25.pdf

**GPU confidential computing / LLM serving**
- PipeLLM (ASPLOS '25): H100 confidential computing drops throughput by up
  to 52.8% (OPT-30B) and 88.2% (OPT-66B). The cost is encrypted
  CPU↔GPU transfers; the mitigation is pipelined, speculative encryption.
  https://ipads.se.sjtu.edu.cn/zh/publications/TanASPLOS25.pdf
- *Confidential Computing on NVIDIA Hopper GPUs: A Performance Benchmark
  Study* (arXiv 2409.03992): <5% for most LLM queries, dominated by data
  transfer. https://arxiv.org/pdf/2409.03992
- *Confidential LLM Inference: Performance and Cost Across CPU and GPU TEEs*
  (arXiv 2509.18886): 4–8% on H100 CC, shrinking with batch/input size.
  https://arxiv.org/abs/2509.18886
- *Benchmarking Confidential GPU Inference on NVIDIA H100 under Intel TDX*
  (arXiv 2607.19353): ~21–30% latency/throughput overhead.
  https://arxiv.org/html/2607.19353v1
- Note: the reported overheads range from 4% to 88% depending on model size,
  batching and implementation. That spread is itself a hint that the
  explanation is batching and transfer, not the TEE per se.

**TrustZone / OP-TEE**
- iperfTZ (arXiv 1909.06662):
  - KV-store ops inside a TA cost 12–17x on a Raspberry Pi and QEMU, due to
    world and context switches per TA invocation;
  - network throughput above 500 Mbit/s takes a ~1.8x world-switch hit.
  https://arxiv.org/pdf/1909.06662
- StreamBox-TZ (USENIX ATC '19): with ≥128K events per batch, >90% of
  time is spent on real compute in the TEE. Most world-switch cost is
  OP-TEE software, not hardware. https://arxiv.org/html/1808.05078v2
- *On the Performance of ARM TrustZone* (practical experience report),
  KEVLAR-TZ (secure cache for TrustZone), and vTZ / ReZone (TrustZone
  virtualisation and privilege reduction). Not read yet.
- Gap: these TrustZone studies are on embedded boards (RPi, QEMU) at
  Mbit/s or kops scale. Nobody seems to look at Gops-scale, DRAM-bound
  data structures on a server-class ARM core. That is our opening, but
  still to be confirmed with a proper literature search.

## Experiments on the Thor

1. **Baseline (done, extend)**: ARMHiT find/insert Mops vs fill and prefetch.
   Add a thread sweep (1→14), a find-queue sweep (16→256) and a pointer-chase
   latency test to get per-core misses in flight.
2. **Boundary cost**: a null TA call, so we know the µs per world switch on
   Thor. Then a TA hash table driven by the normal world with batch size
   B = 1 … 64K. Plot Mops vs B against the native line. That curve is the
   paper's core figure.
3. **Where the table lives**:
   - (a) a small table entirely in secure memory, if the carve-out allows;
   - (b) a large table in normal-world memory, protected by the TA with
     per-bucket encryption + MAC using the ARMv8 AES/PMULL/SHA3
     instructions (all present in `lscpu` flags), with keys held in the TA.
   Compare against native ARMHiT.
4. **Attestation churn**: throughput vs sessions per second, attesting once
   per session, from "once at boot" to "every request".
5. **Optional, needs CUDA**: GPU hash table on the same unified DRAM as a
   throughput reference. GPU confidential mode on Thor is unknown.

## Mitigation ideas

- **Batching across the boundary.** DRAMHiT's API is already batched
  (`insert_batch`/`find_batch`), so one world switch per batch of thousands
  of ops is natural. Measure where batch size stops mattering.
- **Shared-memory ring instead of calls.** The TA polls a request ring in
  registered shared memory and the normal world polls a response ring, so
  there is no SMC per batch (like io_uring or StreamBox-TZ). The cost is a
  core spinning in the secure world, which is the TEE version of guest halt
  polling.
- **Pin a few cores to the secure world**: dedicated TEE cores serving rings.
  Explore the trade-off of k TEE cores vs 14−k REE cores.
- **Deeper prefetch queues to absorb extra per-miss latency** (encryption,
  MAC checks). Little's law: more latency needs more in flight, and
  DRAMHiT's queue size is exactly that knob. Show whether it recovers the
  loss.
- **Bucket-granularity crypto.** One 64 B bucket = one AES-GCM block group
  or one MAC, so integrity cost scales with cachelines touched, not keys.
  Batch MAC verification, and use prefetch to overlap crypto with misses.
- **Attestation amortisation.** Long-lived attested sessions with
  resumption. Only matters if the churn experiment shows it does.

## Risks / open questions

- The OP-TEE TA dev kit and client library may need sudo or a rebuilt
  image. Check what JetPack ships before planning around it.
- Secure carve-out size on Thor: if tiny, 3b becomes the main design and
  that is fine, maybe more interesting.
- TAs may be limited to one thread per session. Multi-session concurrency
  is needed to use more than one core in the secure world.
- Thor's memory controller may not encrypt TZ DRAM. Then there is no
  per-miss encryption cost on the hardware path, only what we add in
  software (3b).
- Novelty versus the GPU-CC/LLM papers: they already cover the "batching
  helps" story for LLMs. Our angle has to be the latency-bound, Gops-scale
  data structure regime plus the boundary/ring design, not LLM serving.
