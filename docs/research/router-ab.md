# Router A/B: KiCadRoutingTools against Freerouting 2.5.0

Date: 2026-11-05 (fixed placement, D3-A) and 2026-11-06 (place and route; D3-B, D3-C; the layout corpus). Branch
`router-ab`, commits a33c1b0, 8ad0eea, a70660f and b518c16.

## Headline: place and route (the full hwflow flow)

hwflow places and routes, so the full flow is the main result. Each board below was placed by hwflow's placer and
routed by one router (`HWFLOW_ROUTER=krt|freerouting`), with every step after the placer as shipped.

| set | boards | router | legal | rout. | clean | DRV | WL x human | vias - human | layout s per board |
|---|---|---|---|---|---|---|---|---|---|
| PCBWorld D3-A | 95 | krt | 0.79 | 0.954 | **0.67** | **0.72** | 0.56 | 0 | 48.0 |
| | | Freerouting | 0.79 | 0.987 | 0.62 | 2.05 | 0.86 | 0 | **20.4** |
| PCBWorld D3-B | 9 | krt | 1.00 | 0.984 | **0.44** | **2.00** | 0.68 | +8 | 55.4 |
| | | Freerouting | 1.00 | 0.962 | 0.33 | 4.43 | 0.95 | +4 | **46.3** |
| PCBWorld D3-C | 9 | krt | 0.89 | 0.946 | 0.22 | **4.50** | 0.70 | +28.5 | 619.8 |
| | | Freerouting | 0.89 | 0.943 | 0.22 | 9.00 | 0.85 | -7 | **178.7** |
| layout corpus | 35 | krt | 25 legal | 14 with 0 unrouted | **8 clean** | 870 errors in all | | | 531 (6 jobs) |
| | | Freerouting | 25 legal | 18 with 0 unrouted | 5 clean | 1587 errors in all | | | 253 (6 jobs) |

- krt has as many or more clean boards on every set, and about half the DRV. Its tracks are shorter.
- Freerouting is 1.2 to 3.5 times faster over the whole flow and uses fewer vias. On D3-A and the corpus it leaves
  fewer connections open.
- The ranking is the same as with the human placement (fixed-placement rows below): krt first on clean and DRV,
  Freerouting first on time, vias and open connections.
- The placer costs both routers the same. With hwflow's placement instead of the human one, clean falls by 0.23 to
  0.45 for both routers. On D3-A, 20 of 95 placements have placer violations, the same 20 for both routers. So most of
  the gap to the human board comes from the placer, not the router.

Recommendation: keep krt as the default (`board.router: krt`). The data supports no per-board rule. Details and
reasons are in "Recommendation after place and route" at the end.

## What was compared

hwflow has two routers. KiCadRoutingTools (krt) is the default (`board.router: krt`).
Freerouting 2.5.0 is the other one. `HWFLOW_ROUTER=krt|freerouting` picks one for every board.

- Boards: the PCBWorld D3-A route benchmark, 95 real boards (`scripts/layout_bench.py --pcbench-only --pcbench-mode route`).
- Placement: every part is fixed at the human placement. Only the router and the steps after it differ.
- Runs: 3 runs per router. Route mode routes each board once per bench call, so one run is one full bench call.
- krt: 3 runs on a33c1b0 and 1 run on 8ad0eea. 8ad0eea changes only the Freerouting DSN code. The 4 krt runs give the same quality numbers to the last digit.
- Freerouting: 3 runs on 8ad0eea (the final integration).
- Machine: the same 16-core laptop for every run. The runs went one after the other, with no other heavy job.
- Java 25.0.5 (Temurin JRE in `tools/jdk25`), `tools/freerouting-2.5.0.jar`. KiCad 10 runs the DRC.
- Metrics are the bench's own (`pcbench_summary`), in PCBWorld's terms:
  - rout. is the mean routed share over all boards. A crash counts as 0.
  - clean is the share of boards with everything routed and DRV 0.
  - DRV counts DRC errors other than unconnected items. It leaves out errors between fixed parts only.
  - DRV, WL (track mm) and vias are means over the fully routed boards.
  - time is the whole layout stage. router is the time in the router call.

## Integration bugs found and fixed

The first Freerouting run (rout. 0.830, clean 0.64) was not fair, because hwflow's integration had these bugs:

1. **Empty padstack.** An SMD pad with no copper layer (a mask- or paste-only pad) became a padstack with no shape. Freerouting 2.5 rejects such a padstack, fails to parse the whole DSN, and then throws an NPE. 15 boards crashed on this. Fix: such a pad is left out of the image and the network (`pcb._padstack`, `export_dsn`).
2. **Passes cap ignored.** `--router.autorouter.max_passes 30` is an unknown argument in 2.x. The settings take `key=value`. Fix: `--router.autorouter.max_passes=30` (`krt.run_freerouting`).
3. **Analytics.** Freerouting sends usage data to freerouting.app. Fix: `-da`. strace shows no connection to the freerouting.app host after the fix.
4. **Router time not recorded.** The bench timed only the krt functions. Fix: the DSN export, Freerouting and the SES import are now one function, `stages._route_freerouting`, and the bench times it as the router phase. That is the same scope as `krt.run_krt` (board write, router, read-back).
5. **Unnumbered copper pads were dropped.** Shield and mounting tabs with no pad number were missing from the DSN. Freerouting routed over them, and KiCad found shorts (pcb_0045, pcb_0083). Fix: they are exported as pins of no net, so they are obstacles.
6. **Edge clearance not passed.** hwflow's DRC wants 0.3 mm of copper to the board edge (`EDGE_CLEARANCE_MM`). Freerouting got no edge clearance. Cut holes were keep-outs, so it kept only the net clearance (0.2 mm) from them. Fix: `--router.copper_to_edge_clearance_um=300` for the outline. Each hole keep-out is also grown by 0.3 mm minus the smallest net clearance (pcb_0018, pcb_0046 and others).
7. **References with a hyphen.** Freerouting reads the pin `L-KEMET1-2` as pin `KEMET1-2` of component `L`. The net lost the pin, Freerouting reported it as routed, and KiCad found it open (pcb_0070). Fix: a reference that is not plain is quoted, as in `"L-KEMET1"-2`. 3 boards have such references.

Tests: `tests/test_freerouting_dsn.py` (every pin padstack has a shape, tabs are obstacles, a mask-only pad stays out of the network, the hole keep-out grows, the command line, quoted references).

## Results

| method | rout. | clean | DRV | WL mm | vias | time s (whole layout) | router s |
|---|---|---|---|---|---|---|---|
| hwflow + krt (4 runs) | 0.990 | 0.91 | 0.09 | 117.6 | 2.72 | 3.97 (3.96 to 4.23) | 1.61 (1.60 to 1.68) |
| hwflow + Freerouting, fixed (3 runs) | 0.990 | 0.88 (0.88 to 0.89) | 0.67 (0.66 to 0.67) | 156.6 (156.61 to 156.65) | 1.04 | 4.95 (4.94 to 4.97) | 3.13 (3.13 to 3.14) |
| hwflow + Freerouting, first run (broken) | 0.830 | 0.64 | 1.18 | 154.8 | 1.17 | 4.7 | not recorded |
| PCBWorld Freerouting (published, fair-95) | 0.98 | - | 0.09 | 158.8 | 0.72 | 3.90 (router only) | |
| PCBWorld KRT (published, fair-95) | 0.94 | - | 1.01 | 156.0 | 3.74 | 0.65 (router only) | |

The ranges are the min and max of the per-run summaries. krt's quality numbers do not change from run to run. Freerouting changes by one board (pcb_0003) between runs.

WL and vias are means over different board sets: krt routes 87 boards fully, Freerouting 92. On the 87 boards that both route fully:
- krt: 117.6 mm and 2.72 vias.
- Freerouting: 160.1 mm and 0.87 vias.
- the human boards: 177.6 mm and 1.03 vias.

Time: Freerouting's router time includes the JVM start. Its optimizer uses up to 15 threads, while krt runs one process. The router median is 1.2 s for krt and 2.9 s for Freerouting. The slowest board takes 17.6 s with krt and 9.0 s with Freerouting.

Our numbers do not match PCBWorld's rows. Our DRC is hwflow's: KiCad 10, a 0.15 mm minimum track width and a 0.3 mm edge clearance. hwflow's krt also has its own pipeline around the router: width-class passes, necking and the repair loop.

## The pipeline is not the same for both routers

`stages.py` runs three steps only when the router is krt: the repair loop, pour stitching and the roads reroute. In this benchmark, pour stitching and roads are off (they are opt-in settings), so only the repair loop differs.
- krt started repair on 13 boards. It made 5 of them clean (pcb_0044, 0049, 0067, 0081, 0088).
- Freerouting gets no repair. It routed those 5 boards clean without one.
- So repair does not explain the gap here. But a Freerouting board with an error gets no second chance that a krt board would get. That comes from hwflow's pipeline, not from the router.

## Where the two disagree

10 boards are clean with one router and not with the other. The verdict is the same in every run, except pcb_0003 with Freerouting, which is clean in 1 of 3 runs.

Clean with krt only (6):

| board | parts | nets | why Freerouting is not clean |
|---|---|---|---|
| 0003_kitspace_grove_adaptor | 2 | 4 | a mask aperture over a reference text on B.Mask (the DSN has no mask layer); clean in 1 of 3 runs |
| 0004_S1G-Mod_JST_Adapter | 7 | 5 | 5 track_width: tracks necked below 0.15 mm |
| 0006_raspberry_pi_pullup_button | 4 | 4 | 2 track_width (0.127 mm), plus 2 edge errors on fixed pads |
| 0016_breakout-boards_avr-isp-x2 | 2 | 6 | 2 track_width |
| 0033_breakout-boards_swd-to-wires | 2 | 8 | 6 track_width (0.107 and 0.133 mm) |
| 0035_KiCad-Like-a-Pro-Tutorial_rf24 | 3 | 8 | 14 track_width (0.114 mm over long runs) |

Clean with Freerouting only (4):

