# home-thermostat-3: conflicts (stage 1, round 1)

Status key: OPEN = waiting for the product person (inbox/questions.json), RESOLVED = no product decision needed (choice is inside the brief).

**Round 1 resolutions (inbox/answers.md):** all of C1-C5 are RESOLVED.
- C1 (q1): boost converter after the 2 AAA cells, 3.3 V rail (R3).
- C2 (q2): Wi-Fi wake every 60 min, > 1 year with ordinary firmware (R5, R7).
- C3/C4 (q3): cells face the user behind the cover plate, under a screw-fixed battery cover; 230 V parts and low-profile relays face the box; insulated probe tips (R33); instruction to switch off the heating breaker before a battery change (R34). Parts on both faces, depth <= 30 mm stays the target (R2).
- C5 (q4): 25 USD covers the board, relays, holder and both 2 m probes; plate and frame excluded (R30).

**Round 2 resolutions (inbox/answers.md):** all of C6-C9 are RESOLVED with the recommended options.
- C6 (q1): fast-reconnect firmware, <= 50 uAh per Wi-Fi wake, control wake <= 20 ms; budget 75.3 uA (R7).
- C7 (q2): keep R3 and the 4.7 mF bulk cap (R24 comes with it); R23 becomes a firmware cut-off at VBAT <= 2.0 V that resets both relays OFF.
- C8 (q3): external 16-bit ratiometric ADC, +~1.5 USD; R30 total to be confirmed in stage 3 (R17).
- C9 (q4): R9 pulse energy limit <= 9 mJ, keep the 30 ms pulse.

**C10. Prototype cost against R30 (RESOLVED by the operator, 2026-10-20).** Parts about 23.9 USD (relays 9.1, consigned Panasonic ADW1103HLW) + PCB/assembly share for 10 units gives about 27-28 USD > 25 USD.
change: cost: prototype unit cost accepted at about 27-28 USD (limit 28.00 USD); 25 USD stays the production target. Keep the ADW1 relays (confirmed insulation and height) over the unconfirmed FH42L clone; size first, then price.
**C11. Prototype cost estimate 37.5 USD against the accepted 28 USD (OPEN, operator decision).** notes/parts.md section 2: parts 24.5 USD (relays 10.2 at 5.11 each, BT1 2.4, probes 2.4) + one-off order costs about 13 USD per unit over 10 units (PCB 0.5, assembly setup and stencil both sides 3.0, extended-part fees 3.0, joints 1.0, relay/THT hand soldering 2.5, shipping 3.0; all estimates). Range 32.4-42.4. None of the options changes the circuit. Options: (1, recommended) accept about 33-38 USD for the 10 prototypes, since the one-off fees fall with volume and the 25 USD production target stays; (2) count the probes and shipping outside the unit price (about 32 USD); (3) single-sided assembly with the relays and THT hand-soldered at home (saves about 2-3 USD per unit, more manual work).

**Round 3 resolutions (inbox/answers.md, corrected round 3; the first round-3 block repeated round 2 by mistake and is ignored):**
- C11 RESOLVED (q4): accept about 33-38 USD per prototype (R30 limit 38.00 USD); 25 USD stays the production target.
change: cost: prototype unit cost accepted at 33-38 USD (PM round 3, q4).
- C12 RESOLVED (q1): R3 guaranteed from the 2.0 V firmware cut-off, 350 mA burst; keep the small bulk capacitors (2 x 3300 uF), which also restores the R6 floor.
- C13 RESOLVED (q2): lithium AAA supported (R35), voltage-limiting part <= 0.3 USD.
- C14 RESOLVED (q3): 300/500 V rated probe cable or sleeve, screw terminal for the probes (R33), +0.5 USD.
- R4 charged-cap reversed insertion (operator): simulated peak per FET recorded; verified by a 20-insertion prototype test because the CJ2305 pulsed rating is not available.

**C15. Layout on 2 layers (RESOLVED, PM 2026-10-21).** The 2-layer attempt at 74 x 46 mm left 95 unconnected nets. Board size is fixed by the 503 box.
change: layers: 2 -> 4 copper layers (inner GND and power), cost accepted by the PM (R31 superseded for layers).

**C16. Board size (RESOLVED, PM round 4 q1).** 74 x 46 mm cannot hold the cells, the 230 V zone with its 6 mm gap and the antenna keep-out (notes/layout-escalation.md).
change: size: 74 x 46 -> 88 x 46 mm with two 8 mm screw-post notches (R1 option 1, fits the 503 box). Large parts get fixed positions from the architecture floorplan (PM round 4 q1).

Also: R18 reworded (standard 3.3 V TVS + series R limits the ADC pin current); R32 button read at the once-a-minute wake. Datasheet numbers that come from search summaries are allowed with "source: search summary, unverified" and a margin, and are checked on the first prototype.

## Reference numbers (with sources)

| Item | Value | Source |
|---|---|---|
| 503 box, inner length x depth | 96 mm x 50 mm | Vimar V71303 data sheet (96 x 59 x 50 inner); BTicino 503E (ext. 108 x 74 x 53.5, inner depth 50) |
| 503 box, inner height | 50 to 59 mm depending on maker; design uses 50 mm (worst case) | Vimar V71303; BTicino/CAME TA350 sheet (96 x 59 x 49.8) |
| Support frame (BTicino 503SA) depth | 17 mm | bticino.com 503SA |
| Frame fixing screws | 2 bosses at 83.5 mm centres on the long axis | standard 503 frame fixing (verify on a physical box) |
| Usable device envelope (proposal) | board <= 88 x 46 mm with two 8 mm boss notches (or <= 74 x 46 mm without notches); total thickness <= 30 mm | 96 - 2x4 mm wall clearance; 50 - 2x2; depth 50 - 10 (frame clips intruding behind wall surface) - 10 (cable loop reserve for 3 x 1.5 mm2 + 2 probe cables) = 30 mm |
| AAA cell | 10.5 mm dia x 44.5 mm | IEC 60086 LR03 |
| 2xAAA PCB holder (Keystone 2468) | 53 x 24.6 x 13 mm | Keystone / Digi-Key 2468 |
| ESP32-C3 / C6 / BL602 supply | 3.0 to 3.6 V | Espressif hardware design guidelines; BL602 spec |
| nRF7002 supply | 2.9 to 4.5 V | Nordic nRF7002 PS v1.1 |
| SiWG917 module supply | 1.71 to 3.63 V; 55 uA associated standby (1 s DTIM); 2.5 uA deep sleep; ~14.8 USD at qty 1 | Silicon Labs SiWG917Y datasheet; cnx-software; Digi-Key |
| Wi-Fi wake (ESP32-C3) | ~1 s to IP, 1.4-3 s to MQTT broker; ~80-100 mA average while awake | Espressif forum / community measurements (to be replaced by datasheet + measurement in stage 2) |

