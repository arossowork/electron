# Conflicts: home-thermostat (stage 1, after round 1)

Status: C1, C3, C5, C6 resolved. C4 resolved by architecture (two boards), but the depth stack is open for the architect. C2 and C7 wait for round 2.

Common assumptions (to be confirmed by the architect with datasheets):
- Usable energy at the 3.3 V rail, 1 year = 31.5 Ms:
  - 1 AA alkaline: 2.5 Ah x 1.25 V avg x 0.7 (pulse-load derating) x 0.85 (boost) = 6.7 kJ -> 64 uA average at 3.3 V
  - 2 AAA alkaline: 1.15 Ah x 2.5 V x 0.7 x 0.90 = 6.5 kJ -> 63 uA
  - 1 AAA alkaline: about 3.3 kJ -> 32 uA
  - 1 AA lithium (Li-FeS2): 3.0 Ah x 1.5 V x 0.95 x 0.85 = 13.1 kJ -> 126 uA
- Sleep floor (Wi-Fi SoC deep sleep about 5 uA, RTC, boost quiescent, sensor bias): 15 uA x 3.3 V x 1 yr = 1.56 kJ/yr.
- One Wi-Fi wake (association with stored credentials, MQTT connect, publish, fetch pending commands, about 1 s at about 150 mA): 0.5 J. With TLS, about 1 J.
- Italian 503 box (BTicino 503E): external 108 x 74 x 53.5 mm, internal depth 50 mm. A 3-module device body is about 66 x 44 mm. About 30 mm of depth is left for electronics after the support frame and the wire space.

## C1 (must): Battery life of 1 year or more against remote-command latency
A device on battery cannot stay connected to Wi-Fi. If Wi-Fi stays associated in power-save mode (about 1 mA average), 1 AA alkaline lasts 6.7 kJ / 3.3 mW = 23 days.
So the device sleeps, wakes at a fixed interval, reports, and collects queued commands (MQTT retained or persistent session). The wake interval is the worst-case command latency.

| Wake interval | Radio energy/yr | Life on 1 AA alkaline | Life on 1 AA lithium |
|---|---|---|---|
| 60 min | 4.4 kJ | 1.13 yr | 2.2 yr |
| 15 min | 17.5 kJ | 0.35 yr (4 months) | 0.69 yr |
| 5 min | 52.6 kJ | 0.12 yr (6 weeks) | 0.24 yr |
| always connected | about 104 kJ | 23 days | 46 days |

The budget on 1 AA alkaline allows 28 wakes per day. On 1 AAA it allows about 10 wakes per day, so 1 AAA is ruled out for 1 year with hourly contact.
Options:
1. Mains power from L and N in the box (recommended if L and N are present). Always connected, latency under 2 s, no battery changes. Cost: an isolated 230 VAC to 3.3 V module (about $3-4, about 34 x 20 x 15 mm), and the device must stay powered from mains under LVD.
2. Battery, 1 AA, wake every 60 min (commands take effect within 60 min). Life is about 13 months on alkaline and about 2 years on lithium. Cost: none.
3. Battery, wake every 15 min. You must use a lithium AA (about $2.5 each), and the cell still needs replacement about every 8 months, so the 1-year target fails. Cost: misses the target.
The heating schedule and temperature control run locally and do not need the radio in any option.
RESOLVED (PM round 1, q1 and q2): option 2. The box has no L and N for the electronics, so it runs on battery only. 1 AA, wake every 60 min. Budget: sleep 1.56 kJ + radio 4.38 kJ + measurements 0.01 kJ + relays 0.02 kJ = 5.97 kJ/yr, against 6.7 kJ/yr alkaline (13 months) and 13.1 kJ lithium (2.2 yr). Recorded in R2 and R11.

