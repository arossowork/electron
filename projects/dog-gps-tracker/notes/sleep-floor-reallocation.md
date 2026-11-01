# PWR-5 sleep floor re-allocation (stage 2, PM round 3 rework)
PWR-5 is derived from PWR-3 (268 uA eDRX average), not a product number.
Floor now: (nRF9151 PSM 2.7 + app RTC 1 + accel 1 + supervisor/WDT 0.4 + leakage 0.5 + supercap leakage 1.0) uA x 1.60 + boost Iq 1.0 = 11.6 uA.
New limit 15 uA: eDRX total = 84.5 + 1.0 x 1.60 (supercap) + (15 - 11.6) = 89.5 uA <= 268 (3.0x headroom), life 2350 / 0.0895 = 26300 h = 36 months;
poll fallback = 137.1 + 1.6 + 3.4 = 142.1 uA <= 268, 16500 h = 23 months. PWR-2 (1630) and PWR-3 hold. The accelerometer stays (1 uA class).

## Re-derivation 2026-11-01 (fix pass: PM round 7, 2 x GZ215F, 4.0 V rail, AON LDO, LC76G backup off, max values, 50 C)
Boost factor at 3.0 V in, 4.0 V out, 10 uA load: TPS61299 datasheet states "up to 91 % at ~10 uA" (VIN 3.6 V, VOUT 5 V; the 3.0 V -> 4.0 V curve in Fig 6-x is an image, not read) -> eta 0.91 (curve value), factor battery-side = (4.0/3.0)/0.91 = **1.465**; a worst case eta 0.85 gives 1.569 (also shown). Boost Iq is inside the efficiency curve (95 nA typ / 300 nA max VOUT; 300 nA x 1.333 = 0.4 uA extra at the max, added below).
Loads at the 4.0 V rail, uA (max values where read, else the allotment; the sum is "datasheet quiescent currents"):
| item | typ | max / used |
|---|---|---|
| nRF9151 PSM | 2.7 | 2.7 (PS typ; max not read) |
| app RTC (in nRF9151 PSM figure? kept as before) | 1.0 | 1.0 |
| LIS2DW12 LP1 1.6 Hz | 0.38 | 1.0 (allotment, ST max not read) |
| supervisor TPS3838K33 | 0.22 | 0.25 (LCSC) |
| TPL5010 | 0.035 | 0.075 (assumed 2x) |
| AON LDO TPS7A0230 | 0.025 | 0.10 (assumed) |
| board leakage | 0.5 | 0.5 |
| supercap balancing 2 x 4.7 M (4.0 V) | 0.43 | 0.43 |
| boost Iq at VOUT | 0.095 | 0.30 x 1.333 equivalent |
| supercap leakage, two modules, 23 C | 1.0 | 4.0 (2 x 2 uA max at 2.3 V/cell; ours is 2.0 V/cell: lower) |
| supercap leakage, two modules, 50 C | 3.8 (x3.75) | 15 (2 x 2 x 3.75 = 15 uA, spec max scaled) |
LC76G backup supply off in sleep (PM round 7 q2): 0 uA, not in the sum (the earlier V_BCKP 13 uA option is gone).
Rail-side sum (excluding supercap leakage and boost Iq): 2.7+1.0+1.0+0.25+0.075+0.10+0.5+0.43 = 6.06 uA.
- 23 C, typ leakage (1.0): (6.06 + 1.0) x 1.465 + 0.1 = **10.4 uA** (eta 0.91).
- 23 C, max leakage (4.0): (6.06 + 4.0) x 1.465 + 0.4 = **15.1 uA** (eta 0.91), 16.2 (eta 0.85): over 15 by 0.1-1.2 uA.
- 50 C, typ leakage (3.8 x scaled): (6.06 + 3.8) x 1.465 + 0.1 = **14.5 uA**; 15.6 at eta 0.85.
- 50 C, max leakage (15): (6.06 + 15) x 1.465 + 0.4 = **31.3 uA**. This is a spec-max bound that no unit reaches together (leakage typ is 0.5 uA per module; the max is 2x typ at 2.3 V/cell and ours runs at 2.0 V/cell, where leakage is lower; guide gives only typ at 70 C). Use the typ-scaled x2 margin as the design corner: (6.06 + 7.6) x 1.465 + 0.4 = **20.4 uA** (eta 0.91), 21.8 (eta 0.85).
Result: PWR-5 = 15 uA is met at 23 C with typical leakage (10.4) and fails at 50 C / max values (20.4-21.8 uA, spec-max bound 31). The pair of supercaps (2 uA leak max each, x3.75 at 50 C) is the cause: +1.0 uA at 23 C typ, +7.6 at 50 C against one GW209F.
Re-allocation (allowed: PWR-5 is derived from PWR-3): PWR-3 = 268 uA eDRX average. Average current with floor F: 74.5 + F uA (from the earlier build-up: 84.5 + 1.6 + (15 - 11.6) = 89.5 at F = 15, so 74.5 + F). At F = 22 uA (covers 21.8): eDRX average 96.5 uA <= 268 (2.8x), life 2350 mAh / 0.0965 mA = 24350 h = 33 months; poll fallback 127.1 + 22 = 149.1 uA <= 268, 15800 h = 21.6 months. PWR-2 (1630 uA) and PWR-3 hold. Proposed PWR-5 text: "<= 22 uA at 50 C, max datasheet values, 3.0 V battery; typ at 23 C 10.4 uA", an edit of requirements.yaml for the lead (not done here). The spec-max bound 31.3 uA would give eDRX 105.8 uA, still <= 268.
Not included: LC76G is 0 in sleep (VCC LDO off, backup off); the first 120 h supercap leakage decay (higher at insertion: test after 5 days); the supervisor and AON LDO currents are assumptions where marked.
