# Circuit notes (stage 4, review-2 fixes in stage 5)

## Review-2 fixes (stage 5), 2026-10-19
Simulated with `hwflow build --until sim` at 0 and 50 C. Values below are from that run.

### M1: R2 sleep corners (typical and worst case)
- `sleep_dc` is the TYPICAL corner (global `.param wc=0`). `sleep_wc` is the WORST-CASE corner (`wc=1`, U6 IQ 500 nA, U2 VOUT max). Both use the same 41.7 uW allowance (208 - 166.3 uW active). The limit is unchanged.
- The loads in each corner, and where each number comes from:

| Load | Typical | Worst case (50 C) | Source |
|---|---|---|---|
| ESP32-C3 module deep sleep + XTAL32K | 5.0 + 0.5 uA | 8.0 + 0.5 uA | Datasheet 5 uA typ at 25 C. No 50 C value is published, so 8 uA is an ESTIMATE (architecture.md section 4 sensitivity row). |
| C1 leakage at 3.3 V | 1 uA | 3 uA | Typical = purchase spec (PM round 4, q2). Worst = architecture "<= 3 uA at 50 C" and the parts.md pessimistic estimate. No datasheet bound exists. |
| U6 TPS3839 IDD | 150 nA | 500 nA | SBVS193D |
| U2 IQ | inside EFFL 0.75 | + (1.1 - 0.4) uA VIN, + (1.5 - 0.6) uA VOUT | SLVSD88M 6.5 |
| U2 VOUT in PFM | 3.40 V | 3.47 V (3.37 x 1.03) | SLVSD88M 6.5 (review-2 S2) |
| M3 IGSS through R16 | 0 (not specified) | 10 uA x VGS / 8 V = 2.8 uA at VGS 2.2 V | AO3416 IGSS 10 uA max at 8 V, scaled linearly. This is an upper bound. |
| Q1 junctions | GP model (pA) | 100 nA | MMBT3904 ICEX 50 nA + IBL 50 nA max |
| M2, M4 IDSS (AO3401A) | model | 5 uA x VDS / 30 V | 55 C max at 30 V, scaled linearly |
| M1 IDSS (2N7002) | model | 4.7 uA x VDS / 60 V | Log interpolation between 1 uA (25 C) and 500 uA (125 C) |
| IO3, IO9 input leakage | 50 nA (IO3) | 50 + 50 nA | ESP32-C3 IIL/IIH max |
| D4 SP0402B-ULC-01 at 3.3 V | 25 nA | 25 nA | Review-2 figure (datasheet not read) |
| D2, D3 | 0 | 0 | At 0 V in sleep, because AVDD_SW is off |

- Results (2.5 V pack):
  - Typical: rail 6.71 uA, pack sleep **30.4 uW** (allowance 41.7 uW). Total 196.7 uW: 16.0 months on alkaline (8.26 kJ), 13.8 months at the worst alkaline energy (7.13 kJ).
  - Worst case: rail 13.71 uA at 3.47 V, pack sleep **72.7 uW**. Total 239.0 uW: 11.3 months (7.13 kJ), 13.1 months (8.26 kJ). **FAIL against 208 uW.** This is conflicts.md C20, a product decision.
- What drives the worst case: module +3 uA (about 14 uW), C1 +2 uA (about 10 uW), M3 IGSS (about 7 uW, linear upper bound), U2 IQ max (about 6 uW), MOSFET IDSS (about 4 uW).

### S1: reversed pack after a quick swap (Q1, R18, C23, R19)
- (Superseded by "S1 fix: Q1 base bias from the held gate" at the end of this file: R20 added, C23 to GND, R18 3.3 M, R19 100 k. The values and results below are the round before.)
- Q1 MMBT3904: emitter to VBAT, collector to the M3 gate, base to **BAT_N** through R18 1 M, with the speed-up capacitor C23 (10 nF) across R18. R19 10 k now separates C21 (node M3_GH) from the gate (M3_G).
- How the values were found:
  - R18 1 M alone gives 2.6 uA of base current, so Q1 sinks only about 1 mA. MC yield was 58-66 %.
  - The Cgd kick from the rising drain lifts the gate. R19 100 k made that worse, because the gate is then high impedance.
  - C23 1 nF gave 94 %. C23 10 nF gives 100 %.
  - R19 10 k limits the C21 feed into the clamped gate to 50 uA.
