/// Compare-and-swap(CAS) with linear probing hashtable based off of
/// the folklore HT https://arxiv.org/pdf/1601.04017.pdf
/// Key and values are stored directly in the table.
/// CASHashtable is not parititioned, meaning that there will be
/// at max one instance of it. All threads will share the same
/// instance.
/// The original one is called the casht and the one we modified with
/// batching + prefetching though is called casht++.
///
/// ARM port of DRAMHiT's include/hashtables/cas_kht.hpp, trimmed to what the
/// uniform test uses: the data structures, init, insert_batch and find_batch
/// (plus the queue helpers they call). Only the plain pop/add batch loops are
/// kept, not DRAMHiT's hand-inlined fast paths. x86 bits were swapped for ARM ones:
///   _mm512_mask_cmpeq_epu64_mask -> bucket_cmp() (NEON, same mask layout)
///   _mm_crc32_u64                -> __crc32cd
///   __builtin_prefetch           -> PRFM (see prefetch_read/prefetch_insert)

#ifndef ARMHIT_CAS_HT_HPP
#define ARMHIT_CAS_HT_HPP

#include <arm_acle.h>
#include <arm_neon.h>
#include <sys/mman.h>

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <mutex>

#include "dramhit.hpp"

// ---------------------------------------------------------------------------
// Data structures (DRAMHiT's kvtypes.hpp)
// ---------------------------------------------------------------------------

struct KVPair {
  uint64_t key;
  uint64_t value;
};

struct ItemQueue {
  // key and value must come first: the CAS writes them into a slot as-is.
  uint64_t key;
  uint64_t value;
  uint32_t idx;
  uint32_t key_id;
#ifdef UNIFORM_HT_SUPPORT
  uint64_t key_hash;
#endif
};

// ARM stand-in for the AVX-512 compare used throughout cas_kht.hpp:
//   _mm512_mask_cmpeq_epu64_mask(KEYMSK, cacheline, _mm512_set1_epi64(key))
// A bucket is 8 uint64s {k0 v0 k1 v1 k2 v2 k3 v3}. Bit 2*s is set if key s
// equals `key`, so ctz(mask) is the uint64 offset of the key in the bucket and
// ctz(mask) >> 1 is its slot -- exactly as with KEYMSK = 0b01010101.
static inline uint8_t bucket_cmp(const uint64_t *bucket, uint64_t key) {
  uint64x2x2_t lo = vld2q_u64(bucket);      // val[0] = {k0, k1}
  uint64x2x2_t hi = vld2q_u64(bucket + 4);  // val[0] = {k2, k3}
  uint64x2_t key_vector = vdupq_n_u64(key);
  uint32x4_t eq = vcombine_u32(vmovn_u64(vceqq_u64(lo.val[0], key_vector)),
                               vmovn_u64(vceqq_u64(hi.val[0], key_vector)));
  const uint32x4_t bits = {1u << 0, 1u << 2, 1u << 4, 1u << 6};
  return (uint8_t)vaddvq_u32(vandq_u32(eq, bits));
}

struct alignas(16) Item {
  KVPair kvpair;

  inline bool is_empty() const { return this->kvpair.key == 0; }

  inline bool compare_key(const void *from) const {
    const KVPair *kvpair = reinterpret_cast<const KVPair *>(from);
    return this->kvpair.key == kvpair->key;
  }

  inline void update_cas(const ItemQueue *elem) {
    this->kvpair.value = elem->value;
  }

  inline uint64_t find(const ItemQueue *elem, uint64_t *retry,
                       ValuePairs &vp) {
    uint64_t found = !this->is_empty() && (this->kvpair.key == elem->key);
    *retry = !this->is_empty() && (this->kvpair.key != elem->key);

    if (found) {
      vp.second[vp.first].id = elem->key_id;
      vp.second[vp.first].value = this->kvpair.value;
      vp.first++;
    }

    return found;
  }

  inline uint64_t find_simd(const ItemQueue *elem, uint64_t *retry,
                            ValuePairs &vp) {
    uint64_t *bucket = (uint64_t *)this;
    uint8_t key_cmp = bucket_cmp(bucket, elem->key);

    *retry = 0;
    if (key_cmp > 0) {
      uint8_t idx = __builtin_ctz(key_cmp);
      vp.second[vp.first].id = elem->key_id;
      vp.second[vp.first].value = bucket[(idx + 1)];
      vp.first++;
      return 1;
    } else {
      // key not found
      uint8_t ept_cmp = bucket_cmp(bucket, 0);
      if (ept_cmp == 0) {
        *retry = 1;
      }
      return 0;
    }
  }
};