Energy assumptions used below: usable battery capacity = 1000 mAh x 0.85 (pulse load and 0.9 V/cell cut-off) x 0.97 (self-discharge ~3 %/yr) = 825 mAh; indoor temperature costs no extra derating. Battery mid-life voltage 2.6 V. Boost efficiency 88 % at mA loads, 75 % at uA loads.

---

## C1. "Two AAA used directly" against the Wi-Fi SoC and the relay coils (OPEN, must-level, q1)

Arithmetic:
- Fresh alkaline pair: 3.2 V open circuit; 2.8-3.0 V under load after the first ~10 % of capacity; end of life 2.0-1.8 V (1.0-0.9 V/cell).
- ESP32-C3/C6 and BL602 need >= 3.0 V at the pin. Wi-Fi TX peak ~350 mA through the pair ESR (~0.5 ohm fresh, >1 ohm aged) drops 0.17-0.35 V, plus ~20 mV for the reverse-polarity FET. Needed open-circuit voltage >= 3.0 + 0.17 + 0.02 = 3.19 V = 1.6 V/cell. Alkaline cells sit above 1.6 V only when brand new: usable capacity is about 0-5 %. Battery life: days, not a year.
- Lithium AAA (Li-FeS2): 1.8 V/cell fresh = 3.6 V pair, at the ESP32 absolute maximum; under a 350 mA pulse the plateau is ~1.4 V/cell = 2.8 V < 3.0 V. Also fails.
- nRF7002 (2.9 V min) fails the same way.
- Latching relay with a 3 V coil: must-operate voltage typically 75-80 % = 2.25-2.4 V. At 2.0 V end of life it does not switch reliably.

Options:
1. (Recommended) Add a small boost converter (2 AAA -> 3.3 V, quiescent current ~1 uA class). Cost about +1.0-1.5 USD and ~40 mm2 of board. Uses the cells down to 0.9 V/cell (~85 % of capacity), runs the ESP32-C3 and the relay coils from a stable 3.3 V rail. The cells are still the only source; "directly" becomes "through a converter".
2. Use a Wi-Fi part that runs from 1.71 V: Silicon Labs SiWG917 (1.71-3.63 V). Truly "direct" from the cells and allows a permanently connected link (see C2 option c). Cost about +12 USD per unit (module ~14.8 USD vs ~2.5 USD for an ESP32-C3 module): total cost about 35 USD, breaks the 25 USD target. Relay coils still need ~2.4 V, so the last ~15 % of the battery cannot switch relays unless a coil boost is added anyway.
3. Keep an ESP32-class part with no converter and accept usable capacity of ~5 %: battery life of weeks. Breaks the 1-year target. Not recommended.

Recommendation: option 1 (size and price are priorities 1 and 2; option 1 is cheapest and smallest).

## C2. 1-year battery life against Wi-Fi command latency and relay drive (OPEN for latency, q2; RESOLVED for relay type)

Allowed average current: 825 mAh / 8760 h = 94 uA with no margin; with a 15 % design margin, <= 80 uA at the battery.

Fixed floor (battery side): ESP32-C3 deep sleep 5 uA -> 5 x 3.3 / (2.6 x 0.75) = 8.5 uA; boost quiescent 1 uA; one control wake per minute (20 ms at 25 mA to read both NTCs and run the schedule) = 8.3 uA at 3.3 V -> 12 uA at the battery. Floor = 21.5 uA. NTC dividers powered only during the reading: < 0.1 uA average. RTC: inside the SoC deep-sleep figure (32.768 kHz crystal).

Energy per Wi-Fi wake (connect, MQTT publish/poll, sleep):
- typical: 1.5 s x 100 mA at 3.3 V = 0.50 J -> 60 uAh per wake at the battery
- optimised firmware (stored channel/BSSID, static IP, TLS session resumption): 0.8 s x 80 mA = 0.21 J -> 26 uAh per wake

| Poll / report interval | Avg current (typical) | Life (typical) | Avg current (optimised) | Life (optimised) |
|---|---|---|---|---|
| 1 min | 3630 uA | 9 days | 1560 uA | 22 days |
| 5 min | 743 uA | 46 days | 329 uA | 104 days |
| 15 min | 262 uA | 131 days | 124 uA | 277 days |
| 30 min | 142 uA | 242 days | 73 uA | 472 days |
| 60 min | 82 uA | 421 days | 47 uA | 729 days |
| Always connected (DTIM, ESP32-C3) | ~1.8 mA | ~19 days | | |
| Always connected (SiWG917, 55 uA + floor) | ~75 uA | ~1.2 years | | |

Result: with an ESP32-class part, 1 year needs a command latency of 30 min (optimised firmware) to 60 min (typical). Commands within 1-5 min cost the 1-year target. The heating schedule and the local temperature control are not affected: they run on the device every minute without Wi-Fi.

Options (q2):
a. (Recommended) Remote commands and reports every 30 min: ~1.3 years with optimised firmware, ~8 months if the firmware reaches only typical connect times. Firmware connect time becomes a requirement (R7).
b. Every 60 min: >= 1.1 years even with typical firmware. Remote changes feel slow.
c. Within seconds: needs SiWG917 (C1 option 2), +12 USD, ~1.2 years, Wi-Fi router with good coverage.
d. Every 5 min with an ESP32: ~2-3.5 months per pair of cells.

