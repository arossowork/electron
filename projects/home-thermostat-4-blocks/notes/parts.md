# Parts: merged BOM, cost, depth stack, GPIO map (stage 3 close-out, 2026-10-20)

Sources: `notes/parts-power.md`, `notes/parts-mcu-sensing.md`, `notes/parts-230v.md`, `notes/datasheets/*.md`, plus the lookups of this pass (hwflow index, LCSC product pages, search summaries). PDFs could not be read in this environment. Every number that is not from a readable datasheet or a vendor product page says "unverified". Prices are USD at qty 10 units unless marked.

## 0. Round 3 changes (2026-10-21; design.yaml refs)

| Change | Ref | MPN | LCSC | Price USD | Reason |
|---|---|---|---|---|---|
| reverted | C1, C2 | RL1A332MG250A00CE0 3300 uF 10 V | C240437 | 0.148 each (was 0.215, C311679) | R3 corner now at 2.0 V passes with 6.6 mF (3.216 V, MC 100 %) |
| replaced | J2 | XY350V-3.5-4P 4-pole 3.5 mm screw terminal (Xinya) | C557656 | 0.293 (was 0.216, C160404 JST SH) | R33 field-wired probes; KiCad Connector:Screw_Terminal_01x04 + TerminalBlock_Phoenix_PT-1,5-4-3.5-H (pads 1-4 verified); TVS D2/D3 and 1 k series kept |
| removed | Q1, R17, C20 | MMBT3906, 10 M, 1 nF | C2143 | -0.010, -0.004 | R4 turn-off no longer acts (guard OFF in sleep, gate driven by IO2 in wakes): circuit.md |
| added | C21 | 100 nF 0402, VBAT_EN to VBAT_P | generic | 0.002 | holds the guard OFF against Cgd coupling on a reversed insertion |
| changed | M1, M2 gate | gate on VBAT_EN (IO2) with R14 1 M to VBAT_P; R1 not restored | - | 0 | R35 lithium limiter = guard OFF by default (body-diode drop) |
| changed | R15/R16, C15 | 20 k / 10 k 0.1 %, 1 nF | - | 0 | 50 us VBAT read window (R35 rail rise during the read) |

No dedicated limiter IC: the R35 limiter is the existing M1/M2 pair (0 USD added). Rejected candidates with numbers are in circuit.md.

## 0c. Review-2 round (2026-10-22): C22, MP1, BT1/BT2 clips, guard FET surge

| Change | Ref | MPN | LCSC | Price USD | Stock | Reason |
|---|---|---|---|---|---|---|
| added | C22 | CL31A476MPHNNNE 47 uF 10 V X5R 1206 | C96123 (basic) | 0.168 | (as C4/C5) | PM D1: LPM -> HPM transition dip (R3) |
| added | MP1 | ITW Formex GK-17, die-cut 26 x 46 x 0.43 mm | - (ESPE Mfg.) | 0.02 material; lot 9 sheets 115.76 one-off | made to order | PM D2: R26 sheet over the 230 V joints, notes/insulating-sheet.md |
| replaced | BT1, BT2 | Myoung MY-AAA-20-J (+) + MY-AAA-19-J (-) per cell | C6937107 + C6937104 | 0.0976 + 0.1503 = 0.2479 per cell (was 1.21) | 4289 / 5756 | BH-AAA-B5AA001 is a 2-cell 62.2 mm holder that does not fit |

hwflow BOM 22.52 -> 20.79 USD. R30 central: 38.60 + 0.168 + 0.02 - 2 x (1.21 - 0.2479) = 36.86 USD; the R30 limit is 38.79 USD (PM D1/D2).

Battery clip alternatives (LCSC, 2026-10-22): Myoung MY-AAA-07 (flat end contact, C6937109, 0.1027) + MY-AAA-22-J (spring, C6937110, 0.1503): end contacts only, 42.4-43.25 mm land span, no side grip (the cell needs a moulded cradle in the cover); Keystone 1020 (single AAA SMT holder, nylon V-0, 11.18 mm high; Digi-Key/Farnell, not seen at LCSC, about 1.5-2 USD). The 19-J/20-J pair grips the cell body and gives the drawing land pattern, so it is the choice.
Clip drawings: WD-CP-0276 (19-J) https://datasheet.lcsc.com/datasheet/pdf/b5953874350f465dec3e0fbc2666dcb4.pdf ; WD-CP-0277 (20-J) https://datasheet.lcsc.com/datasheet/pdf/79e836848861264c18b59c7e848332da.pdf.

### Guard FET M1/M2 surge (DECIDED, PM D4 2026-10-22: AO3413)

