// Uniform test (DRAMHiT --mode 11).
//
// All threads share one CASHashTable. Each thread:
//   1. touches its slice of the table, so page faults stay out of the timing
//   2. inserts its keys, insert_factor times   (timed: set_mops)
//   3. finds its keys again, read_factor times  (timed: get_mops)
// Afterwards the main thread checks the fill and prints DRAMHiT's stats.
//
// Keys: with N = ht_size * ht_fill / 100 keys and T threads, thread t owns
// key indexes [t * N/T, (t+1) * N/T) (the last thread also takes the
// remainder), and index i maps to key hash_knuth(i + 1). Knuth's
// multiplicative hash is a bijection on 64-bit integers, so all keys are
// unique; the + 1 keeps key 0 (the empty-slot marker) out of the workload.
#include <pthread.h>
#include <sched.h>

#include <algorithm>
#include <barrier>
#include <chrono>
#include <thread>
#include <vector>

#include "cas_ht.hpp"
#include "dramhit.hpp"

static inline uint64_t hash_knuth(uint64_t x) {
  return x * 11400714819323198485ULL;
}

struct KeyRange {
  uint64_t start;  // first key index
  uint64_t count;
};

static KeyRange key_range(uint32_t tid, uint64_t num_keys) {
  uint32_t T = config.num_threads;
  KeyRange r{tid * (num_keys / T), num_keys / T};
  if (tid == T - 1) r.count += num_keys % T;
  return r;
}

// Puts keys [from, from + n) of the range into batch[0, n).
static void fill_batch(std::vector<InsertFindArgument> &batch, KeyRange keys,
                       uint64_t from, uint32_t n) {
  for (uint32_t j = 0; j < n; j++) {
    uint64_t key = hash_knuth(keys.start + from + j + 1);
    batch[j] = {.key = key, .value = key, .id = (uint32_t)(from + j)};
  }
}

static void insert_keys(CASHashTable &ht, KeyRange keys) {
  std::vector<InsertFindArgument> batch(config.batch_len);

  for (uint64_t i = 0; i < keys.count; i += config.batch_len) {
    uint32_t n = std::min<uint64_t>(config.batch_len, keys.count - i);
    fill_batch(batch, keys, i, n);
    ht.insert_batch(InsertFindArguments(batch.data(), n));
  }
  ht.flush_insert_queue();
}

// Returns how many of the keys were found.
static uint64_t find_keys(CASHashTable &ht, KeyRange keys) {
  std::vector<InsertFindArgument> batch(config.batch_len);
  std::vector<FindResult> results(config.batch_len);
  ValuePairs vp{0, results.data()};
  uint64_t found = 0;

  for (uint64_t i = 0; i < keys.count; i += config.batch_len) {
    uint32_t n = std::min<uint64_t>(config.batch_len, keys.count - i);
    fill_batch(batch, keys, i, n);
    vp.first = 0;
    ht.find_batch(InsertFindArguments(batch.data(), n), vp);
    found += vp.first;
  }

  // Finds still in the queue; each flush returns at most batch_len results.
  size_t still_queued;
  do {
    vp.first = 0;
    still_queued = ht.flush_find_queue(vp);
    found += vp.first;
  } while (still_queued > 0);

  return found;
}

static void pin_to_cpu(uint32_t cpu) {
  cpu_set_t set;
  CPU_ZERO(&set);
  CPU_SET(cpu, &set);
  if (pthread_setaffinity_np(pthread_self(), sizeof(set), &set))
    PLOGE("could not pin thread to cpu %u", cpu);
}

// Each thread first-touches its own slice of the shared table.
static void distribute_memory(uint32_t tid, const CASHashTable &ht) {
  volatile char *table = (volatile char *)CASHashTable::hashtable;
  size_t bytes = ht.get_capacity() * sizeof(Item);
  size_t slice = bytes / config.num_threads;
  size_t lo = tid * slice;
  size_t hi = (tid == config.num_threads - 1) ? bytes : lo + slice;
  for (size_t off = lo; off < hi; off += 4096) table[off] = 0;
}

// Results of one run, written by the worker threads.
struct RunStats {
  double insert_seconds = 0;
  double find_seconds = 0;
  std::vector<uint64_t> found;  // per thread
};