Relay drive (RESOLVED, inside the brief "latching or solid state, both fine"):
- Latching relay: 3 V coil ~150 mW x 20 ms = 3 mJ per pulse. 20 pulses/day -> 7.5 uAh/day (< 0.1 % of the budget). Zero current while the valve is ON.
- Monostable relay: 3 V coil ~40 mA held while the valve head is ON. 2 rooms x 8 h/day = 640 mAh/day at 3.3 V -> battery flat in < 1 day.
- SSR / photo-triac: the input LED needs ~5 mA continuously while ON: 2 x 5 mA x 8 h = 80 mAh/day -> ~10 days. A triac driven by gate pulses every half cycle costs the same order of current and needs mains zero-cross sensing on the battery side.
- Decision: bistable (latching) relays (R9). Thermal valve heads stay ON for hours, so only a latching contact fits the energy budget.

## C3. Reinforced isolation and antenna keep-out against the 503 footprint (OPEN through C4 and q3)

Isolation numbers (230 V mains, working voltage 250 V rms, pollution degree 2, material group III, overvoltage category II):
- Creepage, reinforced = 2 x basic = 2 x 2.5 mm = 5.0 mm (IEC 60664-1 / IEC 60730-1 tables). Design target 6.0 mm (1 mm manufacturing margin).
- Clearance, reinforced: rated impulse one step above 2.5 kV = 4.0 kV -> 3.0 mm; for overvoltage category III (6 kV) -> 5.5 mm. Design target 5.5 mm, so the board is fine in either category.
- Slot in the PCB (>= 1 mm wide) may replace surface creepage, but not clearance.
- Antenna: no copper under the antenna; keep relays, 230 V copper, the battery holder metal and the cells >= 15 mm from the antenna (Espressif guideline for metal near the PCB antenna).

Arithmetic on the board long axis (88 mm, or 74 mm without boss notches):
- 230 V zone: two relays (~20.5 x 10.5 mm each, side by side across the 46 mm height) + 3-pole 5.0 mm terminal (15 x 10 mm) = ~31 mm along the axis.
- Isolation band: 6 mm.
- Antenna end: module 13.2 x 16.6 mm plus 15 mm free distance from the relays.
- Battery holder: 53 mm long; does not fit across the 46 mm height, so it must lie along the long axis.
- Single side, long axis: 31 (HV) + 6 (gap) + 53 (holder) = 90 mm > 88 mm before the module and the antenna distance are placed. Does not fit.

Resolution: put the cells and the low-voltage parts on one face and the relays and the 230 V terminals on the other face (C4), with the holder kept >= 6 mm (creepage) from any 230 V pad projected through the board and a 3.75 kV hipot between the faces. No product decision needed for the layout itself; battery access (q3) decides which face points to the cover plate.

## C4. Size: cells + two relays + terminals inside 46 x 88 x 30 mm (OPEN, q3)

Area (one face of an 88 x 46 = 4048 mm2 board, or 74 x 46 = 3404 mm2):
holder 1304 + relays 520 (with pads) + HV terminal 150 + isolation band 276 + Wi-Fi module and antenna clearance 420 + NTC terminal 100 + boost/RTC/protection 300 + relay drivers 200 = 3270 mm2.
At a practical fill of 70 % for through-hole and 230 V spacing: 3270 / 0.7 = 4670 mm2 > 4048 mm2. Single-sided does not fit.

Double-sided: face A (cells side) 1304 + 420 + 100 + 300 = 2124 mm2 -> 3030 mm2 at 70 %: fits. Face B (230 V side): 520 + 150 + 276 + 200 = 1146 mm2: fits.
Depth: holder 13 + PCB 1.6 + relay 15.5 = 30.1 mm against a 30 mm limit. Margin -0.1 mm.

Options:
1. (Recommended) Low-profile parts: AAA clips instead of a full holder (~12 mm) or relays <= 12.5 mm tall: depth ~27-28 mm, 2-3 mm margin. Cost about +0-1 USD. Stage 3 picks the parts.
2. Cells sunk into a slot in the PCB (cells half below the board surface): saves ~5 mm depth, costs ~25 mm x 21 mm of board area and a routed slot (+~1 USD per board at 10 units).
3. Accept a 33-35 mm deep device and less cable room at the back of the box: risk that a full box does not close. Needs a check on a real installation.

Battery access (q3) interacts: if the user changes cells by removing the cover plate, the cell face points to the plate and the 230 V face points to the box bottom (where the mains wires arrive). That fits option 1 and keeps live parts behind the board.

## C5. Cost <= 25 USD at 10 units (OPEN for scope, q4)

Rough BOM per unit (LCSC/JLCPCB prices, 10 units):

| Item | USD |
|---|---|
| ESP32-C3-MINI-1 module | 2.3 |
| Boost converter + inductor | 1.4 |
| 2 latching relays (>= 3 A 250 VAC, 3 V coil) | 4.0-6.0 |
| Relay drivers (MOSFETs or 2 small H-bridges) | 0.4-1.2 |
| 2xAAA holder or clips | 0.4-2.2 |
| Reverse-polarity P-MOSFET | 0.1 |
| 32.768 kHz crystal (RTC in SoC) | 0.2 |
| NTC dividers, TVS, filters | 0.3 |
| Terminals (230 V 3-pole, NTC 4-pole) | 1.0 |
| Passives, button, LED | 0.7 |
| Parts subtotal | 10.8-15.5 |
| PCB 2-layer ~88 x 46, 10 pcs | 0.6 |
| SMT + through-hole assembly (setup, stencil, ~8 extended parts at 3 USD, shared over 10) | 4.0 |
| Shipping (shared, EU VAT not included) | 2.5 |
| Electronics total | 17.9-22.6 |
| 2 NTC probes 10k B3950 with 2 m cable | 3.0 |
| Electronics + probes | 20.9-25.6 |
| Blank 3-module cover plate + support frame | 3-6 |
| Everything | 24-32 |