Decision: M1, M2 = AOS AO3413 (LCSC C86648, 0.092 USD, +0.05 each). Same SOT-23 pinout (data sheet p.1: G pin 1, S pin 2, D pin 3 = KiCad Transistor_FET:AO3401A symbol and Package_TO_SOT_SMD:SOT-23 pads 1-3, `hwflow lib show/pads`). The R35 rework adds M4 (a third AO3413) + R41 100 k + C23 10 nF + R42 1 M between C1/C2 and the boost input (notes/circuit.md "R35 with AO3413"): +0.098 USD. hwflow BOM 20.79 -> 20.99 USD; R30 central 36.86 + 0.10 + 0.098 = 37.06 USD (limit 38.79, PM D6). Data sheet read 2026-10-22 (Rev 9.1, Feb 2024, pdftotext + Fig. 1-10 images); model fit in models/thermo.lib and notes/models.md.

Previous analysis (kept for the record):

Need: SOT-23 G1/S2/D3, VDS >= 20 V, RDS(on) near the CJ2305 fit (120 mOhm at -1.8 V) for R4, and a datasheet rating that covers about 4.1 A body-diode current per FET for a few ms (insertion into a dead board, C1 + C2 6.6 mF).

| Part | LCSC, price, stock | RDS(on) -1.8 V / -2.5 V (typ / max) | Surge evidence | Body diode at 10 uA, 25 C (Fig. 6) | Datasheet |
|---|---|---|---|---|---|
| CJ2305 (current) | C8549, 0.040, 171821 | 120 (fit, unverified) | ISM 10 A only for <= 300 us; no SOA/Zth: NOT covered | not given; model assumes 0.455 V | https://datasheet.lcsc.com/datasheet/pdf/0d2be3a8b74744bc825762ee6b25df9d.pdf |
| AOS AO3413 | C86648, 0.092, about 6300 | 85 / 130 at -1.8 V (1 A); 70 / 100 at -2.5 V | Fig. 10 single-pulse power about 15-20 W at 10 ms; Fig. 9 SOA has a 10 ms line. Body diode 4.1 A x 0.85 V = 3.5 W: covered | 0.27 V (125 C: 0.08 V) | https://www.aosmd.com/res/datasheets/AO3413.pdf |
| AOS AO3415A | C133233, 0.116, 245946 | 52 / 65 at -1.8 V; 42 / 53 at -2.5 V | Fig. 10 about 20-25 W at 10 ms: covered | 0.22 V (125 C: 0.05 V) | https://www.aosmd.com/res/datasheets/AO3415A.pdf |
| Diodes DMP2035U-7 | C110499, 0.139, 276522 | 41 / 62 at -1.8 V | ISM -24 A, pulse width not stated ("guaranteed by design"): not a 10 ms rating | not read | https://www.diodes.com/assets/Datasheets/DMP2035U.pdf |

Why not switched: R35 (lithium 3.7 V, guard OFF) relies on the body-diode drop at about 16 uA. It needs more than 0.25 V at 45 C. Fig. 6 interpolated to 45 C gives about 0.23 V (AO3413) and 0.19 V (AO3415A). VBAT_P would then be about 3.47 V and 3.51 V, and +3V3 follows VIN above 3.39 V, so R35 (3.45 V) fails. The surge-rated parts have bigger dies, and a bigger die has a lower drop at uA. The CJ2305 0.455 V is an assumption, not a datasheet value, so R35 is already a bench risk with the current part.
Options: (1) keep CJ2305 and add a bench inrush test (3 samples, 20 insertions into a discharged board, check the leakage after); 0 USD. (2) AO3413 for M1/M2 (+0.10 USD per board) plus a separate R35 fix, for example a firmware or hardware limiter that does not rely on the body diode (circuit stage). (3) Limit the inrush instead of changing the FET (series R or a soft-start for C1/C2, which costs R4 drop); circuit stage.

## 0b. Boost U2 replaced: TPS61299 -> MAX17227A (2026-10-21, package change approved by the product person)

Diagnosis (`hwflow check`, feasibility proof, code `escape`): "U2 (Package_TO_SOT_SMD:SOT-563, pitch 0.5 mm): pads 2 (SW_BOOST 0.6 mm), 5 (+3V3 0.4 mm) sit between foreign pads where at most 0.36 mm leaves a pad (0.15 mm clearance) and no track of the net's width reaches out of the part or to a via, so 2 wide nets would neck at one part. Options: a package with pitch >= 0.62 mm (parts), or narrower +3V3, SW_BOOST (circuit: width_mm/current_a)." No placement or router can fix it (layout attempts 6-9).

What the new part must keep (sources): R3 burst 350 mA for 5 ms at 2.0 V cells through 1 ohm, which needs about 0.95-1.0 A average boost input current at VBAT_P about 1.54 V (circuit.md: the corner at a 1.0 A input limit gave 3.2165 V against 3.2 V). R6 sleep floor 25.26 uA simulated against 26 uA, so 0.74 uA of margin. Battery-side cost of IQ into VOUT: x 3.3 / (2.6 x 0.80) = x 1.59 (architecture.md section 4). R35 and R3 upper limits: 3.45 V and 3.4 V. In stock at JLCPCB/LCSC, with a KiCad footprint.

