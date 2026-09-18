# Home Assistant: solar self-consumption

_State as of 2026-09-18. HA 2026.9.2 at 192.168.0.16, edited through the `tanguille-site` MCP (`homeassistant_ha_*` tools). No battery, no EV, no heat pump other than the bedroom split unit. Everything here lives in HA storage, not in this repo; this file is the map and the reasoning._

## Measured baseline (5 weeks to 2026-09-18)

| Metric | Value | Source |
|---|---|---|
| PV | 80–133 kWh/week | `sensor.sb3_6_1av_40_652_pv_gen_meter`, `ha_get_history statistics period=week change` |
| Grid import | 79–108 kWh/week | `sensor.p1_meter_energy_import` |
| Grid export | 37–72 kWh/week | `sensor.p1_meter_energy_export` |
| Self-consumption | ~45–60 kWh/week | PV − export |
| Total consumption | ~135–160 kWh/week (~20 kWh/day) | import + self-consumption |
| Night floor | ~500 W | `sensor.p1_meter_power` hourly min 03:00–06:00 on 2026-09-17/18: 496–512 W |
| Gas | 1.3–1.9 m³/week, nearly all DHW | `sensor.gas_meter_gas`, `sensor.boiler_dhw_energy` 13–19 kWh/week |
| Capacity-tariff peak | 2.9 kW | `sensor.p1_meter_peak_demand_current_month` |
| Burner cycling | ~8 starts per burner hour | `sensor.boiler_burner_starts` 152258 / `sensor.boiler_total_heat_operating_time` 1145671 min |