Result: plausible for the electronics plus probes (21-26 USD, mid estimate ~23). With the cover plate and frame inside the target, it exceeds 25 USD. With the SiWG917 option (C1 option 2), electronics + probes ~33-38 USD.

Options (q4):
1. (Recommended) 25 USD covers board + relays + battery holder + 2 NTC probes; the cover plate and frame come from the customer's existing series. Fits with ~2 USD margin.
2. 25 USD covers everything including plate and frame: needs ~3-5 USD of cuts (cheaper relays, no-name holder) or accept ~28-30 USD.
3. 25 USD covers the bare board only: comfortable margin.

---

# Stage 2 (architecture) conflicts: OPEN, round 2

The numbers are in `architecture.md` (sections 2-5) and the datasheet facts in `notes/datasheets/`.

## C6. Battery budget: R5 (<= 80 uA) against R7 (<= 60 uAh per Wi-Fi wake) (OPEN, must-level)

Arithmetic (battery side, 2.6 V; architecture.md section 4):
- Sleep floor (R6) = 24.2 uA. This is ESP32-C3 deep sleep 5 uA, 32 kHz crystal 1 uA, boost Iq 0.1 uA and drivers 0.2 uA, converted at 80 % to 10.0 uA, plus 2 uA bulk-capacitor leakage and 12.0 uA for the 20 ms control wake each minute.
- Wi-Fi wake at the R7 limit: 60 uAh per hour = 60.0 uA. Relays (20 pulses/day) + LED = 1.1 uA.
- Total = **85.3 uA > 80 uA**. Life is 825 mAh / 85.3 uA = 403 days: more than 1 year, but with 9 % margin instead of 15 %.
- R5 holds only if a Wi-Fi wake costs <= 54.7 uAh.
- Risk on R6: the floor assumes a 20 ms control wake. Each extra 10 ms adds 6 uA. A 30 ms wake (likely without an optimised boot) gives 30.2 uA > 25 uA.

Options:
1. (Recommended) Tighten R7 to <= 50 uAh per wake (firmware: stored channel/BSSID, kept DHCP lease or static IP, MQTT persistent session with TLS session resumption) and require a <= 20 ms optimised deep-sleep boot. Total 75.3 uA, life 1.25 years. No cost. Bench-measured on the first prototype.
2. Accept R5 at <= 86 uA (1.1 years, 9 % margin), keeping R7 at 60 uAh and the "ordinary firmware" promise.
3. Wi-Fi wake every 70 min instead of 60 (60 uAh -> 51.4 uA, total 76.7 uA). Commands take up to 70 min.
4. Add a low-power co-processor (sub-1 uA standby with RTC) that runs the per-minute control and power-gates the ESP32 between Wi-Fi wakes. The floor drops to about 5 uA and the total to about 66 uA, even at 60 uAh per wake. Cost about +1 USD, a second firmware image, about 30 mm^2 of board.

## C7. Stored energy: R3 burst, R23 relay reset, R24 clock hold-up (OPEN; R3 must, R23 should, R24 could)

Arithmetic (architecture.md 3.1 and 5):
- R3 (350 mA, 5 ms at VBAT 1.8 V through 1 Ohm): the boost needs 1.36 W, and a 1.8 V / 1 Ohm source gives at most 0.81 W. A bulk capacitor on the boost input must cover the deficit: **>= 3.9 mF effective (4.7 mF nominal)** to stay above 1.0 V. With a 0.5 Ohm source the cells alone suffice (input settles at 1.26 V) and ceramics are enough.
- The bulk capacitor is an aluminium electrolytic. Its datasheet leakage limit (0.01 CV) is hundreds of uA; the typical value at <= 55 % of rating is about 1 uA, budgeted at 2 uA, which is 2.5 % of the R5 budget. It is 10 mm in diameter, lies on face B, and costs about 0.2 USD.
- R24 (60 s without cells): needs 3.6 mJ. The 4.7 mF capacitor gives 5.2 mJ from 1.8 V, so it is **met for free** if option 1 below is kept.
- R23 (2 reset pulses from stored energy below cut-off): needs 26.7 mJ, i.e. **19.4 mF** from 1.8 V to 0.7 V. That means a 22-33 mF supercapacitor (+1-2 USD, uA-level leakage, ESR of several Ohm in small sizes). **Not feasible inside the cost and depth budget.**

Options:
1. (Recommended) Keep R3 as written with the 4.7 mF capacitor (R24 comes with it). Change the R23 acceptance to a firmware cut-off: at loaded VBAT <= 2.0 V the firmware resets both relays using the cells (26.7 mJ = 3.7 uAh), reports "battery empty" and stops switching. If the cells are pulled out with a relay ON, it stays ON; R34 already tells the user to switch off the heating breaker first.
2. Add a 22-33 mF supercapacitor, which meets R3, R23 and R24 as written. About +1.5 USD, +2-5 uA leakage (which worsens C6), and a 5-7 mm tall part.
3. Change the R3 source resistance to 0.5 Ohm (fresh to mid-life cells). Use ceramics only (2 x 47 uF), with firmware lowering TX power at end of life. Drop R24 and handle R23 as in option 1. Saves about 0.2 USD and 2 uA, but the end-of-life Wi-Fi bursts become a firmware risk.

## C8. NTC accuracy R17 (+-0.3 C) against cost R30 (OPEN, must-level)

Arithmetic (architecture.md 2.4):
- ESP32-C3 ADC1 after calibration: +-10 mV (0-1.3 V range). The NTC slope at 40 C is 10.5 mV/K (divider kept below 1.3 V), so the error is **+-0.95 C, 3x over R17**. The datasheet does not split the +-10 mV into gain, offset and INL, so a ratiometric layout cannot be proven to cancel it.
- A 16-bit external ADC (ADS1115 class) measuring excitation and NTC node with the same PGA (ratiometric) gives a worst case of **+-0.067 C** (Rf 0.1 % dominates).
- Cost: about +1.5 USD (assumed, stage 3 LCSC price). Together with the low-profile reinforced relays and the bulk capacitor, the C5 estimate for electronics + probes moves from 20.9-25.6 to about 22.8-27.5 USD: R30 is at risk.