| Part | Package, pitch | SW / VOUT pins, walled in? | IQ (typ) | Current limit | Min VIN | Burst efficiency | R6 floor (typ, est.) | Burst R3 | Price @10 | Stock (JLCPCB) |
|---|---|---|---|---|---|---|---|---|---|---|
| TPS61299DRLR (old) | SOT-563, 0.5 mm | SW pin 2 between VIN/EN; VOUT pin 5 between VSEL/GND: both walled, 2 necks -> `escape` error | 95 nA VOUT | input avg 0.96 / 1.2 / 1.44 A | 0.7 V start, runs to 0.5 V | about 87 % (calc. from 88/150 mOhm) | 25.26 uA (sim) | pass (3.2165 V corner) | 1.89 | 3648 |
| **MAX17227AATA+T** (chosen) | TDFN-8 2x2 + EP, 0.5 mm | OUT pins 1-2: pin 1 at the row end, pin 2 next to its own net -> free; LX pin 3 between OUT and GND -> 1 neck (accepted) | 350 nA OUT (660 max) + 12 nA IN | peak 1.8 / 2.0 / 2.2 A -> about 1.4 A min average input | 0.88 V start (0.95 max), runs to 0.4 V | about 89 % (calc. from 50/95 mOhm), 96 % peak (datasheet) | 25.93 uA: +0.40 IQ, +0.27 for ULPM +2.7 % VOUT in sleep (max IQ: 26.43) | pass, more margin (1.4 A min vs 0.96 A) | 2.53 | 158 |
| TPS61099DRVR / TPS610994 | WSON-6 2x2, 0.65 mm | SW pin 5 between EN and VIN (same net) -> free; VOUT pin 2 between GND and FB, 0.6 mm escape >= 0.4 -> passes | 600 nA VOUT + 400 nA VIN | switch peak 0.8 A min, 1.0 typ -> about 0.5 A average | 0.7 V | not reached (current limited) | 26.46 uA: fails R6 | fails (needs about 1 A input) | 1.13 | 8210 |
| TLV61070ADBVR | SOT-23-6, 0.95 mm | SW pin 1 at the row end, VOUT pin 5 between FB and VIN, 1.0 mm escape -> passes | 20 uA VOUT + 0.9 uA VIN | valley 1.8 A min / 2.45 typ | 1.3 V start, runs to 0.5 V | about 89 % (69/89 mOhm) | about 57.7 uA: fails R6 by 32 uA, R5 average about 117 uA | pass | 0.24 | 19941 |
| SGM66099-3.3 | TDFN-6 2x2, 0.65 mm | as TPS61099 -> passes | 0.6 uA VOUT + 0.05 VIN | peak 0.89 A min | 0.9 V | not reached | 26.11 uA: fails R6 | fails | (0.52 WLCSP) | 0 (TDFN not listed) |

Also looked at: TPS61094 (WSON-12 0.5 mm, SW pin 5 walled between VIN and SUP), MAX17225 (1 A peak, 7.60 USD, stock 24), AW36099 (350 nA, 0.5 A limit), TPS61291 (5.7 uA boost IQ, 0.7 A min), TPS61322 (6.5 uA). None keeps both R6 and the burst except MAX17227A.

Choice: MAX17227AATA+T (LCSC C7419179), TDFN-8 Package_DFN_QFN:TDFN-8-1EP_2x2mm_P0.5mm_EP0.8x1.2mm (KiCad descr cites the Maxim 21-0168 outline of this part), project symbol thermo:MAX17227A (no KiCad library symbol; pins 1-9 from the datasheet TDFN pin table, equal to pads 1-9). It is the only candidate that keeps the R3 burst (more input current than the TPS61299) and stays inside the R6 margin at typical IQ, and its pinout leaves OUT at the row end so only LX necks. `hwflow check` after the change: OK, 0 errors, 0 warnings.

Update 2026-10-22 (delta review, PM decision): C4/C5 2 x 10 uF 0805 are replaced by 2 x 47 uF 10 V X5R 1206 (CL31A476MPHNNNE, C96123, basic, 0.168 USD): VIN near VOUT with lithium cells (switching testbench, notes/circuit.md; 3 caps were proposed, the PM chose 2). hwflow BOM 22.25 -> 22.52 USD; R30 central estimate 38.32 + 2 x 0.168 - 2 x 0.03 = 38.60 USD against the new 38.60 USD limit. C3 stays one 22 uF (a second one, +0.15-0.18 USD, was left out for cost). The model and the stale TPS61299 numbers below are superseded by notes/models.md.

Changes in design.yaml (stage 3): U2 (value, mpn, lcsc, price, datasheet, symbol, footprint, pins 1/2 +3V3, 3 SW_BOOST, 4/5/9 GND, 6 BOOST_RSEL, 7/8 VBAT_P; placement anchor x 39.5 y 31.5 removed; spice ports 7 3 8 1 4 on the same TPS61299_AVG behavioral subckt with ILIM 1.6, IQ 350n); new R40 191 k 1 % 0402 (C270632, 0.0012) RSEL to GND; C3 10 uF -> 22 uF 25 V X5R 0805 (CL21A226MAQNNNE, C45783 basic, 0.150), the datasheet minimum at IN. L1 1 uH DFE201610E kept (Isat 2.7 A against the 2.2 A max peak limit, 23 % margin). C4 + C5 2 x 10 uF kept (datasheet: 22 uF recommended, at least 11 uF effective; 2 x 10 uF 25 V 0805 is about 12-14 uF at 3.3 V, estimate). Datasheet numbers: notes/datasheets/MAX17227A.md.

