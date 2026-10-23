# Layout escalation, 4-layer attempt (2026-10-21)

One build: `hwflow build --until layout --auto --brief --reuse`, 4 layers, 74 x 46 mm (the R1 option without notches). FAIL. No further attempts, per the operator instruction.

Renders: `notes/layout_attempt4L_top.png`, `notes/layout_attempt4L_bottom.png` (kicad-cli pcb render).

## Error list
- 92 unconnected items: +3V3 30, GND 18, SENS_EN 14, VBAT_EN 10, BTN 8, VBAT_P 8, EN 6, NTC1_A 6, NTC2_A 6, VBAT_ADC 6, then 2-4 each on 25 more nets. Router: 55 unrouted after 4 net orders per width class.
- DRC 205 errors: clearance 67 (mains-SELV 5.5 mm rule), creepage 40 (6.0 mm rule), silk_over_copper 52, silk_overlap 48, nonmirrored_text_on_back_layer 18 (the mirrored back-body footprints), copper_edge_clearance 6 (BT1, BT2, U1, K1 pads at the edge), silk_edge_clearance 5, lib_footprint_mismatch 1.
- Isolation R10 FAIL: closest 0.20 mm (AC_ROOM2 on In1.Cu next to K1_COIL_B). Examples: C1 GND pad 1.79 mm from AC_ROOM2 on In1.Cu; BT2 GND pad 4.39 mm from AC_L on F.Cu.

## Causes
1. Placement does not follow the zone plan (architecture.md: 230 V at x <= 20 mm, 6 mm gap, SELV beyond). J1 (230 V) landed bottom-left, about 5 mm from the U1 antenna and under BT2; K1/K2 sit in the middle of the SELV area. `edge:` hints and the isolation group do not constrain placement to a region, and `antenna_clearance_mm` did not keep J1 away from U1.
2. The router puts mains nets on the inner layers across the whole board (AC_ROOM2 on In1.Cu under SELV parts), so the 4-layer stack adds isolation errors instead of removing them.
3. Board area at 74 x 46: along the 74 mm axis the 230 V zone (about 20 mm) + 6 mm gap leaves about 48 mm, shorter than a cell holder (about 53 mm), and the antenna must stay 15 mm from cells, relays and mains. The architecture floorplan was drawn for 88 x 46 mm with the two screw-post notches (cells at x 28-80, antenna on the top edge).

## What would fix it (decisions needed)
- Board: 88 x 46 mm with the two 8 mm screw-post notches (R1 option 1, still fits the 503 box) instead of 74 x 46. hwflow outlines are rectangles only, so the notches need keep-out areas now and a notched outline before fabrication.
- Placement: either fixed anchor positions for the large parts from the architecture floorplan (J1, F1, K1, K2, C1, C2, BT1, BT2, U1: a floorplan, not tuning), or an hwflow feature that keeps an isolation group inside a region and keeps its nets out of the other zone on every layer.

# Attempt 5: 88 x 46 mm, fixed floorplan, 4 layers (2026-10-21)

One build without `--reuse` (the agent's build ran with `--reuse` and re-used the stale 74 mm placement; that was the orchestrator's brief, not a valid attempt). Renders: `notes/layout_attempt5_top.png`, `_bottom.png`.
Also fixed in hwflow before the build: `src/hwflow/pcb.py` `_place_footprint` now moves footprint zones to the part position and rotation (the U1 antenna keep-out was landing at the board origin).

Result: FAIL. 73 unconnected, DRC 96 errors: silk_over_copper 39, silk_overlap 33, creepage 17, clearance 6, via_dangling 3, track_dangling 2.
- Isolation R10: clearance 0 violations (5.50 mm in plan); creepage 17 violations, all GND pour at 5.50 mm from mains pads/tracks (limit 6.0).
- Unconnected, all SELV: SENS_EN 20, +3V3 16, VBAT_EN 10, BTN 8, EN 6, NTC1_A 6, NTC2_A 6, VBAT_ADC 6, 4 each on AIN_NTC1, AIN_REF, K2_IN1, K2_IN2, LED_N, SCL, SDA. Router: 44 unrouted after 4 net orders per width class. Cause not established in one build (the mains rule areas are all-allowed and net-filtered, so they do not block SELV routing).
- Clearance 6: U2 (SOT-563, 0.5 mm pitch) pad to pad 0.16 mm and U5 pad to track 0.175 mm against the 0.2 mm net-class clearance.
- Floorplan anchor mistakes: F1 courtyard in the NOTCH_L keep-out; J1 pin 1 to the K2 coil pad A2 1.13 mm short of 6.0 mm; courtyard overlaps BT1/BT2, K1/F1, J1/F1.