| board | parts | nets | why krt is not clean |
|---|---|---|---|
| 0037_hardware-designs_spsgrf-board | 3 | 8 | 1 connection open after 2 repair rounds |
| 0050_MySRaspiGW_PA_LNA_Pimoroni | 5 | 7 | 1 connection open after repair |
| 0054_induction-hob_temperature-sensor | 12 | 6 | 2 connections open after repair |
| 0086_BB-PWR-8009_revA | 14 | 6 | 4 connections open after repair |

The two routers fail in different ways. Freerouting connects more (92 boards fully routed against 87), but it necks tracks below the board minimum width. hwflow does not tell it that minimum: the DSN has no field for it. The only switch, `--router.automatic_neckdown=false`, cost 2 open nets on pcb_0033 in a spot check. krt keeps every rule, but it leaves a few connections open on dense boards.

Not clean with either router: pcb_0024, 0048 and 0051 have open nets with both routers. pcb_0068 has mask-aperture bridges with both (Freerouting 30 DRV, krt 8). pcb_0034 has tracks that run into human pads placed on the board edge (with Freerouting), or open nets (with krt).

Board properties: the Freerouting-only boards have a higher pin density. The median is 16.6 pins per cm² against 6.2 for the krt-only boards (3.2 over all boards). But there are only 4 and 6 boards, and the ranges overlap: pcb_0016 has 11.8 (krt-only) and pcb_0037 has 3.6 (Freerouting-only). Every board has 2 layers. Net count does not separate them (median 6.5 against 5.5). The data does not support a per-board rule.

## Recommendation (D3-A, fixed placement only)

Keep krt as the default.
- krt has slightly more clean boards: 86 against 84 or 85. The spread is 1 board, so this gap is barely a difference. The other numbers below decide it.
- Its DRV is 7 times lower (0.09 against 0.67).
- Its track is 27% shorter on the same boards.
- Its router call is about twice as fast.

Freerouting's strengths are fewer vias (0.87 against 2.72, close to the human 1.03) and more boards fully connected. Its main loss is that it necks tracks below the minimum width. That cause accounts for 5 of the 6 boards that only krt gets clean. A follow-up worth measuring: route with Freerouting first, then let krt's repair loop fix the necked tracks. It is not done here: it would be a new feature.

## Open points

- Freerouting 2.5 still checks for updates at api.github.com on start. No setting to turn that off was found. It is a separate thread and is not the analytics upload.
- The bench JSON files: `build/bench/pcb_route_krt_v2.json` and `build/bench/pcb_route_freerouting_v2{,_r2,_r3}.json`. The krt runs on a33c1b0 are `pcb_route_krt_v2pre*.json`. The Freerouting runs before fix 7 are `pcb_route_freerouting_v2pre*.json` (clean 0.87, DRV 0.68). The first run is `pcb_route_{krt,freerouting}.json`.


---

# Part 2 (2026-11-06): harder boards and the full flow

## What was run

- Code: a70660f (sets, replay, corpus place mode) and b518c16 (fix 8 below). Same laptop, Java, Freerouting and KiCad
  as in part 1. One benchmark at a time. Each bench call routes each board once.
- PCBWorld D3-B (medium, 2-layer, at most 100 pins) and D3-C (hard, 2-layer, more than 100 pins), 10 boards each in
  PCBWorld's `configs/datasets/d3.json`. `uv run python scripts/pcbench.py prep --set d3b|d3c` builds them in
  `build/bench/pcbench/designs_d3b/` and `designs_d3c/`. D3-A stays in `designs/`. `layout_bench.py --pcbench-set`
  selects a set.
- 9 of 10 boards pass the DRC filter in each set. 0288_raspberrypi-3-usb-hub (D3-B) fails with 2
  pth_inside_courtyard and 0473_data-manager (D3-C) with 2 npth_inside_courtyard, under KiCad 10 with their own
  routing (`manifest_d3b.json`, `manifest_d3c.json`). PCBWorld pins KiCad 9.0.8, which may not run that check.
- Three cases per set and router:
  - (a) full flow: `--pcbench-mode place`. The parts the importer marks free are released, hwflow places them, routes,
    repairs (krt only) and picks the best of up to 4 attempts, as shipped.
  - (b) hwflow placement, router alone: `--pcbench-mode replay`. The attempt-1 placement of the krt run (a), every part
    fixed there, routed once by each router. Both routers get the same placement.
  - (c) human placement: `--pcbench-mode route` (part 1 for D3-A).
- Runs: (a) once per router and set (D3-A takes 76 min with krt and 32 min with Freerouting). (b) and (c) once per
  router and set. No repeats: part 1 shows that krt's numbers do not change between runs and Freerouting's change by
  one board in 95.
- Layout corpus: `scripts/layout_corpus.py summary --place --jobs 6`, once per router. `--place` releases each case's
  cutout.yaml `free` parts, then runs `hwflow layout` as usual. 3 cases have no free part (ht1_battery, ht1_mains,
  kb_keys): they are routing only.
- Timeout per board: 3600 s (layout_bench and the corpus). hwflow's own layout budget is 3000 s.