Open points from this change (for the circuit stage and the product person):
1. Cost R30: hwflow BOM 21.50 -> 22.25 USD (+0.75: U2 +0.63, C3 +0.12, R40). Central R30 estimate 37.57 -> 38.32 USD (38.82 with probe sleeves) against 38.00. Product decision.
2. R6: typical floor estimate 25.93 uA against 26 (margin 0.07 uA); at the 660 nA max IQ, 26.43 uA. The datasheet's "90 % at 10 uA" (conditions not in the text) would lower it by about 1 uA against the 0.80 assumed; the circuit stage refits EFFL.
3. R3 / R35 upper limit: at uA loads the part regulates +1.5 / +2.7 / +4.6 % high (ULPM): 3.350 / 3.389 / 3.452 V. Typical passes R3 (3.4 V) by 11 mV; the datasheet maximum is 52 mV over R3 and 2 mV over R35 (3.45 V); ESP32 VDD max 3.6 V. Not in the behavioral model.
4. VIN >= VOUT (alkaline 3.3-3.4 V with the guard ON): the datasheet does not describe a pass-through mode (only "VOUT > VIN + 150 mV in regulation" and a 1.1 A short-circuit path when VOUT < VIN - 1 V). The model keeps pass-through as an assumption; bench check on the prototype.
5. Analyses still say TPS61299 in their realism text and use the TPS61299 corner (vilim 1.0 A, VOUT -2 % = 3.234 V). The MAX17227A corner is VOUT 3.234 V (-1 % accuracy, -1 % load regulation, the same value) and about 1.4 A min average input current; the circuit stage updates them.
6. Stock 158 at JLCPCB (10 units needed), single source (ADI); Mouser/Digi-Key stock not checked.

Probe cable/sleeve (R33): probes are field-wired next to 230 V wiring, so the probe lead needs a 300/500 V rated sheath (H05-class, e.g. H05VV-F 2 x 0.5 mm2, about 0.5-0.8 USD/m) or, for a thin 2-core probe lead, a 300/500 V sleeve over it: silicone-glass-fibre sleeve 2 mm ID rated 600 V / 1.5 kV, or polyolefin heat-shrink 600 V, about 0.10-0.30 USD per 0.5 m (source: estimate from general distributor pricing, unverified; no LCSC code found, `hwflow parts` has no tubing category). Budget 0.5 USD per probe (margin about 2x). Specify in the probe purchase: sheath rated 300/500 V, or supply sleeve and fit at assembly.

Cost against R30 (now <= 38 USD per unit, 10 units): section 2 central 37.5 + J2 0.077 + C21 0.002 - Q1/R17/C20 0.014 = **37.57 central** (low 32.5, high 42.5) + probe sleeve 2 x 0.25 = **38.07 central with sleeves**. hwflow BOM (priced design parts only): 21.50 USD. Central is 0.4 USD under 38 without sleeves and 0.07 USD over with sleeves; the high case is not met. The fees (section 2) dominate the uncertainty: product decision whether the sleeves sit in the probe lot (separate) or this BOM.

## 1. Merged BOM (one unit)

Flags: V = vendor page/datasheet text read; I = hwflow/jlcsearch index; U = search summary or estimate, unverified.