- This deviates from the review proposal (base to GND through 1 k). Simulation showed that the GND-referenced base only turns on once VBAT is below about -0.6 V. With M3 at VGS(th) min, that version gave VIN -1.0 to -1.1 V and 2-3.4 A for microseconds.
  - With the base on BAT_N, Q1 sees the pack polarity directly. A reversed pack gives VBE = +Vpack, so Q1 pulls the gate to VBAT within the time it takes to discharge Ciss.
  - In normal operation VBE = -Vpack (2.2-3.6 V, inside VEBO 6 V).
  - During a TX burst, VBE = V(BAT_N) - VIN, which is about 0.03 - 0.7 V, so Q1 stays off and the hold is intact.
- `reverse_tran` test: pack 3.20 V, pulled at 4.30 s, out for 0.5 s, then reversed (-3.20 V) at 4.80 s. The gate is still at 0.46-0.62 V at that moment. Results:
  - VIN/SW minimum, M3 at VGS(th) min 0.4 V: +0.12 V (0 C) and +0.21 V (50 C); MC (25 C) minimum +0.0005 V. At typical VGS(th): +0.48 / +0.51 V. The limit is -0.3 V.
  - Peak pack current: 1.5 A, above 0.1 A for 5 us. This is the charge of C4/C22 (3-4.4 uC). VIN stays positive, so no current flows through the U2 body diode.
  - Settled reverse current: 2.6-2.7 uA, which is the Q1 base current through R18 (limit +/-10 uA).
- `reverse_ins` (replaces `reverse_dc`): a reversed pack is inserted into a dead board.
  - Results: 1.7 uA (2.2 V pack) and 3.1 uA (3.6 V lithium), VIN >= +0.31 V, rail 0 V. MC 100 %.
  - Why it replaced the DC sweep: the global .nodeset let the DC sweep converge to a false "powered" solution in 2 of 50 MC runs.
- Model change needed for this test: the U2 UVLO now has its 200 mV hysteresis (SLVSD88M), so the converter drains C4 to about 0.45-0.58 V after the pack is pulled. The residual gate voltage equals that value.
- The PFM weight `exp()` overflowed at Wi-Fi load currents ("out of range for ^" in the logs), which corrupted one MC run of power_tran and three of powerup. The exponents are now clipped to +/-50.
- Leakage in normal operation:
  - Q1 junctions: 100 nA max, counted in `sleep_wc`.
  - R19: gate leakage only.
  - C23: none at DC. Q1 E-B reverse voltage is the pack voltage, inside VEBO 6 V.

### S2: PFM output
- The U2 model target is VOUT x 1.03 below about 1 mA of load. The rail sleeps at 3.40 V (typical) and at 3.47 V at the VOUT max corner.
- `s2_rail_max_li_wc` (fresh lithium 3.6 V, sleep, 0 and 50 C): 3.471 V against the 3.6 V ESP32-C3 absolute maximum.
- Pass-through (VIN > 3.8 V) is still not modelled. The 2 x L92 pack is <= 3.6 V, and the manual must exclude 10440 Li-ion cells.

### S3: cold cutoff, temperature source and battery reading (firmware rules 9 and 10)
9. Cutoff table: Voc_cut(T) = max(2.20, 0.72 + 1.10 x (2 Rcell(T) + 0.07)), rounded up to 10 mV, with Rcell(T) = 0.40 x max(1, 2^((25 - T)/25)). That gives 2.56 V at 0 C (was 2.54 V) and 2.20 V at >= 8 C. The extra 20 mV is the review-2 S3 margin.
   - T is the ESP32-C3 internal temperature sensor reading minus 3 C. The chip reads warm after boot, so the guard errs toward the colder (higher) cutoff.
   - Read T in the first ms after wake, before the radio starts. Below 0 C, use the 0 C row (the box is specified 0-50 C).
   - Simulated: r3_vin_tx_110 at 0 C is 0.724 V (limit 0.7 V; it was 0.703 V at 2.54 V).
10. Battery reading for the cutoff and R17:
   - Read with the MCU awake (load >= 22 mA, radio off), >= 3 ms after IO2 goes low (divider settling 0.67 ms x 4.5).
   - Average 8 ADC samples (ATTEN2, calibrated).
   - Never read during or within 100 ms after a TX burst or a relay pulse.
   - The value is the loaded voltage, 30-58 mV below Voc. The comparison against Voc_cut(T) trips early, which is the safe side.

### S4: J4A / J4B mating rule (layout)
- J4B (header, front board) must mate pin-for-pin with J4A (socket, rear board). Place J4B on the front board's B.Cu at the same XY as J4A on the rear board's F.Cu, mirrored, so that pin 1 lands on pin 1.
- If either connector is not mirrored, pins 3<->4 and 5<->6 swap. The coil polarity then flips, the R16 OFF pulse latches the relays ON, and AC_* heating stays on.
- Check: overlay both board files at layout and confirm that the pin-1 markers coincide. On the first assembly, measure continuity J4A.3 to J4B.3.