## Integration bug found and fixed

8. **A Freerouting timeout crashed the layout.** Freerouting 2.x writes the session file only at the end. When it hit
   hwflow's 900 s cap, `run_freerouting` raised "no session file", so the layout stage failed. `_route_freerouting`
   already had a branch for a timeout (no copper, budget used up), but the raise came first. Fix (b518c16): a timeout
   returns its log and the stage reports an unrouted board. Test:
   `tests/test_freerouting_dsn.py::test_freerouting_timeout_is_an_unrouted_result_not_a_crash`. Only Freerouting
   code changed, so only the two corpus cases that crashed (kb_audio_dig, kb_hub) were run again, with Freerouting.

## Is hwflow's placement the same for both routers?

- The placer does not read the router setting. The route cache key does, but that key only decides whether copper
  is reused.
- Same seed, other router: 181 of the 189 attempt placements of D3-A and all 40 of D3-B and D3-C are equal to the last
  digit (positions and rotations) in the krt and the Freerouting full-flow runs.
- The 8 others (8 attempts on 5 D3-A boards: pcb_0018, 0054, 0065, 0074, 0086) differ because of Python's hash
  randomization, not the router. pcb_0074 placed 3 times in a row with the same settings and the same
  router gives 2 different placements. With `PYTHONHASHSEED=0` it gives the same placement 4 times out of 4. This is
  an hwflow determinism gap of the placer (set or dict order of strings).
- The Freerouting DSN has hwflow's placement: on pcb_0376 (D3-B), every one of the 28 parts is in the DSN at the
  position and rotation of the placer's attempt 1.
- So (b) is a fair router-only comparison. The full Freerouting runs (a) are still needed: which attempts run and
  which one is kept depends on the routing results.

## Results per set

legal: share of boards whose placement has no violation that names a released part (in (b) and (c) the same as the
placement it reuses). rout., clean, DRV: as in part 1. HPWL, WL and vias against the human board are medians over the
clean boards. WL mm and vias are means over the boards fully routed by that router (different sets of boards). time:
the whole layout stage per board; router: time in the router calls outside repair; repair: krt's repair loop with its
router calls.

### D3-A (95 boards)

| case | router | legal | rout. | clean | DRV | HPWL x human | WL x human | vias - human | WL mm | vias | time s | router s | repair s | attempts | timeouts |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| (a) full flow | krt | 0.79 | 0.954 | 0.67 | 0.72 | 0.94 | 0.56 | 0 | 96.2 | 2.6 | 48.0 | 12.9 | 22.4 | 2.0 | 0 |
| (a) full flow | Freerouting | 0.79 | 0.987 | 0.62 | 2.05 | 0.93 | 0.86 | 0 | 147.4 | 1.0 | 20.4 | 7.2 | 0 | 2.1 | 0 |
| (b) hwflow placement | krt | 0.79 | 0.939 | 0.66 | 0.72 | 0.93 | 0.57 | 0 | 98.1 | 2.5 | 10.5 | 4.5 | 4.0 | 1 | 0 |
| (b) hwflow placement | Freerouting | 0.79 | 0.986 | 0.61 | 2.92 | 0.94 | 0.85 | 0 | 146.7 | 1.1 | 5.3 | 3.4 | 0 | 1 | 0 |
| (c) human placement | krt | 1.00 | 0.986 | 0.91 | 0.09 | 1.00 | 0.68 | +1 | 117.6 | 2.7 | 4.2 | 1.7 | 0.6 | 1 | 0 |
| (c) human placement | Freerouting | 1.00 | 0.994 | 0.88 | 0.67 | 1.00 | 0.96 | 0 | 156.6 | 1.0 | 5.0 | 3.1 | 0 | 1 | 0 |

On the 80 boards that both full flows route fully: krt 98.2 mm and 2.35 vias, Freerouting 140.2 mm and 0.88 vias, the
human boards 181.6 mm.

### D3-B (9 boards)

| case | router | legal | rout. | clean | DRV | HPWL x human | WL x human | vias - human | WL mm | vias | time s | router s | repair s | attempts | timeouts |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| (a) full flow | krt | 1.00 | 0.984 | 0.44 | 2.00 | 0.83 | 0.68 | +8 | 346.3 | 11.1 | 55.4 | 24.9 | 7.8 | 2.4 | 0 |
| (a) full flow | Freerouting | 1.00 | 0.962 | 0.33 | 4.43 | 0.87 | 0.95 | +4 | 502.9 | 3.6 | 46.3 | 17.1 | 0 | 2.7 | 0 |
| (b) hwflow placement | krt | 1.00 | 0.970 | 0.33 | 2.83 | 0.75 | 0.70 | +16 | 394.5 | 12.8 | 25.5 | 18.5 | 4.2 | 1 | 0 |
| (b) hwflow placement | Freerouting | 1.00 | 0.962 | 0.33 | 7.86 | 0.87 | 0.95 | +4 | 495.3 | 4.6 | 11.4 | 8.3 | 0 | 1 | 0 |
| (c) human placement | krt | 1.00 | 0.991 | 0.67 | 2.25 | 1.00 | 0.74 | +9 | 422.7 | 16.5 | 9.9 | 5.1 | 1.9 | 1 | 0 |
| (c) human placement | Freerouting | 1.00 | 0.988 | 0.56 | 3.00 | 1.00 | 0.96 | +1 | 532.8 | 6.0 | 8.4 | 5.2 | 0 | 1 | 0 |
| PCBWorld paper, Freerouting (10 boards, @5, 4 seeds) | | | 1.00 | 0.78 | 10.60 | | | | 535.4 | 3.48 | 9.94 (router) | | | | |
| PCBWorld paper, KiCadRoutingTools (10 boards, @5) | | | 0.86 | 0.20 | 44.70 | | | | 472.3 | 11.80 | 3.27 (router) | | | | |
| PCBWorld paper, human reference | | | 1.00 | 1.00 | 0 | | | | 570.3 | 6.70 | | | | | |

