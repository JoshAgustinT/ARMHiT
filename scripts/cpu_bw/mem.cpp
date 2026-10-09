// mem.cpp -- how much DRAM bandwidth can the CPU cores pull?
//
//   ./mem read|write|copy [GiB] [seconds] [threads]
//
// The CPU twin of scripts/gpu_bw/mem.cu. Each thread (default one per core,
// pinned) owns a contiguous slice of a big buffer (default 8 GiB, on 2 MiB
// pages) and walks it sequentially, over and over, for ~`seconds`:
//   read   sum the slice         (bytes moved = slice)
//   write  fill the slice        (bytes moved = slice)
//   copy   src slice -> dst      (bytes moved = 2 * slice)
//
// Each thread counts its own bytes and time and the GB/s are summed, so a
// thread the OS slows down just adds less instead of holding the rest up.
//
// If MEM_START_AT is set (unix time, seconds), it sets up first and then
// waits until that moment to measure, so a collector can line it up with
// perf (and scripts/mixed_bw with the GPU test).
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
    while (smaps >> key)
        if (key == "AnonHugePages:" && smaps >> kb) return kb / 1024;
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
    if (!at) return;
    auto start = std::chrono::system_clock::time_point(
        std::chrono::duration_cast<std::chrono::system_clock::duration>(
            std::chrono::duration<double>(atof(at))));
    if (std::chrono::system_clock::now() > start) fprintf(stderr, "[!] setup ran past MEM_START_AT\n");
    std::this_thread::sleep_until(start);
}

// One pass over a slice of `n` words; returns the bytes it moved to/from DRAM.
// The compiler vectorises these loops (-O3 -mcpu=native).
uint64_t sink;

size_t pass(const std::string& pattern, uint64_t* src, uint64_t* dst, size_t n) {
    if (pattern == "read") {
        uint64_t sum = 0;
        for (size_t i = 0; i < n; i++) sum += src[i];
        // Never true for our data, but the compiler can't know that, so the loads stay.
        if (sum == 0xdeadbeef) sink = sum;
        return n * sizeof(uint64_t);
    }
    if (pattern == "write") {
        for (size_t i = 0; i < n; i++) dst[i] = 0x5a5a5a5a5a5a5a5a;
        return n * sizeof(uint64_t);
    }
    for (size_t i = 0; i < n; i++) dst[i] = src[i];
    return 2 * n * sizeof(uint64_t);
}

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s read|write|copy [GiB] [seconds] [threads]\n", argv[0]);
        return 1;
    }
    std::string pattern = argv[1];
    size_t gib = argc > 2 ? atol(argv[2]) : 8;
    double seconds = argc > 3 ? atof(argv[3]) : 5;
    int threads = argc > 4 ? atoi(argv[4]) : std::thread::hardware_concurrency();

    size_t bytes = gib << 30;
    size_t slice = bytes / sizeof(uint64_t) / threads;  // words per thread
    uint64_t* src = alloc_huge(bytes);
    uint64_t* dst = alloc_huge(bytes);

    std::atomic<bool> stop{false};
    std::vector<double> gbs(threads);
    std::vector<std::thread> workers;
    for (int t = 0; t < threads; t++) {
        workers.emplace_back([&, t] {
            pin_to_core(t);
            uint64_t* s = src + t * slice;
            uint64_t* d = dst + t * slice;
            // Fault this slice's pages in on its own core, before timing.
            memset(s, 0x5a, slice * sizeof(uint64_t));
            memset(d, 0, slice * sizeof(uint64_t));

            wait_for_start();
            pass(pattern, s, d, slice);  // untimed warm-up

            size_t moved = 0;
            auto start = Clock::now();
            while (!stop.load(std::memory_order_relaxed)) moved += pass(pattern, s, d, slice);
            double ns = std::chrono::duration<double, std::nano>(Clock::now() - start).count();
            gbs[t] = moved / ns;  // bytes/ns = GB/s
        });
    }

    wait_for_start();
    std::this_thread::sleep_for(std::chrono::duration<double>(seconds));
    printf("huge_page_mib : %zu\n", huge_page_mib());  // should be 2 * buffer
    stop = true;
    for (auto& w : workers) w.join();

    double total = 0;
    for (double g : gbs) total += g;
    printf("threads : %d\n", threads);
    printf("gbs : %.2f\n", total);
}
