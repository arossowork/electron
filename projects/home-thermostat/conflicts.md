# Conflicts: home-thermostat (stage 2, architecture re-check with datasheet numbers)

Status after round 4 (architecture rework):
- C14, C15 and C17 are resolved by the PM's round-4 answers. See "Round 4 resolutions" at the end.
- C18 (holder area) and C19 (cold alkaline cutoff) are new. Both are resolved by architecture, so no round-5 question is needed.
- C19 corrects two statements made to the PM in round 4:
  - "About 1.5 years on alkaline" is about 16 months (13.9 months worst case).
  - "Works down to 0 C until nearly empty" is about 60 % of the capacity when the box is at 0 C.

Status (stage 2):
- C1, C3, C5, C6, C7 resolved.
- C4 resolved by architecture: the AA goes through a cutout in the front board, and the depth is 25.2 mm.
- C2 and C8 updated with real numbers.
- C10 and C11 are new and resolved by architecture.
- **C12 (must, relay insulation)**, C13 (must, inductive rating) and C9 (must, measurement interval) need product answers: inbox/questions.json, round 3.
- Numbers: architecture.md.

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
Mains power was ruled out (C1), so latching relays are required.
RESOLVED (PM round 2, q1): option 1. At a loaded cell voltage of 1.05 V or less, both outputs are switched OFF and a low-battery alarm is sent. The 4.7 mF R3 buffer is the reserve (10.9 mJ against 6 mJ needed), so this costs nothing extra. R16 is raised to must because it is the safety behavior.

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
RESOLVED (PM round 2, q3): plastic plates only. An internal on-module antenna is used and there is no external antenna connector. Recorded in R10.

## C8 (check, no conflict): cost first against the battery-life must
PM round 2, q2 and q4: 10 prototypes at under 25 USD each, with cost ranked above battery life. Rough parts estimate at qty 10 (to be confirmed with real prices in the parts stage): Wi-Fi module 3, two reinforced 5 A latching relays 2 x 2.5 = 5, boost with input current limit 1.5, 4.7 mF buffer 1.5, 230 V and probe terminals 3, AA holder 0.5, board-to-board connector 1, relay drivers 0.8, NTC reference, TVS and RTC crystal 1.5, passives, button and LED 1.5. Total about 19.3 USD, which leaves 5.7 USD (23 %) of margin under 25 USD.
The R2 floor (55 uA average, 13 months alkaline) stays must, so the optimizer can trade cost only above that floor. A cheaper boost with a higher quiescent current is allowed only while the R2 budget holds: for example, 10 uA more quiescent at the cell (about 1.25 V) is about 12.5 uW, or about 3.8 uA at 3.3 V, and the budget has about 4 uA of margin (59 - 55 uA). No product choice is needed.

## Stage 2 updates (architect, datasheet numbers in architecture.md)
- C2 update: the PE014F03 coil is 41 ohm, minimum pulse 20 ms, so R7 (2x) gives 40 ms. A pulse is 3.3 V x 80.5 mA x 40 ms = 10.6 mJ, not 1.5 mJ. The relays use 40 pulses/day x 10.6 mJ = 155 J/yr (1.49 uA), not 22 J/yr. This is in the R2 budget.
- C4 RESOLVED (numbers): the AA sits on the SELV zone of the rear board and is reached through a 58 x 16 mm cutout in the front board. Depth = max(1.0 pins + 1.6 + 10.0 relay + 1.0 + 1.6 + 10.0 C_buf = 25.2 mm, 1.0 + 1.6 + 15.0 AA = 17.6 mm) = 25.2 mm against 30 mm. The rear board is 79 % full and the front board 64 % of its usable area.
- C8 update: estimated parts cost 16.4-19.5 USD before the C12 option. This adds an external ADC (+0.5 USD, see C9 and R4) and uses a cheaper H-bridge.

## C9 (must): Measurement energy against R2 (needs a product answer, round 3 q3)
R2 assumed 1440 measurements a day at 20 uJ each. The ESP32-C3 has no low-power coprocessor, so each measurement is a deep-sleep wake: about 40 ms boot + 50 ms measurement = 5.3 mJ, 265 x the assumption. Average current (limit 55 uA):