| Ref | Role | Value | MPN | LCSC | Price | Symbol | Footprint | H mm | Flag |
|---|---|---|---|---|---|---|---|---|---|
| U1 | Wi-Fi/BLE module | ESP32-C3-MINI-1-H4X | ESP32-C3-MINI-1-H4X | C41349510 | 3.08 (I) | thermo:ESP32-C3-MINI-1 (new) | thermo:ESP32-C3-MINI-1 (new) | 2.4 | V, pads U |
| U2 | Boost 3.3 V | RSEL 191 k (R40) = 3.3 V, 2 A peak limit (replaced TPS61299DRLR 2026-10-21, section 0b) | MAX17227AATA+T | C7419179 | 2.5275 | thermo:MAX17227A (new) | Package_DFN_QFN:TDFN-8-1EP_2x2mm_P0.5mm_EP0.8x1.2mm (pads 1-9 = pins 1-9) | 0.75 | V |
| L1 | Boost inductor | 1 uH, 2.7 A, 57 mOhm | DFE201610E-1R0M=P2 | C161082 | 0.205 (I, stock 22014) | Device:L | Inductor_SMD:L_Murata_DFE201610P | 1.0 | I, H U |
| C4, C5 / C3 | Boost caps | OUT: 2 x 47 uF 10 V X5R 1206 (2026-10-22 delta review and PM decision, VIN near VOUT); IN: 22 uF 25 V X5R 0805 | CL31A476MPHNNNE / CL21A226MAQNNNE | C96123 / C45783 (both basic) | 0.168 each / 0.1503 | Device:C | Capacitor_SMD:C_1206_3216Metric / C_0805_2012Metric | 1.6 / 1.25 | V (JLCPCB API 2026-10-22: C96123 stock 374848, 0.168 at 1-49) |
| M1, M2 (+M4) | Reverse-polarity guard (+R35 limiter M4) | 2 x P-FET in parallel (+1 in the boost input) | AO3413 (PM D4; was CJ2305) | C86648 | 0.092 each | Transistor_FET:AO3401A | Package_TO_SOT_SMD:SOT-23 | 1.1 | I |
| Q2 | VBAT divider switch | P-FET | CJ2305 | C8549 | 0.042 | same | same | 1.1 | I |
| C1 | Bulk hold-up | 2 x 3300 uF 10 V, lying | RL1A332MG250A00CE0 | C240437 | 0.148 each (I, stock 6502) | Device:C_Polarized x2 | Capacitor_THT:CP_Radial_D10.0mm_P5.00mm (lying: draw bent leads in stage 4) | 10 + lead bend | I, leakage U |
| BT1 | 2 x AAA | 1 cell each | BH-AAA-B5AA001 x2 | C964881 | 1.21 each = 2.42 (V) | Device:Battery_Cell x2 | thermo:BH-AAA-B5AA001 (new, PLACEHOLDER) | 12.0 (assumed) | V, dims U |
| Y1 | RTC crystal | 32.768 kHz 6 pF | CM7V-T1A-32.768KHZ-6PF-20PPM-TA-QC | C5366534 | 0.330 | Device:Crystal | Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm | 0.9 | V |
| U3 | 16-bit ADC | ADS1115 | ADS1115IDGSR | C37593 | 1.07 | Analog_ADC:ADS1115IDGS | Package_SO:VSSOP-10_3x3mm_P0.5mm | 1.1 | V/recall U |
| R_F1/2 | NTC reference | 10 k 0.1 % 25 ppm | RT0603BRD0710KL | C95204 | 0.033 | Device:R | R_0603 | 0.5 | V |
| D_T1/2 | NTC ESD TVS | 3.3 V | CESD3V3D5 | C90954 | 0.026 | Device:D_Zener | Diode_SMD:D_SOD-523 | 0.6 | V |
| J2 | NTC connector | SH 4-pin | SM04B-SRSS-TB(LF)(SN) | C160404 | 0.216 | Connector_Generic:Conn_01x04 | Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal | 3.8 (recall) | V |
| SW1 | Setup button | tact 4x3 | TS-1088-AR02016 | C720477 | 0.056 | Switch:SW_Push | Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2 (pad spacing to check) | 2.0 (recall) | V |
| D1, R_D1 | Status LED | red 0603 + 1 k | 150060RS75000 | C3030991 | 0.27 | Device:LED | LED_SMD:LED_0603_1608Metric | 0.5 | V |
| K1, K2 | Latching relays | 3 V, 45 Ohm, 1 coil | ADW1103HLW | none (consigned) | 5.11 each (Digi-Key, U); 4.55 Mouser (U) | Relay:Relay_SPST_Latching_1coil | thermo:Relay_Panasonic_ADW1_1coil (new, PLACEHOLDER pins) | 15.8 | V, pins/price U |
| U4, U5 | H-bridge | DRV8837 | DRV8837DSGR | C39159 | 0.26 | Driver_Motor:DRV8837 | Package_SON:WSON-8-1EP_2x2mm_P0.5mm_EP0.9x1.6mm | 0.8 | V |
| J1 | 230 V terminal | 3 pole 5.08 mm, shrouded header + plug | WJ2EDGRC-5.08-03P-14-00A (C8377, 0.072) + WJ2EDGK-5.08-3P plug (no LCSC code; 0.056 listed, budget 0.25) | C8377 | 0.33 (budget 0.60) | Connector:Screw_Terminal_01x03 | TerminalBlock:TerminalBlock_bornier-3_P5.08mm (placeholder, pad 3.0 mm) | header unknown, plug 17.6 (U) | V header, plug U |
| F1 | Fuse on L-in | T 3.15 A 250 V | 2010T3.15A250V | C354904 | 0.035 (I, stock 285k) | Device:Fuse | TerminalBlock:TerminalBlock_bornier-2_P5.08mm (placeholder for 8.5 x 8.5 x 4 mm, P 5.08) | 8.5 | I |
| RV1/2 | Varistor | DNP | S07K275 class | - | 0 | Device:Varistor | per parts-230v.md | - | DNP |
| EN/boot/I2C/divider passives | pulls, EN RC, decoupling, VSEL | | generic | stage 4 | 0.21 | Device:R, Device:C | 0402/0603 | 0.5 | U |
| PR1/2 | NTC probes 2 m | 10 k B3950 | marketplace | - | 1.2 each (estimate) | - | - | - | U |

