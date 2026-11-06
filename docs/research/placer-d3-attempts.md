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
8 placements illegal or with placer violations; oshw_pico_ice timed out (3600 s; it took 3582 s in router-ab.md).
router-ab.md part 2 (a70660f, 35 cases without pr_mcu and pr_pmic): 8 clean.
Fast tier (`pytest -m "not slow"`): 890 passed.