## C2 (must): Relay holding current against battery
A standard 3 V relay coil draws about 120 mA. Two relays at 50 % heating duty need about 0.4 W, and 1 AA lasts 6.7 kJ / 0.4 W = 4.6 hours.
Resolution (no product choice needed on battery): latching (bistable) relays with no coil current between switch events. A set or reset pulse is about 150 mW x 10 ms = 1.5 mJ. 2 channels x 20 events/day x 365 = 14,600 pulses, or 22 J/yr, which is negligible.
Consequence (needs a product choice, round 2): a latching relay stays in its last state if the battery dies. If heating is ON at that moment, it stays ON. Options:
1. Keep an energy reserve and switch both outputs OFF at the low-battery threshold, then send an alarm (recommended). Cost: about 1 mF reserve capacitor and firmware. Risk: frost if the battery is not replaced.
2. Keep the last state. Cost: none. Risk: heating runs on without control.
3. Switch both outputs ON at the low-battery threshold (frost protection with normally-closed thermal heads). Cost: the same reserve as option 1. Risk: rooms overheat until the cell is replaced.
Mains power was ruled out (C1), so latching relays are required. OPEN: round 2, q1.

## C3 (must): One 1.5 V cell against the Wi-Fi peak current
The Wi-Fi transmit peak is about 350 mA at 3.3 V = 1.16 W. From one cell at its 0.9 V end-of-life voltage with 85 % boost efficiency, that is 1.16 / 0.85 / 0.9 = 1.5 A. An alkaline AA near end of life (internal resistance 0.5-1 ohm) collapses at 1.5 A, so the usable capacity falls well below 2.5 Ah.
Options:
1. 2 AAA in series (3.0 V to 1.8 V): the peak is 1.16 / 0.9 / 1.8 = 0.72 A, with about the same energy as 1 AA (6.5 kJ). Cost: a holder about 24 x 50 mm, about 10 mm wider than an AA holder.
2. 1 AA with a buffer capacitor and an input-current limit of about 400 mA. Cost: about $0.5 and 10 x 10 mm of board area. The life estimate depends on the alkaline cell at 400 mA, so specify a lithium AA for margin.
3. 1 AA lithium only. Cost: $2.5 per cell for the user.
The architect decides between 1 and 2 after the round-1 answer. This is not a product choice unless the cell type changes.
RESOLVED (PM round 1, q2 chose 1 AA): option 2, a boost converter with an input-current limit and a buffer on the 3.3 V rail.
- End-of-life cell model: Voc 1.15 V, Rint 0.3 ohm. At a 0.6 A limit the terminal voltage is 1.15 - 0.18 = 0.97 V, so the boost delivers 0.97 x 0.6 x 0.85 = 0.49 W. That covers the 0.5 W average of a 1 s Wi-Fi wake (150 mA x 3.3 V).
- TX peak 1.16 W for 5 ms: the deficit is (1.16 - 0.49) x 5 ms = 3.3 mJ. A rail drop from 3.3 V to 3.0 V gives 0.5 x C x (3.3^2 - 3.0^2) = 0.945 x C, so C >= 3.5 mF. Specified: >= 4.7 mF (about 35 % margin), low-ESR polymer or EDLC, about 10 x 10 mm, about $0.5.
- The same buffer gives the low-battery reserve for C2: 0.5 x 4.7 mF x (3.3^2 - 2.5^2) = 10.9 mJ, against 6 mJ needed for two reset pulses with 2 x margin (R16).
- Alkaline cells below Voc 1.15 V under this load are counted as unusable. That is already in the 0.7 pulse-load derating. A lithium AA (Rint about 0.15 ohm, flat 1.4-1.5 V) has a large margin. Recorded in R3.

