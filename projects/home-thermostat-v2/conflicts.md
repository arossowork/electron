# Conflicts: home-thermostat-v2 (stage 1, after round 2 answers)

Summary: C1-C5 resolved (C1-C4 by PM round 1, C5 by PM round 2 q1; C1 battery target relaxed to 10 months by PM round 2 q4, inbox/answers.md). Stage 2 added C6 (board area, engineering fix proposed), C7 (R3 bulk-capacitor leakage, verification method) and C8 (R1 load range): open.

Arithmetic script: `notes/power_budget.py`. Status of each conflict at the end of its section.

## Reference data used

| Item | Value | Source |
|---|---|---|
| 503 box (3-module), outer | Bticino 503E 105 x 72 x 53.5 mm; Gewiss GW24203 104 x 68 x 50 mm | RS listings 2700730, 3037465 |
| 503 box, inner (estimate) | about 96 x 58 mm, 47-50 mm deep (walls 2-4 mm) | outer minus wall thickness, to be measured |
| Module grid (Italian "serie civile") | 1 module = 22.5 mm wide x 45 mm high; 3-module window 67.5 x 45 mm | industry convention (DIN 43880 45 mm front) |
| Device depth behind support frame | proposed 38 mm or less, to leave about 12 mm for 1.5 mm2 wiring at the back of a 50 mm box | assumption, to confirm with a sample box |
| ESP32-C3 currents | TX peak 200-400 mA, RX about 84 mA, deep sleep about 5 uA | esp32.co.uk deep-sleep guide; Espressif datasheet in stage 3 |
| WiFi wake energy (connect + MQTT publish + receive retained) | 0.3 J optimized (cached BSSID/channel, static IP, no TLS, about 1 s at 90 mA, 3.3 V); 0.5 J design value; 1.0 J with DHCP + TLS (2-3 s) | published measurements of 3-5 s at about 100 mA for unoptimized code; to be measured in stage 4 |
| Latching relay, 16 A class | Hongfa HF115F-L: 16 A 250 VAC, single coil about 400 mW, 29 x 12.7 x 15.7 mm, 5 kV coil-contact, reinforced insulation per VDE 0700/0631 | Hongfa datasheet |
| Battery energy (low drain, to 0.9 V/cell) | AAA alkaline about 1.1 Ah x 1.2 V = 1.32 Wh; AAA lithium about 1.2 Ah x 1.5 V = 1.8 Wh; AA alkaline about 2.5 Ah x 1.2 V = 3.0 Wh | typical manufacturer data |

Common power overhead at the 3.3 V rail, used in every battery case below:

- Sleep floor: 10 uA x 3.3 V = 33 uW (MCU deep sleep 5 uA, RTC, converter quiescent current, leakage).
- Probe sampling every 2 min without WiFi: 25 mA x 40 ms x 3.3 V = 3.3 mJ per wake; 720 wakes/day = 2.38 J/day = 27.5 uW.
- Relays: 20 latching pulses/day x 25 mJ (400 mW x 50 ms / 0.8 driver efficiency) = 0.5 J/day = 5.8 uW.
- Overhead total: 66.3 uW.

Usable average power = battery Wh x converter efficiency x 0.8 derating (self-discharge, cold, end-of-life cut-off) / 8760 h.

## C1. WiFi against 1 year on 1 or 2 small cells (must, resolved)

| Battery | Wh | Usable avg power | Avg current at 3.3 V | Left for WiFi | WiFi wakes/day at 0.3 / 0.5 / 1.0 J | Command delay at 0.5 J |
|---|---|---|---|---|---|---|
| 1 x AAA alkaline (boost, eff 0.80) | 1.32 | 1.32 x 0.80 x 0.8 / 8760 = 96 uW | 29 uA | 30 uW = 2.6 J/day | 8.7 / 5.2 / 2.6 | about every 4.6 h |
| 1 x AAA lithium (eff 0.82) | 1.80 | 135 uW | 41 uA | 69 uW = 5.9 J/day | 19.7 / 11.8 / 5.9 | about every 2 h |
| 2 x AAA alkaline (buck-boost, eff 0.85) | 2.64 | 205 uW | 62 uA | 139 uW = 12.0 J/day | 40 / 24 / 12 | about every 60 min |
| 1 x AA alkaline (boost, eff 0.80) | 3.00 | 219 uW | 66 uA | 153 uW = 13.2 J/day | 44 / 26 / 13 | about every 55 min |
| 2 x AA alkaline (eff 0.85) | 6.00 | 466 uW | 141 uA | 400 uW = 34.5 J/day | 115 / 69 / 35 | about every 21 min |