### C2: BAT_N ESD
- C22 100 nF from BAT_N to GND, across M3.
- `reverse_tran` and `reverse_ins` show that it does not defeat the reverse protection. It only adds about 0.3 uC to the reversal charge pulse.

### D4
- ESD5Z5CL (1 uA at 5 V) is replaced by SP0402B-ULC-01 (LCSC C468904, 25 nA), 0402 footprint `Diode_SMD:D_0402_1005Metric`.
- Not verified this session: its datasheet, its price (0.05 USD estimate) and whether it is bidirectional (the symbol `Device:D_TVS` assumes it is).

## Round 4 rework (2 x AAA), 2026-10-19
Power path: BT1 (Keystone 2468, `Device:Battery`, pin 1 +) -> M3 AO3416 low-side NMOS (drain BAT_N, source GND, gate M3_G) -> U2 TPS610994 -> +3V3 with C1 EEU-FS1A472 (4.7 mF 10 V). U6 TPS3839K33 drives EN through R5/C6.

### M3 gate hold (R16 100 k, C21 1 uF): new
- With the gate tied straight to pack +, VGS equals the converter VIN.
- At the 0 C cutoff (Voc 2.54 V, 1.67 ohm) and the 1.10 A max input limit, the TX burst pulls VIN to 0.70 V. That is below VGS(th) max (1.1 V), so the body diode (0.62 V at 1 A) takes the current.
- Simulated with the gate on VBAT: VIN 0.673 V and rail 2.964 V (0 C, 1.10 A), and VIN 0.687 V (0 C, 0.85 A). Three R3 failures.
- R16/C21 (tau 0.1 s) hold the gate at the pre-burst pack voltage. The droop over 5 ms is 5 %, so VGS stays about 2 V. All corners pass.
- No DC current flows in sleep (IGSS only, counted in `sleep_wc`).
- (Superseded by review-2 S1.) The original text said that a reversed pack within 0.3 s of a removal was not credible. Review-2 showed that the gate stays charged after removal, and Q1/R18/R19 now clamp it (see above).
- Cost +0.006 USD (+0.015 USD for Q1, R18, C23, R19).

### VBAT sense: high-side switch (M4 AO3401A, R17 1 M): changed
- The low-side switch (M1 in series with R4) left the tap at VBAT when off: 3.6 V with lithium, above VDD (R17).
- Now M4 switches the divider top from VBAT.
  - R17 pulls the M4 gate to VBAT, so M4 is off.
  - M1 (gate AVDD_SW) pulls the M4 gate low while AVDD_SW is on.
  - R4 goes to GND, so with M4 off the tap is at 0 V.
- Simulated: tap - VDD <= -2.47 V in every case; leakage 14 pA (limit 0.5 uA); error +18.8 / +19.1 mV against +/-60 mV.
- Firmware reads VBAT = 3 x ADC (ATTEN2). R17 draws 3.6 uA only while AVDD_SW is on.
- The settling time is 0.67 ms (66.7 k x 10 nF C13). Read >= 3 ms after IO2 goes low.
- The reading is the loaded terminal voltage. With the MCU awake (22 mA at the rail, about 35 mA from the pack) it is 30 mV (25 C) to 58 mV (0 C) below the open-circuit voltage. The R16 cutoff comparison therefore trips early (safe side). Firmware may add I x R if it wants the open-circuit value.

### Other changes
- J4 pins 1-2: VBAT -> GND (guard pins). The pack is on the front board.
- D1: KT-0603R basic LED (R19).
- M2 price corrected.
- C1 ESR in simulation is 0.15 ohm (the R3 0 C budget).
- U6 IQ: 150 nA typ in every analysis except `sleep_wc` (500 nA max). Review-2 changed this; it was 500 nA everywhere.
- M3 uses the max RDS fit. `reverse_tran` also runs it at VTO 0.4 and 0.7 V.

### EN release (U6 td 120-350 ms)
- `powerup` inserts the pack into a dead board at 1 ms.
- EN is released at +3V3 = 3.297-3.304 V at 0 and 50 C in all three passes:
  - U6 typical, cutoff pack: 285 / 291 ms (0 / 50 C).
  - Min corner (VIT- 2.857 V, td 120 ms, 2.20 V pack): 224 / 207 ms.
  - Max corner (2.974 V, 350 ms, 3.60 V pack): 411 / 408 ms.
- The rail reaches 3.0 V in about 90 ms at the worst corner, so the longer delay only lengthens the boot. Rail max 3.30 V.