Options:
1. (Recommended) External ratiometric ADC (+~1.5 USD). R17 is met with 4x margin from datasheet numbers. Stage 3 confirms the total is <= 25 USD; if not, it comes back here.
2. ESP32 ADC plus a per-unit two-point calibration in the test jig (0.1 % resistors on the NTC inputs, constants stored in flash). 0 USD, but R17 is proven only by bench measurement of each unit, not by datasheet or simulation, and drift over temperature is unknown.
3. RC-timing ratiometric measurement (one capacitor, NTC and reference resistor charge it in turn, time ratio = resistance ratio). 0 USD, about 4 GPIOs, which needs the GPIO budget to be reworked. More firmware work; the error is limited by 3.3 V rail ripple during the timing.

## C9. Relay pulse energy R9 (<= 5 mJ) against the reinforced relay (OPEN, must-level)

Arithmetic (architecture.md 2.5):
- Only a relay with >= 6 mm coil-contact creepage meets R11. The 10 mm TE PE has 4 mm and fails. The Panasonic DW ADW1103HLW (3 V, 45 Ohm, set time max 15 ms, 15.8 mm) passes.
- R9 asks for a 2 x 15 ms = 30 ms pulse at >= 110 % of must-operate. From the 3.3 V rail this is 3.3^2 / 45.5 x 30 ms = **7.2 mJ** nominal (8.5 mJ worst case), more than the 5 mJ of R9.
- Battery effect: 20 pulses/day add 0.73 uA, against 0.51 uA at 5 mJ (+0.2 uA). With C7 option 1 there is no stored-energy consequence.

Options:
1. (Recommended) Change the R9 energy limit to <= 9 mJ per pulse and keep the 30 ms (2x max set time) pulse. +0.2 uA average, no cost.
2. Keep 5 mJ and shorten the pulse to 20 ms (4.8 mJ). That is 1.33x the maximum set time instead of 2x, with less margin for an aged or cold relay.
3. Look for another reinforced 3 V latching relay with a coil <= 140 mW (stage 3 search). Possibly taller or more expensive; it may not exist at <= 15.8 mm.

---

## Assumptions stated without a question (round 2 if the product person disagrees)

- Environment: indoor wall box, operating 0 to +45 C, storage -20 to +60 C, 10-90 % RH non-condensing, pollution degree 2, overvoltage category II, altitude <= 2000 m.
- Safe state when the battery runs out: both relays OFF (valve heads unpowered). Thermal heads are usually normally closed, so the rooms get no heating. Alternative (relays ON, rooms heated) is a round-2 question if the product person prefers it.
- Certification for 10 prototypes: they are not placed on the market (internal or friendly-site trials), so no CE marking and no notified-body tests are needed now. The design still follows the CE route so it does not have to change later: LVD 2014/35/EU (EN 60730-1 + EN 60730-2-9, reinforced isolation, hipot test), RED 2014/53/EU (EN 300 328, EN 301 489-1/-17, EN 62311 RF exposure, EN 18031-1 cybersecurity since 1 Aug 2025), RoHS. A pre-certified Wi-Fi module keeps the RED radio tests small later.
- 230 V side has only L in and two switched L outputs; the valve-head neutrals are joined in the box with a separate connector (saves terminal size).

## R15 antenna to battery distance (2026-10-21)
- Conflict: R15 asked >= 15 mm from the antenna to the battery contacts and cells. On the 46 mm board height (fixed by the 503 box), 46 - 2 x 13 mm holder courtyards - 5.4 mm antenna zone = 14.6 mm at most; a realistic layout gives about 11.5 mm (hwflow floorplan).
- Decision (product person): relax the battery distance to >= 11 mm; keep 15 mm to the relays and 230 V copper. Confirm with a range test on the first prototype.

## U2 replacement (2026-10-22)
- The TPS61299 (SOT-563) failed the escape proof (SW_BOOST 0.6 mm and +3V3 0.4 mm at pads where 0.36 mm leaves); replaced by the MAX17227A (TDFN-8, notes/parts.md 0b).
- Product decisions: R30 limit 38.00 -> 38.50 USD (estimate 38.32 USD); R6 verified at typical values (25.93 uA typ, 26.43 uA at max IQ is a known risk); R3 ceiling 3.4 -> 3.46 V (light-load offset, 3.452 V worst case).
- R35: PM decision 2026-10-22: verified with typical datasheet values; the MAX17227A maximum light-load offset (rail 3.452 V, coil about 3.45 V with lithium cells) is a known risk, checked on the prototype.
- Delta review 2026-10-22 (finding 1): ULPM ripple with VIN near VOUT needs about 60 uF effective on +3V3; C4/C5 2 x 10 uF -> C4/C5/C22 3 x 47 uF 1206 (C96123, basic). R30 central 38.32 -> 38.76 USD against 38.50 (2 x 47 uF: 38.60 USD, peak 3.432 V). RESOLVED, PM decision 2026-10-22: two output caps, C4 and C5 47 uF CL31A476MPHNNNE, C22 removed; R30 limit 38.50 -> 38.60 USD (central estimate 38.60 USD).
- Delta review 2026-10-22 (switching testbench): at the ULPM maximum (+4.6 %) the +3V3 peak including ripple is 3.467-3.475 V, above the R3 ceiling 3.46 V (Note 4: the ULPM accuracy excludes ripple). RESOLVED, PM decision 2026-10-22: R3 is verified with typical datasheet values (as R6/R35); the maximum-offset peak with ripple (3.475-3.486 V with 2 x 47 uF) is a known risk, checked in prototype test 7; `r3_ulpm_max_info` is information only.