Alternatives (two per block, price, stock, reason):

| Block | Chosen | Alt 1 | Alt 2 | Reason |
|---|---|---|---|---|
| U1 | -H4X 3.08, 1541 | -H4 C2934569 2.76, 3919 | -N4X (not looked up) | newer chip revision, 105 C |
| U2 | MAX17227AATA+T 2.53, 158 | TPS61299DRLR C41689719 1.89, 3648 (SOT-563: SW and VOUT walled in, `escape` error) | TPS61099DRVR C2842395 1.13, 8210 (0.8 A min limit fails the R3 burst, IQ 1 uA fails R6) | only part found that keeps the burst and R6 with a routable pinout (section 0b) |
| L1 | DFE201610E 0.205, 22014 | CKCS201610 1R0 (code not found) | TFM201610ALMA1R5MTAA C404809 1.5 uH 2.3 A 85 mOhm 0.3475 | smallest DCR, 1.0 mm, in stock |
| C1 | 2 x 3300 uF 10 V D10x25 | 2 x RGA472M0JBK-1030G C311679 4700 uF 6.3 V D10x30, 0.215, stock 967 (9.4 mF) | RGA472M0JBK-1320G C782984 4700 uF 6.3 V D12.5x20, 0.2355 (one part, 12.5 mm) | 6800 uF 6.3 V exists only as EEUFS0J682L C407967 (stock 0); two 10 mm parts keep height 10 mm |
| BT1 | BH-AAA-B5AA001 x2 | MY-AAA-19-J + MY-AAA-20-J + MY-AAA-07 clips (0.43 per cell, needs a plastic seat) | Keystone 2468 (13 mm, fails R2) | no cheaper or lower 2-cell holder found in the index |
| J1 | Kangnex WJ2EDGRC | WJ2EDGR-5.08-3P C70914 (open right-angle header) | Degson 2EDGRC-5.08-03P (not at LCSC) | shrouded pluggable gives IP2X; there is an open screw block (KF301 class) only as a fall-back, which is NOT finger-safe |
| F1 | 2010T3.15A | 2010T2A250V C354902 | - | 2 A nuisance-trips with both heads at 1.5 A |
| K1/K2 | ADW1103HLW | Fanhar FH42L-1AT-L1-DC3V (clone, price unknown, R9 marginal) | none cheaper that meets R9/R11 | requirement: keep Panasonic |

Datasheet/product links: ESP32 https://documentation.espressif.com/esp32-c3-mini-1_datasheet_en.html; MAX17227A https://www.analog.com/media/en/technical-documentation/data-sheets/max17227a.pdf (U2 since 2026-10-22; TPS61299 https://www.ti.com/lit/ds/symlink/tps61299.pdf is the replaced part); ADW1103HLW https://industry.panasonic.com/global/en/products/control/relay/power/number/adw1103hlw; BH-AAA https://www.lcsc.com/product-detail/C964881.html; J1 https://www.lcsc.com/product-detail/C8377.html; others in `notes/datasheets/`.

## 2. Cost against R30 (28.00 USD, 10 units)

| Item | Central | Low | High | Basis |
|---|---|---|---|---|
| Parts (BOM above, relays at 5.11) | 24.48 | 23.1 (relays 4.55, J1 0.33) | 25.5 | table; relay qty-20 price unknown (U) |
| PCB 2-layer 88 x 46, 10 pcs | 0.50 | 0.4 | 0.7 | about 5 USD per 10 (U) |
| JLCPCB assembly setup + stencil, 2 sides | 3.00 | 2.5 | 4.0 | about 30 USD per order (U) |
| Extended-part fees | 3.00 | 2.0 | 4.0 | about 10 unique extended parts x 3 USD per order (U) |
| Placement/joint fees | 1.00 | 0.8 | 1.2 | (U) |
| Hand soldering of consigned relays + THT (2 relays, C1 x2, J1, F1) | 2.50 | 1.5 | 3.0 | (U) |
| Shipping (JLCPCB + relay order) | 3.00 | 2.0 | 4.0 | DHL about 25-30 USD per shipment (U) |
| **Total per unit** | **37.5** | **32.4** | **42.4** | |

Result: R30 (28 USD) is NOT met, even in the low case. Gap 4.4 to 14.4 USD, central 9.5 USD. The largest lines: relays 10.2, one-off fees (setup, extended, hand soldering, shipping) about 11.5 per 10 units, BT1 2.4, probes 2.4. Needs a product decision (not decided here): accept about 33-38 USD for the 10 prototypes, drop the probes from the scope (they are a separate lot), or take the Fanhar clone and relax R9. Fees fall per unit with volume; the 25 USD production target is a separate matter.

## 3. Depth stack (R2, limit 30.0 mm)