Battery energy needed for one year at a given remote-command delay (0.5 J per wake, plus 66.3 uW overhead, divided by 0.85 x 0.8):

- every 60 min: 205 uW -> 2.6 Wh/yr (1 x AA or 2 x AAA, no margin)
- every 30 min: 344 uW -> 4.4 Wh/yr (2 x AA)
- every 15 min: 622 uW -> 8.0 Wh/yr (3 x AA alkaline or 2 x AA lithium)
- every 5 min: 1733 uW -> 22 Wh/yr (not feasible on cells in a wall box)
- always connected (WiFi power-save, about 2 mA average): 6.6 mW -> 68 Wh/yr (impossible on battery)

Consequence for remote control: while the device sleeps, the WiFi radio is off. A command from the phone waits on the MQTT broker as a retained message and takes effect at the next wake. With 1 x AA that is up to about 1 hour. The schedule and the local thermostat loop work offline in every case; only remote commands and remote readings are delayed.

Second problem: peak current. A 350 mA TX burst at 3.3 V draws 0.35 x 3.3 / 0.85 = 1.36 W from the cell. At 1.0 V (end-of-life single cell) that is 1.36 A input current; at 2.0 V (2 cells) it is 0.68 A. An alkaline AAA near end of life (internal resistance 0.3-0.5 ohm) cannot deliver 1.36 A without collapsing. Mitigation: a bulk buffer capacitor for TX bursts, C = 0.35 A x 5 ms / 0.3 V = 5.8 mF, plus an input current limit on the boost. This adds about 1 USD and 1 cm2. Single AAA alkaline is not recommended even with the buffer.

Resolution options:

| Option | Command delay | Cost and consequence |
|---|---|---|
| A. Mains-powered (see C4), always connected | about 1 s | needs live and neutral in the box; isolated AC/DC module about 2-3 USD and about 34 x 20 x 15 mm; no batteries |
| B. 1 x AA, wake every 60 min (or on button press) | up to 60 min | meets 1 year with about 0 % margin at 0.5 J; 1 year with margin only if wake energy is 0.3 J or less (verified in stage 4) |
| C. 2 x AA, wake every 20-30 min | up to 30 min | about 1 year; does not fit in a 3-module device on one board (see C3); needs the full box or a 2-board stack |
| D. 2 x AAA, wake every 60 min | up to 60 min | same energy as 1 x AA, higher cell voltage (easier converter), larger holder than 1 x AA |

Recommendation: A if neutral is present in the box; otherwise B. Asked in round 1, q1.

**Status: resolved (PM round 1, q1): option B.** 1 x AA alkaline, hourly WiFi check-in, immediate check-in on button press. Requirements R1, R2, R4, R21.

Check at the 3.3 V rail (1 x AA usable 3.0 x 0.80 x 0.8 / 8760 = 219.2 uW = 66.4 uA):

| Item | Arithmetic | uW |
|---|---|---|
| Sleep floor | 10 uA x 3.3 V | 33.0 |
| NTC sampling every 2 min | 720 x 3.3 mJ / 86400 s | 27.5 |
| Relay pulses | 20 x 25 mJ / 86400 s | 5.8 |
| Hourly check-in, 0.5 J | 24 x 0.5 J / 86400 s | 138.9 |
| Total at 0.5 J | | 205.2 (62.2 uA): margin 14.0 uW = 6.4 % |
| + 2 button check-ins/day at 0.5 J | 2 x 0.5 / 86400 | +11.6 -> 216.8: margin 1.1 % |
| Total at 0.4 J (24 + 2 wakes) | 66.3 + 26 x 0.4 / 86400 | 186.7 (56.6 uA): margin 14.8 % |
| Total at 0.3 J (24 + 2 wakes) | 66.3 + 26 x 0.3 / 86400 | 156.6 (47.5 uA): margin 28.6 % |

