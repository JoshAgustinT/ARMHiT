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

# Find path prefetch. The insert path follows it: DOUBLE* -> DOUBLE,
# NONE -> NONE, everything else -> PREFETCHW.
PREFETCHES := DOUBLE DOUBLE_L2 L1 L2 L3 NTA L2STRM L3STRM NONE
ifeq ($(filter $(PREFETCH),$(PREFETCHES)),)
$(error PREFETCH must be one of: $(PREFETCHES))
endif
DEFS += -D$(PREFETCH)_PREFETCH
ifneq ($(filter $(PREFETCH),DOUBLE DOUBLE_L2),)
  DEFS += -DCAS_INSERT_PREFETCH_DOUBLE
else ifeq ($(PREFETCH),NONE)
  DEFS += -DCAS_INSERT_PREFETCH_NONE
else
  DEFS += -DCAS_INSERT_PREFETCH_PREFETCHW
endif

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

CXXFLAGS := -std=c++20 -O3 -g -Wall -mcpu=native $(sort $(DEFS))
LDFLAGS  := -pthread

SRCS := src/main.cpp src/args.cpp src/uniform_test.cpp
HDRS := src/dramhit.hpp src/cas_ht.hpp
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
