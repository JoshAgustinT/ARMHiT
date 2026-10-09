// Uniform test (DRAMHiT --mode 11).
//
// All threads share one CASHashTable. Each thread touches its slice of the
// table, then inserts its keys insert_factor times and finds them
// read_factor times. Each phase is timed from the barrier every thread starts
// at to the barrier every thread finishes at.
//
// Keys: with N = ht_size * ht_fill / 100 keys and T threads, thread t owns
// key indexes [t * N/T, (t+1) * N/T) (the last thread also takes the
// remainder). Index i is key (i + 1) * knuth: multiplying by an odd constant
// is a bijection on 64-bit integers, so keys are unique and never 0 (0 marks
// an empty slot).
//
// --sequential: index i is key i + 1 and the table puts it in cache line i,
// counted from the line the first hashed key would land in. Each thread then
// walks its own run of consecutive lines, one key per line.
//
// When run by scripts/common.py with perf, ARMHIT_PERF_CTL / ARMHIT_PERF_ACK name
// perf's --control fifos and perf counts only while a phase runs.
// ARMHIT_PERF_GAP_MS is how long to pause between the phases; it must cover
// a whole perf sample so the two phases stay apart.
#include <fcntl.h>
#include <pthread.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <barrier>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <thread>
#include <vector>

#include "dramhit.hpp"
#include "hashtables/cas_ht.hpp"

using Clock = std::chrono::steady_clock;

constexpr uint64_t KNUTH = 11400714819323198485ULL;

static double insert_seconds, find_seconds;
static std::atomic<uint64_t> found;

static int perf_ctl = -1, perf_ack = -1, perf_gap_ms = 0;

// Sends "enable" / "disable" to perf and waits until perf has done it.
static void perf_control(const char *cmd) {
  if (perf_ctl < 0) return;
  char ack[16];
  if (write(perf_ctl, cmd, strlen(cmd)) < 0 || read(perf_ack, ack, sizeof(ack)) < 0)
    PLOGE("perf control '%s' failed", cmd);
}

static void open_perf_control() {
  const char *ctl = getenv("ARMHIT_PERF_CTL"), *ack = getenv("ARMHIT_PERF_ACK");
  if (!ctl || !ack) return;
  const char *gap = getenv("ARMHIT_PERF_GAP_MS");
  perf_gap_ms = gap ? atoi(gap) : 0;
  perf_ctl = open(ctl, O_WRONLY);
  perf_ack = open(ack, O_RDONLY);
  if (perf_ctl < 0 || perf_ack < 0) PLOGE("could not open perf control fifos");
}

// Calls fn(batch) for each batch_len keys of [first, first + count).
template <typename F>
static void for_each_batch(uint64_t first, uint64_t count, F fn) {
  std::vector<InsertFindArgument> batch(config.batch_len);
  for (uint64_t i = 0; i < count; i += config.batch_len) {
    uint32_t n = std::min<uint64_t>(config.batch_len, count - i);
    for (uint32_t j = 0; j < n; j++) {
      uint64_t key = first + i + j + 1;
      if (!config.sequential) key *= KNUTH;
      batch[j] = {.key = key, .value = key, .id = (uint32_t)(i + j)};
    }
    fn(InsertFindArguments(batch.data(), n));
  }
}