### Firmware rule 4 is relaxed
- Light sleep during relay pulses is no longer needed for R7.
- R7 and R16 are simulated with the MCU awake (22 mA) at the 2.20 V cutoff with 0 C resistance: coil 3.067 V, rail 3.293 V, droop 9.4 mV.
- Keep light sleep to save energy.

### Margins to watch (updated after review-2)
- **r3_vin_tx_110 at 0 C: 0.724 V against 0.7 V** (24 mV margin, cutoff 2.56 V). Bench check at 0 C is still recommended.
- R2: typical 30.4 uW of the 41.7 uW sleep allowance. Worst case 72.8 uW fails (conflicts.md C20).
- MC runs at 25 C only, so it does not cover the 0 C corner. The tx_ilim 1.10 A pass sets C1 to 3.57 mF (worst tolerance x 0.95 at 0 C) explicitly.

## Blocks (design.yaml groups)
- front_power: TPS610994 (U2). It follows TI fig. 9-1: EN to VIN, FB to GND, L1 2.2 uH, C4 10 uF at VIN, C5 10 uF at VOUT, and the C1 4.7 mF buffer (R3). C1 ESR is 0.15 ohm in simulation, the 0 C budget. BT1, M3, R16, C21, R19, Q1, R18, C23, C22 and J4B are in this group (front face / front board).
- front_mcu: ESP32-C3-MINI-1 (U1).
  - EN RC: R5 10 k and C6 1 uF (tau 10 ms, module datasheet).
  - Y1 32.768 kHz on IO0/IO1 with C2/C3 18 pF C0G: CL 12.5 pF = 18/2 + 3 pF stray.
  - Programming is on test pads TP1-TP6: 3V3, GND, D-, D+, EN, IO9 (USB-Serial/JTAG).
- front_adc: CS1238 (U3) is ratiometric. REFIN and VDD = AVDD_SW, which is also the top of R1/R2 (47 k 0.1 %). R7/R8 1 k and C11/C12 100 nF form the input RC.
- front_vbat: R3 200 k / R4 100 k 0.1 % divider to IO3 (ADC1_CH3, not a strapping pin). It is switched on the high side by M4 (gate pulled up by R17 and pulled down by M1, whose gate is AVDD_SW). See "Round 4 rework" above.
- front_relay_drv: 2 x DRV8837 (U4, U5) on AVDD_SW (see C16 below).
- rear_mains: K1/K2 PE014F03, J1/J3 KF301. Nets AC_* are `class: mains`.
- rear_selv: J2/J6 probe terminals with D2/D3 ESD5Z5CL, J4A. BT1 moved to front_power in round 4.
- front_ui: SW1 on IO9 with R11 10 k pull-up, R12 1 k, D4 ESD (SP0402B-ULC-01) and C18 100 nF debounce. D1 LED from 3V3 through R13 680 ohm to IO8, which sinks it, with R14 100 k pull-up.

## Strapping pins
- IO2 must be 1 at reset: R9 100 k pull-up. It is also the M2 gate, so AVDD_SW is off during reset.
- IO8 must be 1 at reset: R14 100 k pull-up. The LED is off while IO8 is high.
- IO9 must be 1 for a normal boot: R11 10 k pull-up. The button pulls it low for download mode.

## C16 decision: no nSLEEP GPIO
The DRV8837 VM, VCC and nSLEEP pins all connect to AVDD_SW. That rail is switched by M2 (AO3401A, gate IO2, 100 k pull-up). In sleep the bridges are unpowered, which gives:
- No standby current. The earlier design had VM on 3V3, which drew 2 x 45 nA on the rail (1 AA era numbers, superseded by the R2 corners above).
- No GPIO needed for nSLEEP. IO21 is now spare, and R10 is removed.
- No relay glitch during boot or reset, because the bridges have no supply until the firmware pulls IO2 low.
- Cost: the 92 mA coil pulse runs through M2. The AO3401A drop is under 10 mV (datasheet); the generic model gives 0.17 V, and the coil voltage still simulates at 3.06 V min against 2.7 V. C14/C16 (1 uF) decouple VM on AVDD_SW.

Firmware rules this circuit depends on:
1. Pull IO2 low and wait >= 1 ms before driving IN1/IN2.
2. Pulse for 41 ms, brake for 1 ms, set IN low, then set IO2 high.
3. Do not overlap relay pulses with a CS1238 conversion. They share AVDD_SW, which is the ADC reference.
4. (Round 4: no longer required for R7; keep it to save energy.) Put the MCU in light sleep (0.13 mA, architecture section 4) during relay pulses. Original 1 AA reasoning: at Voc 0.9 V with Rint 0.3 ohm (the R7 case), an awake 22 mA MCU plus a 92 mA coil needs 0.39 W / 0.85 = 0.46 W from the cell. That gives I = (0.9 - sqrt(0.81 - 4 x 0.3 x 0.46)) / 0.6 = 0.653 A, which is over the 0.65 A average input limit. With light sleep it is 0.49 A. The R16 case (Voc 1.15 V) is simulated with the MCU awake at 22 mA.

