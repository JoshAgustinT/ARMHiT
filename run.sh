#!/bin/bash
# DRAM bandwidth via UCF PMU, sampled every 1 s.
# BW (GB/s) per interval = local_dram_byte_{rd,wr} / 1e9
PERF=/usr/lib/linux-tools/6.8.0-146-generic/perf
U=nvidia_ucf_pmu_0

# sudo $PERF stat -a -I 1000 \
#   -e $U/cycles/,$U/local_dram_byte_rd/,$U/local_dram_byte_wr/,$U/any_byte_rd/,$U/any_byte_wr/ -- \
#   build/dramhit --mode 11 --ht-type 3 --num-threads 14 --ht-size 536870912 --ht-fill 70 \
#   --insert-factor 1 --read-factor 100 --batch-len 16 --find_queue 64 "$@"

sudo $PERF stat build/dramhit --mode 11 --ht-type 3 --num-threads 14 --ht-size 536870912 --ht-fill 70 \
  --insert-factor 1 --read-factor 100 --batch-len 16 --find_queue 64
