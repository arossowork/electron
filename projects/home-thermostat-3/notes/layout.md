# Layout notes

## Round 8 (2026-10-23): 230 V copper at 1.0 mm, no neck (conflicts.md D15)

Goal: AC_L_IN, AC_L, AC_ROOM1, AC_ROOM2 at width_mm 1.0 and min_width_mm 1.0 (no neck); build PASS.

### Blocker (round 7, D14)
AC_ROOM1 (J1.2 -> K1.14, F.Cu) has one way up to K1.14: past the right side of F1.1. There it runs between
F1.1 (AC_L_IN, between_mm 1.5) and the 6 mm isolation circle of K1.A1 (K1_COIL_A). With F1 at x 14.0, the corridor
proof gives 0.342 mm without a neck (0.898 mm with necks) at (16.46, 12.23). The other ways are closed:
- left of F1.2: the F1.2 circle (r 1 + 1.5) and the notch_l conductive isolation (r 4 + 6 around (2.25, 23)) overlap
  by about 0.7 mm;
- between F1.1 and F1.2: 5.08 mm pitch - 2 mm pad - 2 x 1.5 mm = 0.08 mm.

### Geometry of the fix
The pinch is the gap between two circles: F1.1 (r 1 + 1.5 = 2.5) and K1.A1 (r 1 + 6 = 7). Their centres are
(14, 13.25) and (23, 9.35), 9.81 mm apart, so the gap is 0.31 mm. Moving F1 to the left (-x) widens it by about
0.92 mm per mm until the J1.1/F1.1 gap becomes the limit.

Corridor sweep (`hwflow.corridor.margin`, scratch script; F1 y 13.25, rot 0; no neck):

| F1 x | AC_ROOM1 plain | pinch | AC_L plain | AC_L_IN plain |
|------|----------------|-------|------------|---------------|
| 14.0 | 0.342 | F1.1 / K1.A1 | 1.786 | 5.93 |
| 13.5 | 0.801 | F1.1 / K1.A1 | 1.786 | 6.33 |
| 13.0 | 1.259 | F1.1 / K1.A1 | 1.786 | 6.70 |
| 12.7 | 1.542 | F1.1 / K1.A1 | 1.786 | 6.90 |
| 12.5 | 1.728 | F1.1 / K1.A1 / J1.1 | 1.728 | 6.99 |
| 12.4 | 1.796 | F1.1 / J1.1 | 1.786 | 6.99 |

#### Attempt 1: F1 x 12.5 (rejected in the layout stage)
AC_ROOM1 and AC_L both fit 1.73 mm, and `hwflow check` was OK. The round F1.2 pad is 6.03 mm from the notch_l boss
(centre distance 11.03 - pad r 1 - boss r 4). But the layout stage's conductive-isolation check
(postlayout.conductive_clearances) models every pad as its bounding box. By that check the pad corner is
5.68 mm from the boss, under the 6 mm limit, so the layout fails. The corridor proof and `hwflow check` do not test
pads against conductive keep-outs, so only the build showed it. I stopped the build after its first --auto attempts.
Box-metric limit: F1.2 at y 13.26 needs F1 x >= 13.21.

#### Attempt 2: F1 x 13.5 and K1 x 13.5
Second lever: K1 moves +x. K1.A1 and its 6 mm isolation circle move away from F1.1. The contact pads move away from
the left edge, and their y does not change.

| F1 x | K1 x | AC_ROOM1 plain | AC_L | AC_L_IN | F1.2 box to notch_l |
|------|------|----------------|------|---------|---------------------|
| 13.25 | 12.5 | 1.035 | 1.786 | 6.52 | 6.031 |
| 13.25 | 13.0 | 1.494 | 1.786 | 6.87 | 6.031 |
| 13.25 | 13.5 | 1.865 | 1.786 | 7.15 | 6.031 |
| 13.5 | 12.5 | 0.801 | 1.786 | 6.33 | 6.155 |
| 13.5 | 13.0 | 1.259 | 1.786 | 6.70 | 6.155 |
| 13.5 | 13.5 | 1.728 | 1.786 | 7.04 | 6.155 |
| 13.5 | 13.75 | 1.952 | 1.786 | 7.15 | 6.155 |