// ---------------------------------------------------------------------------
// ARM prefetches. PRFM picks a target level (L1/L2/L3) and a policy (KEEP =
// temporal, STRM = streaming) for loads (PLD) or stores (PST).
// ---------------------------------------------------------------------------
// The ops come from the Makefile (FIND_PRFM, ...), so PRFM expands its
// arguments before PRFM_ stringifies them.
#define PRFM(op, addr) PRFM_(op, addr)
#define PRFM_(op, addr) __asm__ volatile("prfm " #op ", [%0]" ::"r"(addr))

class CASHashTable {
 public:
  using KV = Item;
  using KVQ = ItemQueue;

  /// The global instance is shared by all threads.
  static inline KV *hashtable = nullptr;

  /// Sequential mode (--sequential): hash(key) = seq_start + (key - 1) * 4.
  static inline bool sequential = false;
  static inline uint64_t seq_start = 0;

  const static uint64_t CACHELINE_SIZE = 64;
  const static uint64_t KV_IN_CACHELINE = CACHELINE_SIZE / sizeof(KV);
  const static uint64_t KEYS_IN_CACHELINE_MASK =
      (CACHELINE_SIZE / sizeof(KV)) - 1;

  uint32_t find_queue_sz;
  uint32_t insert_queue_sz;
  uint32_t INSERT_QUEUE_SZ_MASK;
  uint32_t FIND_QUEUE_SZ_MASK;

#define PREFETCH_INSERT_NEXT_DISTANCE 8
#define PREFETCH_FIND_NEXT_DISTANCE 8

  CASHashTable(uint64_t c, uint32_t queue_sz)
      : find_head(0), find_tail(0), ins_head(0), ins_tail(0) {
    this->capacity = next_pow2(c);
    this->insert_queue_sz = this->find_queue_sz = queue_sz;

    if (queue_sz == 0 || (queue_sz & (queue_sz - 1)) != 0) {
      PLOGE("Queue size %u is not a power of 2", queue_sz);
      abort();
    }

    {
      const std::lock_guard<std::mutex> lock(ht_init_mutex);

      if (!this->hashtable) {
        this->hashtable = calloc_ht(this->capacity);
        PLOGI("Hashtable base: %p Hashtable size: %lu, %lu GB",
              (void *)this->hashtable, this->capacity,
              (unsigned long)((this->capacity * sizeof(KV)) >> 30));
        PLOGI("queue sz: %u, queue item sz: %zu", find_queue_sz, sizeof(KVQ));
      }
      this->ref_cnt++;
    }

    // Zeroed so the look-ahead prefetch never reads an uninitialized idx.
    this->insert_queue =
        (KVQ *)(aligned_alloc(64, insert_queue_sz * sizeof(KVQ)));
    this->find_queue = (KVQ *)(aligned_alloc(64, find_queue_sz * sizeof(KVQ)));
    memset(this->insert_queue, 0, insert_queue_sz * sizeof(KVQ));
    memset(this->find_queue, 0, find_queue_sz * sizeof(KVQ));
    this->FIND_QUEUE_SZ_MASK = this->find_queue_sz - 1;
    this->INSERT_QUEUE_SZ_MASK = this->insert_queue_sz - 1;
  }

  ~CASHashTable() {
    free(find_queue);
    free(insert_queue);

    // Deallocate the global hashtable if ref_cnt goes down to zero.
    {
      const std::lock_guard<std::mutex> lock(ht_init_mutex);
      this->ref_cnt--;
      if (this->ref_cnt == 0) {
        munmap(this->hashtable, alloc_size(this->capacity));
        this->hashtable = nullptr;
      }
    }
  }

  inline uint32_t get_insert_queue_sz() {
    return (ins_head - ins_tail) & INSERT_QUEUE_SZ_MASK;
  }

  inline void pop_insert_queue() {
    uint64_t retry = 0;
    do {
      prefetch_insert_next(this->ins_tail);
      retry = __insert_one(&this->insert_queue[this->ins_tail]);
      this->ins_tail++;
      this->ins_tail &= INSERT_QUEUE_SZ_MASK;
    } while ((retry));
  }

  // Each key pops (at least) one queued insert once the queue is full, then
  // joins the queue itself.
  void insert_batch(const InsertFindArguments &kp) {
    for (auto &data : kp) {
      if ((get_insert_queue_sz() >= INSERT_QUEUE_SZ_MASK)) {
        pop_insert_queue();
      }
      add_to_insert_queue(&data);
    }
  }