## Margins to watch (1 AA design, superseded by round 4 above)
- r3_icell_avg_eol: 0.5989 A against 0.6 A. This follows from the assumed 85 % efficiency (architecture 3.3). Measure the efficiency at bring-up.
- r3_vmin_eol: 3.034 V against 3.0 V (MC min).
- r3_vin_min: 0.711 V against 0.7 V (TPS61099 VIN min), in the 0.9 V relay case at 0 C.

## Two-board split
hwflow builds one PCB per design file, so this design holds both boards. Groups `front_*` and `rear_*` mark the board of each part. J4A (2x5 socket, rear) and J4B (2x5 header, front) carry GND x4 (pins 1, 2, 7, 8; pins 1-2 were VBAT before round 4), COIL1_A/B, COIL2_A/B and PROBE1/2. At the layout stage, split by group prefix into two layouts (or two design files with J4A/J4B as the interface). Keep every AC_* net and K1/K2 on the rear board, behind the 6 mm creepage slot (R8).

## Open items
- CS1238 pin map and REFIN range: confirmed against the Chipsea datasheet Rev.1.1 (see "CS1238 firmware rules" below).
- The relay footprint is PROVISIONAL (TE DXF needed).
- The JSMSEMI DRV8837DSGT-JSM pinout is assumed equal to TI DRV8837 (review-2 C3). Confirm it against the JSM datasheet.
- Rtop tempco (25 ppm/K) is not modelled. Architecture 5.2 gives 0.015 C.

## Review-1 fixes (stage 5)
- M2 reverse polarity: M3 is a low-side NMOS ideal diode. BT1 - goes to BAT_N, the M3 drain; the source is GND. The gate is driven from VBAT through R16 / C21 / R19, and Q1 clamps it (round 4 and review-2). The part choice is in notes/parts.md. A reversed pack draws 1.7-3.1 uA (reverse_ins; Q1 base current). The FET drop is in every power_tran result.
- M3 EN supervisor: U6 TPS3839K33 monitors +3V3. Its push-pull ~RESET drives R5, and R5/C6 add about 14 ms.
  - Power-up (round 4 numbers): EN is released at +3V3 = 3.34-3.38 V in all three U6 corners at 0 and 50 C (powerup analysis).
  - U6 also resets the MCU if the rail falls below about 2.9 V.
- M5 mains:
  - K1/K2 pad 12 (NC contact, live in the reset state) is on nets AC_NC1/AC_NC2. These are class mains, single pad, `allow_single`.
  - Every AC_* net has a 4.0 mm clearance. hwflow applies a net clearance against all nets, so at layout add a KiCad custom rule for mains to mains of 2.5 mm. The relay 11-14 and MOV pads are 5 mm pitch with about 2.6 mm gaps, and they would otherwise show as DRC errors.
  - **Layout must** also route a slot and keep >= 6.0 mm creepage and >= 4.0 mm clearance between every AC_* copper and any SELV copper. The relay coil pads A1/A2 are SELV.
- S2: R15 10 k + C19 10 nF slow the M2 gate (tau 100 us). The IO2 turn-on edge is simulated at 100 ns. The +3V3 droop at AVDD_SW turn-on is 13 mV at 0 C (limit 100 mV).
- S4: RV1/RV2 are DNP footprints for a 275 VAC MOV (S07K275) across COM-NO of each relay. Fit them if motor loads show contact wear.
- Could: TP7 on IO21 (TXD0) for the boot log.

### Firmware rules added by review-1
5. (M4) CS1238 PGA = 1 on both channels. AINN is at GND, and the input common-mode range reaches AGND - 0.1 V only at PGA 1/2 (review-1, from the adom wiki; the Chipsea PDF could not be downloaded).
6. (S5) Time the hum-averaging window with the ESP32 40 MHz timer, not with the CS1238 sample count. Measure the ADC output rate against the timer (count DRDY over a known interval). Average exactly 20.00 ms of samples and weight the last sample by its fractional share, so the window error stays within +/-1 %. Simulated: 24.0 dB at 47.5 Hz with a 19.8 ms window (worst case) and 24.9 dB at 52.5 Hz with 20.2 ms, against 20 dB. An untrimmed +10 % oscillator would give about 15 dB, which fails.
7. (S3) While AVDD_SW is off, no GPIO may source current into U3/U4/U5. In deep sleep and before IO2 goes low:
   - Drive IO4, IO5, IO6, IO7, IO10 and IO20 low, or set them as inputs with pulls disabled.
   - Use gpio_hold_en / gpio_deep_sleep_hold_en so they stay low in sleep.
   - Before releasing them, check that the IO20 boot pull-up (UART0 RX) does not keep SCLK high once AVDD_SW is on. SCLK high for more than 100 us powers down the CS1238.
