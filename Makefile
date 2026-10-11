# Build knobs mirror DRAMHiT's cmake -D options, passed as make variables:
#
#   make CPUFREQ_MHZ=2600 PREFETCH=DOUBLE BRANCH=simd UNIFORM_PROBING=ON
#
# There is no cache: knobs left out go back to the defaults below. The binary
# is rebuilt whenever the resulting flags change.

BUILD_DIR       ?= build
CXX             ?= g++
CPUFREQ_MHZ     ?=
PREFETCH        ?= DOUBLE
BRANCH          ?= branched
BUCKETIZATION   ?= OFF
UNIFORM_PROBING ?= OFF

ifeq ($(strip $(CPUFREQ_MHZ)),)
$(error CPUFREQ_MHZ is required, e.g. make CPUFREQ_MHZ=2600)
endif
DEFS := -DCPUFREQ_MHZ=$(CPUFREQ_MHZ) -DREAD_BEFORE_CAS

# Prefetch scheme. Each name sets the PRFM ops of the two paths:
#   find:   FIND_PRFM when queued (+ FIND_PRFM_NEXT on the bucket 8 entries
#           before dequeue, for doubles)
#   insert: INSERT_PRFM when queued (+ INSERT_PRFM_NEXT 8 before dequeue)
# Singles X: find PLD X, insert PSTL1KEEP (prefetchw) when queued.
# Doubles X_L1: find PLD X + PLDL1KEEP, insert PLD X + PSTL1KEEP.
# DOUBLE is DRAMHiT's scheme: find L3 + L1, insert L2 + PSTL1KEEP.
LEVELS     := L1 L2 L3 L1STRM L2STRM L3STRM
PREFETCHES := NONE DOUBLE $(LEVELS) $(addsuffix _L1,$(filter-out L1,$(LEVELS)))
ifeq ($(filter $(PREFETCH),$(PREFETCHES)),)
$(error PREFETCH must be one of: $(PREFETCHES))
endif
# L2STRM -> pldl2strm, L3 -> pldl3keep
pld = pld$(if $(findstring STRM,$(1)),$(subst STRM,,$(subst L,l,$(1)))strm,$(subst L,l,$(1))keep)

ifeq ($(PREFETCH),DOUBLE)
  DEFS += -DFIND_PRFM=pldl3keep -DFIND_PRFM_NEXT=pldl1keep
  DEFS += -DINSERT_PRFM=pldl2keep -DINSERT_PRFM_NEXT=pstl1keep
else ifneq ($(filter %_L1,$(PREFETCH)),)
  FIRST := $(call pld,$(PREFETCH:_L1=))
  DEFS += -DFIND_PRFM=$(FIRST) -DFIND_PRFM_NEXT=pldl1keep
  DEFS += -DINSERT_PRFM=$(FIRST) -DINSERT_PRFM_NEXT=pstl1keep
else ifneq ($(PREFETCH),NONE)
  DEFS += -DFIND_PRFM=$(call pld,$(PREFETCH)) -DINSERT_PRFM=pstl1keep
endif
DEFS += -DPREFETCH_NAME=$(PREFETCH)

ifeq ($(BRANCH),simd)
  DEFS += -DCAS_SIMD -DBUCKETIZATION
else ifneq ($(BRANCH),branched)
  $(error BRANCH must be simd or branched)
endif

ifeq ($(BUCKETIZATION),ON)
  DEFS += -DBUCKETIZATION
endif
ifeq ($(UNIFORM_PROBING),ON)
  DEFS += -DUNIFORM_HT_SUPPORT
endif

CXXFLAGS := -std=c++20 -O3 -g -Wall -mcpu=native -Isrc $(sort $(DEFS))
LDFLAGS  := -pthread

SRCS := src/main.cpp src/args.cpp src/tests/uniform_test.cpp
HDRS := src/dramhit.hpp src/hashtables/cas_ht.hpp
BIN  := $(BUILD_DIR)/dramhit
FLAGS_STAMP := $(BUILD_DIR)/.flags

# Rewrite the stamp only when the flags change, so a knob change rebuilds.
$(shell mkdir -p $(BUILD_DIR); echo '$(CXXFLAGS)' | cmp -s - $(FLAGS_STAMP) || echo '$(CXXFLAGS)' > $(FLAGS_STAMP))

.PHONY: all clean
all: $(BIN)

$(BIN): $(SRCS) $(HDRS) $(FLAGS_STAMP) Makefile
	$(CXX) $(CXXFLAGS) $(SRCS) -o $@ $(LDFLAGS)

clean:
	rm -rf $(BUILD_DIR)
