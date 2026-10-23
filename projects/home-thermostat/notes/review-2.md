# Review 2 (stage 5, hw-reviewer, after the 2 x AAA rework)

MUST: 3, SHOULD: 9, COULD: 3

Review-1 status. Closed: M1, M3, S2, S5, C1-TP7. Partly: M2 (DC only, see S1), M5 (slot, 6 mm creepage, mains-to-mains override are layout work), M6 (footprint corrected and mirrored right, still read off the figure), S3. Open: M4 (CS1238), M7 (now C20), S1, S4, S6, S7, C1.

Checked OK: M3 blocks a reversed pack at DC (40 pA); M4 high-side divider switch (no back-power, tap <= 1.2 V); U6 push-pull through R5 into C6; DRV8837 coil kick returns via M2 body diode into C1; coil polarity vs F-code; relay footprint mirroring and pad 12 nets; pin maps of U2, U6, M1/M2/M4, U4/U5; straps IO2/IO8/IO9 = 1 at reset; TPS610994 regulates to VIN 3.8 V typ.

## must
- M1 R2 / C20: 1 % margin (205.9 of 208 uW) on typical values. Not counted: (b) D4 ESD5Z5CL biased at 3.3 V (1 uA at 5 V); (c) U2 Iq max (+~4.5 uW); (d) M3 IGSS through R16; (e) module sleep at 50 C; (f) C1 3 uA estimate, no datasheet bound; (g) generic-model leakages. Fix: D4 -> SP0402B-ULC-01 (C468904, 25 nA); restate the R2 floor; R2 pending bench (3 boards, 25 and 50 C); PM decides whether typical-value margin is acceptable.
- M2 BT1 footprint: Keystone shows 3 posts of 2.9 mm; KiCad footprint has 2 NPTH of 3.45 mm; + pad unconfirmed. Fix: draw a project footprint from the Keystone drawing. Blocks layout.
- M3 U3 CS1238: pin map, reference selection, common mode at PGA 1, operating current still second-hand. Fix: Chipsea PDF.

## should
- S1 M3 gate hold: after removal, VBAT (10 uF) decays ~1.3 V/s; gate stays on 1.5-2 s; a reversed pack in that window sees M3 on (3-6 A through U2 body diode, abs max -0.3 V). Fix: NPN clamp MMBT3904 (E to VBAT, B to GND via 1 k, C to M3_G); transient test +3.2 V, open 0.5 s, -3.2 V.
- S2 U2 PFM output 3.4 V typ, up to ~3.47 V; pass-through at VOUT+0.5 V typ, no current limit; ESP32 abs max 3.6 V. Model PFM; bench fresh L92 and 3.7/3.8 V supply; manual excludes 10440 Li-ion.
- S3 C21 cutoff: use 2.56 V cold cutoff; define temperature source (ESP32-C3 internal sensor, -3 C guard) and read conditions (MCU awake >= 22 mA, >= 3 ms after IO2 low).
- S4 J4A/J4B: if not mirrored, pins 3<->4, 5<->6 swap and the R16 OFF pulse latches ON. Layout: J4B on B.Cu at J4A XY, check pin-1 mating.
- S5 K1/K2 footprint: grid-count reading; 2.5 vs 2.54 mm. Compare a real relay to a 1:1 print; confirm stock (TE "not currently available").
- S6 U2/C1: 4.7 mF vs 100 uF COUT max; EN bounce vs 400 us EEPROM reload. Bench test.
- S7 R6 trace shows verified without inrush / cos 0.4 data; R1/R8 count before layout. Mark R6 unverified or PM restates; R1/R8 pending.
- S8 U1 self-drawn footprint, no keep-out zone; antenna 11.2 mm from cell metal. Espressif footprint + rule area; 3D check.
- S9 AC_*: mains-to-mains 2.5 mm override not expressible; slot and 6 mm creepage pending. KiCad custom rule at layout.

## could
- C1 one cell reversed with mismatched cells gives 0.7 V, above start-up UVLO: fix the architecture text; polarity marks.
- C2 BAT_N contact ESD: add 100 nF BAT_N to GND or extend R14.
- C3 JSMSEMI DRV8837 pinout unconfirmed; stale notes in circuit.md; derating at the TX point.