8. (S1, bench) After a cell insertion, check that EN stays low >= 400 us when contact bounce interrupts the supply.

### Layout-verified requirements (S7, review-2)
- R1 and R8 now carry `evidence_pending` instead of `evidence`, so `hwflow trace` shows them UNVERIFIED until layout.
- R6 (verification: test) does the same, because there is no inrush or cos 0.4 rating.
- The antenna keep-out part of R9/R10 is still pending. R9 shows "pass" from its droop measures only.

### CS1238 datasheet (M4) and relay drawing (M6)
- CS1238: closed by the Chipsea datasheet Rev.1.1 (notes/datasheets/cs1238.md). Pin map, REFIN range and PGA 1 common mode confirmed; no schematic change.
- The PE014 footprint stays PROVISIONAL and blocks release.

### CS1238 firmware rules (datasheet Rev.1.1, sections 2.5, 2.6, 2.6.1, table 11)
11. AVDD_SW is switched off between measurements, so the CS1238 goes through power-on reset at every wake and comes up with PGA 128, 10 Hz, REFOUT on. At every wake, before the first conversion, write the config register (command 0x65): 0x70 (REFO_OFF 1, 1280 Hz, PGA 1, channel A), 0x71 (channel B), 0x73 (CH_SEL 11, internal short). PGA 1 is mandatory (rule 5).
12. Discard the first 4 conversions after power-on and after every channel switch (4 / 1280 Hz = 3.1 ms).
13. Offset: read CH_SEL 11 (0x73) at every wake and subtract it from both channels. The R4 budget keeps 0.2 mV residual.
14. A saturated code (0x7FFFFF) means "probe open or below -6 C" (node ratio >= 0.5 = full scale at PGA 1, Rntc >= 47 k): report a probe fault, do not drive the relay from it.
15. Gain calibration, one constant per channel in flash: at bring-up fit a 10.00 k 0.1 % resistor at J2 (then J6) in place of the probe, k = (10/57) / (offset-corrected ratio), and multiply every later ratio by k. Uncalibrated, the +/-0.5 % gain error gives up to -0.54 C (simulated r4u_*, over R4).
- Measurement energy (R2): the architecture's 144 x 5.3 mJ used 1.5 mA for the CS1238; the datasheet gives 1.26 mA typ at PGA 1 (2.11 mA at PGA 64/128, which applies only for the few ms before the config write). The budget is kept as a conservative figure, not recomputed.

### R4 gain calibration in simulation (ntc_tran)
- Calibration phase: 0-45 ms of ntc_tran (AVDD_SW on at 5 ms), the probe sources are replaced by 10.01 k (0-25 ms) and 9.99 k (25-45 ms); the ideal ratio is tracked and held at 24 ms and 44 ms (r4_cal_hold_a/b 0.1756 / 0.1753). The 5 C point moved from 35 ms to 75 ms (30 ms settling, as before).
- Reading: g_meas x ratio x (10/57) / (g_cal x hold), g = 1 +/- 0.5 %, drift +/-8 ppm/C x |T - 25| on the reading only (calibration at 25 C). The r4_* rows are the worst of 4 combinations (gain sign x cal resistor sign), signed.
- Results (0 and 50 C identical: |T - 25| = 25 at both): calibrated worst -0.407 C (ch1, 5 C), MC 50 -0.394..-0.411 C; uncalibrated worst -0.536 C (r4u_ch1_5c), MC 50 down to -0.554 C. The probe B/R25 corners dominate (architecture 5.2).

