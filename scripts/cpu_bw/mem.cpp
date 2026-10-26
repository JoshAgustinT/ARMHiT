// mem.cpp -- how much memory bandwidth can the CPU cores pull?
//
//   ./mem PATTERN [MiB] [seconds] [threads | core,core,...]
//
// The CPU twin of scripts/gpu_bw/mem.cu. Two buffers, src and dst, of MiB
// each (default 8192), on 2 MiB pages:
//   read      sum src                               (bytes moved = buffer)
//   read_l3   PRFM PLDL3KEEP every 64 B line of src, no loads: pulls the
//             lines into L3 without the core waiting for them
//   write     fill dst                              (bytes moved = buffer)
//   write_nt  fill dst with STNP (non-temporal store; ARM's streaming store)
//   write_pf  fill dst, with PRFM PSTL1KEEP PF_AHEAD lines ahead
//   copy      src -> dst                            (bytes moved = 2 * buffer)
// Small sizes fit in cache, big ones go to DRAM. The asm is volatile, so the
// compiler can't drop it.
//
// The last argument is a thread count N (threads on cores 0..N-1, default all
// cores) or a comma list of cores (one thread on each, e.g. 0,4,8,12).
//
// Each thread (pinned to its core) owns a contiguous slice of both
// buffers and walks it sequentially, over and over, for ~`seconds`. Each
// thread counts its own bytes and time and the GB/s are summed, so a thread
// the OS slows down just adds less instead of holding the rest up.
//
// If MEM_START_AT is set (unix time, seconds), it sets up first and then
// waits until that moment to measure, so a collector can line it up with
// perf (and scripts/mixed_bw with the GPU test).
#include <arm_neon.h>
#include <pthread.h>
#include <sys/mman.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <thread>
#include <vector>

using Clock = std::chrono::steady_clock;

const size_t LINE_WORDS = 64 / sizeof(uint64_t);  // 8 words per 64 B line
const size_t PF_AHEAD = 64;                       // lines ahead for write_pf (4 KiB)

