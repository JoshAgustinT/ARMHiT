// ARMHiT: minimal ARM port of DRAMHiT's casht++ uniform benchmark.
#include <unistd.h>

#include <cstdlib>

#include "dramhit.hpp"

static const char *prefetch_name() {
#if defined(DOUBLE_PREFETCH)
  return "DOUBLE";
#elif defined(DOUBLE_L2_PREFETCH)
  return "DOUBLE_L2";
#elif defined(L1_PREFETCH)
  return "L1";
#elif defined(L2_PREFETCH)
  return "L2";
#elif defined(L3_PREFETCH)
  return "L3";
#elif defined(NTA_PREFETCH)
  return "NTA";
#elif defined(L2STRM_PREFETCH)
  return "L2STRM";
#elif defined(L3STRM_PREFETCH)
  return "L3STRM";
#else
  return "NONE";
#endif
}

#define CHECK(cond, fmt, ...)       \
  do {                              \
    if (!(cond)) {                  \
      PLOGE(fmt, ##__VA_ARGS__);    \
      exit(-1);                     \
    }                               \
  } while (0)

static void validate() {
  long ncpu = sysconf(_SC_NPROCESSORS_ONLN);
  uint32_t q = config.find_queue_sz;
  CHECK(config.mode == MODE_UNIFORM, "only --mode 11 (uniform) is supported, got %u",
        config.mode);
  CHECK(config.ht_type == HT_CASHTPP, "only --ht-type 3 (casht++) is supported, got %u",
        config.ht_type);
  CHECK(config.num_threads >= 1 && config.num_threads <= ncpu,
        "--num-threads must be in [1, %ld], got %u", ncpu, config.num_threads);
  CHECK(config.ht_fill >= 1 && config.ht_fill <= 99,
        "--ht-fill should be in range [1, 99], got %u", config.ht_fill);
  // The queues store a 32-bit table index.
  CHECK(config.ht_size <= (1ull << 32), "--ht-size must be <= 2^32 slots");
  // > 8 so the DOUBLE look-ahead prefetch stays inside the queue.
  CHECK(q >= 16 && (q & (q - 1)) == 0, "--find_queue_sz must be a power of 2 >= 16, got %u", q);
  CHECK(config.batch_len > 0, "--batch-len must be > 0");
  // One key per 4-slot cache line, so the keys need 4x their count in slots.
  CHECK(!config.sequential || config.ht_fill <= 25,
        "--sequential needs --ht-fill <= 25 (one key per cache line), got %u", config.ht_fill);
  CHECK(config.insert_factor > 0 && config.read_factor > 0,
        "--insert-factor and --read-factor must be > 0");
}

int main(int argc, char **argv) {
  parse_args(argc, argv);
  validate();
  PLOGI("Hashtable type : Cas HT, prefetch %s, cpufreq %d MHz", prefetch_name(),
        CPUFREQ_MHZ);
  PLOGI("threads %u, ht_size %lu, fill %u%%, insert_factor %lu, read_factor %lu, "
        "batch %u, queue %u%s",
        config.num_threads, config.ht_size, config.ht_fill, config.insert_factor,
        config.read_factor, config.batch_len, config.find_queue_sz,
        config.sequential ? ", sequential" : "");
  return run_uniform_test();
}
