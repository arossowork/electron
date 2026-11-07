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
| 5 | 9db4423 | a courtyard-less part's SMD pads keep the pad clearance to other parts' through-hole pads (fixes 0076 of attempt 4); extreme routing steps 3 (pour taps on the walled-in pads, all signal nets re-routed) and 4 (pour stitch after it) | 0077 and 0520 only (`--only`) | - | 0077 clean | - | 0520 not clean | 0520 fully routed for the first time (attempts 2 and 3 of its 4: 0 open, GND islands closed by steps 3 and 4); its 24 errors left are annular_width on U1, the PHOTON footprint (0.092 mm ring, min 0.1), a fixed_error in route mode and counted in place mode, as 0113 and 0086 |
| 6 | 9db4423 | attempt 5's code (D3-C run again after the harness stopped the first run at 3 boards) | all denominator boards of the 3 sets | **101/104 = 0.971** | 90/91 = 0.989 | 6/7 = 0.857 | 5/6 = 0.833 | won 0076 back; the 3 boards lost each fail only on annular_width of a released footprint (0086 XRef2 x1, 0113 x6, 0520 U1 x24): the reachable maximum under the metric |
| 7 | 4d0ca12 | annular-ring repair: plated pads under the board's minimum ring grow to it before placement when the clearances allow (layout/board/padring.py; placer model, board, DSN, status.json, REPORT.md) | 0086, 0113 and 0520 only (`--only`) | - | 0086 clean | 0113 clean | 0520 clean | the grown pads: 0086 XRef2 0.838 -> 0.92 mm, 0113 6 mounting pads 3.1 -> 3.22 mm, 0520 U1 24 ovals 1.2 -> 1.236 mm wide; route mode on the 3 (the only D3 boards with such pads): still clean (DRV 0), their annular fixed_errors gone |
| 8 | 4d0ca12 | attempt 7's code | all denominator boards of the 3 sets | **104/104 = 1.000** | 91/91 = 1.000 | 7/7 = 1.000 | 6/6 = 1.000 | won 0086, 0113, 0520; no board lost |

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

Attempts 2 to 8 above (8 of 20 used; attempt 5 on 0077 and 0520 only, attempt 7 on 0086, 0113 and 0520 only).
Best: attempt 8 at 4d0ca12, a full evaluation of the 104 denominator boards: **104/104 = 1.000** (D3-A 91/91,
D3-B 7/7, D3-C 6/6). Attempt 6 (101/104) had left 0086, 0113 and 0520 on footprint-intrinsic annular_width errors of
released parts; the annular-ring repair (attempt 7) grows those pads to the board minimum, as a fab would require
for any board, and route mode on the same three boards stays clean.

## The changes kept (each generic: no board name, no PCBWorld-only path)

- Tight placement (no placer margin, no board margin): the board bounds hold the pad copper (copper_edge_mm + 0.05 mm),
  not the courtyard, which may overhang the edge as `violations` and KiCad allow: bounds term, legalize's clamp,
  settle's raster (3 mm past the edge, repacks also from its outer corner). Won 0056.
- SMD pads outside their own courtyard (a module's pad rows) block other courtyards and such pads on their side
  (`_side_overlap`, legalize pairs, settle raster, the spatial hashes). A stricter rule, not a looser one. With the
  next item, won 0400.
- A pad's drill offset moves its copper in the footprint model (`libs.footprint`, `placer._pad_copper`).
- Net-class pad clearance per copper layer (an F.Cu SMD pad and a B.Cu SMD pad keep none, as KiCad), and a
  courtyard-less SMD part blocks other courtyards only on its copper side (its fallback box on that side); its SMD
  pads keep the pad clearance to other parts' through-hole pads, which are on every layer (`_guard_pairs`). Won
  0077 (attempt 4 lost 0076 without the last rule; attempt 6 won it back).
- Extreme routing steps 3 (pour taps on the parts whose pour pads sit in a piece no via reaches, all signal nets
  re-routed around them) and 4 (the pour stitch after it). 0520 is fully routed now (it was 1-2 open), which
  shows its 24 U1 annular_width errors.
- Tried and dropped: the pour stitch as a third extreme-routing step on the first route (no board won); a packing search over the
  neighbourhood when no region holds the violators (0077: 3 -> 2 violations, still lost).

## Boards still lost

None at attempt 8. At attempt 6, 0113, 0086 and 0520 were lost to annular_width alone (rings of 0.05, 0.069 and
0.092 mm against the 0.1 mm minimum); attempt 7's annular-ring repair grew those pads (status.json
`layout.pad_ring_repair`, REPORT.md `annular ring:` lines).

Extreme placement and routing on the two boards worked on (attempts 3-6):
- 0077: the normal placement had violations, so the tight and then the extreme placement ran (copper 0.15 mm from
  the edge; kept: 0 -> 0 violations at a lower HPWL). Before attempt 4 its violations were on placer rules (the
  layer-blind clearance, both-side guard pads), which no routing round can change: the extreme routing narrowed 3
  nets and kept nothing.
- 0520: every placement was legal, so no tight or extreme placement ran (its gate: violations left). The extreme
  routing ran in all 4 attempts, no gate skipped it (route budget not exhausted, errors left, krt, 33 nets under
  the 60-net cap). Step 1 (33 nets at 0.15 mm) re-routed with 27 -> 29 and 29 -> 26 errors; step 2 found no via
  room in the GND islands, even with a 0.45 mm via (the walled-in pads are QFP pins 0.54 mm wide between signal
  fan-outs), so a smaller via would not help. Steps 3 and 4 (attempt 5) closed the islands in 2 of 4 attempts.

With the annular-ring repair, no board of the denominator is capped by its footprint any more.

## Layout corpus and tests at the final code (4d0ca12)

`scripts/layout_corpus.py summary --place --jobs 6 --timeout 3600`: 15 of 37 clean, the same cases as at 9db4423
(the 14 clean at e360b5e and sp_overfull); the same 5 placements with placer violations (espgaggia, hestiapi_one,
hue_simple_switch, milliohm_meter, rocketbeacon); kb_audio and oshw_pico_ice timed out at 3600 s, as at 9db4423.
No corpus case has a pad under the minimum ring, so the annular-ring repair changes none of them.
Fast tier: 904 passed (tests/test_pad_ring.py: 5 new).