The paper rows are PCBWorld's Table 21 (PCBWorld.pdf, D3-B block; the main Table 3 has CP and Rout. only). They are
on the human placement, so they compare with our row (c). The paper's claim, KiCadRoutingTools 0.20 clean against
Freerouting 0.78, does not hold in hwflow: with the human placement, hwflow + krt is clean on 6 of 9 boards (0.67) and
hwflow + Freerouting on 5 of 9 (0.56). Reasons for the gap:
- The paper runs KiCadRoutingTools bare. hwflow's krt adds width-class passes, necking and the repair loop. The
  paper's KRT DRV (44.7) is 20 times ours (2.25).
- The paper's Freerouting row is the best of 5 rollouts per board, over 4 seeds. Ours is one run.
- The DRC differs: KiCad 10 with hwflow's rules (0.15 mm minimum track, 0.3 mm edge clearance) against PCBWorld's
  KiCad 9 rules. Our Freerouting loses its 4 not-clean boards to necked tracks (0376: 13 track_width), mask bridges
  between the human pads (0144, 0344) and 1 open net (0203).
- Our set has 9 boards, not 10 (0288 fails the KiCad 10 DRC filter).

### D3-C (9 boards)

| case | router | legal | rout. | clean | DRV | HPWL x human | WL x human | vias - human | WL mm | vias | time s | router s | repair s | attempts | timeouts |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| (a) full flow | krt | 0.89 | 0.946 | 0.22 | 4.50 | 0.75 | 0.70 | +28.5 | 912.6 | 49.0 | 619.8 | 313.1 | 208.2 | 3.2 | 0 |
| (a) full flow | Freerouting | 0.89 | 0.943 | 0.22 | 9.00 | 0.74 | 0.85 | -7 | 2038.7 | 45.5 | 178.7 | 97.3 | 0 | 2.3 | 0 |
| (b) hwflow placement | krt | 0.89 | 0.938 | 0.11 | 1.00 | 0.77 | 0.67 | +31 | 983.8 | 33.5 | 135.1 | 95.1 | 31.9 | 1 | 0 |
| (b) hwflow placement | Freerouting | 0.89 | 0.943 | 0.22 | 9.00 | 0.74 | 0.85 | -7 | 2038.7 | 45.5 | 35.6 | 27.3 | 0 | 1 | 0 |
| (c) human placement | krt | 1.00 | 0.975 | 0.56 | 0.00 | 1.00 | 0.70 | +22 | 1015.4 | 45.2 | 42.0 | 32.9 | 2.1 | 1 | 0 |
| (c) human placement | Freerouting | 1.00 | 0.982 | 0.56 | 0.57 | 1.00 | 0.96 | +3 | 1452.8 | 32.4 | 27.2 | 20.8 | 0 | 1 | 0 |

PCBWorld does not evaluate D3-C ("fixed for reference", Table 28). On the 5 boards both routers fully route with the
human placement: krt 1015 mm and 45.2 vias, Freerouting 1275 mm and 24.4 vias, the human boards 1343 mm. With
hwflow's placement only 2 boards (a) or 1 board (b) are fully routed by both, too few to compare track length.

### What (a), (b) and (c) show

- Placement cost, (c) to (b): hwflow's placement loses clean boards with both routers, by about the same amount.
  D3-A: krt 0.91 to 0.66, Freerouting 0.88 to 0.61. D3-B: 0.67 to 0.33 and 0.56 to 0.33. D3-C: 0.56 to 0.11 and 0.56 to
  0.22. On D3-A, 20 of 95 placements have placer violations (both routers), and 11 more are legal but clean with
  neither router.
- hwflow's placements are tighter than the human ones: HPWL x0.74 to 0.94 of the human board, and with krt the track
  is 0.56 to 0.70 of the human length.
- Search and repair, (b) to (a): the attempts and the repair loop add 0.01 to 0.11 clean for krt (D3-A +0.01, D3-B
  +0.11, D3-C +0.11) and 0 to 0.01 for Freerouting. They cost krt 4.6 times the time on D3-C (135 to 620 s per board): most of it is
  repair (208 s) and the router calls of the later attempts.