| Item | mm | Flag |
|---|---|---|
| Face A tallest: AAA cell in BH-AAA-B5AA001 | 12.0 | assumed (cell 10.5 + seat), holder height not published: U |
| PCB | 1.6 | R31 |
| Face B tallest: ADW1103HLW HL | 15.8 | search summary and RS listing, tolerance unknown: U |
| Stack | 29.4 | margin 0.6 mm (2 %) |
| Face B other: C1 lying 10 (+ lead bend about 1), F1 8.5, J1 header + plug | J1 plug listing 17.6 | J1 mated height on a right-angle header unknown: could break R2 |
| Face A others: U1 2.4, SW1 2.0, L1 1.0 | | below the holder |

R2 holds on paper (29.4 <= 30.0) with 0.6 mm margin and two open items: the holder height (needs 12.0 or less) and J1 (must be <= 15.8).

## 4. Requirement status from this pass

- R2: see section 3.
- R3: C1 = 2 x 3300 uF = 6.6 mF (5.3 mF at -20 %), against 6.8 mF wanted in parts-power.md and 3.9 mF minimum. Burst at the 1.2 A limit is for the SPICE stage to decide.
- R11: relay coil-contact 5000 Vrms, surge 12 kV (vendor page); creepage/clearance >= 6 mm is a search summary (unverified); "reinforced" wording not read. Meets the alternative branch only if the 6 mm is confirmed.
- R16: CE RED certificate for ESP32-C3-MINI-1 exists: EU-Type Examination Certificate E1177-210536, Notified Body 1177 (TIMCO), 2 June 2021 (search summary of the Espressif PDF, PDF not opened, unverified). The Espressif certificates page fetch did not list it (filter issue). The design must keep the antenna at the board edge with the 5.4 mm keep-out and 15 mm clearance.
- R18: CESD3V3D5 clamp 13 V at 16 A (LCSC page), series 1 k: (13 - 3.9) / 1 k = 9.1 mA against the ADS1115 10 mA continuous limit (recall, unverified): margin 9 %. 1.5 k gives 6 mA but adds 0.5 k to the source resistance of 5 k (error 0.08 % to 0.09 %).
- R30: see section 2 (not met).

## 5. Project library (lib/)

`lib/symbols/thermo.kicad_sym`: ESP32-C3-MINI-1 (53 pins, GND stacked on 22 pin numbers, NC 14 pins), TPS61299 (6 pins). `lib/footprints/thermo.pretty/`: ESP32-C3-MINI-1 (61 pads: 53 numbers, pad 49 as 9 pads), Relay_Panasonic_ADW1_1coil (pads 13, 14, A1, A2), BH-AAA-B5AA001 (pads 1 +, 2 -). Generator: `notes/gen_thermo_lib.py`. Verified with `Libs(extra_dirs=[lib/symbols, lib/footprints])` (the `hwflow lib show/pads` CLI does not load project libraries; the build does): symbol pin numbers equal footprint pad numbers for the ESP32 and relay, and TPS61299 pins 1-6 equal the SOT-563 pads 1-6. Not KiCad-opened (no KiCad GUI here); the s-expressions parse in hwflow only.

Relay pin roles (symbol Relay_SPST_Latching_1coil): 13, 14 = contact terminals (symmetric; L in on one, room output on the other; no distinct COM/NO on a Form A); A1, A2 = coil (set polarity unconfirmed, firmware resolves it because the DRV8837 drives both ways).

## 6. GPIO map (from parts-mcu-sensing.md, module pin numbers from datasheet Table 3-1)

| GPIO | Pin | Function | Notes |
|---|---|---|---|
| IO0 | 12 | XTAL_32K_P | RTC |
| IO1 | 13 | XTAL_32K_N | RTC |
| IO2 | 5 | VBAT_EN (active low) | strapping, 1 M pull-up to VBAT_P |
| IO3 | 6 | VBAT_ADC | ADC1 |
| IO4 | 18 | SENS_EN | 100 k pull-down |
| IO5 | 19 | SDA | pull-up to SENS_EN |
| IO6 | 20 | SCL | pull-up to SENS_EN |
| IO7 | 21 | K1 IN1 | |
| IO10 | 16 | K1 IN2 | |
| IO20 | 30 | K2 IN1 (RXD0) | |
| IO21 | 31 | K2 IN2 (TXD0) | |
| IO8 | 22 | LED (active low) | strapping, 10 k pull-up |
| IO9 | 23 | SW1 to GND | strapping, 10 k pull-up |
| IO18 / IO19 | 26 / 27 | USB D- / D+ | J3 pads |
| EN | 8 | reset | 10 k to 3V3, 1 uF, J3 pad |
| 3V3 | 3 | supply | |
| GND | 1, 2, 11, 14, 36-53 | | |
| NC | 4, 7, 9, 10, 15, 17, 24, 25, 28, 29, 32-35 | | |

## 7. Unverified numbers to check on the first prototype

