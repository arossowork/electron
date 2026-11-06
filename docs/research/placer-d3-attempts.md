# Placer on PCBWorld D3-A/B/C: hwflow placement against human placement (krt)

Goal: of the D3 boards that route clean with the human placement (route mode, krt), at least 90% also clean with
hwflow's placement (place mode: the importer's `free` parts released, the rest fixed, full `layout --auto` flow).
Clean: fully routed, DRV 0, no placer violation naming a released part (layout_bench's rule, unchanged).
Score: `scripts/place_vs_route.py --route <route json> --place <place json>` per set.

## Denominator (route mode, human placement, krt, code 3aeee21)

`layout_bench.py --pcbench-only --pcbench-set <s> --pcbench-mode route -j 8`: D3-A 91 of 95, D3-B 7 of 9, D3-C 6 of 9,
104 boards in all. The last results before the main merge (a70660f) had 86 + 6 + 5 = 97. The merged code is clean on
7 more boards (D3-A 0024, 0034, 0037, 0050, 0086; D3-B 0203; D3-C 0599) and on every board that was clean before.

## Attempts

`-j 8`: 8 boards at a time on a 16-thread laptop, so per-board times are not comparable with the serial runs in
router-ab.md. Every placement step stops on a count (docs/layout-flow.md, reproducibility), not on the clock.

| # | commit | change | evaluated | pooled | D3-A | D3-B | D3-C | notes |
|---|---|---|---|---|---|---|---|---|
| 1 | 3aeee21 | baseline: merged main (tight, extreme placement, extreme routing), no placer change | all denominator boards of the 3 sets | **98/104 = 0.942** | 88/91 = 0.967 | 6/7 = 0.857 | 4/6 = 0.667 | target reached: stop |
| 2 | bae99f9 | tight placement: the board bounds hold the pad copper, not the courtyard (settle raster 3 mm past the edge, packed from its outer corner; legalize clamp; bounds term); SMD pads outside their courtyard block other courtyards on their side; pad drill offset in the copper model; pour stitch as extreme-routing step 3 | all denominator boards of the 3 sets | **99/104 = 0.952** | 88/91 = 0.967 | 6/7 = 0.857 | 5/6 = 0.833 | won 0056, 0400; lost 0083 (an LCD mounting pad 0.291 mm from the edge: the copper bound clamped pads exactly at 0.3 mm) |
| 3 | 772514a | attempt 2 + the copper bound keeps 0.05 mm (`EDGE_CU_MARGIN`) inside copper_edge_mm; pour-stitch step dropped (won nothing) | all denominator boards of the 3 sets | **100/104 = 0.962** | 89/91 = 0.978 | 6/7 = 0.857 | 5/6 = 0.833 | won 0083 back; 0086 now fully routed, lost to 1 annular_width on its released via footprint XRef2 (intrinsic, as 0113) |
| 4 | c0a4e26 | net-class pad clearance per copper layer (SMD pads on opposite sides keep none, as KiCad); a courtyard-less SMD part blocks only its own copper side, its fallback box on that side | all denominator boards of the 3 sets | **100/104 = 0.962** | 89/91 = 0.978 | 6/7 = 0.857 | 5/6 = 0.833 | won 0077; lost 0076 (a bottom-mount USB socket without courtyard: its B.Cu pads 0.09 mm from a socket strip's pin, no check paired them once guarded pads stopped blocking the front courtyard) |
| 5 | (next commit) | a courtyard-less part's SMD pads keep the pad clearance to other parts' through-hole pads (fixes 0076 of attempt 4); extreme routing steps 3 (pour taps on the walled-in pads, all signal nets re-routed) and 4 (pour stitch after it) | 0077 and 0520 only (`--only`) | - | 0077 clean | - | 0520 not clean | 0520 fully routed for the first time (attempts 2 and 3 of its 4: 0 open, GND islands closed by steps 3 and 4); its 24 errors left are annular_width on U1, the PHOTON footprint (0.092 mm ring, min 0.1), a fixed_error in route mode and counted in place mode, as 0113 and 0086 |

Attempt 1 wall time: D3-A 22 min, D3-B 5 min, D3-C 57 min (0571 and 0626 about 45 min each).

Place-mode summary (all boards of each set, not only the denominator):

| set | legal | rout. | clean | DRV | HPWL x human | WL x human | vias - human | time s |
|---|---|---|---|---|---|---|---|---|
| D3-A (95) | 0.98 | 1.000 | 0.95 | 0.11 | 0.88 | 0.54 | +1 | 70 |
| D3-B (9) | 1.00 | 0.98 | 0.67 | 0.62 | 0.85 | 0.62 | +5 | 175 |
| D3-C (9) | 1.00 | 0.96 | 0.44 | 0.60 | 0.75 | 0.72 | +25.5 | 1299 |

Earlier (a70660f, serial, router-ab.md part 2): clean 0.67 / 0.44 / 0.22, about 70 of the old 97 denominator boards.

## The 6 boards still lost (attempt 1)

| board | set | class |
|---|---|---|
| 0056_nrf2rfm69 | D3-A | placer violation (U1, the radio module) |
| 0077_HaveSome_PCB | D3-A | placer violation (SOT223, PINS1, isolation) on a 7.6 x 18.4 mm board |
| 0086_BB-PWR-8009 | D3-A | 2 connections open, 11.4 x 11.4 mm board |
| 0113_maytal | D3-B | 6 annular_width: footprint-intrinsic, see below |
| 0400_laptimer58_Chickadee | D3-C | 1 solder_mask_bridge, 1 copper_edge_clearance, 1 shorting_items |
| 0520_RGBMatrixPanelCPLD | D3-C | 2 connections open |

**A scorer asymmetry (not changed).** An error that names only fixed parts is a `fixed_error` and not a DRV
(`repair.fixed_error`). In route mode every part is fixed, so a footprint's own error (a pad's annular ring under the
board rule, a malformed courtyard) is excluded. In place mode the same footprint is released, so the same error
counts as a DRV, wherever the placer puts the part. 0113 is lost to this alone: its 6 annular_width errors are the
same as its 6 fixed_errors in route mode. No placement can cure it. Whether such footprint-intrinsic errors should be
excluded in place mode too is a decision about the metric, left open here.

## Layout corpus and tests at e360b5e (no hwflow code changed, so start and end are the same run)

`scripts/layout_corpus.py summary --place --jobs 6 --timeout 3600`, krt: 14 of 37 cases clean (ht4_s11_mains,
ht4_s1_k2in, ht4_s1_mains, kb_keys, oshw_ac_relay, oshw_buck_tps54202, oshw_ch32v003_usb_devboard,
oshw_ch32v003_usbpd, oshw_hot_plate_power, oshw_ina228_simple, oshw_powerline_usbc, pr_mcu, pr_pmic, sp_control);
6 placements with placer violations (esp32c3_devkit_lipo, espgaggia, hestiapi_one, hue_simple_switch, milliohm_meter, rocketbeacon); oshw_pico_ice timed out (3600 s; it took 3582 s in router-ab.md).
router-ab.md part 2 (a70660f, 35 cases without pr_mcu and pr_pmic): 8 clean.
Fast tier (`pytest -m "not slow"`): 890 passed.

# Phase 2 (branch placer-d3-100): toward 100%

Attempts 2 and 3 above (attempts 1-3 of 20 used). Best: attempt 3 at 772514a, a full evaluation of the 104
denominator boards: **100/104 = 0.962** (D3-A 89/91, D3-B 6/7, D3-C 5/6). No board clean in attempt 1 was lost.

## The changes kept (each generic: no board name, no PCBWorld-only path)

- Tight placement (no placer margin, no board margin): the board bounds hold the pad copper (copper_edge_mm + 0.05 mm),
  not the courtyard, which may overhang the edge as `violations` and KiCad allow: bounds term, legalize's clamp,
  settle's raster (3 mm past the edge, repacks also from its outer corner). Won 0056.
- SMD pads outside their own courtyard (a module's pad rows) block other courtyards and such pads on their side
  (`_side_overlap`, legalize pairs, settle raster, the spatial hashes). A stricter rule, not a looser one. With the
  next item, won 0400.
- A pad's drill offset moves its copper in the footprint model (`libs.footprint`, `placer._pad_copper`).
- Tried and dropped: the pour stitch as a third extreme-routing step (no board won); a packing search over the
  neighbourhood when no region holds the violators (0077: 3 -> 2 violations, still lost).

## The 4 boards still lost

| board | why | curable? |
|---|---|---|
| 0113_maytal (D3-B) | 6 annular_width on a released part, intrinsic to its footprint | not by placement: metric question |
| 0086_BB-PWR-8009 (D3-A) | now fully routed; 1 annular_width on XRef2, a released via footprint (route mode: a fixed_error) | not by placement: same metric question |
| 0077_HaveSome_PCB (D3-A) | 13 parts on 7.6 x 18.4 mm; the back side needs about 11 slots of 3.5 mm for 10: five 3 mm test pads, the header, and the SOT-223's pads, which the tight placement keeps clear on both sides (a part without a courtyard); the net-class clearance between pads is also layer-blind (a front pad and a back pad 0.05 mm apart in plan view count) | only by relaxing those two rules: a decision, not made here |
| 0520_RGBMatrixPanelCPLD (D3-C) | legal placement, 1-2 open: small F.Cu GND islands (QFP and 0402 GND pads walled in by tracks) or a signal net near U2 | not by the pour stitch (each island needs a displacement that leaves a signal net open), not by pour taps (G2 left open) nor by the routability ranking (single-board diagnostics) |

Under the current metric the reachable maximum is 102/104 (0113 and 0086 lost to intrinsic footprint errors).

## Layout corpus and tests at the final code (4a8dbae, same placer as 772514a)

`scripts/layout_corpus.py summary --place --jobs 6 --timeout 3600`: 14 of 37 clean, the same 14 cases as at
e360b5e; the same 6 placements with placer violations; oshw_pico_ice timed out at 3600 s, as before.
Fast tier: 895 passed (5 new tests in tests/test_place_edge.py).
