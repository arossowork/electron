# Router A/B: KiCadRoutingTools against Freerouting 2.5.0

Date: 2026-11-05. Branch `router-ab`, commits a33c1b0 and 8ad0eea.

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

## Recommendation

Keep krt as the default.
- krt has slightly more clean boards: 86 against 84 or 85. The spread is 1 board, so this gap is barely a difference. The other numbers below decide it.
- Its DRV is 7 times lower (0.09 against 0.67).
- Its track is 27% shorter on the same boards.
- Its router call is about twice as fast.

Freerouting's strengths are fewer vias (0.87 against 2.72, close to the human 1.03) and more boards fully connected. Its main loss is that it necks tracks below the minimum width. That cause accounts for 5 of the 6 boards that only krt gets clean. A follow-up worth measuring: route with Freerouting first, then let krt's repair loop fix the necked tracks. It is not done here: it would be a new feature.

## Open points

- Freerouting 2.5 still checks for updates at api.github.com on start. No setting to turn that off was found. It is a separate thread and is not the analytics upload.
- The bench JSON files: `build/bench/pcb_route_krt_v2.json` and `build/bench/pcb_route_freerouting_v2{,_r2,_r3}.json`. The krt runs on a33c1b0 are `pcb_route_krt_v2pre*.json`. The Freerouting runs before fix 7 are `pcb_route_freerouting_v2pre*.json` (clean 0.87, DRV 0.68). The first run is `pcb_route_{krt,freerouting}.json`.