Break-even wake energy with 26 wakes/day: (219.2 - 66.3) uW x 86400 / 26 = 0.51 J.

It fits, but the margin depends on the WiFi wake energy. At the 0.5 J design value the margin is only 6 % (1 % with button use). R2 therefore sets an engineering target of 0.4 J or less per wake (cached BSSID and channel, static IP or fast DHCP, persistent TLS session or no TLS on the LAN broker), with an average-power limit of 186 uW (15 % margin). The 0.8 derating already covers self-discharge, cold and the end-of-life cut-off, and the 30-day low-battery warning (C2) uses capacity inside that derating. If stage 4 measures more than 0.5 J per wake, this becomes a product question again (check-in every 75-90 min, or an AA lithium cell). Not run here: `notes/power_budget.py` (the command needed approval and was denied); the figures above are hand arithmetic with the same inputs as the script.

**Update (PM round 2, q4: cost first, 10-11 months accepted if it saves 0.5-1 USD per unit).** R2 is now 10 months or more (10 x 730 h = 7300 h):

| Item | Arithmetic | Result |
|---|---|---|
| Usable average power | 3.0 Wh x 0.80 x 0.8 / 7300 h | 263.0 uW (79.7 uA at 3.3 V) |
| R2 limit (15 % margin) | 0.85 x 263.0 | 223.6 uW (67.8 uA) |
| Wake-energy limit, 26 wakes/day | (223.6 - 66.3) x 86400 / 26 | 0.52 J |
| Break-even wake energy, 26 wakes/day | (263.0 - 66.3) x 86400 / 26 | 0.65 J |
| Total at 0.5 J design value | 66.3 + 26 x 0.5 / 86400 | 216.8 uW: margin 17.6 %, life 1.92 Wh / 216.8 uW = 8856 h = 12.1 months |
| Total at 0.4 J | 66.3 + 26 x 0.4 / 86400 | 186.7 uW: margin 29.0 %, life 14.1 months |

Consequence: the 0.4 J engineering target is dropped. The 0.5 J design value meets R2 with 17.6 % margin, so firmware can use DHCP or TLS session resumption if needed (up to 0.52 J). The cost saving goes to parts: a cheaper boost converter with higher quiescent current is allowed as long as the sleep total stays at or below 10 uA (R3), and the buffer capacitor only needs to meet R4. Same rule as before: more than 0.52 J per wake measured in stage 4 goes back to the PM. Figures added to `notes/power_budget.py` (round 2 block; not run, command needs approval).

## C2. Relays on battery, and the state when the battery is empty (must, resolved)

Monostable relay: holding coil 200-400 mW. In the heating season (about 4380 h), with heating on 30 % of the time per zone: 0.2 W x 0.3 x 4380 h = 263 Wh per relay. Battery has 1.3-6 Wh. Over budget by about 100 times. Not possible.

Latching (bistable) relay: energy only when it switches. 25 mJ per pulse x 20 pulses/day x 365 = 183 J/yr = 0.05 Wh/yr. Feasible (about 2 % of 1 x AA). Each relay needs an H-bridge or a 2-coil type with 2 drivers; the pulse is about 130 mA at 3 V for 50 ms, which the buffer capacitor from C1 can supply.

New problem: a latching relay keeps its last state when the battery dies or is removed. If heating was on, the valve stays open until a person acts. Resolution options:

| Option | Behaviour | Cost |
|---|---|---|
| A. Firmware switches both relays OFF at a low-battery threshold that still keeps 2 pulses of energy, and sends low-battery alerts starting at least 30 days earlier | heating off when battery is empty; frost risk only if nobody replaces the battery for weeks | zero BOM cost; battery voltage measurement |
| B. As A, plus a hold-up capacitor that fires the OFF pulses even if the battery is pulled out | OFF also on sudden battery removal | about 0.5-1 USD, 50 mJ storage (0.1 F at 3 V holds 0.45 J) |
| C. Keep last state | no cost; heating may run without control until the battery is replaced | zero |
| D. Mains-powered with monostable relays (C4) | relays drop OFF on power loss naturally | part of C4 option A |