  void flush_insert_queue() {
    size_t curr_queue_sz = get_insert_queue_sz();
    while (curr_queue_sz > 0) {
      pop_insert_queue();
      curr_queue_sz--;
    }

    // all stores must be flushed.
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
  }

  size_t flush_find_queue(ValuePairs &vp) {
    size_t curr_queue_sz = get_find_queue_sz();
    while ((curr_queue_sz > 0) && (vp.first < config.batch_len)) {
      pop_find_queue(vp);  // gurantee to reduce curr_queue_sz
      curr_queue_sz--;
    }

    return curr_queue_sz;  // how many are still queued
  }

  inline size_t get_find_queue_sz() {
    return (this->find_head - this->find_tail) & FIND_QUEUE_SZ_MASK;
  }

  inline void pop_find_queue(ValuePairs &vp) {
    uint64_t retry = 0;
    do {
      prefetch_find_next(this->find_tail);
      retry = __find_one(&this->find_queue[this->find_tail], vp);
      this->find_tail++;
      this->find_tail &= FIND_QUEUE_SZ_MASK;

    } while ((retry));
  }

  // Same as insert_batch. Pops add their results to vp, at most one per key,
  // so vp needs room for kp.size() results.
  void find_batch(const InsertFindArguments &kp, ValuePairs &vp) {
    for (auto &data : kp) {
      if ((get_find_queue_sz() >= FIND_QUEUE_SZ_MASK)) {
        pop_find_queue(vp);
      }
      add_to_find_queue(&data);
    }
  }

  size_t get_fill() const {
    size_t count = 0;
    for (size_t i = 0; i < this->capacity; i++) {
      if (!this->hashtable[i].is_empty()) {
        count++;
      }
    }
    return count;
  }

  size_t get_capacity() const { return this->capacity; }

 private:
  /// Assure thread-safety in constructor and destructor.
  static inline std::mutex ht_init_mutex;
  /// Reference counter of the global `hashtable`.
  static inline uint32_t ref_cnt = 0;
  uint64_t capacity;

  KVQ *find_queue;
  KVQ *insert_queue;
  uint32_t find_head;
  uint32_t find_tail;
  uint32_t ins_head;
  uint32_t ins_tail;

  // HASHER=crc: CRC32C of the 8-byte key. In sequential mode, key k goes to
  // the bucket k - 1 buckets after seq_start, so consecutive keys fill
  // consecutive cache lines, one key per line.
  uint64_t hash(const void *k) {
    uint64_t key = *(const uint64_t *)k;
    if (sequential) return seq_start + (key - 1) * KV_IN_CACHELINE;
    return __crc32cd(0xffffffff, key);
  }

  static uint64_t next_pow2(uint64_t v) {
    uint64_t p = 1;
    while (p < v) p <<= 1;
    return p;
  }

  static size_t alloc_size(uint64_t capacity) {
    const size_t huge = 2ul << 20;
    return (capacity * sizeof(KV) + huge - 1) & ~(huge - 1);
  }