## C4 (must): 503 box volume against battery, relays, terminals, and mains isolation
Board budget for a single 62 x 40 mm board (2480 mm2): AA holder 15 x 57 (855), 2 latching relays about 20 x 10 (400), 230 V terminals 4 x 5 mm pitch (160), probe terminals (100), isolation barrier 6 mm x 40 mm (240), Wi-Fi module (300), antenna keep-out (200), boost and passives (200). Total 2455 mm2, which is 99 % of one side. That is not feasible on one side with routing.
Options:
1. Two stacked boards (recommended). The rear board carries mains, relays, and terminals. The front board carries the SELV electronics, the battery, and the antenna behind the front plate. Cost: board-to-board connector (about $0.3) and more assembly.
2. One board with parts on both sides and the battery on the back. Cost: double-sided assembly. The battery lies near the mains side, which makes isolation harder.
3. Mains power (C1 option 1) instead of a battery. The supply module (about 680 mm2) takes about the same area as the AA, so this does not save area.
Whichever option is chosen, the device must fit about 30 mm of depth (see R1).
Re-evaluation after round 1 (2 A loads, battery only, no mains supply):
- Smaller relays: subminiature latching signal relays (about 15 x 7.5 mm) are rated for about 1-2 A at 250 VAC, but they lack a reinforced 4 kV coil-to-contact rating, and R8 needs it because the coil side is touchable SELV. Reinforced 5 A latching power relays are about 20 x 10 x 10-12 mm. The 2 A rating therefore does not shrink the relay area (400 mm2). It does lower the terminal rating to 6 A (R15).
- New area: the C3 buffer and boost take about 300 mm2. New single-board total: 855 + 400 + 160 + 100 + 240 + 300 + 200 + 300 = 2555 mm2, which is 103 % of 2480 mm2. One side is still not feasible.
- Two boards fit by area: rear board (relays 400 + 230 V terminals 160 + probe terminals 100 + barrier 240 = 900 mm2, 36 %); front board (AA 855 + Wi-Fi 300 + keep-out 200 + power 300 = 1655 mm2, 67 %).
- Depth is the open risk: 1.6 (rear PCB) + 12 (relay) + 1 (gap) + 1.6 (front PCB) + 15 (AA holder) = 31.2 mm, against 30 mm. The fixes cost no product choice: a relay of 10.5 mm or less in height (stack 29.7 mm), or place the AA on the rear board beside the relays (rear board 1755 mm2, 71 %).
RESOLVED BY ARCHITECTURE: option 1 (two stacked boards). The architect proves the depth stack with R1. If neither fix fits, this returns to the product person as a choice between a 2-module-deep housing and a smaller AAA cell (which fails R2).

## C5 (must): Safety isolation between 230 VAC and user-touchable SELV parts
The probes, the battery, and the button are touchable, so they need reinforced insulation from the relay contacts. For IEC 60730-1 / IEC 62368-1 at 250 V, pollution degree 2, overvoltage category II, the minimum creepage is 5.0 mm and the minimum clearance is 3.0 mm (4 kV impulse). The target is 6.0 mm creepage and 4.0 mm clearance, with a routed slot where space is short. The relays need a reinforced coil-to-contact rating (4 kV or more). These parts are in R8. No product choice is needed, but the barrier costs area (see C4).

## C6 (must): Second probe in another room against probe wiring
"Two rooms independently" means the second probe must sit in the second room. A probe wire that shares a conduit with 230 V wires must have insulation rated for 230 V (CEI 64-8), and an NTC wire longer than about 10 m picks up mains hum. The probe length and route are a product question (round 1, q4).
Options:
1. Probe 1 at the device and probe 2 on a cable of 10 m or less, with double-insulated cable (recommended). Cost: a cable about $1/m. The installer must route it.
2. Both probes on cables of 10 m or less. Cost: same as option 1.
3. The second room gets its own wireless sensor. This is out of scope and changes the product.
RESOLVED (PM round 1, q4): both rooms are next to the box, and the probe cables are shorter than 2 m. Cable resistance 2 m x 0.2 ohm/m = 0.4 ohm against a 10 kohm NTC at 25 degC (about 440 ohm/degC) gives 0.001 degC of error, so the limit is set to 0.02 degC. With 200 pF of cable capacitance and a short loop, a 50 Hz rejection of 20 dB is enough (it was 40 dB for 10 m). The probe wires still enter a box with 230 V actuator wiring, so the probe cable must be rated 300/500 V or double insulated, and the probe terminals stay on the SELV side of the R8 barrier. Recorded in R5.

## C7 (should): Wi-Fi range from inside a wall box
Brick and a metal plate attenuate 2.4 GHz. A metal (aluminium) front plate blocks Wi-Fi.
Resolution: the antenna sits at the front, 15 mm or more from relays and mains traces, and the device needs a plastic front plate (R10). If the customer wants metal plates, the device needs an external antenna (product question, round 2).
