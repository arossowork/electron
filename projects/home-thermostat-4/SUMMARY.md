# home-thermostat-3: summary

**Battery-only Wi-Fi (MQTT) thermostat for an Italian 503 wall box.** It runs on 2 AAA cells (alkaline, NiMH or lithium) through a MAX17227A boost to 3.3 V. An ESP32-C3-MINI-1 holds the schedule and runs the control loop offline, and it connects to Wi-Fi once every 60 min. An ADS1115 reads 2 NTC probes ratiometrically. Two Panasonic latching relays switch the 230 V valve heads of two rooms. The design targets 10 prototypes, which carry no CE mark (R28, notes/compliance.md).

## Status

- **Layout round 8 PASS (2026-10-23):** 0 unrouted, 0 DRC errors, 85 vias (87 post-layout), post-layout SPICE 109/109, trace 35/35.
  - 230 V copper with no neck (D15): AC_L_IN and AC_L 1.4 mm on B.Cu, AC_ROOM1 and AC_ROOM2 1.0 mm on F.Cu. A 1.0 mm track melts at about 102 A2s against F1's 98 A2s pre-arcing; the round-7 0.15 mm neck (2.3 A2s) is gone.
  - R10: 6.00 / 6.0 mm. Different 230 V nets: 1.60 / 1.5 mm apart (D11).
  - R3: 3.204 V minimum at U1.3 (limit 3.2), 3.418 V maximum (limit 3.46). R4: drop 36.4 / 55 mV, guard copper 9.51 / 13.64 mOhm.
  - Placement change from round 7: F1 to (13.5, 13.25) and K1 to (13.5, 5.6).
  - Outline checked: both notches (r 4.0 mm) are in the Edge.Cuts gerber.
- **Before ordering:**
  - Check the ADW1 footprint against a real relay (state.md).
  - Confirm J1 C474953 stock and price (risk 15).
- **Accepted warnings:**
  - AC_ROOM1 F.Cu up to x 21.1, under MP1 and 6 mm from SELV (D16).
  - SW_BOOST and VIN_BST pad-entry necks at U2 (0.25 / 0.15 mm, at most 1.0 / 0.9 mm long, low current, under 1 C rise).
  - C18 / C19 are 9.2 / 6.5 mm of copper from U5 (limit 4.0). Review 2c: about 10 mV of supply noise; probe VM on the bench, move them in rev B.
  - 4 vias in the U2 EP need filling and capping (Fab notes).

## Board

- 88 x 46 mm with two 8 mm screw-post notches, centred at (2.25, 23) and (85.75, 23) mm. These are the R1 option, PM round 4.
- 4 layers (inner GND and power), FR4 1.6 mm, 1 oz copper (design.yaml board; R31; PM C15).
- Face A faces the user: cells, module, boost, ADC. Face B faces the box: relays, J1, F1, C1/C2, H-bridges, J2.
- 230 V zone at x <= 20 mm (F.Cu copper up to x 21.1 under MP1, D16). Mains to SELV: 6.0 mm creepage and clearance (R10).
- Depth stack (R2): 12.0 cell + 1.6 PCB + 15.8 relay = 29.4 mm, against a 30 mm limit (architecture.md 6.2).

## Key parts (design.yaml)