Recommendation: A (OFF plus early alerts). Asked in round 1, q4.

**Status: resolved (PM round 1, q4): option A.** Latching relays (R7). Firmware switches both outputs OFF at a cut-off that still holds at least 2 pulses (2 x 25 mJ = 50 mJ, R8). Low-battery warnings go to the app at least 30 days earlier (R5): 30 / 365 = 8.2 % of usable capacity, design threshold 10 %. No hold-up capacitor for sudden battery removal; BOM cost zero. Relay energy: 183 J/yr = 0.05 Wh/yr, 1.7 % of 3.0 Wh.

## C3. 230 VAC reinforced isolation, batteries and antenna in a 503 box (must, resolved)

Isolation rule (IEC 60664-1 / EN 60730-1, working voltage 250 V RMS, pollution degree 2, FR4 material group IIIb, fixed installation overvoltage category III, 4 kV impulse):

- Reinforced (mains to SELV, which includes the probes, the button, the battery): creepage 2 x 2.5 mm = 5.0 mm; clearance at the next impulse step 6 kV = 5.5 mm. Design rule: 6.0 mm creepage and clearance, PCB slot if the copper gap is shorter.
- Basic (relay output 1 to relay output 2 if they can be on different circuits): creepage 2.5 mm, clearance 3.0 mm.

Area check, single-sided placement:

| Zone | 16 A relays (HF115F-L) | 5 A small latching relays (about 20 x 10 mm) |
|---|---|---|
| Relays | 2 x 29 x 12.7 = 737 mm2 | 2 x 20 x 10 = 400 mm2 |
| 4-pole terminal (COM1 NO1 COM2 NO2), 5 mm pitch | 160 mm2 | 160 mm2 |
| Contact routing, relay-to-relay gap | 200 mm2 | 150 mm2 |
| Isolation barrier 6 mm across the 42 mm board width | 252 mm2 | 252 mm2 |
| Mains zone total | about 1350 mm2 | about 960 mm2 |
| WiFi module + antenna keep-out (ESP32-C3-MINI-1 13.2 x 16.6 plus edge keep-out) | 350 mm2 | 350 mm2 |
| Converter, RTC, button, LED, 2 probe terminals, passives | 300 mm2 | 300 mm2 |

Available area:

- 3-module device on the standard frame: PCB about 65 x 42 = 2730 mm2. After the mains zone and electronics (16 A: 2730 - 1350 - 650 = 730 mm2; 5 A: 2730 - 960 - 650 = 1120 mm2).
  - 1 x AAA holder (52 x 12 = 624 mm2): fits in both.
  - 1 x AA holder (57 x 16 = 912 mm2): fits only with 5 A relays.
  - 2 x AAA (50 x 24 = 1200 mm2) or 2 x AA (57 x 31 = 1767 mm2): do not fit on one board. They need a second board stacked behind (depth budget: 1.6 + 16 relay + 6 gap + 1.6 + 15 cell = about 40 mm, at the 38 mm limit) or the full box.
- Full 503 box behind a blank plate: about 90 x 55 = 4950 mm2, minus about a third for wiring. Everything fits on one board, including 2 x AA.
- 2-module device: 42 x 42 = 1760 mm2. The mains zone alone uses 55-75 %. Not feasible with batteries.

Antenna: the module antenna must sit at a board edge facing the front plate, at least 10-15 mm from the relays and from the battery can, with no copper under it. The masonry wall and the mains wires behind cost about 3-10 dB of range. Metal support frames must not be used (plastic frame required).

Resolution options:

| Option | Fits | Cost |
|---|---|---|
| A. 3-module device on the standard frame, 5 A relays, 1 x AA (or mains module instead of the cell) | yes, one board, tight | lowest; works with all plate brands |
| B. 3-module device with 16 A relays and 1 x AAA | yes, one board | 1 x AAA fails C1 |
| C. Full 503 box, custom blank cover, any battery choice | yes, easy | custom cover plate tooling or 3D print; uses the full box (no other socket in it) |
| D. 3-module device, 2-board stack for 2 x AA | yes, at the depth limit | +1-2 USD (second PCB, board-to-board connector, insulating sheet) |