| Interval | Average current | Alkaline life |
|---|---|---|
| 1 min | 79.1 uA | 9.8 months (fails) |
| 5 min | 57.7 uA | 13.4 months (over 55 uA, no margin) |
| 10 min | 55.0 uA | 14.0 months (meets, 0 % margin to 55 uA) |
| 15 min | 54.1 uA | 14.3 months |

A firmware wake stub (no flash boot) may bring a 10-minute measurement to 53.6 uA.
Recommended: 10 min.
The R2 acceptance changes to "24 Wi-Fi wakes + 144 measurement wakes of <= 5.3 mJ + 40 relay pulses of 10.6 mJ" after the answer.

## C10 (must): R16 cell-removed reserve against relay pulse energy and the ESP32-C3 minimum voltage
- Two reset pulses need about 23 mJ. C_buf 4.7 mF holds only 4.4 mJ above the ESP32-C3 minimum of 3.0 V (datasheet T5-2; the 2.5 V brown-out in R16 is below the specification).
- One pulse alone takes the rail from 3.3 V to 2.68 V in 40 ms. Holding 23 mJ would need about 12 mF and 8 mm more depth.
- RESOLVED BY ARCHITECTURE (the PM's choice of OFF + alarm is kept): the safe state runs while the cell is still connected, at the 1.05 V threshold. At the end-of-life model (Voc 1.15 V, Rint 0.3 ohm), the boost delivers 0.495 W against the 0.32 W needed. The OFF pulses run first, then the alarm.
- R16 acceptance updated. A cell pulled out by the user leaves the relays in their last state. On re-insertion the firmware drives both relays to the commanded state.

## C11 (must): R3 "input current limited to <= 0.6 A" against the TPS61099 switch limit
- The TPS61099 has a fixed peak switch limit of 0.8 A min and 1 A typ, and it cannot be set to 0.6 A.
- The R3 buffer sizing needs >= 0.6 A average input, which the 0.8 A minimum peak gives. At the 1 A typ peak, the end-of-life cell terminal is 0.85 V, above the 0.7 V minimum, for 5 ms TX bursts only.
- RESOLVED BY ARCHITECTURE: R3 acceptance updated to "peak switch limit >= 0.8 A, average input during the 1 s wake <= 0.6 A".

## C12 (must): Reinforced relay insulation against depth, area and cost (needs a product answer, round 3 q1)
- The only latching relay found at <= 10.5 mm height, the TE PE bistable, has 4000 Vrms coil-to-contact. Its clearance/creepage is 3.2 / 4 mm with a PTI 250 base, and its datasheet does not claim reinforced insulation.
- Reinforced insulation at 230-250 V, PD2, material IIIa needs a creepage of 4.6-5.0 mm. The PE is therefore basic insulation only, and R8 fails for the touchable battery and probes.
- Reinforced latching relays (Panasonic DW, TE RT1 bistable, Hongfa HF115F-L) are 15.7-16 mm tall and 29 x 12.7 mm, and need about 2 x the coil energy. Rear board 90 % full; depth 23.3 mm only if C_buf moves to the rear board.

Options:
1. PE relays (basic insulation) plus supplementary insulation by construction: a screw-fixed battery cover, probes with insulated tips and double-insulated cable, and a plastic button. The battery is changed only with the heating circuit breaker off. Cost about +0.3 USD. Not CE-ready without a safety review.
2. PE relays plus an isolated coil supply and drive (small transformer, 4 optocouplers, coil-side H-bridge and capacitor). The user can change the battery at any time.
   - Cost: about +3 USD, about 20-23 USD in total.
   - Space: about 375 mm2 more, so the front board is about 88 % full (layout risk).
   - Battery: the relay energy rises by 1/0.65, which adds 0.8 uA, so the measurement interval must be 15 min to keep R2.
3. Reinforced 16 mm relays. Cost +2-5 USD and -0.5 to -1 month of battery life. Rear board about 90 % full: likely does not fit the 503 box.

## C13 (must): R6 inductive rating against the PE relay
- R6 asks for >= 2 A inductive (cos phi 0.4) and 10 A inrush for 20 ms.
- The PE datasheet gives 5 A 250 VAC resistive (100k cycles) and UL 508 pilot duty B300, which is a break of 1.5 A at 240 V. It gives no inrush value.
- Thermal heads (2-3 W each, PTC inrush) are within the rating. A zone valve motor load above 1.5 A per output is not.
- Product answer needed: round 3 q2.

## Round 3 resolutions
- C9: measure every 10 min (PM round 3, q3). R2 updated: 144 wakes/day x 5.3 mJ; 54.99 uA average.
- C12: low bistable relays (basic insulation) + screw-fixed battery cover, insulated probe tips, breaker-off instruction (PM round 3, q1). R8 updated. Safety review before CE.
- C13: loads are thermal heads or motors < 1.5 A (PM round 3, q2). R6 updated to >= 1.5 A inductive.

## C14 (should): R19 parts cost against the relay price (parts stage, needs a product answer)
- BOM without relays: 8.86 USD (qty 10 LCSC prices, see notes/parts.md). The architecture estimated 2 x 2.5-3.5 USD for the relays.
- PE014F03 is not on LCSC. DigiKey lists 1415390-1 at 9.00 USD at quantity 1. 2 x 9.00 + 8.86 = 26.86 USD, which is 1.86 USD above the 25 USD target.
- The target holds if each relay costs <= (25 - 8.86) / 2 = 8.07 USD at quantity 10. No quantity-10 quote could be fetched (Mouser and Farnell pages timed out).
- Options: (1) get a qty-10 quote from TE distributors (likely 10-20 % under the single price, which would pass: 7.2-8.1 USD); (2) accept about 27 USD (R19 is "should"); (3) Panasonic DE1A-L-3V, reinforced 8 mm, single coil 100 mW, 12.5 mm tall, 25 x 12.5 mm, but quoted at 11.23 USD (qty 1-39) and its pin layout is unconfirmed: costs more, solves C12 properly.
- Recommendation: option 1 first.

## C15 (must, risk): R2 against the buffer capacitor leakage
- R2 budgets 1 uA of leakage for the 4.7 mF buffer. The Panasonic ECA-0JHG472B datasheet limit is 0.01 CV = 296 uA and no steady-state value is specified. A typical 3 uA gives +2 uA x 0.85 / 0.65 = +2.6 uA, so 57.6 uA: alkaline 13.4 months, lithium 2.18 years (R2 life targets met, the 55 uA acceptance not met). At 5 uA the average is 60.2 uA and the alkaline life 12.9 months, below 13 months (R2 fails).
- No product decision yet. Mitigation: measure the leakage of 3 capacitor samples at 3.3 V over 24 h at bring-up; if above 3 uA, test a 4700 uF 10 V part (same size) or reduce the buffer to 3.5 mF.

## C16 (must, risk): the relay driver needs a GPIO for nSLEEP
- DRV8837 sleeps at 45 nA only with nSLEEP low. The architecture uses 15 of 15 GPIOs. Circuit stage: use one shared nSLEEP line for both bridges on IO18 (USB D-, only used by the programming pads) or free a pin by merging the LED and the VBAT-divider enable. If neither works, use 4 discrete complementary MOSFET pairs (AO6604 class), which need no sleep pin.
- RESOLVED IN CIRCUIT (stage 4): DRV8837 VM, VCC and nSLEEP connect to AVDD_SW, the rail switched by the IO2 P-FET (M2). The bridges are unpowered in sleep (0 nA instead of 2 x 45 nA), no GPIO is used, and IO21 is spare. See notes/circuit.md.

## C15 update (stage 5, review-1 M7): R2 sleep budget with C1 leakage and the EN supervisor
- The sleep testbench now loads +3V3 with an explicit 3 uA source for C1. ECA-0JHG472B specifies only the 2-min limit of 0.01 CV = 296 uA, with no steady-state value, so 3 uA is the parts-stage typical. U6 (TPS3839, review-1 M3) adds 150 nA.
- Simulated cell sleep current: **35.18 uA against 26.8 uA** (was 26.45 uA at the 1 uA allocation). The rail "board" current is 150 nA against 100 nA, all of it U6. R2 is now FAIL instead of a false "verified".
- Effect on the R2 average: rail +2.15 uA, x 0.85/0.65 = +2.8 uA, so 57.8 uA against 55 uA.
  - Alkaline: 6.7 kJ / (57.8 uA x 3.3 V) = 13.4 months, which meets the 13-month target but not the 55 uA acceptance.
  - Lithium: 2.2 years.
  - At 5 uA of leakage: 60.4 uA, 12.8 months alkaline, which fails.
- Prices below are not re-checked this session (no web access).

| Option | Leakage effect | Cost | Notes |
|---|---|---|---|
| 1. Keep ECA-0JHG472B; measure 3 samples at 3.3 V over 24 h at bring-up | unknown, 1-5 uA | 0 | decides between 2-4 |
| 2. 4700 uF 10 V aluminium (for example Panasonic ECA-1AHG472) | typically lower at 33 % of rated voltage | about +0.3-0.5 USD | larger can (D16 class): depth check against the 27.7 mm stack |
| 3. Change the R2 acceptance to <= 58 uA (life targets still met at 3 uA) | - | 0 | product decision; no margin at 5 uA |
| 4. Smaller buffer (3.5 mF minimum from C3) | leakage scales with C | about 0 | R3 acceptance says >= 4.7 mF; reopens R3 |

- Supervisor 150 nA: the 100 nA board allocation must become 250 nA (+0.6 uA at the cell in sleep, +0.2 uA on the average). No lower-Iq supervisor with a KiCad symbol was found.
- Recommendation: option 1 first, then option 2 if any sample is above 2 uA.

## C17 (must, stage 5, review-1 M1): R3/R7/R9 against real boost efficiency and a cold alkaline cell (needs a product answer)
The boost now uses the SLVSD88M Fig 6-5 efficiency, 80 % at VIN 0.7 V and 83 % at 1.0 V, instead of the 85 % assumption. The cell resistance is 0.3 ohm at 25-50 C and 0.6 ohm at 0 C (alkaline). The reverse-polarity FET adds 30-45 mV. Simulated with the requirement cell model:

| Case | 50 C | 0 C | Limit |
|---|---|---|---|
| R3 Wi-Fi wake, Voc 1.15 V: average cell current | 0.650 A (boost at its input limit) | 0.641 A | <= 0.6 A |
| R3 rail minimum | 2.993 V | 2.861 V (U6 resets the MCU) | >= 3.0 V |
| R3/R7 VIN minimum | 0.727 V | 0.678 V | >= 0.7 V |
| R7 relay set at Voc 0.9 V: coil / rail / droop | pass (3.04 V / 3.26 V / 37 mV) | 2.694 V / 2.931 V / 370 mV | 2.7 V / 3.0 V / 100 mV |
| R16 reset pulses at Voc 1.15 V | pass | pass (coil 3.02 V, rail 3.24 V) | - |

The R3 0.6 A average needs about 85 % efficiency, so it fails at every temperature. No circuit change fixes it inside the TPS61099 limits. Options, from simulation with the same testbench and only Voc changed:

1. **Raise the end-of-life / low-battery cutoff voltage** (the R16 trigger and the R3 cell model).
   - R3 passes at Voc 1.25 V at 50 C (0.569 A, rail 3.08 V; 1.20 V still fails with 0.611 A). At 0 C it needs Voc 1.40 V (0.585 A, rail 3.07 V; 1.25 V fails).
   - The relay case at 0 C passes at Voc 1.1 V (coil 3.06 V, droop 19 mV; 1.0 V fails with 161 mV of droop).
   - Battery-life cost: this estimate uses a typical alkaline open-circuit voltage against depth of discharge. It is not read from a datasheet, so verify it on the bench. Usable capacity above Voc is about 80 % at 1.15 V, 60 % at 1.25 V and 30 % at 1.40 V.
     - 1.25 V cutoff: life 13 x 60/80 = about 10 months.
     - 1.40 V cutoff: about 5 months. Both fail R2.
   - Variant: a temperature-aware cutoff, 1.25 V above 20 C and rising to 1.40 V at 0 C, using the measured temperature. The life cost then applies only in a cold box.
2. **Limit the Wi-Fi wake current** (lower TX power, 80 MHz CPU, shorter airtime).
   - At Voc 1.15 V and 0.6 A, the cell can supply a 1 s wake average of 141 mA at 25-50 C, against the 150 mA assumed, and 112 mA at 0 C (-25 %).
   - This needs a firmware measurement, it is uncertain at 0 C, and it does not fix R7 at 0.9 V / 0 C.
3. **Lithium cell only (Li-FeS2, Energizer L91 class).**
   - Its Voc stays >= 1.4 V for most of its capacity, and its resistance holds up in the cold. The sim shows that a 1.40 V cell at 0.6 ohm passes R3 at 0 C.
   - Cost: about 2.5 USD per cell against about 0.5 USD for alkaline, for the user. Life is 2.2 years.
   - The firmware can detect alkaline at insertion (Voc under 1.65 V) and warn.
- R7 at 0.9 V is reachable only if the cutoff is below 0.9 V. With option 1 or 3 the relays never run below the cutoff, because R16 switches them off first. R7 should then read "at the low-battery cutoff voltage". That is a spec change.
- Recommendation: option 3 for the product (state "lithium AA" on the label), with option 1 (temperature-aware) as the firmware guard for alkaline cells. Product person to decide.

## Round 4 resolutions (architecture rework, numbers in architecture.md sections 3-4 and notes/power_calc.py)

- **C17 RESOLVED (PM round 4, q1: 2 x AAA in series).**
  - TPS610994 kept. Down mode regulates 3.3 V up to VIN 3.8 V, so a fresh 3.6 V lithium pack never passes through to the ESP32 (3.6 V absolute maximum).
  - Wi-Fi wake pack current at the cutoff: 0.279 A at 25 C, 0.338 A at 0 C, against the 0.65 A minimum average limit (was 0.65 A, at the limit, with 1 AA).
  - Relay pulse at the 0 C cutoff with the MCU awake: 0.224 A, coil 3.23 V.
  - R3, R7, R16 rewritten for the pack model (2 x Voc at the cutoff, Rint 2 x 0.40 / 0.80 ohm + 0.07 ohm). The TPS63900 buck-boost was rejected: its 1.63-1.67 V UVLO is crossed during TX at the cutoff (section 3.2).
- **C15 RESOLVED (PM round 4, q2).**
  - C1 becomes 4.7 mF, >= 10 V, leakage <= 1 uA at 3.3 V / 25 C after 24 h, D <= 12.5 mm, +0.4 USD. Bring-up measures 3 samples.
  - The buffer cannot shrink. Room temperature needs 1.17 mF, but the 0 C / 1.10 A-limit corner needs 3.41 mF effective; 4.7 mF x 0.8 x 0.95 = 3.57 mF.
  - R2 is now set on pack power: <= 208 uW, design 195.8 uW. A leakage of 3 uA still meets it (204.6 uW); 5 uA does not (213.4 uW).
- **C14 RESOLVED (PM round 4, q3).** R19 is now <= 28 USD. The estimate is 27.16 + 0.25 (2468 holder) + 0.40 (C1) = 27.81 USD. Cheaper relays are a production task.

## C18 (must, R1/R10): the 2 x AAA holder does not fit on the rear board (RESOLVED BY ARCHITECTURE)
- Keystone 2468 is 52.98 x 24.59 x 13 mm, against 58 x 16 x 15 mm for the AA holder.
- On the rear board it needs 2495 mm2 of 2480 (101 %). The 14.4 mm strip left beside it cannot take 2 relays (40 mm) + J1 (20 mm) + J2 + J4 + barrier.
- Resolution: the holder moves to the front face of the front board.
  - Depth 1.0 + 1.6 + 10.0 + 1.0 + 1.6 + 13.0 = 28.2 mm against 30 mm.
  - The module sits at the top-right corner. The antenna is 11.2 mm from the holder (guideline 10 mm) and 15.4 mm from the mains zone (R9: 15 mm).
  - Holder body (24.6) + module (16.6) = 41.2 mm against the 40 mm PCB height, so the holder body overhangs the PCB edge by up to 1.2 mm, inside the 44 mm envelope. R1 acceptance updated.
  - The rear board drops to 41 %. VBAT no longer crosses J4.
- Fallback if the enclosure cannot take the overhang: individual AAA clips with a block <= 23.4 mm wide (parts stage).

## C19 (must, R3/R13 vs R2): cold alkaline pack at the cutoff (RESOLVED BY ARCHITECTURE, report to the PM)
- At 0 C with the pack at 2 x 1.10 V (Rint 2 x 0.80 + 0.07 = 1.67 ohm, maximum power 0.72 W), a TPS610994 at its maximum average input limit (1.10 A) pulls VIN to 0.36 V during a TX burst. That is below UVLO, so the boost stops.
- At the minimum and typical limits it runs (VIN 1.11 / 0.78 V).
- Resolution: a temperature-aware cutoff (R16 trigger), Voc >= 0.7 + 1.10 x (2 Rcell(T) + 0.07).
  - 1.10 V/cell at a box temperature of 8 C or more; 1.155 V at 5 C; 1.27 V at 0 C.
  - Life at room temperature is unchanged: 16.0 months typ, 13.9 worst.
  - A box held at 0 C stops at about 60 % of the alkaline capacity (about 11 months). Lithium (Rint about 0.2 ohm, 1.4-1.7 V) is not affected.
- The R2 floor (13 months) is met at the datasheet temperature (21 C), so no product question is raised. The PM gets corrected figures (see Status).
- Rint at the cutoff (0.40 ohm, 0.80 at 0 C) is an estimate: 2 x the fresh E92 pulse resistance, and x2 at 0 C per review-1. Bench-check 2 used E92 cells at 0 C.

## C20 (must, NEEDS A PRODUCT DECISION, review-2 M1): R2 meets 208 uW on typical values only
The R2 limit is unchanged: <= 208 uW average from the pack, which is 7.13 kJ worst alkaline energy over 13 months. Active wakes take 166.3 uW (architecture.md section 4), so sleep may take 41.7 uW. The sleep testbench now has two corners (notes/circuit.md "Review-2 fixes", which also gives the source of every number):

| Corner | Rail sleep | Pack sleep | Pack total | Alkaline life (8.26 kJ typ / 7.13 kJ worst) | Lithium (13.4 kJ) |
|---|---|---|---|---|---|
| Typical (module 5.5 uA, C1 1 uA, U6 150 nA, PFM 3.40 V) | 6.71 uA | 30.4 uW | **196.7 uW (passes)** | 16.0 / 13.8 months | about 26 months |
| Worst case at 50 C (module 8.5 uA est., C1 3 uA, U6 500 nA, U2 IQ max, VOUT 3.47 V, M3 IGSS, MOSFET IDSS, Q1, IO leakage) | 13.71 uA | 72.7 uW | **239.0 uW (FAILS by 31 uW)** | 13.1 / 11.3 months | about 21 months |

- Contributions to the worst case:
  - Module at 50 C: about 14 uW. This is an estimate; no datasheet value exists.
  - C1 at 3 uA: about 10 uW. This is an estimate; no datasheet bound exists.
  - M3 IGSS: about 7 uW. This is the 8 V maximum scaled linearly, an upper bound.
  - U2 IQ max: about 6 uW.
  - IDSS of M1, M2 and M4: about 4 uW.
- The largest items are estimates or bounds, not measured values. The real worst case is probably lower, but no document proves it.

Options for the product person:
1. **Accept verification on typical values plus a bench measurement.**
   - Measure the pack sleep current on 3 boards, at 25 C and 50 C, after 24 h powered.
   - Pass criterion: <= 16.7 uA at a 2.5 V pack (41.7 uW). C1 is screened in the same test.
   - Cost: none.
   - Risk: if boards land near the worst case, alkaline life is 11.3-13.1 months instead of >= 13, and lithium about 21 months instead of >= 24.
2. **Reduce the active energy so that the worst case meets 208 uW** (it needs >= 31 uW less):
   - a. Measurement every 15 min instead of 10 min: -3.2 uW, so the worst case is 235.9 uW and 11.5 months (+0.15 months). Not enough on its own.
   - b. Wi-Fi wake every 90 min instead of 60 min (remote commands take effect within 90 min, R11): -50.3 uW. Worst case 188.8 uW, **14.4 months** (typical 21.5 months).
   - c. Wi-Fi wake energy from 0.5 J to <= 0.35 J (TLS session resumption, lower TX power; firmware must measure it): -45.3 uW. Worst case 193.8 uW, **14.0 months** (typical 20.7 months). Latency is unchanged, but this is unproven until measured.
3. Change R2 to "typical design value <= 208 uW, worst case reported". This is a spec change.

Recommendation: option 1 now (bench on 3 boards), with 2c as the firmware target. If the bench result is above 16.7 uA, ask again with 2b.

### C20 (superseded, stage 4 round 4): R2 "rail sleep floor <= 6.7 uA" against the simulated sleep floor
- The R2 acceptance lists the rail sleep floor as 6.7 uA: module 5.0 + XTAL 0.5 + C1 1.0 + U6 0.15 + IO3 0.05.
- The simulation uses the numbers the parts stage can justify:
  - C1: 3 uA (1.1 Mohm). This is an estimate; the FS datasheet gives only 470 uA at 10 V after 2 min.
  - U6: 500 nA (SBVS193D maximum).
  - Simulated rail floor: 5.5 + 3.0 + 0.5 + 0.05 = 9.0 uA.
- The R2 limit itself is still met:
  - Pack sleep power at 2.5 V: 9.0 uA x 3.3 V / 0.75 = 39.6 uW (simulated 39.59 uW).
  - Allowance: 208 - 166.3 = 41.7 uW.
  - Total: 166.3 + 39.6 = 205.9 uW <= 208 uW (1 % margin).
- With C1 at its 1 uA purchase spec the floor is 7.0 uA (U6 max): 30.8 uW, total 197.1 uW.
- The 6.7 uA figure is a design value that assumes U6 typical (150 nA). It is not met at U6 maximum even with C1 at 1 uA (7.05 uA). The binding R2 number is the 208 uW; the simulation measures that (r2_pack_sleep_uw) and not the 6.7 uA.
- Request: reviewer/requirements to restate the floor as "<= 7.1 uA at U6 max with C1 <= 1 uA", or keep 6.7 uA as typical. No product impact. C1 leakage stays a bench item (3 samples).

## C21 (note, stage 4 round 4): R3 cold-cutoff margin at the 1.10 A input limit
- Cutoff rule: Voc >= 0.7 + 1.10 x (2 x 0.80 + 0.07) = 2.537 V, rounded up to 2.54 V at 0 C. By construction this leaves 3 mV.
- Simulated r3_vin_tx_110 at 0 C: 0.7034 V against 0.7 V. It passes.
- Any extra milliohm fails it: 1.10 A x 3 mOhm = 3.3 mV. Examples are holder spring resistance above the 0.04 ohm model share, or an M3 above its max fit.
- Suggestion for the architect: 2.56 V (1.28 V/cell) at 0 C gives about 22 mV. Life cost: only while the box is at 0 C, about 1 % less usable capacity.
- Not a requirement conflict. Recorded so that review decides.
- RESOLVED (review-2 S3): the 0 C cutoff is now 2.56 V (R3 and R16 updated).
  - The temperature source is the ESP32-C3 internal sensor minus 3 C (notes/circuit.md rule 9).
  - Simulated r3_vin_tx_110 at 0 C: 0.724 V against 0.7 V (24 mV, was 3 mV). Bench check at 0 C.

## Round 5 resolutions
- C20: PM round 5, q1: keep 60 min Wi-Fi, accept 11-13 months for a worst-case unit. R2 restated: typical <= 208 uW (13 months), worst-case sleep corner <= 79.9 uW (246 uW total = 11 months at 7.13 kJ).
- R6 surge gap: PM round 5, q2: risk accepted for the prototypes; first-article bench test with a real valve head.

## Round 6 resolution
- Final review MUST (relay housing 1.0 mm under front copper, R8 in 3D): PM round 6, q1: 0.4 mm polycarbonate/Formex sheet, die-cut 62 x 40 mm, between the boards. Stack 28.6 mm <= 30 mm (R1). Cost about 0.10 USD, a mechanical item outside the R19 parts sum (like the enclosure).