| Ref | Part | LCSC | USD |
|---|---|---|---|
| U1 | ESP32-C3-MINI-1-H4X | C41349510 | 3.08 |
| U2 | MAX17227AATA+T boost, RSEL 191 k = 3.3 V | C7419179 | 2.53 |
| L1 | Murata DFE201610E-1R0M 1 uH | C161082 | 0.205 |
| C4, C5, C22 | 47 uF 10 V X5R 1206 CL31A476MPHNNNE | C96123 | 0.168 each |
| C1, C2 | 3300 uF 10 V RL1A332MG250A00CE0, lying | C240437 | 0.148 each |
| M1, M2, M4 | AO3413 (reverse guard, R35 limiter) | C86648 | 0.092 each |
| M3 | CJ2305 (VBAT divider switch) | C8549 | 0.042 |
| U3 | ADS1115IDGSR | C37593 | 1.07 |
| U4, U5 | DRV8837DSGR H-bridge | C39159 | 0.26 each |
| K1, K2 | Panasonic ADW1103HLW latching 3 V, 16 A | none (consigned, Digi-Key/Mouser) | 5.11 each |
| J1 | Kefa KF128-5.08-3P-AA, 230 V screw terminal | C474953 | 0.30 |
| F1 | Walter 2010T3.15A250V, T 3.15 A | C354904 | 0.035 |
| J2 | Xinya XY350V-3.5-4P, NTC terminal | C557656 | 0.293 |
| BT1, BT2 | Myoung MY-AAA-20-J + MY-AAA-19-J clip pair per cell | C6937107 + C6937104 | 0.248 per cell |
| Y1 | 32.768 kHz CM7V-T1A 6 pF 20 ppm | C5366534 | 0.33 |
| D2, D3 | CESD3V3D5 TVS | C90954 | 0.026 each |
| MP1 | ITW Formex GK-17 sheet, 26 x 46 x 0.43 mm | none (ESPE Mfg.) | 0.02 material |

## Cost (R30, limit 38.79 USD per unit at 10 units)

- hwflow BOM: **20.96 USD per board** (build/REPORT.md).
- R30 central estimate: **37.06 USD** (conflicts.md D6). That figure covers board, parts, relays, cell clips, 2 probes, PCB, assembly and shipping over 10 units. D8 then made J1 0.03 USD cheaper, which gives about 37.03 USD, 1.76 USD under the limit.
- Not included: the Formex GK-17 lot (115.76 USD one-off, notes/insulating-sheet.md). The prototypes use sheet that is already in stock (D5).
- The fees are estimates; the range is about 32-42 USD (notes/parts.md section 2).

## Requirement status (`uv run hwflow trace`, 2026-10-23, round 8)

- **35/35 verified, 0 failing, 0 unverified.** The only open evidence items were the R28 and R34 files, which this file and notes/compliance.md supply.
- **Simulation:** R3, R4, R6, R9, R17, R19, R22, R23, R24, R25, R35 pass.
- **Layout:** R1, R2, R10 (6.00 / 6.0 mm), R13 and R15 pass. R13 (1.0 mm on all 230 V nets) is now enforced: each 230 V net has `min_width_mm: 1.0`, so a narrower track fails the layout (D15).
- **Datasheet, analysis or inspection:** the remaining requirements.
- **Verified only at typical datasheet values, by PM decision (2026-10-22):**
  - R3: the rail ceiling is 3.46 V. At the MAX17227A maximum ULPM offset, the peak with ripple is 3.464-3.471 V (notes/circuit.md). This is a known risk, checked in prototype test 7.
  - R6: 25.53 uA typical against 26 uA. With maximum IQ the floor is 26.07 uA (information run).
  - R35: +3V3 is 3.389 V and the coil at most 3.376 V. At the maximum offset the coil reaches about 3.44 V. This is a known risk.
  - R4: the simulations use typical RDS(on). The copper budget is sized for the RDS(on) maximum (D7): FETs 45.45 mV + copper 9.51 mOhm x 0.7 A = 6.7 mV, about 52 mV against 55 mV.
- **R27 (hipot):** no record exists yet. It is run on every prototype.

## PM decisions

