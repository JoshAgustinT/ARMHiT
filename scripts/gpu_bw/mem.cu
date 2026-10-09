// mem.cu -- how much DRAM bandwidth can the GPU pull?
//
//   ./mem read|write|copy [MiB] [seconds]
//
// Streams a buffer (default 1 GiB, far bigger than any cache) over and over
// for ~`seconds`, with every thread slot on the GPU busy, and prints GB/s:
//   read   sum the buffer        (bytes moved = size)
//   write  fill the buffer       (bytes moved = size)
//   copy   src -> dst            (bytes moved = 2 * size)
//
// If MEM_START_AT is set (unix time, seconds), it sets up first and then
// waits until that moment to measure, so a collector can line it up with
// perf (and scripts/mixed_bw with the CPU test).
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>

#define CHECK(call)                                                        \
    do {                                                                   \
        cudaError_t err = (call);                                          \
        if (err != cudaSuccess) {                                          \
            fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__,             \
                    cudaGetErrorString(err));                              \
            exit(1);                                                       \
        }                                                                  \
    } while (0)

using Clock = std::chrono::steady_clock;

// 16-byte elements: the widest load/store a thread can issue in one instruction.
using Element = uint4;

// Each thread walks the buffer with a stride of the whole grid, so on every
// step a warp touches 32 neighbouring elements (512 contiguous bytes).
__global__ void read_kernel(const Element* src, size_t n, unsigned* sink) {
    unsigned sum = 0;
    for (size_t i = blockIdx.x * blockDim.x + threadIdx.x; i < n; i += gridDim.x * blockDim.x) {
        Element e = src[i];
        sum ^= e.x ^ e.y ^ e.z ^ e.w;
    }
    // Never true for our data, but the compiler can't know that, so the loads stay.
    if (sum == 0xdeadbeef) *sink = sum;
}

__global__ void write_kernel(Element* dst, size_t n) {
    for (size_t i = blockIdx.x * blockDim.x + threadIdx.x; i < n; i += gridDim.x * blockDim.x)
        dst[i] = make_uint4(1, 2, 3, 4);
}

__global__ void copy_kernel(const Element* src, Element* dst, size_t n) {
    for (size_t i = blockIdx.x * blockDim.x + threadIdx.x; i < n; i += gridDim.x * blockDim.x)
        dst[i] = src[i];
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

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s read|write|copy [MiB] [seconds]\n", argv[0]);
        return 1;
    }
    const char* pattern = argv[1];
    size_t mib = argc > 2 ? atol(argv[2]) : 1024;
    double seconds = argc > 3 ? atof(argv[3]) : 5;

    // Every thread slot on every SM busy, as in hello.cu.
    cudaDeviceProp gpu;
    CHECK(cudaGetDeviceProperties(&gpu, 0));
    int threads_per_block = 256;
    int blocks = gpu.multiProcessorCount * gpu.maxThreadsPerMultiProcessor / threads_per_block;

    size_t bytes = mib << 20;
    size_t n = bytes / sizeof(Element);
    Element *src, *dst;
    unsigned* sink;
    CHECK(cudaMalloc(&src, bytes));
    CHECK(cudaMalloc(&dst, bytes));
    CHECK(cudaMalloc(&sink, sizeof(unsigned)));
    CHECK(cudaMemset(src, 0x5a, bytes));  // touch every page up front
    CHECK(cudaMemset(dst, 0, bytes));
    CHECK(cudaDeviceSynchronize());

    // One pass over the buffer; returns the bytes it moved to/from DRAM.
    auto pass = [&]() -> size_t {
        if (!strcmp(pattern, "read")) {
            read_kernel<<<blocks, threads_per_block>>>(src, n, sink);
            return bytes;
        }
        if (!strcmp(pattern, "write")) {
            write_kernel<<<blocks, threads_per_block>>>(dst, n);
            return bytes;
        }
        copy_kernel<<<blocks, threads_per_block>>>(src, dst, n);
        return 2 * bytes;
    };

    wait_for_start();
    pass();  // untimed warm-up
    CHECK(cudaDeviceSynchronize());

    // Launch batches of 8 passes (tens of ms of GPU work) until time is up;
    // waiting after each batch makes the wall clock cover the GPU's work.
    size_t moved = 0;
    auto start = Clock::now();
    auto deadline = start + std::chrono::duration<double>(seconds);
    while (Clock::now() < deadline) {
        for (int i = 0; i < 8; i++) moved += pass();
        CHECK(cudaDeviceSynchronize());
    }
    double ns = std::chrono::duration<double, std::nano>(Clock::now() - start).count();
    CHECK(cudaGetLastError());

    printf("gbs : %.2f\n", moved / ns);  // bytes/ns = GB/s
}