### reverse_tran testbench rebuilt (S1 re-check, review-2 S1 / post-layout timeout)
- Old testbench: global schedule to 5.3 s, pack pulled at 4.30 s (0.1 s after the alkaline Wi-Fi wake), reversed at 4.80 s, trapezoidal, 0.5 ms max step, 3 corners = 3 full 5.3 s runs (about 11.6 k points each). Post-layout hit the 600 s ngspice limit.
- Finding 1, the old pass was not converged. After the pull, C1 recharging drains VIN to the UVLO (0.45 V) within 9 ms, then the converter hiccups between 0.45 and 0.65 V. The gate residual at 4.80 s (0.50-0.61 V) depends on the hiccup phase, which depends on the time step. Same schedule, VIN min at VGS(th) min, 0 / 50 C: 0.5 ms trap (old) +0.080 / +0.120 V; 20 us trap -0.458 / +0.288 V; 20 us Gear +0.092 / +0.220 V; 0.5 ms Gear with a 0.5 us grid -0.460 / +0.101 V.
- Finding 2, a pack pulled in sleep is the worse case. With no load after the pull, VBAT and the M3 gate (C21) stay charged: 2.72 V and 2.82 V after 0.5 s. When the pack goes back in reversed, M3 conducts until VBAT reaches about -0.6 V, where Q1 (base held at BAT_N, about 0 V, by C23) turns on and pulls the gate down. VIN min is -0.64 / -0.66 / -0.65 V at 0 C (VGS(th) max fit / min / typ) and -0.50 / -0.52 / -0.52 V at 50 C. The peak pack current is 2.9 A (0 C) and 4.8 A (50 C) for about 12-19 us, and the U2 substrate diode carries part of it. Settled current 2.6-2.7 uA. MC 50: VIN min -0.53 to -0.58 V. **S1 FAILS against -0.3 V.** A BJT clamp referenced to BAT_N cannot act before VIN is about -0.6 V while the gate is charged. A fix needs a circuit decision (for example a Schottky clamp GND -> VBAT rated for the peak current, or a different reverse-protection topology). This is a lead / architect decision.
- Trapezoidal integration rings point to point on the Q1/M3 switching. In one trap run it latched a false state: 2.3 A reverse current for > 50 ms, Q1 base at -2.8 V. Gear does not show this state. Gear at 5 us max step agrees with the final setting within 30 mV (the final setting is the more negative).
- New testbench: reverse_tran uses `alter @src[pwl]` in its own control block (global sources unchanged). It runs from the sleep operating point at Voc 3.20 V, pulls the pack at 50 ms, reverses it at 0.55 s and stops at 1.05 s. It uses `option method=gear`, a 0.5 ms max step and a VSGN breakpoint grid (0.5 us for 100 us, then 2 us / 20 us / 200 us). The list run is a 10 ms placeholder whose values fail every limit (v(BAT_N,VBAT)); the control echoes overwrite them, and an aborted run fails the analysis.
- Run time, pre-layout: 1.2-1.9 s and about 4.1 k points per corner (old: about 5.6 s and 11.6 k points per corner). The whole analysis takes 4.8 s wall time in the parallel build. Post-layout was not run (layout frozen). Estimate: the post netlist has about 8 times the elements, so about 3-4 ms per point, i.e. 12.4 k points in about 40-60 s if Gear avoids the step collapse. The old run used about 35 k trap points and hit 600 s, which points to step collapse rather than point count.
- reverse_ins: the operating point now holds +3V3 and VBAT at 0 V (`.param deadop`, terms inside BCBUF_LEAK and BIQ2I, released by 0.5 ms). Without this, the global .nodeset gave a false powered solution in 2 of 50 MC runs after the netlist changed. Dead-board results: VIN min -0.02 / -0.04 V (was +0.31 V from a partly charged start), pack 1.7 / 3.1 uA. MC yield is 100 %.

### S1 fix: Q1 base bias from the held gate (R20 added, C23 to GND, R18 3.3 M, R19 100 k)
- Root cause: while M3 is on, BAT_N is held at GND (30 mOhm), so a base referenced to BAT_N sees the reversal only as VBAT falling, and Q1 conducts only at VBAT < -0.6 V. The reversed pack discharges C4 (2.7 V) at about 0.36 V/us, so the gate must be pulled down while VBAT is still positive.
- Fix: R20 6.8 M from M3_GH (held gate) to Q1_B, R18 3.3 M from Q1_B to BAT_N, C23 10 nF from Q1_B to GND. Base bias = 3.3/10.1 x V(M3_GH) = 0.327 x Vgate (0.92 V with the 2.8 V sleep gate). Q1 turns on at VBAT about 0.3 V and pulls the gate to VBAT. C23 holds the bias over the 10-15 us edge and supplies the base charge.
- Normal operation: VEB = 0.67 x pack <= 2.4 V (VEBO 6 V). TX burst at the 0 C cutoff: gate held at about 2.4 V, base 0.78 V, VIN min 0.72 V, so VBE = +0.06 V and Q1 stays off. r3_vin_tx_110 0.7246 V (was 0.7241 V), r3_vrail_110 3.071 V.
- Simulation steps (reverse_tran, Gear, 0 and 50 C, VGS(th) max fit / 0.4 V / 0.7 V):