- C1-C5 (round 1): boost converter; 60 min Wi-Fi interval; cells face the user and 230 V parts face the box; price scope is board + relays + holder + probes.
- C6-C9 (round 2): fast-reconnect firmware (<= 50 uAh per wake); 4.7 mF hold-up and a firmware cut-off for R23; external 16-bit ADC; R9 limit 9 mJ.
- C10/C11 (round 3): prototype cost 33-38 USD accepted, with 25 USD kept as the production target. C12: R3 from 2.0 V, small bulk caps. C13: lithium supported (R35). C14: 300/500 V probe cable or sleeve.
- C15: 4 layers. C16: 88 x 46 mm with notches.
- R15: battery-to-antenna distance relaxed to 11 mm (2026-10-21).
- U2 changed from TPS61299 to MAX17227A. R30 limit raised to 38.50, then to 38.60 USD for 2 x 47 uF. R3 ceiling raised to 3.46 V. R3, R6 and R35 verified at typical values.
- F1: same fuse on its real TE5 footprint ("smaller fuse").
- D1: third 47 uF output cap C22; R30 limit 38.79 USD.
- D2: MP1 insulating sheet over the 230 V joints (R26).
- D3: F1 stays the Walter 2010T (100-130 A breaking). Open certification risk.
- D4: guard FETs changed from CJ2305 to AO3413, plus the M4 limiter for R35.
- D5: prototype sheets cut from stock material (>= 0.4 mm, V-0); the material is recorded.
- D6: R30 stays 38.79 USD.
- D7: R4 relaxed from 50 to 55 mV, held at the RDS(on) maximum.
- D8: J1 is the KF128-5.08-3P fixed screw terminal.
- D9: AC nets may use F.Cu inside the mains region.
- D10: J1 pin order L / R1 / R2, printed on the silkscreen.
- D11: 1.5 mm functional spacing between different 230 V nets (`between_mm`).
- D12: R12 amended: finger safety by location, checked by inspection.
- D13-D14: see conflicts.md. D14 (AC_ROOM1 width from its current) is reverted by D15.
- D15: every 230 V net at 1.0 mm or more with no neck (fuse coordination, review 2c); re-layout instead of a smaller fuse.
- D16: F.Cu 230 V copper allowed up to x 21.1 under MP1 (amends D9).

## Open risks and first-article bench tests

The simulations cannot prove the items below. Each has a bench test on the first boards (sources in brackets).

1. **J1 wire dressing (review 2b M2).** Only 4.65 mm of straight wire fits before the left screw boss; 1.5 mm2 wire needs a 9-12 mm bend radius. Check wiring in a real 503 box on the first prototype (PM decision); if it fails, rev B uses a top-entry terminal.
2. **Reversed and forward insertion surge.** Run 20 reversed insertions with both cells, waiting >= 90 s each time. Run 5 reversed lithium insertions; reverse current must stay < 1 uA (R4). Run 20 forward lithium insertions into a dead board, which puts about 4.1 A per FET for a few ms. Check guard drop and leakage after each set (notes/circuit.md tests 1-3).
3. **R35 body-diode drop.** VBAT_RAW - VIN_BST must be >= 0.39 V at 10 uA, 45 C. Measure on 3 AO3413 samples (test 4).
4. **R3 margin is 4.3 mV** post-layout (3.204 V against 3.2 V). Copper resistance rises about 8 % at 45 C and etching can take 2-3 mV of it (review 2c). Measure in test 7.
5. **MAX17227A with VIN near VOUT (pass-through).** The datasheet does not describe a pass-through mode. Test 7 a-f: +3V3 mean and peak against 3.45 / 3.46 / 3.2 V with lithium and alkaline cells; the 3.70 V guard-ON fault; deep-sleep level on 3 samples; dead-board start at 0 C from 2.0 V; L1 saturation.
6. **RSEL power-up.** 20 dead-board power-ups at 0 C and 20 at 45 C. +3V3 must always settle at the 3.3 V setting (test 8).
7. **C1/C2 leakage** after conditioning. 2 uA is assumed, and it sets R6 (test 5).
8. **Relay coil inductance.** 30 mH is assumed (test 6). Also check:
   - the relay maximum continuous coil voltage, since the coil sees 3.38 V = 113 % for 31 ms;
   - the A1/A2 set polarity;
   - the coil-contact creepage of >= 6 mm (notes/parts.md 7);
   - the H-bridge supply VM at U5 during a coil pulse (C18 / C19 are 9.2 / 6.5 mm away; review 2c estimates about 10 mV).