## F1 230 V pad against the left screw notch (2026-10-22, RESOLVED, must-level R10)
- Found by the layout floorplan (`hwflow floorplan`, region `mains` minus the conductive notch grown by 6.0 mm). Architecture 6.3 keeps every 230 V copper >= 6 mm from the notch edge (the 503 frame screw is accessible metal with the cover plate off, R10 reinforced 6.0 mm creepage), i.e. outside r = 10 mm of (2.25, 23).
- Numbers: F1 at its anchor (11.5, 14.9, rot 0, back): pad 2 (AC_L, 3 mm round, centre 6.42, 14.9) is 9.11 mm from the notch centre, so its copper is **3.61 mm** from the notch edge (pad 1, AC_L_IN square: 6.18 mm, ok). Need 6.0 mm: 2.4 mm short.
- No layout fix with K1, K2 and J1 at their anchors: a search of F1 positions (0.25 mm grid, 4 rotations, back face) finds none with both pads in the 230 V zone (x <= 20), copper >= 6 mm from the notch and from the K1/K2 coil pads, and no courtyard overlap with K1, K2, J1 or the notch. Between the K1 and K2 courtyards (y 10.85-35.15, 24.3 mm) the notch exclusion leaves only x 13.8-20; J1 (16.1 mm) and F1 (11.0 mm) stacked need 27.1 mm.
- Options (product decision): (1) a smaller 250 VAC T3.15 A fuse that fits next to J1 (part change, stage 3); (2) move the mains/SELV boundary right to make room (architecture: SELV area and the 74 % front fill get worse); (3) state that the frame screw is not accessible (insulating cap or plastic screw, safety argument, R10 evidence); (4) move J1 and F1 together (J1 is a wiring anchor; needs a new floorplan of the 230 V zone).
- RESOLVED, decision 2026-10-22 "smaller fuse, product person" (option 1). The fuse stays the same part (Walter 2010T3.15A250V, C354904, T 3.15 A, 250 VAC, 100-130 A breaking, I2t 98 A2s, UL/VDE/CCC, 0.035 USD); its real body is 8.2 x 4.0 mm (TE5), so the 10.5 x 8 mm terminal-block placeholder footprint was replaced by `thermo:Fuse_Littelfuse_395Series_back` (courtyard 9.0 x 4.5 mm). F1 sits on face B at (14.0, 13.25) rot 0 between the K1 and J1 courtyards: pad copper 6.42 mm from notch_l, 6.73 mm from the K1.A1 coil pad. `hwflow floorplan` no longer reports the isolation failure (FAIL -> PASS with risks). Cost delta 0. Candidates and grid-search proof in notes/parts-230v.md "F1 footprint and notch fit".

## Review 2 decisions (2026-10-22)

### D1. Third 47 uF output capacitor on +3V3 (R3, R30) - DECIDED
- Source: PM, review-2 round, 2026-10-22.
- Decision: add C22, 47 uF 10 V X5R 1206 (CL31A476MPHNNNE, LCSC C96123, same part as C4/C5, 0.168 USD) on +3V3 at U2 OUT. It covers the LPM -> HPM transition dip that the averaged U2 model (MAX17227A_AVG) does not show (R3). `decouples: U2`, group power, placed above C5 at (52.0, 33.9) rot 0.
- Cost: the PM accepted the BOM going about 0.05 USD over R30. R30 limit 38.60 -> 38.79 USD (central estimate 38.60 + 0.168 for C22 + 0.02 for the D2 sheet). With the AAA clip pairs of the same round (BT1/BT2, -1.924 USD) the central estimate is 36.86 USD (hwflow BOM 22.52 -> 20.79 USD). Lowering the limit back is a PM call.
- Open: the sim stage has not been rerun with C22 (no build in this round). The switching testbench COUT (52.6 uF) and the averaged model do not include C22 yet.

### D2. R26 insulating sheet over the 230 V solder joints - DECIDED
- Source: PM, review-2 round, 2026-10-22.
- Decision: a die-cut insulating sheet is a BOM item: MP1, ITW Formex GK-17 (0.43 mm flame-retardant polypropylene, UL 94 V-0, ESPE Manufacturing 24 x 48 in sheets), 26 x 46 mm with an r 4.5 cut-out at the left screw boss, on face A over board x 0-26, held by the battery cover. design.yaml MP1 (footprint thermo:InsulatingSheet_26x46_FaceA, Dwgs.User only, no copper). Drawing: notes/insulating-sheet.md.
- Cost: 0.02 USD per board as material (10.64 USD per sheet / 598 pieces). Open product item: the smallest lot is 9 sheets at 10.64 USD + 20 USD small-order charge = 115.76 USD one-off (11.58 USD per board if charged to the 10 prototypes). Options: an ITW Formex sample, the lot, or a hand-cut sheet from an existing stock.
- Open: the 0.4 mm distance-through-insulation figure for reinforced solid insulation and the Formex GK-17 dielectric/RTI data are not checked against IEC 60730-1 here; confirm with the certification lab.

### D3. F1 breaking capacity - DECIDED, open certification risk
- Source: PM, review-2 round, 2026-10-22.
- Decision: F1 stays Walter 2010T3.15A250V (LCSC C354904, 100-130 A breaking at 250 VAC).
- Open certification risk: "confirm with the lab; alternatives SCT520T (5 kA) / Hongda 40T (1000 A)" (SETsafe SCT520T3.15A250V C49208729, D5.1 x 20 mm, 0.134 USD; Hongda 40T 3.15A250V C48392809, 10.5 x 3.2 mm, 0.157 USD; both need a new footprint and placement in the 230 V zone; notes/footprints.md "F1 breaking capacity").

