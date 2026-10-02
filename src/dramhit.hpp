// Shared config, operation types and small helpers (DRAMHiT's types.hpp,
// trimmed to what the uniform test needs).
#ifndef ARMHIT_DRAMHIT_HPP
#define ARMHIT_DRAMHIT_HPP

#include <cstdint>
#include <cstdio>
#include <span>
#include <utility>

#ifndef CPUFREQ_MHZ
#error "CPUFREQ_MHZ is required (e.g. make CPUFREQ_MHZ=2600)"
#endif

// Modes / ht types kept numerically identical to DRAMHiT.
constexpr uint32_t MODE_UNIFORM = 11;
constexpr uint32_t HT_CASHTPP = 3;

struct Configuration {
  uint32_t mode = 8;  // DRAMHiT's default; only MODE_UNIFORM is supported
  uint32_t ht_type = 0;
  uint32_t num_threads = 1;
  uint32_t numa_split = 4;  // accepted for compatibility; Thor has one node
  uint32_t ht_fill = 70;
  uint64_t ht_size = 1ull << 30;
  uint64_t insert_factor = 1;
  uint64_t read_factor = 1;
  uint32_t batch_len = 16;
  uint32_t find_queue_sz = 16;
  // Accepted so DRAMHiT command lines keep working, but unused here.
  double skew = 1.0;
  int64_t seed = 0;
  bool hw_pref = false;
  bool no_prefetch = false;
  bool drop_caches = true;
  // Check fill and found counts at the end.
  bool test = false;
};

extern Configuration config;

void parse_args(int argc, char **argv);
int run_uniform_test();

/// Argument for one hashtable operation (insert/find).
// NEVER USE KEY 0: it marks an empty slot.
struct InsertFindArgument {
  uint64_t key;
  uint64_t value;
  /// Returned as `FindResult::id`.
  uint32_t id;
};
using InsertFindArguments = std::span<InsertFindArgument>;

/// The result of a find operation on a hashtable.
struct FindResult {
  uint32_t id;
  uint64_t value;
};
/// {number of results, results}
using ValuePairs = std::pair<uint32_t, FindResult *>;

#define PLOGI(fmt, ...) fprintf(stderr, "[INFO] " fmt "\n", ##__VA_ARGS__)
#define PLOGE(fmt, ...) fprintf(stderr, "[ERROR] " fmt "\n", ##__VA_ARGS__)

#endif  // ARMHIT_DRAMHIT_HPP
