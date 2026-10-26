# TEE (OP-TEE) progress

## What it is
- **OP-TEE** (Open Portable Trusted Execution Environment) is an open-source TEE OS for Arm TrustZone. ST-Ericsson started it, then Linaro maintained it, and it now lives under **TrustedFirmware.org** (github.com/OP-TEE: optee_os, optee_client, optee_test). The Linux driver (`drivers/tee/optee`) is upstream mainline.
- **On this board it is NVIDIA's fork**, not a stock upstream build: package `nvidia-l4t-optee` 39.2.1 (JetPack R39.2.1). Source: `gitlab.com/nvidia/nv-tegra/tegra/optee-src/{nv-optee,atf}` (`nvidia-jetson-optee-source.tbz2`). It is based on upstream **OP-TEE 4.6** (dmesg: `optee: revision 4.6 (565c7642)`) plus a Tegra platform port and NVIDIA's own TAs (fTPM, LUKS key, hardware key / PKCS#11 helpers, trusted keys).
- **How it works**: the CPU has two worlds. Linux (kernel EL1 + user EL0) is the normal world. OP-TEE OS (S-EL1) runs **Trusted Applications** (TAs, S-EL0) in the secure world, on the same 14 cores. A normal-world client opens a session on a TA by UUID through `libteec` -> `/dev/tee0` -> an SMC into TF-A (EL3) -> OP-TEE. The call runs **on the calling core** until it returns. A normal-world interrupt (timer, IPI) makes OP-TEE return to Linux and resume the call later, so long calls are preemptible. `tee-supplicant` / `nv-tee-supplicant` are user-space helpers that OP-TEE calls back for file storage, loading TAs from `/lib/optee_armtz`, etc.
- Secure memory is a DRAM carveout that Linux can't see (not in `/proc/device-tree/reserved-memory`; TZ protection is in the memory controller). TrustZone isolates memory; it **does not encrypt DRAM**.
- What it does here: firmware TPM (`tpm_ftpm_tee`, `/dev/tpm*`), hardware RNG (`optee_rng` = pseudo-TA `ab7a617c-...`), disk-encryption keys (`nvluks-srv-app`), PKCS#11 token, plus the optee_test TAs for `xtest`.

## Main takeaways
- **A TA is a separate program written for OP-TEE, not a protected copy of a Linux binary.** Two parts: the TA (C, links OP-TEE's `libutee`, runs in the secure world) and a normal-world client (links `libteec` -> `/dev/tee0`) that asks OP-TEE to run its commands. Nothing runs unmodified; every workload is a port.
- **What it guarantees**: the TA's code and private memory can't be read or changed from the normal world, including Linux root (hardware-enforced). The world switch is TF-A's job, loading / signature checking OP-TEE OS's; the TA does neither.
- **What it doesn't**:
  - Availability: Linux decides when (and whether) to call it (PARTEE's point).
  - Inputs / outputs: arguments and shared buffers come from Linux and are visible to it.
  - Side channels: same cores, caches and DRAM as Linux. Traffic volume / timing (perf's UCF counters, if they count secure traffic) and especially cache timing (prime+probe) leak whatever depends on secrets. TEEs generally leave this out of scope; only constant-time / oblivious code is protected. A plain hash table in a TA leaks its access pattern by design.
  - Physical attacks: TrustZone doesn't encrypt DRAM.
  - On this board the TA signing key is public, so anyone with root can load a TA (they still can't read another TA's memory).
- **So running our benchmarks in a TA gains nothing for the programs themselves.** The value is measuring the **cost of moving memory-heavy work into the TEE**: same GB/s or not, world-switch cost, how many cores can be in the TEE at once, memory limits. That is the research question, and it's only worth it if TEE overhead is.

## What is on this system (checked 2026-10-11)
- Kernel 6.8.12-1021-tegra; modules `optee_rng`, `tpm_ftpm_tee`; DT `/firmware/optee` present.
- `/dev/tee0`, `/dev/teepriv0`: **root-only (crw-------)**. thor has no passwordless sudo, so nothing below that opens a session has been run yet.
- Daemons: `tee-supplicant`, `nv-tee-supplicant` (systemd `nv-tee-supplicant.service`).
- Userspace: `libteec.so.2`, `libckteec`, `libteeacl`, headers `tee_client_api.h` etc. in `/usr/include`, **`xtest`** (suites: regression, benchmark, pkcs11; applets `--hash-perf`, `--aes-perf`, `--asym-perf`, `--stats`).
- 24 TAs in `/lib/optee_armtz` (copy in `/usr/lib/optee_armtz`), ids from their strings: `5b9e0e40` os_test, `cb3e5ba0` crypt, `d17f73a0` rpc_test, `e13010e0` **concurrent** (`busy_loop`, `sha256`), `f157cda0` storage benchmark, `873bcd08` socket, `fd02c9da` pkcs11, `f04a0fe7` trusted_keys; the rest are other optee_test / NVIDIA TAs. **No `sha_perf` / `aes_perf` TA** was found by these upstream UUIDs (from memory, unverified), so `--hash-perf` / `--aes-perf` may fail with "item not found".
- **Every shipped TA is signed with OP-TEE 4.6's public default dev key** (`optee_os/keys/default.pem` at tag 4.6.0, RSA-2048, RSASSA-PSS-SHA256). Checked: verified the signed hash (header offset 20) against the signature (offset 52) for all 24 with `openssl pkeyutl -verify ... rsa_padding_mode:pss`; all pass. (Upstream master's key is now 4096-bit and doesn't match.) So **OP-TEE here accepts TAs signed with a key anyone has: we can build and sign our own TA** without NVIDIA's key. (The mixed_bw note guessed otherwise.)

## Can our tests run in it?
A TA is a small C program (libutee + libutils, no libc++, no pthreads, no CUDA, no Linux syscalls). Each call runs on the core that made it.

| test | in a TA? | notes |
|---|---|---|
| cpu_bw (`mem.cpp`) | **yes, with a port** | Loop is plain C. Memory: either (a) TA heap / secure carveout: small (likely MBs to tens of MB, see `--stats --alloc`), so it may stay in the SLC and not measure DRAM; or (b) normal-world memory passed as a registered shared-memory memref: can be big, but OP-TEE maps it with **4 KiB pages** (no THP) and the TA's VA space may be ~4 GiB (`CFG_LPAE_ADDR_SPACE_BITS` 32 on many platforms; unverified here). Threads: one normal-world pinned thread per core, each with its own session (multi-instance TA) calling "stream for N s". Needs `CFG_NUM_THREADS` >= 14 to run 14 at once (unverified). Measurement can stay as is: the UCF DRAM counters are memory-side, so `perf stat -a` should count secure-world traffic too (to check). |
| gpu_bw | **no** | The GPU and CUDA are not reachable from the secure world. |
| mixed_bw | **partly** | CPU part in a TA, GPU part in Linux as today, same `MEM_START_AT`. |
| threads / prefetches (DRAMHiT hash table) | **hard** | DRAMHiT is C++ with threads; a TA has neither. Would need a C rewrite of the core loop. PRFM prefetches do work at S-EL0. Not worth it unless the TEE result matters. |

## Plan
1. **Port cpu_bw into a TA and check whether it reaches the same GB/s** (first and main step). Self-contained in `TEE/`: TA (read / write / copy loop, one command "stream for N s", returns bytes and time), `mem.cpp` client (same CLI and `gbs :` output as cpu_bw, 14 pinned threads, one session each, `MEM_START_AT`), Makefile, collector (same perf UCF counters and clock check as cpu_bw).
   Fair comparison, three numbers per pattern:
   | setup | why |
   |---|---|
   | cpu_bw, 2 MiB pages | today's result |
   | cpu_bw, 4 KiB pages (no THP), same buffer size as the TA | fair baseline: shared memory is 4 KiB-mapped in the TA |
   | TA, 4 KiB shared memory | the TEE result |
   If OP-TEE limits the shared buffer size, both baselines use that size too. Expected: same GB/s (same cores, caches, DRAM, no encryption); a gap would be the TEE (or its mapping).
2. **Thread sweep 1..14** in the TA: can all cores be in the secure world at once, or does OP-TEE serialize (`CFG_NUM_THREADS`, locks)?
3. **World-switch cost**: empty-call latency; GB/s vs calls per second (one call every X passes; the plan in mixed_bw/progress.md); one vs 14 threads calling.
4. **Later: attestation and its cost.** A real deployment would have to prove what runs in the TEE. Options here: the fTPM (`tpm_ftpm_tee`, a TA; `tpm2-tools` installed incl. `tpm2_quote` / `tpm2_checkquote`; `/dev/tpm0`, `/dev/tpmrm0` need the `tss` group or root), or OP-TEE's attestation PTA (upstream `CFG_ATTESTATION_PTA`, signs a TA's measurement; enabled in NVIDIA's build? unchecked). Measure: TA load / verify time vs TA size (PARTEE: linear in ELF size), quote / attestation latency, and whether it disturbs bandwidth on the other cores.
5. Only if the above is worth it: a C core of the hash table (DRAMHiT) in a TA.

Before 1: run the root checks below (secure heap size, concurrent sessions), they decide the buffer size and thread count.

## Related: PARTEE (Habeeb, Chen, Yoon, Shao; ACSAC'25)
"It's a non-stop PARTEE! Practical multi-enclave availability through partitioning and asynchrony" (flint.cs.yale.edu/certikos/publications/partee.pdf).
- Topic is **availability** (Linux or one enclave denying service to another), not porting. They replace OP-TEE with their own partitioning TEE OS (Raspberry Pi 4B, drone). Their enclaves are ported too (own LibC; MiBench "ported to each be PARTEE enclaves"), so unmodified programs don't run there either.
- Points about OP-TEE that matter to us:
  - **Heap drain**: OP-TEE allocates kernel objects from a small static secure heap (193 malloc/calloc call sites); each shared-memory registration allocates on it. Our "register a big normal-world buffer" plan is bounded by that heap (their Fig. 2 heap is ~49 KiB on the Pi build).
  - Many OP-TEE services are RPCs back to Linux (9 essential ones) that block the TEE until Linux returns: the secure world depends on Linux scheduling.
  - GlobalPlatform-style IPC is synchronous client-server calls; the caller's thread switches into the server TA.
  - TA load time grows linearly with ELF size (signature hash over the whole file).
  - World switch ~5 us on the Pi 4B; each secure-timer trap resets the branch predictor (Spectre mitigation), which cost 0.5-4% on Linux benchmarks. Relevant to the call-cost experiment.

## How to build a TA (not done)
- TA dev kit: build upstream `optee_os` **4.6.0** (or NVIDIA's nv-optee source of the same release, safer) with `make ta_dev_kit CFG_ARM64_core=y` natively on the board (aarch64 gcc). The output `export-ta_arm64/` has the TA makefiles, libutee and `default_ta.pem`; `sign_encrypt.py` signs with it. The libutee syscall ABI must match the running 4.6 OS.
- Host side: link `-lteec`, open a session by UUID. Install the `.ta` in `/lib/optee_armtz/` (root) or `xtest --install-ta`.
- Running needs root (`/dev/tee0` is 0600), e.g. `sudo` or a udev rule / `teeclnt` group.

## To run first (needs root; type `! sudo ...` in the session)
- `sudo xtest --stats --alloc` -> secure heap size / usage (how big a buffer fits in secure memory).
- `sudo xtest --stats --ta` and `--time` -> loaded TAs, REE vs TEE clock.
- `sudo xtest -t regression 1013` -> concurrent TA test (several sessions at once; hints at the thread count).
- `sudo xtest -t benchmark` -> storage / call benchmarks (if the TAs are present).
- `sudo xtest --hash-perf` -> SHA throughput in a TA (may fail if the perf TA is missing).

## Open
- Secure heap size, `CFG_NUM_THREADS`, TA VA size on this build (read NVIDIA's tegra platform `conf.mk`, or the xtest results).
- Does `nvidia_ucf_pmu_0` count secure-world DRAM traffic? (If yes, a coarse side channel on any TA; cache timing is the stronger one.)
- Which attestation path is available (fTPM quote, OP-TEE attestation PTA)?

## History
- 2026-10-11: surveyed OP-TEE on the board; found the TAs signed with the public 4.6 default key; no xtest runs (no root). Wrote takeaways (what a TA does / doesn't guarantee) and the plan: cpu_bw in a TA vs 2 MiB and 4 KiB baselines first, attestation later.
- 2026-10-26: build prerequisites for the OP-TEE source (`atf_and_optee_README.txt` points to optee.readthedocs.io/en/latest/building/prerequisites.html, which only gives a Dockerfile for Ubuntu 22.04). Its apt list adapted to this board (Ubuntu 24.04), installing everything instead of picking the needed packages:
  ```
  sudo apt update
  sudo apt install -y \
      adb acpica-tools autoconf automake bc bison build-essential ccache cpio cscope curl \
      device-tree-compiler e2tools expect fastboot flex ftp-upload gdisk git \
      libgnutls28-dev libattr1-dev libcap-ng-dev libfdt-dev libftdi-dev libglib2.0-dev \
      libgmp-dev libhidapi-dev libmpc-dev libncurses-dev libpixman-1-dev libslirp-dev \
      libssl-dev libtool libusb-1.0-0-dev make mtools netcat-openbsd ninja-build \
      python3-cryptography python3-pip python3-pyelftools python3-serial python3-tomli \
      python-is-python3 rsync swig unzip uuid-dev wget xdg-utils xsltproc xterm xz-utils \
      zlib1g-dev python3-pycryptodome pkg-config
  ```
  Changes from the Dockerfile:
  | Dockerfile | here | why |
  |---|---|---|
  | `libgmp3-dev` | `libgmp-dev` | renamed in 24.04 |
  | `libncurses5-dev` | `libncurses-dev` | renamed in 24.04 |
  | `netcat` | `netcat-openbsd` | `netcat` is only a virtual package in 24.04 |
  | — | `python3-pycryptodome` | named in NVIDIA's README |
  | — | `pkg-config` | named in NVIDIA's README (PKG-CONFIG section) |
  | `upgrade -y`, `repo init/sync`, `make toolchains`, `make check` | dropped | they build the upstream QEMU dev environment, not needed |

  The renames don't affect the build, which uses the headers and libraries (`gmp.h`, `libncurses`, `nc`), not package names, and those are the same. Neither optee_os nor optee_client uses gmp, ncurses or netcat (they are for QEMU / kernel menuconfig). What this build actually needs: build-essential, python3-cryptography (41.0.7, new enough), pkgconf, uuid-dev (already installed) and **python3-pyelftools (was missing)**. pycryptodome is only used by optee_os's `sign_rproc_fw.py`, which isn't part of this build.
- 2026-10-26: the README's `<platform>` is **264** on this board (`/proc/device-tree/model` = "NVIDIA Jetson AGX Thor Developer Kit", `compatible` includes `nvidia,tegra264`; 234 is Orin). So it's `./optee_src_build.sh -p t264`, `optee/tegra264-optee.dts` and outputs in `optee/build/t264/` / `optee/install/t264/`.
- 2026-10-26: toolchain: skip the download, use the native gcc (the build script still needs the vars):
  ```
  export CROSS_COMPILE_AARCH64_PATH=/usr
  export CROSS_COMPILE_AARCH64=/usr/bin/aarch64-linux-gnu-
  export PKG_CONFIG=/usr/bin/pkg-config
  ```
- 2026-10-26: from `atf_and_optee_README.txt`, only run `./optee_src_build.sh -p t264` (no `-t`, fTPM not needed). It gives `optee/build/t264/export-ta_arm64/` and `optee/install/t264/usr` for the TA/CA build. Skip the dtb, ATF, tos.img and flashing sections (firmware already on the board), and don't copy `install/t264` to the system (it would replace tee-supplicant / libteec); point `OPTEE_CLIENT_EXPORT` / `TEEC_EXPORT` at it in place.
- 2026-10-26: built OP-TEE (with `-t` after all), in `TEE/jetson-public-srcs/Linux_for_Tegra/source/jetson-optee-srcs/`:
  `./optee_src_build.sh -p t264 -t`
  Outputs present: `optee/build/t264/export-ta_arm64/` and `optee/install/t264/usr/{include,lib,sbin}` (incl. `libteec.so`).
- 2026-10-26: TA examples: `TEE/optee_examples/` (github.com/linaro-swg/optee_examples, from the OP-TEE docs). The checkout is **4.11.0**, newer than the board's OP-TEE 4.6 (fine for hello_world; `git checkout 4.6.0` to match). More TA source: `jetson-optee-srcs/optee/optee_test/ta/` (xtest TAs) and `optee/samples/` (NVIDIA's).
  hello_world: the CA opens a session on TA `8aaaf200-2450-11e4-abe2-0002a5d5c51b`, sends 42 with command `INC_VALUE`, the TA increments it and the CA prints 43. Build, install, run:
  > make -C ~/Desktop/josh/ARMHiT/scripts/TEE/optee_examples/hello_world CROSS_COMPILE=/usr/bin/aarch64-linux-gnu- TA_DEV_KIT_DIR=~/Desktop/josh/ARMHiT/scripts/TEE/jetson-public-srcs/Linux_for_Tegra/source/jetson-optee-srcs/optee/build/t264/export-ta_arm64/ OPTEE_CLIENT_EXPORT=~/Desktop/josh/ARMHiT/scripts/TEE/jetson-public-srcs/Linux_for_Tegra/source/jetson-optee-srcs/optee/install/t264/usr TEEC_EXPORT=~/Desktop/josh/ARMHiT/scripts/TEE/jetson-public-srcs/Linux_for_Tegra/source/jetson-optee-srcs/optee/install/t264/usr -j"$(nproc)"
  > sudo cp ~/Desktop/josh/ARMHiT/scripts/TEE/optee_examples/hello_world/ta/8aaaf200-2450-11e4-abe2-0002a5d5c51b.ta /lib/optee_armtz/
  > sudo cp ~/Desktop/josh/ARMHiT/scripts/TEE/optee_examples/hello_world/host/optee_example_hello_world /usr/sbin/
  > sudo optee_example_hello_world

  Worked: prints `TA incremented value to 43`.
- 2026-10-26: copied hello_world (source only, `git archive` from optee_examples) to `TEE/josh_ta/hello_world/` to build it outside optee_examples. Unchanged, same UUID, so it replaces the installed one. Build, install, run:
  > make -C ~/Desktop/josh/ARMHiT/scripts/TEE/josh_ta/hello_world CROSS_COMPILE=/usr/bin/aarch64-linux-gnu- TA_DEV_KIT_DIR=~/Desktop/josh/ARMHiT/scripts/TEE/jetson-public-srcs/Linux_for_Tegra/source/jetson-optee-srcs/optee/build/t264/export-ta_arm64/ OPTEE_CLIENT_EXPORT=~/Desktop/josh/ARMHiT/scripts/TEE/jetson-public-srcs/Linux_for_Tegra/source/jetson-optee-srcs/optee/install/t264/usr TEEC_EXPORT=~/Desktop/josh/ARMHiT/scripts/TEE/jetson-public-srcs/Linux_for_Tegra/source/jetson-optee-srcs/optee/install/t264/usr -j"$(nproc)"
  > sudo cp ~/Desktop/josh/ARMHiT/scripts/TEE/josh_ta/hello_world/ta/8aaaf200-2450-11e4-abe2-0002a5d5c51b.ta /lib/optee_armtz/
  > sudo cp ~/Desktop/josh/ARMHiT/scripts/TEE/josh_ta/hello_world/host/optee_example_hello_world /usr/sbin/
  > sudo optee_example_hello_world
  Clean (removes build outputs in the source dir only; the installed `.ta` and CA stay):
  > make -C ~/Desktop/josh/ARMHiT/scripts/TEE/josh_ta/hello_world CROSS_COMPILE=/usr/bin/aarch64-linux-gnu- TA_DEV_KIT_DIR=~/Desktop/josh/ARMHiT/scripts/TEE/jetson-public-srcs/Linux_for_Tegra/source/jetson-optee-srcs/optee/build/t264/export-ta_arm64/ clean
- 2026-10-26: `josh_ta/first_ta/`: copy of hello_world, the TA hashes the value instead: `v *= 2654435761u` (uint32, wraps). Own UUID `3bab25ff-a946-433e-a002-b40f1013800e`, command `TA_FIRST_CMD_HASH_VALUE`, CA `first_ta`; files renamed to `ta/first_ta.c`, `ta/include/first_ta.h`. Builds, signs and **runs**: `sudo first_ta 1..5` -> 2654435761, 1013904226, 3668339987, 2027808452, 387276917 (correct). Not a secure hash: invertible (inverse 244002641 mod 2^32), constant is public. The CA takes the value as its first arg (decimal, or 0x hex), and prints usage if it's missing. Expected: `TA hashed value to 4112119562` (42 * 2654435761 mod 2^32).
  Build, install, run:
  > make -C ~/Desktop/josh/ARMHiT/scripts/TEE/josh_ta/first_ta CROSS_COMPILE=/usr/bin/aarch64-linux-gnu- TA_DEV_KIT_DIR=~/Desktop/josh/ARMHiT/scripts/TEE/jetson-public-srcs/Linux_for_Tegra/source/jetson-optee-srcs/optee/build/t264/export-ta_arm64/ OPTEE_CLIENT_EXPORT=~/Desktop/josh/ARMHiT/scripts/TEE/jetson-public-srcs/Linux_for_Tegra/source/jetson-optee-srcs/optee/install/t264/usr TEEC_EXPORT=~/Desktop/josh/ARMHiT/scripts/TEE/jetson-public-srcs/Linux_for_Tegra/source/jetson-optee-srcs/optee/install/t264/usr -j"$(nproc)"
  > sudo cp ~/Desktop/josh/ARMHiT/scripts/TEE/josh_ta/first_ta/ta/3bab25ff-a946-433e-a002-b40f1013800e.ta /lib/optee_armtz/
  > sudo cp ~/Desktop/josh/ARMHiT/scripts/TEE/josh_ta/first_ta/host/first_ta /usr/sbin/
  > sudo first_ta 42
  Clean:
  > make -C ~/Desktop/josh/ARMHiT/scripts/TEE/josh_ta/first_ta CROSS_COMPILE=/usr/bin/aarch64-linux-gnu- TA_DEV_KIT_DIR=~/Desktop/josh/ARMHiT/scripts/TEE/jetson-public-srcs/Linux_for_Tegra/source/jetson-optee-srcs/optee/build/t264/export-ta_arm64/ clean