Proposed fixes (one more build):
1. Correct the anchors: move F1 out of the notch; J1 pin 1 >= 6.0 mm from the K1/K2 coil pads; clear the BT1/BT2, K1/F1, J1/F1 overlaps.
2. Isolation clearance_mm 5.5 -> 6.0, so pours pull back to the creepage distance (tightening; on a flat board without slots, creepage equals the surface clearance).
3. A fine-pitch clearance rule (0.15 mm, within JLCPCB capability) for U2/U5 pads.
4. If SELV nets are still unrouted after 1-3, it is a router issue for hwflow, not a design decision.

# Attempt 6: corrected anchors, clearance 6.0, fine-pitch rule (2026-10-21)

Anchors: BT1 (53.3, 6.4, 0), BT2 (53.3, 19.5, 180), C1 (36, 34, 0), C2 (48, 34, 0), U1 (62, 37.5, 180), J2 (82.5, 41.5, 90), K1 (12.5, 5.6, 180), K2 (12.5, 40.4, 180), J1 (18.4, 29.2, 270), F1 (11.5, 14.9, 0). `board.isolation` clearance 6.0 / creepage 6.0. Rule `fine_pitch` (0.15 mm, U2/U5) added to `board.drc_rules`; the router cannot parse it (a clearance rule with a footprint condition is skipped, docs/layout-perf-router-rules.md); KiCad DRC honours it.
Pre-build check (notes/anchor_check.py, courtyard bboxes and pad edge gaps): J1.1 to K2.A2 6.37 mm, to K2.A1 11.22, K1.A1 18.54, K1.A2 23.51; F1 courtyard to notch centre 4.35 (circle, >= 4.3); no courtyard overlap (BT1/BT2 gap 0.1, K1/F1 0.05, J1/F1 0.29 in x); K1 pad 14 and K2 pad 13 at 0.35 mm from the edge.
Build (no --reuse): FAIL, stopped at attempt 1 (162 errors > 40, structural). 88x46, 4 layers, 14 vias, 71 unrouted, DRC 91 errors.
DRC: clearance 15 (mains-SELV 6.0: J1 pads 2/3 against SENS_EN / NTC1_A tracks on F.Cu at 5.58-5.96 mm; AC_ROOM1 on B.Cu 17.4 mm track against NTC1_A vias at 4.7 mm), creepage 5, silk_over_copper 39, silk_overlap 35, via_dangling 2. The attempt-5 U2/U5 clearance errors are gone.
Unconnected 71: +3V3 11, SENS_EN 5, VBAT_EN 5, BTN 4, EN 3, VBAT_ADC 3, 2 each on 14 nets, 1 each on 8 nets.
Placer still reports two fixed-part violations: F1 in keep-out NOTCH_L (the placer tests the courtyard box against the bounding box of the r 4.3 circle: x -2.05..6.55, y 18.7..27.3; F1 courtyard bottom 18.9 overlaps by 0.2 mm; F1 is 8.0 mm tall and K1 courtyard ends at 10.85, so it cannot clear without moving K1 or F1 sideways into J1), and J1.1/K2.A2 0.39 mm short (placer pad-box metric; my Euclid edge gap is 6.37). Post-layout did not run.
Renders: notes/layout_attempt6_top.png, notes/layout_attempt6_bottom.png.

# Attempt 7: router fix (KICAD_RIP_PREEXISTING=0), J1 y 28.6, mains bubble rule (2026-10-21)