- Freerouting gets fewer attempts on hard boards. On D3-C, `--auto` stops after attempt 1 on 3 Freerouting boards
  (0400, 0496, 0599) against 1 krt board: attempt 1 has more errors than the limit (HWFLOW_AUTO_MAX_ERRORS, by default
  the repair limit). Freerouting's errors there are mostly necked tracks (0599: 39 track_width).

## Where the two disagree (full flow, a)

- D3-A: 59 boards clean with both, 5 with krt only, 0 with Freerouting only, 31 with neither (20 of them with placer
  violations). Freerouting is not clean on the 5 because of necked tracks (0004, 0016, 0033: 2 to 5 track_width), a
  mask bridge (0003) and copper at the edge (0034). These are the same boards and causes as with the human placement
  in part 1.
- D3-B: 2 clean with both. krt only: 0203 (krt's attempt 2 is clean; Freerouting keeps attempt 1 with 4 open
  connections) and 0376 (Freerouting: 20 track_width). Freerouting only: 0316 (krt: 1 connection open after 3 repair
  rounds). With the same placement (b): 0376 krt only (39 track_width), 0316 Freerouting only (2 open with krt).
- D3-C: 2 clean with both (0448, 0546), no disagreement. In (b), 0448 is clean with Freerouting only (1 net open with
  krt). The other 7 boards fail with both: open nets (0496, 0571), mask bridges and shorts between parts the placer put
  close together (0400, 0571), annular width of a footprint (0520, both routers, 24 errors) and a placer violation
  (0626).
- Board properties: every board is 2-layer. Clean with krt only or Freerouting only happens on 1 to 5 boards per set.
  Pin count, net count and pin density do not separate them: 0376 (93 pins, 2.6 pins/cm²) is krt only, 0316 (74 pins,
  5.0 pins/cm²) is Freerouting only, and on D3-C nothing disagrees.

## Layout corpus, place and route

35 cases, 15 of them 4-layer. Each case once per router, 6 cases at a time. "unrouted / DRC" are the counts after the
flow; clean means routed, legal, 0 unrouted and 0 DRC errors. HPWL is the hwflow placement against the case's own
(source) placement. The reference copper is the human board (oshw cases) or none (cut-outs of hwflow designs). krt-only
steps: what ran in the krt run that a Freerouting run never gets.

