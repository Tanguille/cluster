# Home battery: predictive charging trial

Status on 2026-11-22: Omnibattery predictive charging runs untouched as a trial. Review from 2026-12-05.

Battery: Marstek Venus E 3.0, 5.12 kWh, 800 W both ways, plug-in. Controlled by the Omnibattery integration (v1.6.1) in Home Assistant.

## The question

Does grid charging at night pay on the fixed day/night tariff?

- Day 0.25009, night and weekend 0.19998, export 0.04635 EUR/kWh (Home Assistant values, 2026-11-18).
- Gain per kWh charged at night and used in the day: `efficiency x 0.25009 - 0.19998`.
- Break-even round-trip efficiency: 0.19998 / 0.25009 = 80 %.
- Weekend nights have no spread, so only the conversion loss remains.

Estimates, assuming about 2.8 kWh per night on about 185 deficit nights a year, of which about 130 are weekday nights (gain per the formula) and about 55 are weekend nights (loss only):

| Option | Arbitrage per year | Peak shaving per year |
|--------|--------------------|-----------------------|
| Predictive charging off | 0 | 0 |
| Top-up to the reserve only | 0 | +5 to 20 EUR |
| Full predictive, every night | about 0 at 85 % (+4.6 weekdays, -4.6 weekends), +6 EUR at 90 % | +5 to 25 EUR |
| Full predictive, Sunday to Thursday nights | +5 EUR at 85 %, +9 EUR at 90 % | +5 to 20 EUR |

The peak-shaving figures are guesses: the battery shaves at most 800 W, worth up to about 45 EUR a year at a capacity tariff of roughly 4 to 5 EUR per kW per month (not looked up). All options are within about 20 EUR a year of each other.

About 100 extra cycles a year is not a real cost against a rating of several thousand.

## How Omnibattery predictive charging behaves

- It is configured in the integration options, not by entities: Time Slot mode, window 22:05 to 06:55, every day. Saving the options reloads the integration for about a minute.
- It charges the forecast deficit: expected consumption minus solar forecast minus usable battery. Sun is about 13 kWh on a bright November day against 24 to 26 kWh of use, so in winter there is always a deficit.
- The "guaranteed minimum SOC" floor is a minimum, not a cap, and only acts while the main predictive switch is on. There is no setting for a maximum target.
- The planner uses a default round-trip efficiency of 85 %.
- It plans up to midnight. On the first night it did not charge between 22:05 and 00:00 with the battery at the reserve.
- Switching it on in the middle of the window needed a press on "Re-evaluate Predictive Charging" before it started.
- Observed 2026-11-21: with the floor at 50 % it charged past 50 % and re-planned to a 74 % target ("Energy deficit: 11.69 kWh").
- Charging respects the capacity-protection limit and backs off for the house first.

## Current setup

| Setting | Value |
|---------|-------|
| `switch.omnibattery_predictive_charging` | on |
| Night window | 22:05 to 06:55, all days |
| `switch.omnibattery_min_soc_floor_enabled` | on |
| `number.omnibattery_predictive_min_soc_floor` | follows the reserve |
| `number.omnibattery_capacity_protection_soc_threshold` (reserve) | 50 %, or 20 % before a day forecast above 15 kWh, set at sunset |
| `number.omnibattery_capacity_protection_limit` | max(2500 W, this month's peak) |
| Weekly full charge | on, Sunday |
| Charge limit (`number.marstek_venus_charging_cutoff_capacity`) | 100 %, left there on purpose |
| Discharge cutoff | 12 % |
| Backup port | off (it caused a standby drain of about 29 W) |

Automations:

- `automation.battery_capacity_protection_settings`: sets the peak limit, and at sunset sets the reserve and the floor together.
- `automation.battery_night_top_up_to_the_reserve`: switched off, kept as a fallback. It turns predictive charging on at 22:04 only while the battery is under the reserve, and off once the reserve is reached or at 06:55. Untested since it was simplified to start before the window.

## Findings so far

- The charge reading was well off before the first full charge: on 2026-11-22 going from "49 %" to 100 % took only 1.15 kWh. Efficiency and loss figures from before that date are unreliable.
- The lifetime round-trip figure of about 40 % in Home Assistant is almost all the old standby drain.
- On 2026-11-22 the fill to 100 % was the weekly full charge, not predictive charging.

## Solar surplus priority (open)

By rough value per kWh, assuming a heat pump efficiency of 3.5, a 90 % boiler, 10.5 kWh per m3 of gas and 85 % battery round trip: AC heating to the normal setpoint (0.44 EUR) beats the battery (0.17 to 0.21 EUR), which beats a resistive heater (0.13 EUR) and export (0.05 EUR). The current priority puts the WC heater before the battery. Not changed; measure where the surplus goes first.

## Review from 2026-12-05

Measure from the 2026-11-22 full charge:

1. Real round-trip efficiency.
2. Export on bright days.
3. The month's peak.

Then choose: keep it, limit the window to Sunday through Thursday nights, go back to the top-up automation, or switch it off.

## Related incident

On 2026-11-22 the battery was idle for about nine hours after a breaker trip, see [breaker trip investigation](breaker-trip-investigation.md). The integration had also followed the battery to a wrong address after the outage; address tracking is now off and an alert covers an unreachable battery.