### Review 2 follow-ups (component engineer, 2026-10-22)
- BT1/BT2: BH-AAA-B5AA001 is a 2-cell holder (62.2 mm). Replaced by one Myoung clip pair per cell (MY-AAA-20-J + / MY-AAA-19-J -, LCSC C6937107 / C6937104, in stock 4289 / 5756). Same refs and nets, no restructure. Cost -1.924 USD per board. BT1 now in the lower row (VBAT_RAW pad 7 mm above the guard block, shorter R4 path), BT2 in the upper row. notes/footprints.md.
- Guard FETs M1/M2 (body-diode surge): RESOLVED by D4 below (AO3413). Original note: The drop-ins that rate the surge (AOS AO3413, AO3415A: single-pulse power curve about 15-25 W at 10 ms against about 3.5 W) have a lower body-diode drop at uA (AO3413 0.27 V, AO3415A 0.22 V at 10 uA, 25 C, datasheet Fig. 6) than the CJ2305 model (0.455 V, assumed). R35 (lithium, guard OFF) needs more than 0.25 V at about 16 uA and 45 C, so it would fail (VBAT_P about 3.47-3.5 V against 3.45 V). Options in notes/parts.md section 0c.

## Review-2 decisions, PM round of 2026-10-22 (D4-D6)

### D4. Guard FETs CJ2305 -> AO3413 (R4, R35, R30) - DECIDED, implemented
- Source: PM, review-2 round, 2026-10-22.
- Decision: M1, M2 = AOS AO3413 (LCSC C86648, +0.10 USD per board for the pair): the CJ2305 has no rating for the dead-board insertion surge (about 4.1 A per FET for a few ms); the AO3413 data sheet covers it (IDM 15 A, Fig. 10 single-pulse power, notes/parts.md 0c). The lower AO3413 body-diode drop (0.27 V at 10 uA, 25 C; about 0.23 V at 45 C) lifts VBAT_P to 3.41-3.49 V with 3.7 V lithium cells, so R35 (3.45 V) had to be reworked by a circuit change.
- Implemented (circuit stage, 2026-10-22): M1/M2 AO3413 (same SOT-23 pinout, G1 S2 D3), model fitted to the data sheet (notes/models.md). R35 rework: M4 (a third AO3413) between C1/C2 and the boost input, ON with the guard, its gate delayed by R41 100 k / C23 10 nF / R42 1 M (gate to VIN_BST); in sleep and on lithium the boost input sits a second body diode lower (VIN_BST 3.10-3.27 V at 3.7 V). Cost +0.098 USD (R35 limiter budget 0.3 USD). Rejected: a lower RSEL setpoint (does not lower VBAT_P; fails the R3 burst), a bleed resistor (fails R6). Numbers in notes/circuit.md "Review-2 round".
- Results (typical values): R35 +3V3 3.389 V DC, ripple peak <= 3.414 V, coil <= 3.376 V; R4 drop 24.9-29.7 mV; R6 25.53 uA (was 24.32, limit 26). hwflow BOM 20.79 -> 20.99 USD; R30 central 36.86 -> 37.06 USD (limit 38.79).
- Open for the PM: (1) R6 margin shrinks to 0.47 uA (cost of the limiter in sleep). (2) R4 at the AO3413 RDS(on) maximum (130 mOhm at -1.8 V) is about 45 mV for the FETs alone (+8 mV copper of the last layout = about 53 mV); the sims verify R4 at typical RDS(on), like R3/R6/R35. (3) requirements.yaml R4 acceptance still names the CJ2305 ("pulsed rating unknown ... verified by test"); wording update is a requirements change, not done here.

### D5. R26 insulating sheet for the prototypes - DECIDED
- Source: PM, review-2 round, 2026-10-22.
- Decision: the prototype sheets are cut from insulating sheet already in stock, >= 0.4 mm thick and UL 94 V-0; the material used is recorded (notes/insulating-sheet.md "Prototypes"). The production order of ITW Formex GK-17 stays on the open list. MP1 stays in design.yaml as the production part.

### D6. R30 limit - DECIDED
- Source: PM, review-2 round, 2026-10-22.
- Decision: R30 stays at 38.79 USD. Central estimate after D4 and the R35 limiter: 37.06 USD (hwflow BOM 20.99 USD).

### D7. R4 forward drop 50 -> 55 mV at the AO3413 RDS(on) maximum - DECIDED
- Source: PM, review-2 round, 2026-10-22 (question: typical corner / copper <= 7 mOhm / relax to 55 mV; answer: relax to 55 mV).
- Numbers: FETs at RDS(on) max 29.705 mV x 1.53 = 45.45 mV; copper budget (55 - 45.45) / 0.7 A = 13.64 mOhm (last layout 11.7 mOhm). About 5 mV less cell voltage at end of life (R3/R25 battery-life margin).
- Implemented: requirements.yaml R4 acceptance (55 mV, AO3413 wording); design.yaml vdrop_guard_mv max 55, budgets guard_copper max_mohm 13.64.

## Layout round 2 decisions, PM 2026-10-22 (D8-D10)

Analysis: notes/mains-zone-options.md (the right-angle pluggable WJ2EDGRC J1 collides with the notch_l screw boss).

### D8. J1 = fixed rising-clamp screw terminal KF128-5.08-3P (option A) - DECIDED, implemented
- Source: PM, layout round 2, 2026-10-22.
- Decision: J1 = Cixi Kefa KF128-5.08-3P-AA (LCSC C474953, about 0.30 USD; was WJ2EDGRC C8377 + plug, 0.33), back face, wire entry toward -x, pins at x 17.0. R12 IP2X is argued by location: J1 faces the box and cannot be reached with the plate off (R26); the part itself is an open screw terminal.
- Drawing (Kefa KF128-5.08 rev A 2021-03-13): body 15.24 x 10.7 x 14.1 mm high, 0.60 mm interlock nubs at one end, hole d1.40. Footprint thermo:TerminalBlock_KF128-5.08-3P_back (notes/footprints.md).
- Deviation from the PM numbers: the pins sit at y 18.90 / 23.98 / 29.06 instead of 18.30 / 23.38 / 28.46 (0.60 mm down). With the body as drawn, the nubs fall at the pin-1 end; at y 18.30 they reach y 15.16, which touches the F1 body (y 15.25) and overlaps the F1 courtyard (y 15.5) by 0.59 mm. At y 18.90 all 6 mm distances still hold (notch 9.28, K1.A1 8.78, K2.A2 7.18 mm pad edge to pad edge). This is geometry only, with no product impact.
- R2: 14.1 mm on face B, below the 15.8 mm relay. R30: -0.03 USD.