  // DRAMHiT's calloc_ht: an anonymous mapping, which the kernel zeroes. Uses
  // hugetlbfs pages if any are reserved, otherwise THP. Pages are faulted in
  // by whoever touches them first (see distribute_memory in the test).
  static KV *calloc_ht(uint64_t capacity) {
    size_t sz = alloc_size(capacity);
    void *addr = mmap(nullptr, sz, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS | MAP_HUGETLB, -1, 0);
    if (addr != MAP_FAILED) {
      PLOGI("allocated %zu bytes on hugetlb pages", sz);
      return (KV *)addr;
    }
    addr = mmap(nullptr, sz, PROT_READ | PROT_WRITE,
                MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (addr == MAP_FAILED) {
      PLOGE("mmap of %zu bytes failed", sz);
      exit(1);
    }
    madvise(addr, sz, MADV_HUGEPAGE);
    PLOGI("allocated %zu bytes on THP", sz);
    return (KV *)addr;
  }

  // Prefetches, set by PREFETCH= (see Makefile): FIND_PRFM / INSERT_PRFM
  // when a key is queued, and for the doubles FIND_PRFM_NEXT /
  // INSERT_PRFM_NEXT on the entry PREFETCH_*_NEXT_DISTANCE behind the tail.
  inline void prefetch_read(uint64_t idx) {
#if defined(FIND_PRFM)
    PRFM(FIND_PRFM, &this->hashtable[idx]);
#else
    (void)idx;
#endif
  }

  inline void prefetch_find_next(uint32_t tail) {
#if defined(FIND_PRFM_NEXT)
    uint32_t next_tail = (tail + PREFETCH_FIND_NEXT_DISTANCE) & FIND_QUEUE_SZ_MASK;
    PRFM(FIND_PRFM_NEXT, &this->hashtable[this->find_queue[next_tail].idx]);
#else
    (void)tail;
#endif
  }

  inline void prefetch_insert(uint64_t idx) {
#if defined(INSERT_PRFM)
    PRFM(INSERT_PRFM, &this->hashtable[idx]);
#else
    (void)idx;
#endif
  }

  inline void prefetch_insert_next(uint32_t tail) {
#if defined(INSERT_PRFM_NEXT)
    uint32_t next_tail =
        (tail + PREFETCH_INSERT_NEXT_DISTANCE) & INSERT_QUEUE_SZ_MASK;
    PRFM(INSERT_PRFM_NEXT, &this->hashtable[this->insert_queue[next_tail].idx]);
#else
    (void)tail;
#endif
  }

#if defined(CAS_SIMD)
  uint64_t __find_simd(KVQ *q, ValuePairs &vp) {
    uint64_t retry;
    size_t idx = q->idx;

    KV *curr_cacheline = &this->hashtable[idx];
    curr_cacheline->find_simd(q, &retry, vp);

    if (retry) {
#ifdef UNIFORM_HT_SUPPORT
      uint64_t old_hash = q->key_hash;
      uint64_t hash = this->hash(&old_hash);
      idx = hash & (this->capacity - 1);
#ifdef BUCKETIZATION
      idx = idx - (size_t)(idx & KEYS_IN_CACHELINE_MASK);
#endif
      this->find_queue[this->find_head].key_hash = hash;
#else
      idx += CACHELINE_SIZE / sizeof(KV);
      idx = idx & (this->capacity - 1);
#endif

      prefetch_read(idx);

      this->find_queue[this->find_head].key = q->key;
      this->find_queue[this->find_head].key_id = q->key_id;
      this->find_queue[this->find_head].idx = idx;
      this->find_head++;
      this->find_head &= FIND_QUEUE_SZ_MASK;
    }

    return retry;
  }
#endif

  uint64_t __find_branched(KVQ *q, ValuePairs &vp) {
    // hashtable idx where the data should be found
    size_t idx = q->idx;

  try_find:
    KV *curr = &this->hashtable[idx];
    uint64_t retry;
    curr->find(q, &retry, vp);

    if (retry) {
      // insert back into queue, and prefetch next bucket.
      // next bucket will be probed in the next run
      idx++;
      idx = idx & (this->capacity - 1);  // modulo

      // If idx still on a cacheline, keep looking until idx spill over
      if ((idx & KEYS_IN_CACHELINE_MASK) != 0) {
        goto try_find;
      }

#ifdef UNIFORM_HT_SUPPORT
      uint64_t old_hash = q->key_hash;
      uint64_t hash = this->hash(&old_hash);
      idx = hash & (this->capacity - 1);
#ifdef BUCKETIZATION
      idx = idx - (size_t)(idx & KEYS_IN_CACHELINE_MASK);
#endif
      this->find_queue[this->find_head].key_hash = hash;
#endif

      // key is at a different cacheline, prefetch and delay the find
      prefetch_read(idx);

      this->find_queue[this->find_head].key = q->key;
      this->find_queue[this->find_head].key_id = q->key_id;
      this->find_queue[this->find_head].idx = idx;
      this->find_head++;
      this->find_head &= FIND_QUEUE_SZ_MASK;
    }

    return retry;
  }

  uint64_t __find_one(KVQ *q, ValuePairs &vp) {
#if defined(CAS_SIMD)
    return __find_simd(q, vp);
#else
    return __find_branched(q, vp);
#endif
  }

  uint64_t __insert_branched(KVQ *q) {
    // hashtable idx at which data is to be inserted
    size_t idx = q->idx;
    KV *curr;

#if defined(CAS_SIMD) && defined(BUCKETIZATION)
    //  The intuition is, we load a snapshot of a cacheline of keys and see
    //  how far ahead we can skip into. It is okay to be outdated with the
    //  world, because, that just means we skip less than we could have. We can
    //  do this because keys are never deleted in the hashtable.

    // ex. We load a cacheline like this | - , - , 0, 0 |.
    //  The world can update the keys like this during operation | -, -, X, 0|
    // but it will never remove any of keys.

    uint64_t *bucket = (uint64_t *)&this->hashtable[idx];

    // Check of the keys exists,
    uint8_t key_cmp = bucket_cmp(bucket, q->key);
    if (key_cmp > 0) {
      uint8_t offset = __builtin_ctz(key_cmp);
      bucket[(offset + 1)] = q->value;
      return 0;
    }

    // Check for empty slot
    uint8_t ept_cmp = bucket_cmp(bucket, 0);
    if (ept_cmp != 0) {
      idx += (__builtin_ctz(ept_cmp) >> 1);  // |-, -, 0, 0|
    } else {
#ifdef UNIFORM_HT_SUPPORT
      uint64_t old_hash = q->key_hash;
      uint64_t hash = this->hash(&old_hash);
      idx = hash & (this->capacity - 1);
      idx = idx - (size_t)(idx & KEYS_IN_CACHELINE_MASK);
      this->insert_queue[this->ins_head].key_hash = hash;
#else
      idx += 4;
      idx = idx & (this->capacity - 1);
#endif

      prefetch_insert(idx);

      this->insert_queue[this->ins_head].key = q->key;
      this->insert_queue[this->ins_head].key_id = q->key_id;
      this->insert_queue[this->ins_head].value = q->value;
      this->insert_queue[this->ins_head].idx = idx;

      ++this->ins_head;
      this->ins_head &= INSERT_QUEUE_SZ_MASK;

      return 1;
    }
#endif

  try_insert:
    curr = &this->hashtable[idx];

    // we first check if key is 0.if we use cas,
    // it will request for exclusive state unneccesarrily.
#ifdef READ_BEFORE_CAS
    if (curr->is_empty())
#endif
      if (__sync_bool_compare_and_swap((__int128 *)curr, 0, *(__int128 *)q)) {
        return 0;
      }

    if (curr->compare_key(q)) {
      curr->update_cas(q);
      return 0;
    }

    idx++;
    idx = idx & (this->capacity - 1);  // modulo

    if ((idx & KEYS_IN_CACHELINE_MASK) != 0) {
      goto try_insert;
    }

#ifdef UNIFORM_HT_SUPPORT
    uint64_t old_hash = q->key_hash;
    uint64_t hash = this->hash(&old_hash);
    idx = hash & (this->capacity - 1);
#ifdef BUCKETIZATION
    idx = idx - (size_t)(idx & KEYS_IN_CACHELINE_MASK);
#endif
    this->insert_queue[this->ins_head].key_hash = hash;
#endif

    prefetch_insert(idx);

    this->insert_queue[this->ins_head].key = q->key;
    this->insert_queue[this->ins_head].key_id = q->key_id;
    this->insert_queue[this->ins_head].value = q->value;
    this->insert_queue[this->ins_head].idx = idx;

    this->ins_head++;
    this->ins_head &= INSERT_QUEUE_SZ_MASK;

    return 1;
  }

  uint64_t __insert_one(KVQ *q) { return __insert_branched(q); }

  void add_to_find_queue(const InsertFindArgument *key_data) {
    uint64_t hash = this->hash(&key_data->key);
    size_t idx = hash & (this->capacity - 1);

#ifdef BUCKETIZATION
    idx = idx - (size_t)(idx & KEYS_IN_CACHELINE_MASK);
#endif

    prefetch_read(idx);

    this->find_queue[this->find_head].idx = idx;
    this->find_queue[this->find_head].key = key_data->key;
    this->find_queue[this->find_head].key_id = key_data->id;

#ifdef UNIFORM_HT_SUPPORT
    this->find_queue[this->find_head].key_hash = hash;
#endif

    this->find_head++;
    this->find_head &= FIND_QUEUE_SZ_MASK;
  }

  inline void add_to_insert_queue(const InsertFindArgument *key_data) {
    uint64_t hash = this->hash(&key_data->key);
    size_t idx = hash & (this->capacity - 1);
#ifdef BUCKETIZATION
    idx = idx - (size_t)(idx & KEYS_IN_CACHELINE_MASK);
#endif

    prefetch_insert(idx);

    this->insert_queue[this->ins_head].idx = idx;
    this->insert_queue[this->ins_head].key = key_data->key;
    this->insert_queue[this->ins_head].key_id = key_data->id;
    this->insert_queue[this->ins_head].value = key_data->value;

#ifdef UNIFORM_HT_SUPPORT
    this->insert_queue[this->ins_head].key_hash = hash;
#endif

    this->ins_head++;
    this->ins_head &= INSERT_QUEUE_SZ_MASK;
  }
};

#endif  // ARMHIT_CAS_HT_HPP
