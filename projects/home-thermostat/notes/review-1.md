# Review 1 (stage 5, hw-reviewer)

Summary: 7 must, 7 should, 1 could. Datasheets: TPS61099 SLVSD88M p.3-12; CS1238 only from the adom wiki (Chipsea PDF not readable).

## must
- M1 U2/BT1 (R3, R7, R16): EFF fixed at 0.85 is generous. SLVSD88M Fig 6-5: 80-83 % at VIN 0.7-1.0 V, 100-150 mA. At 83 % the cell needs 0.618 A (> 0.6 A, R3); at 80 % 0.648 A (= input limit). Alkaline R roughly doubles at 0 C (0.6 ohm: no operating point). Fix: EFF(VIN, IOUT) from Fig 6-5, RCELL(T), rerun at 0 C; PM chooses: raise low-battery threshold, limit TX power, or change cell.
- M2 BT1/U2: no reverse-polarity protection. Reversed cell: VIN/SW at -1.5 V (abs max -0.3 V), ~2 A through low-side body diode. Keystone 2460 has no documented keying. Fix: low-side NMOS ideal-diode (RDS(on) specified at VGS 0.9-1.2 V) or keyed holder; resimulate R3.
- M3 U1 EN: boost limits current below VOUT 1.6 V (200 mA), soft start 0.7 x ILIM, 4.7 mF charges in 0.12-0.2 s; 10 ms EN RC releases U1 at 1.5-2 V (< 3.0 V min). Boost needs > 3 kohm load below 1.6 V. Fix: nanopower supervisor (~2.9 V, e.g. TPS3839 ~150 nA) on EN.
- M4 U3: CS1238 pin map, REFIN/REFOUT, input common-mode unverified (wiki only). Common mode AGND-0.1 V only at PGA 1/2; AINN at GND forces PGA 1. Fix: verify against Chipsea PDF; firmware PGA = 1; consider REFOUT for the dividers.
- M5 AC nets, K1/K2 pad 12 (R8): mains class clearance 2.5 mm < 4.0 mm; creepage 6 mm unchecked; pad 12 (NC contact, live when reset) has no net. Fix: net AC_NC1/2, 4.0 mm mains-to-other rule, slot.
- M6 K1/K2 footprint: pad positions provisional. Fix: TE DXF (1415390-1) before layout.
- M7 R2 trace: shows verified, but parts.md gives 57.6 uA at 3 uA C1 leakage (limit 55 uA), +1.6 uA from U2 max IQ. Fix: mark R2 unverified until C1 leakage is measured; C15 to PM.

## should
- S1 U2/C1: COUT 4.7 mF vs 100 uF max; EN bounce on cell insertion (>= 400 us off for EEPROM reload). Bench test; optional RC on EN.
- S2 M2/AVDD_SW: hard turn-on into ~3.3 uF dips +3V3. Slow gate (10 k + Cgs, 50-100 us); simulate at 100 ns step.
- S3 back-power of U3/U4/U5 through IO20, IO4-IO7 pulls at reset. Firmware: drive low / disable pulls, GPIO hold in sleep.
- S4 K1/K2 (R6): inductive rating only UL B300 at 6000 ops; no AC15 life or 10 A inrush data. Ask TE; DNP 275 VAC MOV footprint across 11-14.
- S5 R5 hum: 20 dB assumes ideal 20.0 ms boxcar; 25 samples at 1280 SPS = 19.53 ms; +10 % oscillator gives ~15 dB at 47.5 Hz. Timer-gated 20 ms window or model tolerance.
- S6 U1 footprint self-drawn, keep-out only a drawing line. Use Espressif footprint, add keep-out rule area, check 15 mm to relays in 3D.
- S7 trace R1, R8, R9, R10 verified by layout but no layout yet: mark pending.

## could
- C1 TP on IO21 (UART TX); confirm JSMSEMI DRV8837 pinout = TI; derating at the Wi-Fi operating point.

## Checked OK
U2 YFF ball map and FB to GND; relay coil polarity vs DRV8837 inputs; ESP32 strap pull-ups on IO2/IO8/IO9.
