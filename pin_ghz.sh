#!/usr/bin/env bash
# Pin all CPU cores to a fixed frequency (default: cpuinfo_max_freq, 2.601 GHz on Thor)
# and the memory clock (EMC, devfreq "bwmgr") to its max (4266 MHz on Thor) for
# stable benchmarking, and restore the previous settings afterwards.
# The EMC is pinned the way jetson_clocks does it: min_freq = max_freq.
#
# Usage: ./pin_ghz.sh on [freq_khz]   # pin (e.g. ./pin_ghz.sh on 2600000)
#        ./pin_ghz.sh off             # restore saved governor/min/max and EMC min/max
#        ./pin_ghz.sh status
set -euo pipefail

CPUFREQ=/sys/devices/system/cpu/cpufreq
STATE=/tmp/pin_ghz.state
EMC=/sys/class/devfreq/bwmgr
EMC_STATE=/tmp/pin_ghz.emc.state

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
    if [[ -d $EMC ]]; then
        printf "%-9s %-10s gov=%-11s min=%-10s max=%-10s cur=%s\n" emc "" \
            "$(cat "$EMC/governor")" "$(cat "$EMC/min_freq")" \
            "$(cat "$EMC/max_freq")" "$(cat "$EMC/cur_freq")"
    fi
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
    if [[ -d $EMC ]]; then
        [[ -f $EMC_STATE ]] || echo "$(cat "$EMC/min_freq") $(cat "$EMC/max_freq")" > "$EMC_STATE"
        cat "$EMC/max_freq" > "$EMC/min_freq"
    fi
    echo "Pinned CPUs to ${2:-max} kHz, EMC to $(cat "$EMC/max_freq" 2>/dev/null) Hz"
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
    if [[ -d $EMC ]]; then
        if [[ -f $EMC_STATE ]]; then
            read -r min max < "$EMC_STATE"
            rm -f "$EMC_STATE"
        else
            # No saved state: the full range of available frequencies.
            min=$(tr ' ' '\n' < "$EMC/available_frequencies" | grep . | sort -n | head -1)
            max=$(tr ' ' '\n' < "$EMC/available_frequencies" | grep . | sort -n | tail -1)
        fi
        # Lower min before max, as for the CPUs.
        echo "$min" > "$EMC/min_freq"
        echo "$max" > "$EMC/max_freq"
    fi
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
