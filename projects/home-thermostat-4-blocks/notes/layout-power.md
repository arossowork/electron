# Power tracks at 0.3 mm (layout attempt 9, 2026-10-21)

Change (operator): width_mm 0.3 on VBAT_RAW, VBAT_P, SW_BOOST, +3V3 (U2 has 0.5 mm pitch pads); SENS_EN class signal.

Post-layout numbers (build/REPORT.md, attempt 2 of 4 kept, 8 unrouted, so partly incomplete copper):
- IR drop reported by hwflow: +3V3 0.00 mV (U2.5), VBAT_P 0.00 mV (C1.1), VBAT_RAW 0.00 mV (BT1.1). Not credible next to the guard measure below; the nets are split in islands (VBAT_P 3, +3V3 3, SW_BOOST 2), so the check probably measures only within one island. To recheck once routing is complete.
- Post-layout SPICE `vdrop_guard_mv` (R4, limit 50 mV at 0.7 A): 53.9 / 57.2 / 59.9 mV at 0 / 25 / 45 C, against 32.9 / 36.3 / 39.0 mV pre-layout. The extracted trace resistance adds about 21 mV, i.e. about 30 mohm in the battery path at 0.7 A. R4 FAILS post-layout.
- Current density at the 1.5 A boost input peak (5 ms Wi-Fi burst): 1.5 A / (0.3 mm x 0.035 mm) = 143 A/mm2 on 1 oz outer copper. IPC-2221 (external, 10 C rise) gives roughly 1 A continuous for 0.3 mm on 1 oz; the burst is 5 ms per Wi-Fi wake, so heating is negligible, but the DC average is not the problem: the resistive drop is (R4 above). Inner-layer segments (0.5 oz if the stack uses it) would double the density. hwflow printed no separate current-density finding.

Conclusion: 0.3 mm is acceptable for SW_BOOST and +3V3 locally at the U2 pins, but the VBAT_RAW / VBAT_P path from the cells through M1/M2 to C1 and U2 needs to be wide (or a pour) away from the U2 pads to keep R4 (<= 50 mV): a neck-down to 0.3 mm only at the fine-pitch pads.

# R4 guard path sizing (layout cycle 1 of the resistance budget, 2026-10-21)

Measured nodes: `vdrop_guard_mv` = v(VBAT_RAW) - v(VBAT_P). The post-layout netlist names a net by its first pad (sorted by ref, J first): VBAT_RAW -> BT1.1, VBAT_P -> C1.1 (the 0.7 A test load BTEST is also on the VBAT_P node = C1.1). The measured copper is therefore BT1.1 -> M1.3/M2.3 (VBAT_RAW) and M1.2/M2.2 -> C1.1 (VBAT_P). U2/L1 hang off C1.1 and are not in the measured path (they only share the net).
Budget: pre-layout 33-39 mV at 0.7 A against 50 mV; allow 15 mV of copper: 15 mV / 0.7 A = 21 mohm total, copper TC at 45 C (+8 %) included.
Copper: 1 oz = 35 um, rho 1.72e-8 ohm m -> 0.49 mohm/sq at 20 C, 0.53 mohm/sq at 45 C.
Length: BT1.1 (29.55, 6.4) -> M2 (26.5, 6.4) / M1 (26.35, 12.7): about 5 mm (VBAT_RAW); M1 -> C1.1 (36, 34): about 29 mm (VBAT_P); total about 34 mm.
Width 1.2 mm on both nets: 34 / 1.2 = 28 sq x 0.53 = 15 mohm -> 10.5 mV at 0.7 A, plus about 4 vias at 1 mohm = 3 mV: 13.5 mV, inside 15 mV. (1.0 mm would give 18 mohm + vias = 15.3 mV, no margin.)
Floorplan: M1 (26.35, 12.7, rot 0) and M2 (26.5, 6.4, rot 90) beside the BT1.1 pad in the strip between the K1 courtyard (x <= 24.75, y <= 10.85) and the cell courtyards (x >= 28.3); U2 at (39.5, 31.5) on the front above the C1/C2 back-side bodies (pins 1/3 4.5 mm from C1.1). Pre-build check: no courtyard overlap (M2 x 24.8-28.2 vs K1 24.75 / BT1 28.3; M1 y 11.0-14.4 vs K1 10.85, x to 28.2 vs 28.3), M1 pad to J1.3 pad edge 6.17 mm (>= 6.0). SW_BOOST and +3V3 keep the class defaults (neck-down at U2).