Recommendation: A. Asked in round 1, q2 (size) and q3 (load, which sets relay size).

**Status: resolved: option A.** Source: PM round 1, q2 (delegated to engineering) and q3. 3-module device on the standard plastic support frame (R12: PCB 65 x 42 mm or less, 38 mm deep or less), 5 A small latching relays, 1 x AA holder. Area: 2730 - 960 (mains zone) - 650 (electronics) = 1120 mm2 free, AA holder 912 mm2, 208 mm2 left (19 % of the free area): tight, checked again in stage 5 layout.

Load check for 5 A contacts (R6), worst case per output = 4 thermoelectric heads at 230 V, using conservative figures (about 0.3 A steady, up to about 1 A inrush each for a short time while the PTC element is cold):

- Steady: 4 x 0.3 A = 1.2 A, 24 % of 5 A.
- Inrush: 4 x 1 A = 4 A, 80 % of the 5 A continuous rating. Because the peak stays below the continuous rating, its duration does not matter for the contact.
- Typical 230 V heads draw much less (about 2-3 W, about 10-15 mA steady; 4 heads about 55 mA), so the conservative figures leave margin. Real head datasheet values are checked in stage 3.
- Zone valve motor or boiler enable: well below 1 A, inductive. Covered by the 2 A at cos phi 0.6 requirement.
- Electrical life: 20 operations/day x 365 x 10 years = 73k, so the relay needs 100k or more.

## C4. Mains in the box (should, resolved, linked to C1)

To switch a 230 VAC actuator, the live conductor (L) must already come into the box. The neutral (N) is often there too, but not always. If L and N are present, a small isolated AC/DC module (for example Hi-Link HLK-PM03, 3.3 V 3 W, about 34 x 20 x 15 mm, reinforced isolation, about 0.1 W no-load class) powers the device permanently:

- C1 disappears: WiFi always connected, commands in about 1 s.
- C2 simplifies: monostable relays possible, OFF on power loss by design.
- Cost: about 2-3 USD for the module plus input fuse and varistor; adds mains-side surge and EMC tests (EN 61000-4-4, -4-5) and a larger mains zone (+700 mm2), which competes with the battery holder area (no battery needed then).
- Energy: about 0.3 W x 8760 h = 2.6 kWh/yr, about 0.8 EUR/yr.

Without N, a "no-neutral" supply through the load is not advised: thermoelectric valve heads would see leakage current. Asked in round 1, q1.

**Status: resolved (PM round 1, q1): mains not used as a supply.** The PM chose 1 x AA (C1 option B). 230 VAC appears only on the relay contacts and the output terminals; no N terminal, no AC/DC module (R30). Consequences: no mains surge or EFT tests on a supply input (the contacts still need them as switched ports), no 700 mm2 converter zone, and remote commands take up to 60 min instead of about 1 s.

## C5. Probe type, accuracy, and cable length (should, resolved)

| | NTC 10 k, B 3950, 1 % | DS18B20 digital |
|---|---|---|
| Accuracy 10-30 C | +/-0.5 C with 1 % NTC, 0.1 % reference resistor, ratiometric 12-bit ADC and one-point calibration | +/-0.5 C (-10 to +85 C) per datasheet |
| Energy per reading (2 probes) | divider 3.3 V / 20 k = 165 uA for 1 ms, about 0.5 uJ; the MCU wake (3.3 mJ) dominates | 2 x 1.5 mA x 3.3 V x 0.75 s (12-bit) + MCU light sleep = 7.75 mJ; at 2 min = 64.6 uW, which is 42 % of the 1 x AA WiFi share |
| Cable length | 2 wires, 10 m or more with RC filter | 3 wires (or parasitic 2-wire), 10-30 m |
| Cost | about 0.5-1 USD per probe with cable | about 1-2 USD per probe |

Recommendation: NTC 10 k 1 %, accuracy +/-0.5 C over 10-30 C, cable up to 5 m (option 15 m). Note: the probe is in the room and touchable, so the probe circuit is SELV with reinforced isolation from mains (C3). If the probe cable shares a conduit with 230 V wires, the cable must be rated for 450/750 V or run in a separate conduit (installation rule CEI 64-8).

