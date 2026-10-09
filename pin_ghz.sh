#!/usr/bin/env bash
# Pin all CPU cores to a fixed frequency (default: cpuinfo_max_freq, 2.601 GHz on Thor)
# and the memory clock (EMC, devfreq "bwmgr", 4266 MHz on Thor) and GPU clocks
# (devfreq "gpu-gpc-0" 1386 MHz, "gpu-nvd-0" 1557 MHz at the 120W power mode) to
# their max for stable benchmarking, and restore the previous settings afterwards.
# Devfreq clocks are pinned the way jetson_clocks does it: min_freq = max_freq
# (max_freq is the cap the power mode sets, so the pinned clock follows it).
#
# Usage: ./pin_ghz.sh on [freq_khz]   # pin (e.g. ./pin_ghz.sh on 2600000)
#        ./pin_ghz.sh off             # restore saved governor/min/max and devfreq min/max
#        ./pin_ghz.sh status
set -euo pipefail

CPUFREQ=/sys/devices/system/cpu/cpufreq
STATE=/tmp/pin_ghz.state
DEVFREQ=/sys/class/devfreq
# label devfreq-name: the label names the saved-state file /tmp/pin_ghz.<label>.state
DEVICES=("emc bwmgr" "gpc gpu-gpc-0" "nvd gpu-nvd-0")

if [[ $EUID -ne 0 && "${1:-}" != "status" ]]; then
    exec sudo "$0" "$@"
fi

status() {
    for p in "$CPUFREQ"/policy*; do
        printf "%-9s cpus=%-5s gov=%-11s min=%-8s max=%-8s cur=%s\n" \
            "$(basename "$p")" "$(cat "$p/related_cpus" | tr ' ' ',')" \
            "$(cat "$p/scaling_governor")" "$(cat "$p/scaling_min_freq")" \
            "$(cat "$p/scaling_max_freq")" "$(cat "$p/scaling_cur_freq")"
    done
    for dev in "${DEVICES[@]}"; do
        read -r label name <<< "$dev"
        d=$DEVFREQ/$name
        [[ -d $d ]] || continue
        printf "%-9s %-10s gov=%-11s min=%-10s max=%-10s cur=%s\n" "$label" "" \
            "$(cat "$d/governor")" "$(cat "$d/min_freq")" \
            "$(cat "$d/max_freq")" "$(cat "$d/cur_freq")"
    done
}

case "${1:-}" in
on)
    # Only save state if not already pinned, so a double "on" doesn't clobber the original.
    if [[ ! -f $STATE ]]; then
        for p in "$CPUFREQ"/policy*; do
            echo "$(basename "$p") $(cat "$p/scaling_governor") $(cat "$p/scaling_min_freq") $(cat "$p/scaling_max_freq")"
        done > "$STATE"
    fi
    for p in "$CPUFREQ"/policy*; do
        f=${2:-$(cat "$p/cpuinfo_max_freq")}
        echo performance > "$p/scaling_governor"
        # Order matters: raise max before min, lower min before max.
        echo "$f" > "$p/scaling_max_freq"
        echo "$f" > "$p/scaling_min_freq"
        echo "$f" > "$p/scaling_max_freq"
    done
    for dev in "${DEVICES[@]}"; do
        read -r label name <<< "$dev"
        d=$DEVFREQ/$name
        [[ -d $d ]] || continue
        state=/tmp/pin_ghz.$label.state
        [[ -f $state ]] || echo "$(cat "$d/min_freq") $(cat "$d/max_freq")" > "$state"
        cat "$d/max_freq" > "$d/min_freq"
    done
    echo "Pinned CPUs to ${2:-max} kHz, EMC/GPU clocks to their max"
    status
    ;;
off)
    if [[ -f $STATE ]]; then
        while read -r pol gov min max; do
            p=$CPUFREQ/$pol
            echo "$gov" > "$p/scaling_governor"
            echo "$min" > "$p/scaling_min_freq"
            echo "$max" > "$p/scaling_max_freq"
        done < "$STATE"
        rm -f "$STATE"
    else
        for p in "$CPUFREQ"/policy*; do
            echo schedutil > "$p/scaling_governor"
            echo "$(cat "$p/cpuinfo_min_freq")" > "$p/scaling_min_freq"
            echo "$(cat "$p/cpuinfo_max_freq")" > "$p/scaling_max_freq"
        done
    fi
    for dev in "${DEVICES[@]}"; do
        read -r label name <<< "$dev"
        d=$DEVFREQ/$name
        [[ -d $d ]] || continue
        state=/tmp/pin_ghz.$label.state
        if [[ -f $state ]]; then
            read -r min max < "$state"
            rm -f "$state"
        else
            # No saved state: only release the pin. max_freq is left alone,
            # since the power mode may cap it below the top available frequency.
            min=$(tr ' ' '\n' < "$d/available_frequencies" | grep . | sort -n | head -1)
            max=$(cat "$d/max_freq")
        fi
        # Lower min before max, as for the CPUs.
        echo "$min" > "$d/min_freq"
        echo "$max" > "$d/max_freq"
    done
    echo "Restored"
    status
    ;;
status)
    status
    ;;
*)
    echo "Usage: $0 on [freq_khz] | off | status" >&2
    exit 1
    ;;
esac