| Step | VIN min 0 C (max / min / typ) | VIN min 50 C (max / min / typ) | Sleep typ / wc (uW) | Note |
|---|---|---|---|---|
| Before (R18 1 M, C23 to BAT_N, R19 10 k) | -0.64 / -0.66 / -0.65 | -0.50 / -0.52 / -0.52 | 30.4 / 72.7 | FAIL |
| + R20 2.2 M, C23 still to BAT_N | +0.21 / -1.03 / +0.20 | +0.37 / -1.17 / +0.36 | 32.3 / 74.2 | FAIL: latch at VGS(th) min, see below |
| + C23 moved to GND (R18 1 M, R20 2.2 M, R19 10 k) | +0.14 / +0.13 / +0.13 | +0.24 / +0.22 / +0.22 | 32.3 / 74.2 | pass, +1.9 uW |
| R18 1 M, R20 6.8 M (ratio 0.13) | -0.34 / -0.35 / -0.35 | -0.25 / -0.26 / -0.26 | 31.2 / 73.3 | FAIL: threshold too low |
| R18 3.3 M, R20 6.8 M, R19 10 k | +0.06 / -0.40 / -0.21 | +0.28 / -0.07 / +0.12 | 31.0 / 73.2 | FAIL: Q1 linear after the edge |
| **R18 3.3 M, R20 6.8 M, R19 100 k (chosen)** | **+0.24 / +0.23 / +0.23** | **+0.33 / +0.28 / +0.32** | **31.0 / 72.6** | pass |

- Latch with C23 across R18: after Q1 clamps, C21 recharges C4 through R16 and VBAT drifts up to the M3 threshold. M3 starts to conduct, BAT_N falls, and C23 kicks Q1_B down to -2.5 V. Q1 lets go, C21 drives the gate to 1.4 V, and M3 carries 1.3 A for milliseconds (VIN -1.0 to -1.2 V). With C23 to GND the same drift is a stable loop: M3 conduction pulls VBAT and the gate down.
- R19 100 k: after the edge, Q1 must sink the C21 feed (Vgate/R19). The only DC base drive is R18 from BAT_N (3.4 V reversed): 0.9 uA with 3.3 M. With R19 10 k (280 uA) Q1 left saturation, the gate rose with the drifting VBAT, and VBAT settled at -0.40 V (0 C). With 100 k (28 uA) Q1 stays saturated. The earlier reason for 10 k (Cgd kick into a high-impedance gate) applied to the BAT_N-referenced base. reverse_ins still passes (VIN min -0.02 / -0.04 V).
- Chosen-fix results: s1_vin_min / s1_sw_min +0.24 / +0.23 / +0.23 V at 0 C, +0.33 / +0.28 / +0.32 V at 50 C. Peak pack current is unchanged (2.9 A at 0 C, 4.8 A at 50 C, 10-15 us, 24-25 uC). It is the C4/C22 charge through the pack and does not pass the U2 body diode. Settled reverse current 0.80 / 0.84 uA (was 2.6 / 2.7 uA). MC 50: VIN min +0.254..+0.294 V, yield 100 %. reverse_ins: 0.52 / 0.94 uA (was 1.7 / 3.1 uA).
- R2: the VBAT -> R16 -> R20 -> R18 -> BAT_N path is 10.2 M, 0.25 uA at 2.5 V. Typical 30.99 uW (limit 41.7), worst case 72.58 uW (limit 79.9; the M3 IGSS term falls because the gate is 1 % lower).
- Options not taken:
  - b) Schottky GND -> VIN, not simulated. 1) VIN would be clamped at -Vf, so VBE of the BAT_N-referenced Q1 (V(BAT_N) - VIN, about 0.1 + Vf) never reaches 0.6 V. The pack would then stay shorted through M3 and the diode (2-4 A) until R16 discharges C21 (about 0.3 s). 2) A 5 A Schottky with Vf below 0.3 V at 5 A is not a small part (the typical SS54 class is 0.5 V at 5 A). 3) Low-Vf Schottkys leak tens to hundreds of uA at 3.6 V and 50 C, against 7 uW of R2 worst-case margin. These are generic class figures, not read from a datasheet. Rejected.
  - c) P-FET high-side ideal diode: not needed, because a) passes.
- Not covered by reverse_tran: a pack reversed within about 0.3 s of a pull right after a TX burst, when the gate holds a residual 0.45-0.62 V. Then the base bias is about 0.2 V and Q1 acts only at VBAT about -0.45 V. In the round before, this case gave VIN >= +0.12 V at VGS(th) min, because the gate is barely above threshold. Not re-simulated (the global schedule's 4.3-4.8 s pull is not run). The bench swap test covers it.
- Layout: new R20 0603 (B.Cu, front board, next to Q1 / R18 / C21). C23 pad 2 changes from BAT_N to GND, so C23 needs a GND via or a pour connection.