Build 1 (no edits, `--auto`): the first run hit the route cache (the key does not include the router environment; same 71 unrouted as attempt 6), so `build/routecache` was removed and the build re-run. Result: 11 unrouted, DRC 61 errors (clearance 34, creepage 16 at 6.0 mm: J1 pads 1/2/3 and F1 pad 1 against SELV tracks and vias on F.Cu, In1.Cu, In2.Cu, B.Cu; K1_COIL_B on In2.Cu 0.375 mm from the J1 pads), isolation R10 FAIL. Placer: F1 vs NOTCH_L and J1.1/K2.A2 (0.39 mm short).
Cause: the router applies creepage track-to-track only (docs/layout-perf-router-rules.md), so SELV tracks and vias ran inside the 6 mm bubble of the J1/F1 through-hole pads, mostly on inner layers.

Edit set (cycle 1): J1 anchor y 29.2 -> 28.6 (J1.1 to K2.A2 pad edge 6.94 mm, clears the placer metric). `lib/mknotch.py` adds a rule area MAINS_BUBBLE (6.2 mm around the J1, F1, K1, K2 mains pads, all copper layers) to the MainsRuleAreas footprint. `board.drc_rules` adds `selv_bubble`: disallow track/via in MAINS_BUBBLE for every net class but iso_mains_SELV (tightening only).
Build 2 (no --reuse, 4 attempts): attempt 1 kept: 11 unrouted, DRC 11 errors (all unconnected items, none of clearance or creepage), 73 vias. Isolation R10: 6.00 mm in plan, DRC clearance 0, creepage 0. J1.1/K2.A2 violation gone. Still reported: F1 courtyard in the NOTCH_L keep-out (the placer tests the bounding box of the r 4.3 circle; true distance from the F1 courtyard to the notch centre is 4.32 mm >= 4.3, and KiCad DRC has no items_not_allowed error). No anchor move clears it: F1 courtyard is y 10.9-18.9 between K1 (ends 10.85) and the bbox (starts 18.7); the x move would be +2.94 into J1.
Unconnected 11: GND zone 2, SENS_EN 3 (U3.8, U4.7, U5.7), VBAT_P 2, +3V3 1, SW_BOOST 1, K1_COIL_B (K1.A2) 1, K2_COIL_A (K2.A1) 1. No silkscreen errors in the error list. Post-layout did not run (layout FAIL on unrouted nets).
Renders: notes/layout_attempt7_top.png, notes/layout_attempt7_bottom.png.

# Attempt 8: 8 attempts, no polygon change (2026-10-21)

Channel: not cut. The coil pads already lie outside the MAINS_BUBBLE polygon (checked numerically with shapely against the polygon in lib/mknotch.py and in the built .kicad_pcb): K1.A1 (22.25, 8.1) is 3.91 mm outside, K1.A2 (22.25, 3.1) 7.70 mm, K2.A2 (22.25, 37.9) 2.37 mm, K2.A1 (22.25, 42.9) 7.01 mm. A channel would have changed nothing, so the polygon was left as is.
Build (--auto --attempts 8, no --reuse): kept attempt 1 of 8: 12 unrouted, DRC 12 errors (all unconnected, 0 clearance, 0 creepage), 86 vias. Attempts 2-8: 9 to 25 unrouted, 16 to 48 DRC errors (attempt 8: 9 unrouted but 36 DRC errors). Isolation R10: 6.00 mm in plan, DRC clearance 0, creepage 0.
Unconnected 12: GND zone islands 3, SENS_EN 3 (U3.8, U4.7, U5.7), +3V3 2 (U5.1, U2.5), SW_BOOST 1 (U2.2), VBAT_P 2 (U2.1 with U2.3 and a via), K2_COIL_B 1 (K2.A2 to U5.3). K1_COIL_B and K2_COIL_A of attempt 7 are routed now; K2_COIL_B is open instead. The other open nets cluster at U2 (boost) and the SENS_EN / +3V3 area, so the open coil net looks like router congestion, not the bubble.
Placer: F1 vs NOTCH_L false positive (accepted). Post-layout did not run. Renders: notes/layout_attempt8_top.png, notes/layout_attempt8_bottom.png.