Takeaway: consumption is flat base load. All shiftable appliance load together is ~5–8 kWh/**week**; the 500 W floor is 4.4 MWh/**year**. The base-load audit (what the cluster, NAS, switches and standby TVs draw) is the biggest open lever and is not automated.

## PV plant

- SMA SB3.6-1AV-40 (`sensor.sb3_6_1av_40_652_*`), AC-limited at 3.68 kW. Strings A and B are identical and peak in the same hour, so one south-facing array; clips 11:00–17:00 on clear September days and did 22.9 kWh on 2026-09-18.
- **Forecast.Solar** (core, free, no key), config entry `01M2V332QBQ4R8GBRT5HDPG59J`: lat/lon from `zone.home`, plane subentry `01M2V332QBH096FRXSFFJREHHB` = 35° / azimuth 180 / **6000 W** (estimated from the clip width; correct it if the real kWp is known), option `inverter_size` 3680. Attached to the Energy dashboard solar source. Entities: `sensor.energy_production_today`, `_today_remaining`, `_tomorrow`, `sensor.power_production_now`, `sensor.power_highest_peak_time_today`. On 2026-09-18 it forecast 15.9 kWh against 22.9 actual, so it runs conservative; calibrate kWp after a few clear days.

## Signals

- `input_boolean.solar_surplus_stable`: 10-min average P1 net power < −100 W on / > 0 W off (`automation.solar_surplus_stable_helper`). The reactive "we are exporting" flag everything hangs off.
- `input_number.solar_good_day_threshold` (12 kWh): the one tuning knob for "is today/tomorrow a solar day".
- `sensor.p1_meter_average_demand`: the running 15-min average the capacity tariff is billed on.
- `sensor.thermostat_damped_outdoor_temperature`: EMS-ESP damped outdoor temp, used for season gating.

## Automations

| Automation | Trigger | Effect | Guards |
|---|---|---|---|
| Solar forecast morning briefing | 07:30 | Phone push (s24) with expected kWh + peak hour | forecast today ≥ threshold |
| Dishwasher on solar surplus | surplus on, remote start on, `power_production_now` change, 10:00 | `select.dishwasher_active_program` → Eco 50 | armed, ready, door closed, surplus on, PV now ≥ 2 kW, ≥ 3 kWh left, 10:00–16:00, peak guard, stacking lock |
| Dishwasher solar fallback | 90 min after `power_highest_peak_time_today` | PV ≥ 1 kW → start; else wait for tomorrow if tomorrow ≥ threshold and armed < 18 h ago; else start anyway (never at night) | armed, ready, door closed, peak guard (push if blocked) |
| Washer on solar surplus | surplus on, Smart Control on, `power_production_now` change, 10:00 | `select.washer` → run | armed, idle, surplus on, PV now ≥ 2 kW, ≥ 2 kWh left, 10:00–16:00, peak guard, stacking lock |
| Washer solar fallback | 90 min after peak | same decision as the dishwasher, wait for tomorrow only if armed < 6 h ago | armed, idle, peak guard |
| Wet appliance armed stamp | remote start / Smart Control → on | stamps `input_datetime.dishwasher_armed_at` / `washer_armed_at` | |
| Solar Excess Climate Comfort (pre-existing) | surplus on/off | bedroom Better Thermostat 21–23 heat_cool band; away setback when surplus ends and nobody home | bedroom automation enabled, override timer idle |
| Bedroom heat pump on solar surplus | surplus on | bedroom band 22–24 **and** bedroom TRV `climate.radiator_valve_1` parked at 5 °C; both restored when surplus ends or after 8 h | damped outdoor 3–14 °C (below 3 °C the split unit defrosts), peak guard (+1 kW), stacking lock, bedroom guards |
| Solar end-of-day bedroom bank | forecast remaining < 2 kWh | thermal bank: cool to 20 if outdoor > 22, heat to 23 if outdoor < 14, nothing in between; band back to 21–23 when surplus ends / 4 h | surplus on, bedroom in heat_cool, `sensor.home_tanguille_distance` < 100 km |
| AC Power Guard (pre-existing) | surplus off | IR AC off | |
| Capacity tariff interlock | avg demand > 2.2 kW | WC + tech-cave resistance heaters off until avg < 1.5 kW (max 30 min), then back to heat | no surplus |
| 🔥 Advanced Heating Control Main | AHC 5.5.7 blueprint | gas heating comfort 21 / eco 16, schedule + presence + proximity | `input_force_eco_temperature` was **removed** 2026-09-18 (see below) |

### How loading the appliances works (flat tariff)

The tariff is flat: import costs the same at every hour, injection pays little, and the capacity tariff bills the month's highest 15-min import. So the only two things worth optimising are PV self-use and never adding a new 15-min peak. Night starts are the worst option (100 % import plus a 2 kW heating burst on a ~0.9 kW base ≈ 2.9 kW, which is what the current month peak looks like).

Both appliances are "arm and forget": load it, arm it, the automations pick the moment.

- **Dishwasher**: switch it on, close the door, press Remote Start (`binary_sensor.dishwasher_remote_start` on). **Washer**: load it, enable Smart Control (`binary_sensor.washer_remote_control` on; Samsung drops it after every cycle so an unloaded machine can never be started).
- **Instant choice (live)**: inside 10:00–16:00 it starts when `solar_surplus_stable` is on **and** Forecast.Solar `power_production_now` ≥ 2 kW, so the heating burst is mostly covered by PV rather than by the +100 W the surplus flag alone proves.
- **Day choice (forecast)**: 90 min after the forecast peak hour, an armed load that hasn't started is handled by the fallback: PV still ≥ 1 kW → start now; otherwise wait for tomorrow only if tomorrow's forecast ≥ `input_number.solar_good_day_threshold` and the load is young enough (dishwasher armed < 18 h ago, washer < 6 h ago); otherwise start now, at the day's PV maximum, because there is no better hour under a flat tariff.
- **Peak guard** (all starts, replaces the old fixed 2.2 kW gate): `p1_meter_average_demand + 2000 W ≤ max(p1_meter_peak_demand_current_month, 2500 W)`. A start may never raise this month's peak above the 2.5 kW floor or the peak already paid for. During PV the import average is ~0 so it always passes. If it blocks a fallback start, you get a push instead.
- **Stacking lock**: `input_datetime.wet_appliance_last_start`; no dishwasher, washer or AC-on-surplus start within 30 min of another.

## Boiler settings changed 2026-09-18

| Entity | Before | After | Why |
|---|---|---|---|
| `number.thermostat_hc1_design_temperature` | 75 | 65 | top of the weather-compensated heating curve (flow at −10 °C design outdoor) |
| `number.thermostat_hc1_max_flow_temperature` | 75 | 65 | hard ceiling on flow temp |

Reason: condensing only happens with return water below ~55 °C. At 75 °C flow the return is ~57 °C exactly when the most gas is burnt; at 65 °C it is ~45–50 °C all season. Lower flow also means longer, lower-modulation burns instead of the current ~8 starts per burner hour. 65 is the conservative first step, not the optimum; the optimum is the lowest flow at which rooms still reach setpoint on the coldest day.

**Follow-up procedure:** during the first spell with damped outdoor ≤ 0 °C, check `climate.thermostat_hc1` reaches setpoint. With margin → try 60 and repeat. Without → back to 70. Watch `sensor.boiler_burner_starts` per operating hour as the cycling metric. Untouched on purpose: `number.boiler_heating_temperature` 84 (boiler max), DHW 60 °C (lower needs disinfection runs that cost more than they save), disinfection off, `dhw_priority` on, `dhw_charge_optimization` on.

Why the house-wide hc1 force-eco on surplus was dropped: the AC only heats the bedroom, so forcing the whole house to 16 °C during a winter surplus lets every other room cool and the boiler reheats it in the evening at high flow temp, non-condensing, with extra burner starts. Net gas ≈ 0 and comfort loss. Replaced by closing only the bedroom TRV while the AC runs.

## Advice not implemented (Fable, 2026-09-18)

Ranked by expected value; € figures assume ~€0.30/kWh import, €0.04 injection, ~€0.11/kWh gas, €45/kW/yr capacity with a 2.5 kW floor. Check against the actual contract.

1. **Base-load audit** (only item likely > €100/yr): subtract known items from the 500 W night floor. HA-side candidates: air cleaner on `switch.smart_plug_2` only when someone is home, TV standby cut-off on metered plugs, PC stays WoL-only.
2. **Batch compute in the PV clip window**: GPU benches / builds / embedding jobs (1–2 kWh per 4 h run) are the biggest shiftable load. Publish `solar_surplus_stable` via MQTT/webhook for the cluster scheduler to read.
3. Resistance heaters on surplus: marginal (€5–10/yr), heating season only, WC only, last in line after the AC. Not implemented.
4. Capacity tariff beyond the 2.5 kW floor: nothing to gain; the interlock is defensive only.
5. DHW off/eco while `input_boolean.everyone_away_stable`, `switch.boiler_dhw_one_time_charging` on return: ~€10/yr. Not implemented.

Rejected as bad ideas: DHW below 55 °C; enabling disinfection at a 60 °C setpoint; replacing the surplus trigger with a fixed peak-time delayed start (loses on cloudy days); cool-mode banking on mild evenings; tech-cave heater as a surplus sink (it has its own thermostat).

## Known broken / open

- **WashData** (`ha_washdata`, entries `01KZM4RRV3FD1DGJH87FX597BE` washer, `01KZM4SYFNRNM26QCWWP8ZE7C9` dishwasher): both fed nonsense. Washer entry reads `sensor.washer_power` (SmartThings, `unavailable`, the whole `powerConsumptionReport` capability stopped reporting); dishwasher entry reads the Home Connect `energyforecast` **percentage** and shows it as watts. There is no metered plug on either appliance. Options: delete both entries and use the native state sensors, or add a Zigbee metering plug per appliance and point WashData at it.
- Restore-on-wait automations (bedroom heat pump, interlock) restore state at the end of a `wait_for_trigger`; an HA restart mid-wait leaves the bedroom TRV at 5 °C / heaters off until the next trigger.
- Forecast.Solar kWp is estimated; `_today_remaining` drives the dishwasher/washer/bank thresholds, so recalibrate after a few clear days.
- `automation.turn_off_gas_heating_when_solar_power` is off and dead (superseded); safe to delete.
- Capacity tariff interlock (heater pause) still triggers on a fixed 2.2 kW; a `numeric_state` trigger can't express `max(month peak, 2.5 kW)`. Fine while the month peak sits near 2.9 kW; revisit if the peak drops.
- Fallback timing uses `power_highest_peak_time_today` + 90 min, not a per-day computed window; on very overcast days that is still solar noon, which is the least-bad hour.
- Tuya cloud dropouts (bedroom temp sensor, tuinhuis plug), Zigbee panel range.