**Status: resolved (PM round 2, q1): NTC option.** Standard 2-wire NTC 10 k probes, cable up to 5 m, +/-0.5 C over 10-30 C, 0.5-1 USD per probe (R13, R14, R15). 5 m of 2-wire cable adds about 2 x 0.5 ohm, under 0.01 C error at 10 k. Energy effect is negligible (0.5 uJ per reading against the 3.3 mJ MCU wake).

## C6. Board area with the real parts (must R12, R9, R22; open, stage 2)

Stage 2 numbers (architecture.md section 6) replace the C3 estimate:

| Item | C3 estimate | Stage 2 | Change |
|---|---|---|---|
| Relays | 2 x 20 x 10 = 400 mm2 | Panasonic DW (the 20 x 10 mm TE PE type is not reinforced: 3.2 / 4 mm coil-contact) 2 x 24 x 10 = 480 mm2 | +80 |
| Bulk capacitor for R4 | not counted | 8200 uF, 12.5 mm can: 13.5 x 13.5 = 182 mm2 | +182 |
| External ADC, drivers, programming pads, 15 % routing | inside 300 mm2 | 400 mm2 + J2 112 mm2 | +212 |
| Total demand | 2522 mm2 (208 free) | 2972 mm2 | board 2730 mm2: **-242 mm2** |

Proposed engineering fix (no BOM cost, no requirement change): AA cell on two end clips instead of a full holder; SMD parts no higher than 1.2 mm (drivers, NTC front end, divider, ADC, decoupling) placed under the cell between the clips: 45 x 12 = 540 mm2, balance +298 mm2. To check in stage 3 (clip height under the cell) and stage 5 (layout). If it fails, PM question: double-sided assembly (about +1-2.5 USD per unit at qty 10) or a 2-board stack (+1-2 USD, C3 option D).

**Status: engineering fix accepted (orchestrator, stage 2).** AA on end clips with low SMD parts under the cell; checked in stage 3 (clip height) and stage 7 (layout). PM question only if it fails.

## C7. Sleep current R3 against the bulk-capacitor leakage (must R3, R4; open, stage 2)

R4 needs storage: an end-of-life cell (1.0 V, 0.5 ohm) gives at most 1.0^2 / (4 x 0.5) x 0.8 / 3.3 V = 121 mA at 3.3 V even with an ideal converter, against 350 mA TX peaks. The required 4.4 mF effective (8200 uF nominal) is only practical as an aluminium electrolytic or polymer capacitor.

| Item | Arithmetic | uA |
|---|---|---|
| Datasheet leakage, aluminium electrolytic | 0.01 CV = 0.01 x 8200 uF x 6.3 V (2 min at rated voltage) | 517 |
| Low-leakage series (0.002 CV) | 0.002 x 8200 x 6.3 | 103 |
| R3 limit (sum of datasheet values) | | 10 |
| Rest of the sleep budget | 8.90 - 2.0 (architecture.md 4.1) | 6.90 |
| Allowed for the capacitor | 10 - 6.90 | 3.1 |

No electrolytic datasheet value meets R3; leakage at 52 % of rated voltage after conditioning is much lower but is only known from measurement. Options: (a) R3 evidence for the bulk capacitor becomes a bench measurement (leakage at 3.3 V after 24 h, <= 2 uA design value, <= 3.1 uA limit), limit unchanged; (b) as (a) plus a P-FET that disconnects the capacitor between check-ins (+0.05 USD, inrush/recharge to solve); (c) no bulk capacitor: R4 fails. Recommendation: (a). This changes the verification method of R3, not its limit: decision for the orchestrator / PM.

**Status: resolved (orchestrator, stage 2): option (a).** R3 is derived, not a PM number, and its 10 uA limit is unchanged. Active parts are verified by datasheet sum; the bulk capacitor by a first-article bench test (leakage at 3.3 V after 24 h <= 2 uA design, <= 3.1 uA limit). Fallback if the bench fails: option (b). Listed in the SUMMARY first-article risks.

## C8. R1 load range against the boost current limit (must R1, R3, R4; open, stage 2)

