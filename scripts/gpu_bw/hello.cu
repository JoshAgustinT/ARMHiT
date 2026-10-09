// hello.cu -- every GPU thread prints where it is running.
#include <cstdio>

// Which SM (streaming multiprocessor) the calling thread is running on.
// CUDA has no built-in function for this, so read the hardware register directly.
__device__ unsigned sm_id() {
    unsigned id;
    asm("mov.u32 %0, %%smid;" : "=r"(id));
    return id;
}

__global__ void hello_gpu() {
    int thread = blockIdx.x * blockDim.x + threadIdx.x;  // unique across the whole launch
    int block  = blockIdx.x;
    int warp   = threadIdx.x / warpSize;                       // warp within the block
    int lane   = threadIdx.x % warpSize;                       // position within the warp

    printf("thread %6d  sm %3u  block %4d  warp %2d  lane %2d\n",
           thread, sm_id(), block, warp, lane);
}

int main() {
    cudaDeviceProp gpu;
    cudaGetDeviceProperties(&gpu, 0);

    // Launch exactly enough threads to fill every SM.
    int threads_per_block = 256;
    int total_threads     = gpu.multiProcessorCount * gpu.maxThreadsPerMultiProcessor;
    int blocks            = total_threads / threads_per_block;

    printf("%s: %d SMs x %d threads/SM = %d threads (%d blocks of %d)\n",
           gpu.name, gpu.multiProcessorCount, gpu.maxThreadsPerMultiProcessor,
           total_threads, blocks, threads_per_block);

    // The default 1MB printf buffer silently drops lines at this scale.
    cudaDeviceSetLimit(cudaLimitPrintfFifoSize, 64 << 20);

    hello_gpu<<<blocks, threads_per_block>>>();
    cudaDeviceSynchronize();  // also flushes the GPU's printf buffer
}