| case | layers | free | krt: unrouted / DRC | Freerouting: unrouted / DRC | HPWL x source | WL x ref (krt / FR) | vias krt / FR / ref | time s krt / FR | krt-only steps (krt run) |
|---|---|---|---|---|---|---|---|---|---|
| ht1_battery | 4 | 0 (routing only) | 15 / 28 | 0 / 123 | 1.00 | - / - | 75 / 71 / - | 1035.2 / 78.0 | repair |
| ht1_mains | 4 | 0 (routing only) | 0 / 20 | 0 / 30 | 0.95 | - / - | 8 / 5 / - | 22.0 / 16.5 | repair |
| ht3_mains | 4 | 6 | 1 / 1 | 0 / 14 | 0.71 | - / - | 0 / 1 / - | 686.9 / 39.2 | repair |
| ht4_s11_mains | 4 | 1 | **clean** | 0 / 11 | 0.68 | - / - | 0 / 0 / - | 33.8 / 35.3 | - |
| ht4_s1_k2in | 4 | 18 | **clean** | 0 / 33 | 0.89 | - / - | 51 / 54 / - | 223.1 / 185.2 | repair |
| ht4_s1_mains | 4 | 1 | **clean** | 0 / 11 | 0.69 | - / - | 0 / 0 / - | 37.3 / 35.2 | - |
| kb_audio | 4 | 165 | not routed (illegal placement) | not routed (illegal placement) | 2.44 | - / - | 0 / 0 / - | 553.6 / 665.8 | - |
| kb_audio_dig | 4 | 80 | 152 / 156 | 178 / 182 | 1.74 | - / - | 43 / 0 / - | 849.1 / 1086.4 | repair |
| kb_back_conn | 4 | 72 | 56 / 60 | 21 / 28 | 0.84 | - / - | 52 / 92 / - | 1035.0 / 679.2 | repair |
| kb_hub | 4 | 92 | 161 / 161 | 96 / 99 | 0.97 | - / - | 24 / 82 / - | 936.6 / 921.6 | repair |
| kb_keys | 4 | 0 (routing only) | **clean** | **clean** | 1.00 | - / - | 6 / 24 / - | 9.5 / 31.9 | - |
| kb_usb_c | 4 | 12 | 30 / 30 | 24 / 32 | 0.99 | - / - | 44 / 58 / - | 654.0 / 397.2 | repair |
| oshw_ac_relay | 2 | 12 | 2 / 4, pv 1 | 0 / 6, pv 1 | 1.23 | 0.73 / 1.13 | 19 / 13 / 5 | 225.1 / 144.3 | repair, pour_stitch |
| oshw_b_parasite | 2 | 19 | 3 / 3 | **clean** | 0.96 | 0.87 / 1.22 | 26 / 24 / 51 | 112.4 / 98.9 | repair, pour_stitch |
| oshw_buck_tps54202 | 2 | 20 | **clean** | 0 / 2 | 1.06 | 2.03 / 3.32 | 18 / 12 / 19 | 47.9 / 50.2 | pour_stitch |
| oshw_ch32v003_usb_devboard | 2 | 20 | 1 / 1 | **clean** | 0.96 | 0.79 / 1.11 | 26 / 16 / 29 | 143.8 / 69.3 | repair, pour_stitch |
| oshw_ch32v003_usbpd | 2 | 18 | **clean** | **clean** | 0.72 | 0.67 / 0.90 | 22 / 12 / 22 | 61.7 / 56.1 | pour_stitch |
| oshw_esp32c3_devkit_lipo | 2 | 43 | 30 / 53, pv 3 | 2 / 288, pv 3 | 1.27 | 0.87 / 1.23 | 93 / 53 / 88 | 1174.7 / 246.0 | repair, pour_stitch |
| oshw_espgaggia | 2 | 14 | 0 / 5, pv 5 | 0 / 4, pv 4 | 1.27 | 1.18 / 1.16 | 87 / 2 / 12 | 257.4 / 187.6 | repair, pour_stitch |
| oshw_eurorack_ecg | 2 | 38 | 8 / 8 | 5 / 7 | 0.99 | 0.91 / 1.61 | 43 / 33 / 19 | 1083.0 / 153.3 | repair, pour_stitch |
| oshw_hestiapi_one | 2 | 8 | 0 / 33, pv 3 | 0 / 34, pv 3 | 0.63 | 0.63 / 0.76 | 17 / 1 / 21 | 82.0 / 95.8 | repair, pour_stitch |
| oshw_hot_plate_power | 2 | 16 | **clean** | 0 / 4 | 1.03 | 0.93 / 0.98 | 15 / 0 / 6 | 49.5 / 45.1 | pour_stitch |
| oshw_hue_simple_switch | 2 | 25 | 5 / 14, pv 2 | 1 / 21, pv 3 | 1.43 | 1.01 / 1.81 | 30 / 40 / 27 | 843.4 / 168.2 | repair, pour_stitch |
| oshw_ina228_simple | 2 | 22 | **clean** | 0 / 23 | 0.86 | 0.70 / 1.16 | 4 / 6 / 48 | 38.9 / 38.2 | pour_stitch |
| oshw_milliohm_meter | 4 | 25 | 0 / 0, pv 3 | 0 / 0, pv 3 | 0.91 | 1.21 / 1.89 | 18 / 19 / 30 | 73.8 / 79.3 | pour_stitch |
| oshw_mini_env_sensor | 2 | 29 | 1 / 12 | 4 / 15 | 1.05 | 0.59 / 1.07 | 13 / 12 / 15 | 169.2 / 99.0 | repair, pour_stitch |
| oshw_openblind32 | 2 | 17 | 0 / 4 | 3 / 7 | 0.90 | 0.97 / 1.08 | 21 / 10 / 4 | 141.1 / 85.0 | repair, pour_stitch |
| oshw_pico_ice | 4 | 81 | 74 / 95, pv 14 | 83 / 193, pv 14 | 1.01 | 0.79 / 0.65 | 171 / 84 / 189 | 3581.8 / 957.8 | repair, pour_stitch |
| oshw_powerline_usbc | 2 | 17 | 0 / 10 | 1 / 28 | 1.03 | 3.09 / 4.51 | 13 / 17 / 9 | 39.7 / 63.6 | repair, pour_stitch |
| oshw_riegel | 4 | 87 | 32 / 32 | 9 / 208 | 1.20 | 1.17 / 1.46 | 213 / 267 / 157 | 2111.1 / 1193.6 | repair, pour_stitch |
| oshw_rocketbeacon | 2 | 26 | 29 / 59, pv 32 | 22 / 106, pv 32 | 3.37 | 0.83 / 1.48 | 15 / 19 / 51 | 215.0 / 278.5 | repair, pour_stitch |
| oshw_rp2040_designguide | 2 | 30 | 52 / 52 | 34 / 34 | 0.57 | 0.27 / 0.54 | 28 / 56 / 32 | 1003.4 / 216.4 | repair, pour_stitch |
| s60_escape | 2 | 60 | 28 / 28 | 40 / 44 | 0.87 | - / - | 78 / 84 / - | 1021.7 / 318.9 | repair |
| sp_control | 2 | 11 | 1 / 1 | **clean** | 0.99 | - / - | 6 / 3 / - | 20.7 / 19.2 | repair |
| sp_overfull | 2 | 11 | not routed (illegal placement) | not routed (illegal placement) | 0.77 | - / - | 0 / 0 / - | 8.2 / 8.9 | - |

kb_audio_dig and kb_hub with Freerouting are the run after fix 8, 2 cases at a time (the first run crashed on both).
kb_audio_dig: Freerouting hit its 900 s cap, so the board has no copper (178 unrouted). That is a timeout for
Freerouting. kb_hub finished inside the cap in the second run. No case timed out with krt (the slowest, oshw_pico_ice,
took 3582 s of the 3600 s limit).