static void worker(uint32_t tid, uint64_t num_keys, std::barrier<> &barrier) {
  uint32_t T = config.num_threads;
  uint64_t first = tid * (num_keys / T);
  uint64_t count = num_keys / T + (tid == T - 1 ? num_keys % T : 0);

  cpu_set_t cpus;
  CPU_ZERO(&cpus);
  CPU_SET(tid, &cpus);
  pthread_setaffinity_np(pthread_self(), sizeof(cpus), &cpus);

  // Each thread first-touches its slice, so page faults stay out of the timing.
  CASHashTable ht(config.ht_size, config.find_queue_sz);
  size_t bytes = ht.get_capacity() * sizeof(Item);
  size_t lo = tid * (bytes / T), hi = tid == T - 1 ? bytes : lo + bytes / T;
  for (size_t off = lo; off < hi; off += 4096) ((volatile char *)ht.hashtable)[off] = 0;

  // Insert phase.
  if (tid == 0) perf_control("enable");
  barrier.arrive_and_wait();
  auto start = Clock::now();
  for (uint64_t r = 0; r < config.insert_factor; r++) {
    for_each_batch(first, count, [&](auto batch) { ht.insert_batch(batch); });
    ht.flush_insert_queue();
  }
  barrier.arrive_and_wait();
  if (tid == 0) {
    insert_seconds = std::chrono::duration<double>(Clock::now() - start).count();
    perf_control("disable");
    std::this_thread::sleep_for(std::chrono::milliseconds(perf_gap_ms));
  }

  // Find phase.
  std::vector<FindResult> results(config.batch_len);
  ValuePairs vp{0, results.data()};
  uint64_t my_found = 0;
  if (tid == 0) perf_control("enable");
  barrier.arrive_and_wait();
  start = Clock::now();
  for (uint64_t r = 0; r < config.read_factor; r++) {
    for_each_batch(first, count, [&](auto batch) {
      vp.first = 0;
      ht.find_batch(batch, vp);
      my_found += vp.first;
    });
    // Each flush returns at most batch_len results.
    size_t still_queued;
    do {
      vp.first = 0;
      still_queued = ht.flush_find_queue(vp);
      my_found += vp.first;
    } while (still_queued > 0);
  }
  barrier.arrive_and_wait();
  if (tid == 0) {
    find_seconds = std::chrono::duration<double>(Clock::now() - start).count();
    perf_control("disable");
  }
  found += my_found;
}

int run_uniform_test() {
  uint32_t T = config.num_threads;
  uint64_t num_keys = config.ht_size * config.ht_fill / 100;
  open_perf_control();
  if (config.sequential) {
    CASHashTable::sequential = true;
    CASHashTable::seq_start = __crc32cd(0xffffffff, KNUTH);  // where key 1 * KNUTH lands
  }

  // Keeps the shared table alive until the fill is counted.
  CASHashTable ht(config.ht_size, config.find_queue_sz);
  PLOGI("capacity %lu slots, %lu keys, %lu per thread", ht.get_capacity(),
        num_keys, num_keys / T);

  std::barrier<> barrier(T);
  std::vector<std::thread> threads;
  for (uint32_t tid = 0; tid < T; tid++)
    threads.emplace_back(worker, tid, num_keys, std::ref(barrier));
  for (auto &t : threads) t.join();

  uint64_t fill = ht.get_fill();
  uint64_t insert_ops = num_keys * config.insert_factor;
  uint64_t find_ops = num_keys * config.read_factor;

  // DRAMHiT's stats block; the scripts parse "name : value". Cycles are wall
  // time at CPUFREQ_MHZ.
  uint64_t insert_cycles = insert_seconds * CPUFREQ_MHZ * 1e6;
  uint64_t find_cycles = find_seconds * CPUFREQ_MHZ * 1e6;
  printf(
      "\n"
      "============================================\n"
      "fill : %lu, capacity : %lu, fill_factor : %f\n"
      "global_find_cycle : %lu, find_ops : %lu, found : %lu\n"
      "global_insert_cycle : %lu, insert_ops : %lu\n"
      "insert_seconds : %f, find_seconds : %f\n"
      "set_cycles : %lu, get_cycles : %lu, set_mops : %.0f, get_mops : %.0f\n"
      "============================================\n",
      fill, ht.get_capacity(), (double)fill / ht.get_capacity(), find_cycles,
      find_ops, found.load(), insert_cycles, insert_ops, insert_seconds,
      find_seconds, insert_cycles * T / insert_ops, find_cycles * T / find_ops,
      insert_ops / insert_seconds / 1e6, find_ops / find_seconds / 1e6);
  fflush(stdout);

  if (!config.test) return 0;
  if (fill != num_keys) PLOGE("fill: %lu != expected_fill: %lu", fill, num_keys);
  if (found != find_ops) PLOGE("found: %lu != expected_found: %lu", found.load(), find_ops);
  if (fill != num_keys || found != find_ops) return 2;
  PLOGI("test passed");
  return 0;
}