9. **U2 EP land pattern.** Compare the KiCad TDFN-8 EP 0.8 x 1.2 mm with Maxim land pattern 90-0065 before ordering the stencil (notes/layout-constraints.md 4).
10. **ADS1115 data rate.** Firmware rule 12: convert at 8 or 16 SPS so that the ~45 mV ULPM ripple averages out (notes/circuit.md).
11. **Boot at VBAT 2.0 V.** IO2 is a strapping pin; check boot at this voltage (firmware rule 9).
12. **Wi-Fi wake energy and control wake.** Measure <= 50 uAh per Wi-Fi wake and a <= 20 ms control wake (R7, conflicts.md C6). If the R3 corner fails, cap TX power at 15 dBm (rule 11).
13. **Antenna range test** with the cells 11 mm from the antenna (R15).
14. **F1 breaking capacity** (100-130 A). Confirm with the certification lab (D3). Alternatives: SCT520 (5 kA) or Hongda 40T (1000 A).
15. **J1 at LCSC.** The C474953 price (~0.30 USD) comes from a search listing; stock and price are not confirmed (notes/mains-zone-options.md). Confirm before ordering.
16. **Insulating sheet.** Record the prototype sheet material: maker, measured thickness, UL 94 V-0 file, who cut it and the date (notes/insulating-sheet.md "Prototypes"). The material field is still blank. The 0.4 mm distance through insulation goes to the lab. The Formex production lot is open.
17. **Hipot** of 3.75 kVrms for 60 s on every unit (R27). Test-finger (IP2X) check with the plate off (R26, R12).
18. **J2.** Check the footprint drill against the Xinya drawing. The wire range is 18-24 AWG, so probe leads must be >= 0.2 mm2 or use ferrules (notes/parts.md 7 items 23-24).
19. **MAX17227A supply.** Single source (ADI) and low stock: 158 at JLCPCB (notes/parts.md 0b).

## User instruction: battery change (R34)

Battery cover label text, to be printed on the screw-fixed battery cover:

> **Switch off the heating breaker before changing the batteries.**
> 2 x AAA (alkaline, NiMH or lithium). Observe the + / - marks.

Steps for the user:
1. Switch off the heating breaker.
2. Pull off the cover plate.
3. Unscrew the battery cover.
4. Change both cells.
5. Refit the cover and the plate.
6. Switch the breaker back on.

If the cells are removed while a room relay is ON, that relay stays ON (R23). This is a second reason to switch off the breaker first.

## Installer notes

Source: notes/mains-zone-options.md "Installer note (D10)".

- Switch off the heating breaker before wiring.
- J1 is a screw terminal on the back of the board (box side). Its wire entries face the left screw post.
- Terminal order, top to bottom as printed next to the terminals:
  - **L**: incoming live from the heating breaker.
  - **R1**: room 1 valve live.
  - **R2**: room 2 valve live.
- Neutrals are joined in the box, not on the board.
- Wire: solid or stranded, 0.5-2.5 mm2. Strip 6-7 mm. Tighten the M2.5 screws to at most 0.4 N.m (Kefa drawing).
- NTC probes go to J2 on the low-voltage side. Use 300/500 V rated cable or a sleeve over the probe lead (R33). Leads must be >= 0.2 mm2.
- The terminal faces the box and cannot be reached with the cover plate removed (R12 as amended 2026-10-22: finger safety by location, checked by inspection on the prototype). The KF128 itself is not IP2X.

## Fab notes

- 4-layer FR4, 1.6 mm, 1 oz outer copper. Inner layers carry GND and power, with no mains copper on them (design.yaml).
- **Outline:** 88 x 46 mm with two 8 mm notches (r 4 at (2.25, 23) and (85.75, 23)). Use routed edges, not V-score, along the relay edges: the K1/K2 230 V pads are 0.60-0.75 mm from the edge (notes/layout-constraints.md 6). Both notches are in the Edge.Cuts gerber (checked 2026-10-23).
- **U2 EP (pad 9):** vias in pad, **filled and capped, IPC-4761 type VII** (build/REPORT.md via-in-pad warnings). Do not tent them only.
- No copper plane under R40 or under the U2 pad-6 trace (RSEL, < 2 pF; notes/layout-constraints.md 2).
- **230 V copper:** do not reduce any 230 V track below 1.0 mm (fuse coordination, D15); 1 oz (35 um) finished outer copper is required.
- **Bottom-side parts:** the CPL lists the face-B parts as Bottom (rotation + 180). J1, F1, C1 and C2 are through-hole on face B: hand or selective solder at JLCPCB.
- Assembly: K1, K2, J1, F1, C1 and C2 are through-hole parts on face B. The relays are consigned and hand-soldered. MP1 is fitted under the battery cover, over x 0-26 on face A.