Totals: krt 8 clean, Freerouting 5. krt leaves 681 connections open and 870 DRC errors in all, Freerouting 523 and 1587.
Freerouting is about 2.1 times faster (8845 s against 18582 s of case time).

- Clean with both: kb_keys, oshw_ch32v003_usbpd.
- krt only (6): ht4_s11_mains, ht4_s1_mains, ht4_s1_k2in, oshw_buck_tps54202, oshw_hot_plate_power,
  oshw_ina228_simple. With Freerouting they fail on mains isolation (the two mains cases: 7 clearance and 4 creepage
  errors each), keep-out areas (ht4_s1_k2in: 31 items_not_allowed), necked tracks (ina228: 23 track_width) and copper
  at the edge (hot_plate: 4).
- Freerouting only (3): oshw_b_parasite (krt: 3 open), oshw_ch32v003_usb_devboard (krt: 1 open), sp_control (krt:
  1 open).
- Per layer count: on 2-layer cases krt and Freerouting are both clean on 4. On 4-layer cases krt is clean on 4 and
  Freerouting on 1, but 3 of those 4 are cases where Freerouting does not get the rules (next section).

### The pipeline is not the same for both routers

`stages.py` runs these only with krt: the repair loop, pour stitching and the roads reroute. In the corpus:
- repair ran on 25 of 35 cases with krt.
- pour stitch ran on 20 cases (the oshw cases run with HWFLOW_FAST=1, which turns it on).
- the roads reroute ran on none.
- So on 30 of 35 cases the Freerouting run got less of the pipeline. On D3-A/B/C, only the repair loop differs (pour
  stitching and roads are off there).

The Freerouting DSN also leaves out two kinds of rules that krt and the DRC use:
- board.isolation (mains to SELV clearance and creepage between net groups). The DSN has one clearance per net
  class, no class-to-class clearance. The 4 ht*_mains cases have clearance and creepage errors with Freerouting
  (ht1_mains has creepage errors with krt too).
- keep-out rule areas. Only cut holes and NPTH holes are DSN keep-outs. ht1_battery (118 items_not_allowed) and
  ht4_s1_k2in (31) fail on it.
These are missing features, not plain export bugs, so they were not built here. Until they exist, Freerouting is
not safe on a mains board or a board with keep-out areas.

### Timeouts

| set | krt | Freerouting |
|---|---|---|
| D3-A, D3-B, D3-C (all cases) | 0 | 0 |
| layout corpus | 0 | 1 (kb_audio_dig, at Freerouting's 900 s cap; counted as a failure) |

The first corpus run had 2 more Freerouting failures (kb_hub, kb_audio_dig) from the crash of fix 8.

## Recommendation after place and route

Keep krt as the default (`board.router: krt`).
- Full flow, clean boards: krt is first or tied on every set (D3-A 0.67 against 0.62, D3-B 0.44 against 0.33, D3-C 0.22
  each, corpus 8 against 5).
- DRV: krt has about half (D3-A 0.72 against 2.05, D3-B 2.00 against 4.43, D3-C 4.50 against 9.00).
- Track length: krt's is 18 to 30% shorter on the boards both route fully.
- Freerouting is not safe where hwflow's rules matter most: it gets no isolation rules and no keep-out areas.
- The ranking does not change between the human placement and hwflow's placement. The placer, not the router, is
  where most clean boards are lost (D3-A: 0.91 with the human placement, 0.66 with hwflow's, same router).

What Freerouting does better:
- Time. It is 1.2 to 3.5 times faster over the full flow. On D3-C krt takes 620 s per board, most of it in repair and
  later attempts.
- Vias: 2.4 to 5.5 times fewer on the boards both route fully.
- Open connections: fewer on D3-A (0.987 against 0.954) and on the corpus (523 against 681).

No per-board rule. Neither net count, pin count, pin density nor layer count predicts which router gets a board clean.
The few boards where only one router is clean (0 to 6 per set) fall on both sides of every property.

Worth measuring next, as features (not done here):
- the placer's determinism (PYTHONHASHSEED): it makes run-to-run spread larger than needed;
- Freerouting first, then krt's repair loop on its result: it would fix the necked tracks, Freerouting's main loss;
- isolation and keep-out areas in the DSN, if Freerouting is kept as an option.

## Open points (part 2)

- One run per case. D3-B and D3-C have 9 boards each, so 1 board is 0.11 of clean. The D3-B and D3-C gaps are 1 board.
- The bench runs one job at a time, but the load average was 2 to 6 at the start of most runs (13 at the start of D3-B
  route krt, right after the D3-C prep). krt's router is single-threaded; Freerouting uses several threads.
- The corpus runs 6 cases at a time, so its case times include contention.
- Files: `build/bench/pcb_{d3a,d3b,d3c}_{place,replay,route}_{krt,freerouting}.json` (D3-A route: the part 1 files,
  linked), `build/bench/corpus_place_{krt,freerouting}.json`, `corpus_place_freerouting_fix.json` (the 2 cases run
  again), `corpus_place_freerouting_merged.json`. Work dirs: `build/bench/work_<set>_<mode>_<router>/`.