1. Relay pin positions and hole sizes: placeholder footprint (contacts 7.5 mm apart, coil 5 mm apart, drills 1.7 / 1.4). Margin: none; compare with the Panasonic drawing before layout; the +0.2 mm hole margin is not applicable to unknown sizes.
2. Relay coil-contact creepage/clearance >= 6 mm (search summary). Margin: 0 against R11's 6 mm.
3. Relay height 15.8 mm (search summary). Margin to R2: 0.6 mm.
4. Relay price 5.11 (Digi-Key) / 4.55 (Mouser) at an unknown quantity break; margin used: +12 % on the lower value.
5. Relay max continuous coil voltage 110 % = 3.3 V (vendor page); the rail reaches 3.4 V (113 %) for a 30 ms pulse. Margin: -3 %, short pulse.
6. Relay set polarity of A1/A2 (not confirmed; firmware resolves it).
7. BH-AAA-B5AA001 height (assumed 12.0 mm), pad size and span (placeholder). Margin to R2: 0.6 mm.
8. ESP32-C3-MINI-1 pad coordinates (Espressif KiCad library via web summary; cross-checked with the 11.8 / 9.8 mm spacings in the datasheet HTML).
9. Antenna keep-out 5.4 mm (datasheet HTML) and the 15 mm clearance (guideline summary). Margin: 0.
10. CE RED certificate E1177-210536 (search summary; whether it covers H4X/N4X). Margin: none.
11. TPS61299 VSEL = GND gives 3.3 V (Table 7-1 summary); input current limit minimum (not read); Rds(on) 150 / 88 mOhm (search extract).
12. CJ2305 leakage at -3.4 V and Rds(on) 120 mOhm at -1.8 V (round 3, source: search summary, unverified; replaces the 90 mOhm LCSC attribute; simulated drop 39.0 mV at 45 C against 50 mV: 22 % margin). Body-diode VSD at uA (assumed 0.455 V at 10 uA, 0.395 V at 45 C in the model): R35 relies on VSD >= 0.25 V at about 16 uA (at 45 C the boost is in pass-through, +3V3 3.305 V); bench check at 10 uA, 25/45 C, 3 samples. Model line fixed (missing space before CBD).
13. DFE201610E Isat 2.7 A (LCSC) against 2.3 A needed: 17 % margin; height 1.0 mm (RS/Farnell summary).
14. C1 leakage (series datasheet not read): the sleep budget assumes 2 uA at 1.5 V, a 330 uA rated-voltage spec could be far higher; bench measure. Capacity 5.3 mF at -20 % against 3.9 mF minimum (36 % margin).
15. C1 height 10 mm from the diameter plus lead bend; lying footprint to be drawn.
16. J1 plug LCSC code and price (budget 0.25 against 0.056 listed); mated height of header + plug (listing says 17.6 mm for the plug); IP2X statement not read; 300 V / 10 A header (vendor page) against 250 V / 4 A needed: V margin ample.
17. ADS1115 input impedance about 6 MOhm, absolute-max input current 10 mA continuous, power-down current (recall from the TI datasheet). R18 margin 9 %.
18. NTC probe price 1.2 each (estimate) and the sleeved-tip requirement R33.
19. J2 height 3.8 mm, SW1 height 2.0 mm (recall); SW1 pad spacing against the KMR2 footprint.
20. PCB, assembly setup, extended-part, placement, hand-soldering and shipping costs (section 2): estimates, range 32-42 USD.
21. Y1 ESR 70 k equals the Espressif limit; margin 0.
22. Passive prices (C2/C3, divider and pull-up resistors): estimate 0.03 each.
23. J2 (XY350V-3.5-4P) wire range: the LCSC listing gives 18-24 AWG (search summary). 24 AWG (0.2 mm2) is the minimum, so the probe leads must be >= 0.2 mm2 or fitted with ferrules. Put this in the probe lot spec and the installer sheet.
24. J2 footprint drill: TerminalBlock_Phoenix_PT-1,5-4-3.5-H is a stand-in with the same 3.5 mm pitch. Check its hole diameter against the pin diameter on the Xinya XY350V-3.5 drawing (search summary only; drawing not read) before ordering boards.
25. J2 placement (layout intent): >= 10 mm from the J1 wire entries, on the opposite (right) board edge, as in the architecture.md 6.3 floorplan (J2 at x 80-88, 230 V zone at x <= 20). Set as `placement: {edge: right}` in design.yaml, with J1 at `{edge: left}`.

Count: 25 items.

## 8. Risks

- Cost: R30 not met (central 37.5 against 28 USD): product decision.
- Placeholder footprints: relay and battery holder pins; do not order boards before replacing them.
- Single source: Panasonic relays (consigned, 450 pcs at one distributor), MAX17227A (ADI), ADS1115, ESP32 module.
- Low stock: MAX17227A 158 at JLCPCB (10 needed; section 0b), J1 plug 1600, C1 parts 6502 (fine); C782984/C311679 about 1000.
- Missing models: no vendor SPICE for ADS1115, ESP32, CJ2305 (fit), crystal (fit), TVS (fit). TPS61299 PSpice (SLVME14 / SLVME79 / SLVME62) not downloaded; DRV8837 SLVM958A not downloaded.