### D9. AC nets on F.Cu inside the mains region - DECIDED, implemented
- Source: PM, layout round 2, 2026-10-22.
- Decision: AC_L_IN, AC_L, AC_ROOM1 and AC_ROOM2 may use F.Cu as well as B.Cu, only inside the mains region (x <= 20), which lies under the MP1 insulating sheet (R26). design.yaml nets: `layers: [B.Cu, F.Cu]` (In2.Cu is no longer listed). This removes the weak spot where ROOM1 passes F1.1 (L_IN) at 0.2-1.1 mm on B.Cu.

### D10. J1 pin order and labels - DECIDED, implemented
- Source: PM, layout round 2, 2026-10-22.
- Decision: top to bottom, pin 1 L_IN (next to F1.1), pin 2 ROOM1, pin 3 ROOM2. The face B silkscreen shows L / R1 / R2 next to the pins (B.SilkS text in the footprint, on the rear side at board x about 23.6). architecture.md section 7 is updated. Installer note for SUMMARY.md and the installer sheet: notes/mains-zone-options.md "Installer note (D10)".

## Review-2b decisions, PM round of 2026-10-22 (D11-D13)

### D11. Functional spacing between different 230 V nets - DECIDED
- Source: PM, review-2b round, 2026-10-22. Review 2b B1: AC_L_IN / AC_L 0.23 mm (across F1: a short bypasses the fuse), AC_ROOM1 0.23 mm from F1.1 and 0.26 mm from J1.1 (across an open contact); the .kicad_dru set only mains-to-SELV 6 mm.
- Decision: 1.5 mm clearance and creepage between different AC nets (IEC 60664-1 Table F.4, 250 V, OVC II, PD2, printed board), as the new design.yaml field `board.isolation[].between_mm: 1.5` (approved new field; hwflow implements it in the router, DRC and post-layout checks). One more layout round.

### D12. R12 acceptance amended: finger safety by location - DECIDED
- Source: PM, review-2b round, 2026-10-22 (review 2b M3). The KF128-5.08-3P is not IP2X; with D8, R12 is met by location (J1 on face B toward the box, not reachable by the IEC 61032 test finger with the plate removed), verified by inspection on the prototype. requirements.yaml R12 updated.

### D13. J1 wire dressing checked on the first prototype - DECIDED
- Source: PM, review-2b round, 2026-10-22 (review 2b M2). 4.65 mm of straight wire fits before the left screw boss; 1.5 mm2 wire needs a 9-12 mm bend radius. Prototypes are ordered as designed; wiring in a real 503 box is checked on the first unit; if it fails, rev B uses a top-entry terminal (height against R2).

### D14. AC_ROOM1 track width from its current (IPC-2221) - DECIDED on the PM's behalf (goal mandate, 2026-10-23) - REVERTED by D15
- Problem: layout round 4 left AC_ROOM1 (J1.2 -> K1.14, 1.0 mm hand width) unrouted in 2 builds. The new corridor proof (hwflow check `corridor`, 69f66c9) shows its only path, past F1.1 (AC_L_IN, between_mm 1.5) and K1.A1 (coil, 6 mm isolation) at (16.46, 12.23), fits a 0.34 mm track, or 0.90 mm with a neck of at most 3 mm, on either layer (the THT pads of F1, J1, K1 block both faces).
- Decision: AC_ROOM1 width from current (current_a 1.5 A -> IPC-2221 about 0.53 mm at 10 C rise; the router necks it to >= 0.15 mm for at most 3 mm at the corridor). Rejected: moving K1 or F1 (board edge, notch_l 6 mm band, J1/F1 courtyards at 0.01 mm), lowering between_mm (safety, D11), a second layer (both faces blocked by the THT chain).
- Effect: none on R10/R11/D11 spacing; the neck carries 1.5 A for <= 3 mm (hwflow neck rule, post-layout track-current warning).

### D15. 230 V copper at 1.0 mm or more, no neck (reverts D14) - PM 2026-10-23
- Problem: review 2c. The round-7 AC_ROOM1 neck (0.15 mm x 2.33 mm, 1.5 mm from F1.1 on the unfused side) has an adiabatic melt I2t of about 2.3 A2s against F1's 98 A2s pre-arcing (T3.15A, notes/parts-230v.md). In a short the neck melts in 2-5 ms at 10 In, before F1 opens (at least 20 ms), and the arc can bypass F1. R13 (1.0 mm on all 230 V nets) showed as verified by mistake.
- Options put to the PM: (1) re-layout so every 230 V track is 1.0 mm or more (102 A2s, matches F1), no spec change; (2) a smaller fuse with R11 at about 0.3 A per room; (3) a hand-soldered bypass wire on the prototypes.
- Decision (PM): option 1. Every 230 V net has width_mm 1.0 and min_width_mm 1.0 (hwflow: no neck under it, a narrower track fails the layout). The J1/F1/K1 placement changes until AC_ROOM1 fits at 1.0 mm.

### D16. F.Cu 230 V copper up to x 21.1 under MP1 (amends D9) - PM 2026-10-23
- Problem: layout round 8 (D15, 1.0 mm no neck). AC_ROOM1 on F.Cu runs right of J1.1 to x 21.1 (y 17-21), past D9's x <= 20. The only path inside x <= 20, between J1.1 and F1.1, is about 1.15 mm wide: too narrow for 1.0 mm plus router slack. F1 cannot move left (6 mm from the notch_l screw boss) or up (K1 courtyard).
- Decision (PM): accept. F.Cu 230 V copper may lie anywhere under MP1 (x 0-26) that keeps the 6.0 mm mains-SELV isolation (R10, 6.00 mm measured) and stays out of the SELV_region (x >= 21.5). The `region-nets` warning for AC_ROOM1 is accepted under this decision.
