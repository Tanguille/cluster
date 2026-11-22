# Breaker trip investigation (networking room goes dark)

Status on 2026-11-22: cause not found, elimination test on the garage running, alerting in place.

## Symptom

The 30 mA differential at the head of the bottom row of the main panel trips every few days, usually at night. It only feeds the five breakers to its right (H to L: storage room sockets with the networking gear, appliance circuits, KOT, GAR), so little else in the house notices. The problem is years old and predates the home battery and the robot mower.

Because the tech cave UPS is on another circuit, it never goes on battery and the existing "UPS power cut" alert stays silent.

## What Home Assistant recorded

| Trip | Mower dock | Dishwasher | Cudy AP | Back |
|------|------------|------------|---------|------|
| Mon 2026-11-16 | lost 02:13:45 | no data | lost 02:15:13 | 12:04 |
| Sun 2026-11-22 | charging stopped 03:15:46 | lost 03:17:15 | lost 03:17:59 | 12:34 |

- The UPS reported mains, 100 % charge and 400 to 480 W load throughout the 11-22 outage.
- Grid draw in the hour before the 11-22 trip averaged 782 W (max 1182 W), so overload is unlikely.
- Dishwasher and washing machine were idle at both trips.
- The P1 meter and the home battery go dark with the AP (the P1 meter is a Wi-Fi client of it; the battery sits idle with frozen readings).
- 2026-11-20 17:32 to 21:51 looked similar but was not a trip: the mower was back on charge after two minutes and the dishwasher kept reconnecting. That one was a network problem.

So real trips are about six days apart.

## What is ruled out

- Overload: the device that trips is a differential, and the load was low.
- The outdoor wall socket with the damaged cable entry: no bottom-row breaker switches it off, so it is not behind this differential. The cable entry still needs sealing.
- The robot mower as root cause: the trips predate it. Its dock is on the tripped circuit and is the earliest signal of a trip.
- The home battery as root cause: the trips predate it. It does add some leakage of its own.

## Panels

Main panel:

- Top row: 300 mA differential, then lighting and socket circuits (labels include BERGING, BUITEN, KETEL, AIRCO).
- Bottom row: the 30 mA differential that trips, then five circuits (BERGING sockets, appliance circuits, KOT, GAR).

Garage box (Hager): no differential of its own. Four 2-pole C16 breakers and a grey C25 on the right, which appears to be the box's main switch. The third C16 is off for good: it was the old tech cave feed, and the tech cave now has a dedicated line from the main panel.

## Alerting

- `automation.breaker_tripped_cudy_ap_offline`: the Cudy AP unreachable for 5 minutes sends a critical alert. The wait skips the AP's own reboots (firmware window 03:00 to 05:00). It also leaves a persistent notification per trip listing what went offline with the AP, which switches or heating changed in the ten minutes before, the grid 15-minute average and the UPS load.
- `script.notify_owner` has a third priority, `critical`: everything `high` does, plus the Android alarm channel and the message spoken at maximum alarm volume. The alarm channel alone stayed silent in testing; the spoken message worked.
- Alerts set to critical: breaker trip, UPS power cut (the cut message only), water running for 90 minutes, boiler low pressure, high pressure and new error codes.

Gap: the push needs the router and modem to have power.

## Elimination test in progress

Started 2026-11-22 at about 15:48: the grey C25 in the garage box is off. The mower woke up at 15:48 and its battery then fell 100, 99, 98 % by 15:54 without charging, the same pattern as at the 03:15 trip, so the dock is without power. This comes from the mower's own readings; the dock's light being off is the direct check.

- No trip until about 2026-12-20: the leak is in the garage. Switch the C16 breakers back on one at a time, a few weeks each, to find the circuit.
- A trip anyway: the garage loads are cleared. That leaves the cable from the house to the garage box (still live in this test) and the other four bottom-row circuits.

The mower drains its battery when the dock has no power, so it should be switched off and stored indoors for the duration.

## Faster ways to the answer

- A leakage clamp meter around both wires of each bottom-row circuit, then around the two wires leaving the differential for the total. One circuit standing out means a fault on it. A high total (around 10 to 15 mA) with no outlier means accumulated leakage from many power supplies, which leaves a 30 mA differential almost no margin.
- An insulation test by an electrician.

## Likely fixes

- If one circuit or device leaks: repair or replace it.
- If it is the garage: move the GAR feed behind the 300 mA differential and fit a 30 mA differential in the garage box, so a garage leak no longer takes the networking room down. A second differential in the garage without moving the feed does not help, because the leak still passes through both.
- If it is accumulated leakage: spread the bottom-row circuits over two differentials.