R1 acceptance asks 3.0-3.6 V "for cell voltage 0.9-1.65 V at 0-350 mA load". Continuous 350 mA at 3.3 V from 0.9 V needs 0.35 x 3.3 / 0.85 / 0.9 = 1.51 A input current: above the 1.5 A that R4 allows, and above what a 0.5 ohm cell can deliver (121 mA at 3.3 V maximum, C7). Boosts with a higher limit and start at <= 0.9 V have about 17 uA Iq (TPS61021A class), which breaks R3 on its own. The chosen TPS610995 (0.8 A peak limit) delivers about 102 mA continuous at 1.0 V / 0.5 ohm, 87 mA at 0.9 V / 0.5 ohm, about 255 mA at a fresh 1.65 V cell (1.65 x 0.6 x 0.85 / 3.3).

Proposed R1 acceptance: "3.0-3.6 V for cell 0.9-1.65 V at 0-80 mA continuous; 350 mA peaks per R4 (5 ms, from the bulk capacitor)". Consequence: a WiFi connect (about 100 mA average at 0.5 J) works down to about 1.0 V cell EMF at 0.5 ohm, so the low-battery cut-off is set at 1.02 V rest (architecture.md 2.6), and the capacity below it lies inside the 0.8 derating of R2. Changing R1 acceptance is a requirement change: decision for the orchestrator / PM.

**Status: resolved (orchestrator, stage 2).** The 350 mA in R1 was an engineering figure, not a PM number; the product outcome (1 x AA, >= 10 months, hourly check-in) is unchanged. R1 now reads 0-80 mA continuous with 350 mA peaks per R4; cut-off 1.02 V rest, inside the R2 0.8 derating.

## C9. Unit cost R28 against the relay choice and board size R12 (should R28, must R6/R9/R12; open, stage 3)

change: cost, size

Stage 3 found no latching relay in JLCPCB/LCSC stock that has a 3 V coil, 5 A 250 VAC and >= 4 kV reinforced coil-contact insulation (notes/parts-relay.md, "Cost alternatives").

| Item | (A) Panasonic ADW1203TW, 65 x 42 mm | (B) Omron G5RL 3 V, 75 x 48 mm |
|---|---|---|
| Non-relay parts (4 parts notes) | 13.9 | 13.9 |
| Relays, 2 x | 2 x 8.5 (7-10 each) = 17.0 | 2 x 3.5 = 7.0 |
| JLCPCB batch costs / 10 units (PCB 5 + setup 8 + stencil 8 + 18 extended x 3 + joints 2.4 = 77.4) | 7.74 | 7.74 |
| Total, 16-bit SGM58031 ADC | 38.6 USD | 28.6 USD |
| Total, 12-bit ADC instead (saves about 4 USD; error check below) | about 34.6 USD | about 24.6 USD |
| R6 inductive / R9 reinforced evidence | not in catalog (Panasonic statement needed) | 16 A, 10 kV impulse, 8 mm creepage (listing, to verify) |
| Area | fits 65 x 42 with the AA-clip fix (C6, +298 mm2) | relays +265 mm2: 65 x 42 left with +33 mm2, not routable; 75 x 48 = 3600 mm2 fits the 503 box interior (about 96 x 58, C3) behind a blank plate |
| Supply | not stocked, hand-solder | not at LCSC, hand-solder, lead time up to 190 days at one distributor |

12-bit ADC check (engineering, no PM decision): divider slope at 20 C = 3.3 V x 0.25 x 0.044 /C = 36 mV/C; 1 LSB INL at 2.048 V FSR = 1 mV = 0.03 C; gain and reference errors cancel in the ratio (notes/parts-ntc.md). The R14 worst case moves by about +0.03 C, inside the margin of option B (0.459 C). To confirm in stage 4.

Neither option meets R28 (25 USD) with the 16-bit ADC. With the 12-bit ADC, (B) meets it, and (A) misses it by about 10 USD. Board format was delegated to engineering (PM round 1, q2), but a blank-plate 75 x 48 mm board changes R12 and the look of the product, so the product person decides. Question: inbox/questions.json, round 3, q1.

**Status: open, waiting for PM round 3.**
