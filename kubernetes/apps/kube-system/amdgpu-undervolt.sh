#!/bin/sh
# R9700 power-profile controller, run by the amdgpu-undervolt DaemonSet (env comes from there).
# shellcheck shell=busybox
set -euo pipefail

VM=http://vmsingle-victoria-metrics.observability.svc.cluster.local:8428/api/v1/query
DWELL=900

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"
}

apply_fan_setting() {
    node="$1"
    want="$2"
    node_name=$(basename "$node")

    if [ ! -w "$node" ]; then
        log "skipping $node_name: not writable"
        return
    fi

    echo "$want" >"$node"
    echo c >"$node"
    got=$(tr -d '\000' <"$node" | awk 'NR==2{print $1; exit}')
    if [ "$got" = "$want" ]; then
        log "set $node_name to $want"
    else
        log "$node_name failed: wanted $want, got $got"
    fi
}

find_card() {
    for card in /host-sys/class/drm/card[0-9]/device; do
        [ "$(cat "$card/vendor" 2>/dev/null)" = 0x1002 ] && [ -w "$card/pp_od_clk_voltage" ] || continue
        for hw in "$card"/hwmon/hwmon*; do
            [ -w "$hw/power1_cap" ] && return 0
        done
    done
    return 1
}

# A rejected sysfs write (cap outside the driver's range, commit failing during a GPU reset) is
# logged, not fatal: under set -e it would crashloop the pod, and read_state then reports the
# mismatch so the next tick retries. Each returns 1 so apply_profile stops at the first failure.
set_cap() {
    echo "$(($1 * 1000000))" >"$hw/power1_cap" || {
        log "write power1_cap=$1W failed"
        return 1
    }
}

set_offset() {
    { echo "vo $1" >"$card/pp_od_clk_voltage" && echo c >"$card/pp_od_clk_voltage"; } ||
        {
            log "write vo=$1mV failed"
            return 1
        }
}

read_state() {
    cur_w=$(($(cat "$hw/power1_cap") / 1000000))
    cur_mv=$(tr -d '\000' <"$card/pp_od_clk_voltage" | awk '/OD_VDDGFX_OFFSET:/{getline; gsub(/[^0-9-]/,""); print; exit}')
}

# apply_profile NAME REASON: writes only what differs, lowering the cap before and raising it
# after the offset change, so a cap and offset pair that never passed the gate is never live.
# Runs every tick, so it also undoes anything else that changed the card.
apply_profile() {
    # shellcheck disable=SC2086 # each profile is a "W mV" pair
    case $1 in
    balanced) set -- "$1" "$2" $BALANCED_PROFILE ;;
    *) set -- "$1" "$2" $EFFICIENCY_PROFILE ;;
    esac
    read_state
    [ "$cur_w" = "$3" ] && [ "$cur_mv" = "$4" ] && return 0
    # A failed step ends the tick: continuing would raise the cap against an offset that never applied.
    if [ "$3" -lt "$cur_w" ]; then set_cap "$3" || return 0; fi
    if [ "$4" != "$cur_mv" ]; then set_offset "$4" || return 0; fi
    if [ "$3" -gt "$cur_w" ]; then set_cap "$3" || return 0; fi
    read_state
    if [ "$cur_w" = "$3" ] && [ "$cur_mv" = "$4" ]; then
        log "profile $1: $3W $4mV ($2)"
    else
        log "profile $1 failed: wanted $3W $4mV, got $cur_w W $cur_mv mV"
    fi
}

# Instant query, prints the single value or nothing. PromQL here must not contain + & or %.
q() {
    { wget -qO- -T 10 --post-data "query=$1" "$VM" 2>/dev/null || true; } |
        sed -n 's/.*"value":\[[^,]*,"\([^"]*\)"\].*/\1/p'
}

# True when $1 is a number and $1 $2 $3 holds; missing data is never true.
cmp() {
    [ -n "$1" ] && awk -v a="$1" -v b="$3" "BEGIN{exit !(a $2 b)}"
}

# A malformed pair would only fail inside the loop under set -u; refuse it up front instead.
for pair in "$EFFICIENCY_PROFILE" "$BALANCED_PROFILE"; do
    if ! echo "$pair" | grep -Eq '^[0-9]+ -?[0-9]+$'; then
        log "invalid profile '$pair': want 'W mV', e.g. '210 -100'"
        exit 1
    fi
done

until find_card; do
    log "waiting for a writable AMD GPU"
    sleep 5
done
echo manual >"$card/power_dpm_force_performance_level"
apply_fan_setting "$card/gpu_od/fan_ctrl/fan_target_temperature" "$FAN_TARGET_TEMP_C"
apply_fan_setting "$card/gpu_od/fan_ctrl/acoustic_target_rpm_threshold" "$ACOUSTIC_TARGET_RPM"
apply_fan_setting "$card/gpu_od/fan_ctrl/acoustic_limit_rpm_threshold" "$ACOUSTIC_LIMIT_RPM"

apply_profile efficiency startup
profile=efficiency
changed=$(date +%s)
solar=0
ups_block=1

# Balanced while 200W solar surplus is exported (5 min to set, 5 min of 100W import to
# clear), 15 min dwell. UPS danger (85% over 5 min, OB/LB/OVER) or missing data forces
# efficiency at once; it lifts after 10 min below 75%, then the dwell applies.
while sleep 30; do
    load5=$(q 'max(max_over_time(network_ups_tools_ups_load[5m]))')
    load10=$(q 'max(max_over_time(network_ups_tools_ups_load[10m]))')
    ups_age=$(q 'time() - max(timestamp(network_ups_tools_ups_load))')
    ups_bad=$(q 'max(network_ups_tools_ups_status{flag=~"OB|LB|OVER"})')
    p1_max=$(q 'max(max_over_time(hass_sensor_power_w{job="homeassistant",entity="sensor.p1_meter_power"}[5m]))')
    p1_min=$(q 'max(min_over_time(hass_sensor_power_w{job="homeassistant",entity="sensor.p1_meter_power"}[5m]))')
    p1_age=$(q 'time() - max(timestamp(hass_sensor_power_w{job="homeassistant",entity="sensor.p1_meter_power"}))')

    force=0
    if ! cmp "$ups_age" '<' 180 || cmp "$load5" '>=' 85 || ! cmp "$ups_bad" '==' 0; then
        force=1
        ups_block=1
    elif [ "$ups_block" = 1 ] && cmp "$load10" '<' 75; then
        ups_block=0
    fi

    if ! cmp "$p1_age" '<' 180; then
        force=1
        solar=0
    elif cmp "$p1_max" '<=' -200; then
        solar=1
    elif cmp "$p1_min" '>=' 100; then
        solar=0
    fi

    # A bench run owns the card while it keeps /tmp/hold fresh (kubectl exec touch), but
    # UPS danger or missing data still forces efficiency through the hold.
    if [ "$force" = 0 ] && [ -n "$(find /tmp/hold -mmin -60 2>/dev/null)" ]; then
        continue
    fi

    want=efficiency
    [ "$solar" = 1 ] && [ "$ups_block" = 0 ] && want=balanced
    now=$(date +%s)
    if [ "$want" != "$profile" ] && { [ "$force" = 1 ] || [ $((now - changed)) -ge "$DWELL" ]; }; then
        profile=$want
        changed=$now
    fi
    apply_profile "$profile" "solar=$solar ups_block=$ups_block force=$force load5=$load5 p1=$p1_min..$p1_max"
done