Chosen: F1 x 13.5 and K1 x 13.5. AC_ROOM1 fits 1.728 mm with no neck, and the pinch is still F1.1 / K1.A1. F1.2 is
6.155 mm from the boss by the box metric.
- K1 courtyard is x 1.25-25.75, inside the hv_parts barrier (x <= 26). Its coil pads are at x 23-25 (SELV). The contact
  pads keep their y, so K1.14 is still 0.60 mm from y = 0 (layout-constraints.md 6), and pad 13 is now 1.75 mm from x = 0.
- F1 courtyard is x 6.46-15.46, y 11.0-15.5. The move is along x only, so the vertical gaps to K1 and J1 do not change.
- The MAINS_BUBBLE polygon in lib/mknotch.py is not used by any drc_rule now, so the stale F1/K1 positions in it have
  no effect.
- `hwflow check`: OK (0 errors). Placer on the saved placement: 0 violations, and legalize moved no part.

Placement changes: F1 (14.0, 13.25, 0) -> (13.5, 13.25, 0); K1 (12.5, 5.6, 180) -> (13.5, 5.6, 180). K2, J1, U3, C11
and the room-to-relay mapping did not change. AC_ROOM1 stays on F.Cu only.

### Build (2026-10-23 11:52, `hwflow build --reuse --auto`): PASS
Movable parts keep the saved placement; fixed parts take the new design positions. The mains route group missed the
cache and was routed again, and attempt 1 was kept.
- Layout: 88 x 46 mm, 4 layers, 0 unrouted, DRC 0 errors / 88 warnings, 0 placement violations. Vias: 85 (layout),
  87 (post-layout count). too_narrow [], off_layer [], conductive_isolation [].
- 230 V tracks (build/home_thermostat_3.kicad_pcb, read with pcb.read_board). There are no necks and no AC vias.

| net | narrowest = widest track | layer | length |
|-----|--------------------------|-------|--------|
| AC_L_IN | 1.4 mm | B.Cu | 6.8 mm |
| AC_L | 1.4 mm | B.Cu | 44.8 mm |
| AC_ROOM1 | 1.0 mm | F.Cu | 29.6 mm |
| AC_ROOM2 | 1.0 mm | F.Cu | 14.1 mm |

- R10 mains-SELV: 6.00 mm in plan (limit 6.0; closest AC_ROOM2 / GND on B.Cu), DRC clearance 0 and creepage 0.
  between_mm: 1.60 mm (limit 1.5; closest AC_L_IN / AC_ROOM1 on F.Cu), DRC 0 / 0.
- The round-7 AC_ROOM1 track-current warning (0.15 mm neck) is gone. IR drop AC_ROOM1: 20.6 mV at 1.5 A (13.7 mOhm;
  it was 36.6 mV).
- Post-layout SPICE: 109/109 pass, no regressions. R3: tightest v3v3_min_b20c 3.204 V (min 3.2), v3v3 max 3.418 V
  (max 3.46). R4: vdrop_guard_mv max 36.41 mV (max 55), guard_copper budget 9.51 / 13.64 mOhm, i_rev_dead max 0.0046 uA
  (max 1).
- trace: 35/35 verified.

Remaining new warning: region-nets, AC_ROOM1 up to 1.10 mm outside the mains region (F.Cu, x 20.1-21.1 at y 17-21).
The track runs to the right of J1.1. This is outside the D9 wording (F.Cu at x <= 20), but every limit still holds:
- the 6 mm isolation to SELV holds (6.00 mm);
- the SELV_region rule area (x >= 21.5) is respected;
- the copper is still under the MP1 insulating sheet (x 0-26), which is the reason D9 gives for the x <= 20 limit.

Inside x <= 20 the only path is between J1.1 and F1.1. That gap is 6.65 - 1.5 - 1 - 2 x 1.5 = 1.15 mm, too tight for
a 1.0 mm track plus the router's 0.1 mm slack per side. F1 cannot move left (pad-box distance to notch_l) or up (the K1
courtyard), so that gap cannot grow. The PM should confirm that D9 covers x <= 21.1 (under MP1).