// Anonymous mapping on 2 MiB transparent huge pages, as cas_ht.hpp does.
uint64_t* alloc_huge(size_t bytes) {
    void* addr = mmap(nullptr, bytes, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (addr == MAP_FAILED) {
        perror("mmap");
        exit(1);
    }
    madvise(addr, bytes, MADV_HUGEPAGE);
    return static_cast<uint64_t*>(addr);
}

// How much of this process sits on huge pages, from the kernel's own count.
size_t huge_page_mib() {
    std::ifstream smaps("/proc/self/smaps_rollup");
    std::string key;
    size_t kb;
    while (smaps >> key) {
        if (key == "AnonHugePages:" && smaps >> kb) {
            return kb / 1024;
        }
    }
    return 0;
}

void pin_to_core(int core) {
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(core, &set);
    pthread_setaffinity_np(pthread_self(), sizeof(set), &set);
}

// Sleeps until MEM_START_AT (unix time, seconds), if it is set.
void wait_for_start() {
    const char* at = getenv("MEM_START_AT");
    if (!at) {
        return;
    }
    auto start = std::chrono::system_clock::time_point(
        std::chrono::duration_cast<std::chrono::system_clock::duration>(
            std::chrono::duration<double>(atof(at))));
    if (std::chrono::system_clock::now() > start) {
        fprintf(stderr, "[!] setup ran past MEM_START_AT\n");
    }
    std::this_thread::sleep_until(start);
}

// Walks a slice of `n` words once; returns the bytes it moved.
// The compiler vectorises these loops (-O3 -mcpu=native).
uint64_t sink;

size_t run_test(const std::string& pattern, uint64_t* src, uint64_t* dst, size_t n) {
    if (pattern == "read") {
        uint64_t sum = 0;
        for (size_t i = 0; i < n; i++) {
            sum += src[i];
        }
        // Never true for our data, but the compiler can't know that, so the loads stay.
        if (sum == 0xdeadbeef) {
            sink = sum;
        }
        return n * sizeof(uint64_t);
    }
    if (pattern == "read_l3") {
        for (size_t i = 0; i < n; i++) {
            asm volatile("prfm pldl3keep, [%0]" :: "r"(src + i));
        }
        return n * sizeof(uint64_t);
    }
    if (pattern == "write") {
        for (size_t i = 0; i < n; i++) {
            dst[i] = 0x5a5a5a5a5a5a5a5a;
        }
        return n * sizeof(uint64_t);
    }
    if (pattern == "write_nt") {
        uint8x16_t v = vdupq_n_u8(0x5a);
        for (size_t i = 0; i < n; i += LINE_WORDS) {
            asm volatile("stnp %q1, %q1, [%0]\n\t"
                         "stnp %q1, %q1, [%0, #32]"
                         :: "r"(dst + i), "w"(v) : "memory");
        }
        return n * sizeof(uint64_t);
    }
    if (pattern == "write_pf") {
        for (size_t i = 0; i < n; i += LINE_WORDS) {
            asm volatile("prfm pstl1keep, [%0]" :: "r"(dst + i + PF_AHEAD * LINE_WORDS));
            for (size_t j = 0; j < LINE_WORDS; j++) {
                dst[i + j] = 0x5a5a5a5a5a5a5a5a;
            }
        }
        return n * sizeof(uint64_t);
    }
    if (pattern == "copy") {
        for (size_t i = 0; i < n; i++) {
            dst[i] = src[i];
        }
        return 2 * n * sizeof(uint64_t);
    }
    return 0;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s PATTERN [MiB] [seconds] [threads | core,core,...]\n", argv[0]);
        return 1;
    }
    std::string pattern = argv[1];
    size_t mib = argc > 2 ? atol(argv[2]) : 8192;
    double seconds = argc > 3 ? atof(argv[3]) : 5;

    std::vector<int> cores;
    if (argc > 4 && strchr(argv[4], ',')) {
        for (char* core = strtok(argv[4], ","); core; core = strtok(nullptr, ",")) {
            cores.push_back(atoi(core));
        }
    } else {
        int n = argc > 4 ? atoi(argv[4]) : std::thread::hardware_concurrency();
        for (int core = 0; core < n; core++) {
            cores.push_back(core);
        }
    }
    int threads = cores.size();

    if (pattern != "read" && pattern != "read_l3" && pattern != "write" &&
        pattern != "write_nt" && pattern != "write_pf" && pattern != "copy") {
        fprintf(stderr, "unknown pattern: %s\n", pattern.c_str());
        return 1;
    }

    size_t bytes = mib << 20;
    size_t slice = bytes / sizeof(uint64_t) / threads;  // words per thread
    slice = slice / LINE_WORDS * LINE_WORDS;            // whole lines
    uint64_t* src = alloc_huge(bytes);
    uint64_t* dst = alloc_huge(bytes);

    std::atomic<bool> stop{false};
    std::vector<double> gbs(threads);
    std::vector<std::thread> workers;
    for (int t = 0; t < threads; t++) {
        workers.emplace_back([&, t] {
            pin_to_core(cores[t]);
            uint64_t* s = src + t * slice;
            uint64_t* d = dst + t * slice;

            // Fault this slice's pages in on its own core, before timing.
            memset(s, 0x5a, slice * sizeof(uint64_t));
            memset(d, 0, slice * sizeof(uint64_t));

            wait_for_start();
            run_test(pattern, s, d, slice);  // untimed warm-up

            size_t moved = 0;



            auto start = Clock::now();
            while (!stop) {
                moved += run_test(pattern, s, d, slice);
            }
            auto end = Clock::now();


            
            double ns = std::chrono::duration<double, std::nano>(end - start).count();
            gbs[t] = moved / ns;  // bytes/ns = GB/s
        });
    }

    wait_for_start();
    std::this_thread::sleep_for(std::chrono::duration<double>(seconds));
    printf("huge_page_mib : %zu\n", huge_page_mib());  // should be ~2 * MiB
    stop = true;
    for (auto& w : workers) {
        w.join();
    }

    double total = 0;
    for (double g : gbs) {
        total += g;
    }
    printf("threads : %d\n", threads);
    printf("gbs : %.2f\n", total);
}