// Runs `phase` on every thread between two barriers. The time thread 0 sees
// from the first barrier to the second is how long the slowest thread took.
template <typename F>
static double timed_phase(std::barrier<> &barrier, F phase) {
  barrier.arrive_and_wait();
  auto start = std::chrono::steady_clock::now();
  phase();
  barrier.arrive_and_wait();
  return std::chrono::duration<double>(std::chrono::steady_clock::now() - start)
      .count();
}

static void worker(uint32_t tid, uint64_t num_keys, std::barrier<> &barrier,
                   RunStats &stats) {
  pin_to_cpu(tid);

  CASHashTable ht(config.ht_size, config.find_queue_sz);
  KeyRange keys = key_range(tid, num_keys);
  distribute_memory(tid, ht);

  double insert_seconds = timed_phase(barrier, [&] {
    for (uint64_t r = 0; r < config.insert_factor; r++) insert_keys(ht, keys);
  });

  uint64_t found = 0;
  double find_seconds = timed_phase(barrier, [&] {
    for (uint64_t r = 0; r < config.read_factor; r++) found += find_keys(ht, keys);
  });

  stats.found[tid] = found;
  if (tid == 0) {
    stats.insert_seconds = insert_seconds;
    stats.find_seconds = find_seconds;
  }
}

// Same block DRAMHiT prints; scripts/collect_data.py parses "name : value".
// "cycles" are wall time expressed at CPUFREQ_MHZ.
static void print_stats(uint64_t fill, uint64_t capacity, uint64_t insert_ops,
                        uint64_t find_ops, uint64_t found,
                        const RunStats &stats) {
  uint64_t T = config.num_threads;
  uint64_t insert_cycles = stats.insert_seconds * CPUFREQ_MHZ * 1e6;
  uint64_t find_cycles = stats.find_seconds * CPUFREQ_MHZ * 1e6;
  uint64_t set_cycles = insert_ops ? insert_cycles * T / insert_ops : 0;
  uint64_t get_cycles = find_ops ? find_cycles * T / find_ops : 0;
  uint64_t set_mops = insert_cycles ? CPUFREQ_MHZ * insert_ops / insert_cycles : 0;
  uint64_t get_mops = find_cycles ? CPUFREQ_MHZ * find_ops / find_cycles : 0;

  printf(
      "\n"
      "============================================\n"
      "fill : %lu, capacity : %lu, fill_factor : %f\n"
      "global_find_cycle : %lu, find_ops : %lu, found : %lu\n"
      "global_insert_cycle : %lu, insert_ops : %lu\n"
      "set_cycles : %lu, get_cycles : %lu, set_mops : %lu, get_mops : %lu\n"
      "============================================\n",
      fill, capacity, (double)fill / capacity, find_cycles, find_ops, found,
      insert_cycles, insert_ops, set_cycles, get_cycles, set_mops, get_mops);
  fflush(stdout);
}

int run_uniform_test() {
  uint32_t T = config.num_threads;
  uint64_t num_keys = config.ht_size * config.ht_fill / 100;

  // This handle keeps the shared table alive until the fill is counted.
  CASHashTable ht(config.ht_size, config.find_queue_sz);
  PLOGI("capacity %lu slots, %lu keys, %lu per thread", ht.get_capacity(),
        num_keys, num_keys / T);

  std::barrier<> barrier(T);
  RunStats stats;
  stats.found.resize(T);

  std::vector<std::thread> threads;
  for (uint32_t tid = 0; tid < T; tid++)
    threads.emplace_back(worker, tid, num_keys, std::ref(barrier),
                         std::ref(stats));
  for (auto &t : threads) t.join();

  uint64_t found = 0;
  for (uint64_t f : stats.found) found += f;
  uint64_t fill = ht.get_fill();
  uint64_t insert_ops = num_keys * config.insert_factor;
  uint64_t find_ops = num_keys * config.read_factor;

  print_stats(fill, ht.get_capacity(), insert_ops, find_ops, found, stats);

  if (config.test) {
    bool ok = true;
    if (fill != num_keys) {
      PLOGE("fill: %lu != expected_fill: %lu", fill, num_keys);
      ok = false;
    }
    if (found != find_ops) {
      PLOGE("found: %lu != expected_found: %lu", found, find_ops);
      ok = false;
    }
    if (!ok) return 2;
    PLOGI("test passed");
  }
  return 0;
}
