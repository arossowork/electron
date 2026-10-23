# ASIC placement and routing ideas on hwflow: the experiment record

This file records the experiments of 2026-10-23 (branches `asic-ideas` and `diagnose`). Their outcome is the single
layout flow in `docs/layout-flow.md`. The switches, files and tests named below are what existed **during** the
experiments. The outcome:

| idea | outcome | where it is now |
|---|---|---|
| macro stage (blocks and large parts first, mixed-size global placement) | kept | stage 1, `asicflow.place` |
| analytic global placement (idea 2a) | kept: HPWL -13 % to -49 % on every design | stage 2, `gplace.py` |
| RUDY with pad blockage (idea 1), as density feedback of global placement | kept | stage 2, `asicflow.rudy_map` |
| legalization (2b) | kept | stage 3, `Placer.legalize` |
| detailed local moves (2c) | kept | stage 4, `Placer.detailed` |
| short low-temperature anneal with the existing terms | kept | stage 5 |
| pin access into the narrow-first `necked` path (idea 6) | kept | stage 6, `pinaccess.py`, `asicflow.necked` |
| KRT routing with neck-down, seed route, region split, repair, prune | kept, unchanged | stage 7 |
| PathFinder global router as feasibility predictor, net order, layer hints, density source | **removed**: it never predicted a failure, its order lost on 4 of 5 designs, and its hints hurt | `globalroute.py` deleted, with `HWFLOW_GROUTE*`, `HWFLOW_ASIC_DENSITY=groute`, `scripts/groute_bench.py` |
| pin-access placer term | **removed**: no gain on any proxy, and 2-3 × the placement time | `asicflow.PinAccess`, `HWFLOW_ASIC_ACCESS` deleted |
| pin-access net order | **removed**: it changed no result and cost a retry order | `HWFLOW_ASIC_ORDER` deleted |
| RUDY pad blockage inside the annealer cost (`HWFLOW_RUDY_PADS`) | **removed**: replaced by the density feedback of stage 2 | switch and incremental blockage deleted |
| flows 2 and 3 and macro-first as separate entry points (`HWFLOW_ANALYTIC`, `HWFLOW_ANALYTIC_DP`, `HWFLOW_MACRO_FIRST`) | **removed**: the layout flow supersedes them | switches and `Placer._macro_first` deleted |
| annealing-only flow | kept for now as `HWFLOW_FLOW=legacy`, to be removed after two more routed designs | `Placer.run` |

The confirmation runs of the single flow are at the end of this file.

# ASIC placement ideas on the hwflow placer

Experiment of 2026-10-23 on branch `asic-ideas`: three ideas from open-source ASIC placement (OpenROAD/RePlAce,
DREAMPlace, OpenDP, macro placement) tried on `placer.py`. Every idea sits behind a switch that is off by default
(`placer._switch`: env `HWFLOW_<NAME>=1`, or `placement: {<name>: true}` in design.yaml). The unit tests are in
`tests/test_asic_ideas.py`.

| switch | idea | code |
|---|---|---|
| `HWFLOW_RUDY_PADS` | idea 1: RUDY capacity minus pad blockage | `Placer._pad_blockage`, `_blk_move` |
| `HWFLOW_ANALYTIC` | flow 2: global placement, then legalization, then the annealer at a low start temperature | `gplace.py`, `Placer.run` |
| `HWFLOW_ANALYTIC_DP` | flow 3: global placement, then legalization, then OpenDP-style detailed placement, with no annealing | `gplace.py`, `Placer.detailed` |
| `HWFLOW_MACRO_FIRST` | idea 3: blocks and large parts first, then the small parts, then everything cool | `Placer._macro_first` |
| `HWFLOW_SEED0` | the first placer seed of `layout --auto` (attempt k uses seed0 + 4k) | `cli._auto_attempt` |

Instrumentation that is always on and changes no result:
- the layout stage result has `timing` (`place_s`, `route_s`, `repair_s`, `drc_s`; `drc_s` includes the repair's
  fast DRC);
- `--auto` writes `build/auto/attempt<k>.metrics.json`;
- `placer.ANNEAL` counts annealing moves and seconds;
- `placer.STAGES` records each stage of the analytic flows.

No parameter is tied to a design. Every one is derived from the board, or is a documented default:

| parameter | value |
|---|---|
| typical part size u | √(median movable courtyard area), `Placer._unit_size` |
| global placement: LSE γ | 1.6 u → 0.2 u |
| global placement: softplus width | 0.4 u → 0.04 u |
| global placement: density weight | 0.05, ×4 per round, 8 rounds of L-BFGS-B |
| anneal start after global placement | `ANALYTIC_T0` = 0.03 × the usual start 2 max(W, H) |
| detailed placement: shifts | 0.25, 0.5, 1 and 2 track pitches |
| detailed placement: swap and window radius | 3 u |
| macro | a unit with courtyard ≥ 2.5 u², or a layout block |
| macro-first: small-part and final passes | start temperature 0.06 × 2 max(W, H) |
| RUDY grid | 1 mm, as before |
| RUDY pad blockage | each pad grown by half a track pitch |

## Placer

### Protocol

The plan changed during the experiment. It started with one full `build --until layout --auto` per idea. A thermostat
build takes 26-36 minutes, so the product person asked for fast placer-only proxies on several designs, and for at
most one final route of the best variant against one baseline.

- **Proxies.** Each sample is `placer.place(seeds=4, iters=30000)`, as the layout stage calls it. There are 5 samples
  per variant (attempt seed0 1, 5, 9, 13 and 17). Each sample is then measured with the default cost model:
  - HPWL: weighted half-perimeter wirelength, the placer's `t_wire`;
  - RUDY overflow: the sum of squared overflow, without pads (`ovf`) and with pad blockage (`ovf+pads`);
  - the largest overflow of one cell, in mm of wire;
  - placement violations;
  - narrow corridors: `Placer.corridors()`, the corridor model of `hwflow check`.

  Each cell is the median of the samples, with [min-max] where it matters.
- **Times.** Up to 20 variants ran in parallel on 16 cores, one thread each, so wall times and anneal rates are
  inflated and noisy. Compare them only within one design.
- **Designs.**

| design | board | parts | character |
|---|---|---|---|
| ht4 (`projects/home-thermostat-4`) | 88 × 46 mm, 4 layers | 74 parts, 41 movable | mixed, mostly fixed, mains |
| synth24 (`make_synth_board.py --profile synth60 --parts 24 --seed 11`) | 58 × 44 mm, 2 layers | 28 parts, 9 nets | a few large ICs, sparse |
| synth60 (`evals/bench/synth60`) | 63 × 48 mm, 2 layers | 60 parts | medium |
| synth150 (`evals/bench/synth150`) | 85 × 64 mm, 2 layers | 150 parts | many small parts, dense |

### Baseline: full layout of home-thermostat-4 (code as of 079f82b, fresh route cache)

`uv run hwflow build projects/home-thermostat-4 --until layout --auto`, seed0 1. Attempt 1 runs alone, then
attempts 2-4 run in parallel.

| attempt (seeds) | route mains / rest | repair | unrouted | DRC errors | vias |
|---|---|---|---|---|---|
| 1 (1-4) | 352 s (AC_ROOM1 open) / 237 s | 4 → 4 in 1 round, 120 s | 4 | 4 | 97 |
| 2 (5-8), kept | cache / 374 s | 2 → 1 in 3 rounds, 81 s | 1 | 1 | 92 |
| 3 (9-12) | cache / 282 s | 2 → 1 in 3 rounds, 78 s | 1 | 1 | 97 |
| 4 (13-16) | cache / 408 s | 5 → 1 in 3 rounds, 86 s | 1 | 1 | 97 |
| seed0 17 run, attempt 1 | 323 s (AC_ROOM1 open) / 372 s | stopped at the change of plan | | | |

- Layout stage: 1466 s. Whole build: 26 min 10 s.
- Verdict: FAIL, with 1 unrouted (AC_ROOM1) and 1 DRC error.
- Kept board: 1421 mm of track and 599 segments.
- Router retries on the kept board:
  - 12 net orders tried (3 width classes × 4 orders);
  - 4 neck-downs (3 pad-entry neck-downs, plus K1_COIL_B necked to 0.15 mm).
- Placement: 42-56 s for 4 seeds on an idle machine.

### Idea 1: RUDY with pad blockage (`HWFLOW_RUDY_PADS`)

The placer already had a RUDY term:
- each net's HPWL is spread over its bounding box on a 1 mm grid;
- capacity is layers × cell² / pitch × 0.4, minus the track keep-outs;
- the cost is the squared overflow.

The switch adds pad blockage:
- Each copper pad, grown by half a track pitch, takes its share of the cell capacity on each of its layers: all
  layers for a THT pad, one layer for an SMD pad.
- The blockage moves with its part and stays incremental. The test checks that the incremental cost equals the full
  cost over 400 moves, undo included.
- It adds to the corridor term and does not duplicate it. The corridor term measures the channel width of each
  constrained net (isolation, `layers`, region nets). RUDY measures the wire demand of all nets against the room left.

| design | HPWL off → rudy | ovf+pads off → rudy | ovf off → rudy | anneal rate rudy / off |
|---|---|---|---|---|
| ht4 | 1431 → 1386 | 31.0 → 23.6 | 10.2 → 9.3 | 82 % (88 % idle) |
| synth24 | 152 → 150 | 0.8 → 0.4 | 0 → 0 | 56 % |
| synth60 | 1258 → 1418 (+13 %) | 259 → 138 (−47 %) | 79 → 41 | 60-73 % |
| synth150 | 4784 → 5101 (+7 %) | 820 → 599 (−27 %) | 298 → 179 (−40 %) | 73 % |

- **Proxy result.** It lowers RUDY overflow on all 4 designs. On the congested 2-layer boards that costs 7-13 % HPWL.
- **Speed.** The rate drops below the 70 % limit on boards made mostly of 2-pad passives, where the per-move grid
  work weighs most.
- **Routing.** The synth60 routes did not finish (see "Routing check"), so routing gives no verdict.
- **Verdict.** Off by default. It is a candidate to pair with flow 2, see below.

### Idea 2: the ASIC flow, global placement, legalization, detailed placement

The stages:
- **(a) Global placement** (`gplace.Global`).
  - Wirelength: log-sum-exp HPWL with the placer's net weights. Decoupling pairs are two-pin nets with weight
    `decouple`. Pads of fixed parts are anchors.
  - Density: the smooth (softplus) overlap area of every pair of courtyards on the same board side. The rule areas
    that forbid footprints and the exclusive floorplan regions are fixed blockages.
  - Clamps: board margin, `edge` line, region box.
  - Solver: L-BFGS-B with continuation (see the parameter table). The test checks the gradient against finite
    differences.
  - Rotations stay as the existing rules give them. Blocks move as rigid units.
- **(b) Legalization.** `Placer.legalize`: overlaps are pushed apart along the axis of least overlap, then parts are
  clamped into the board and into rectangular regions.
- **(c) Detailed placement** (`Placer.detailed`, OpenDP style). Local moves only, kept only when the cost falls:
  - shifts;
  - the 3 other rotations;
  - swaps with units of about the same size (within 15 %) within the radius;
  - window reordering: every permutation of 3 such neighbours.

  At most 4 passes are made. In flow 3 the refine pass of `place()` is skipped too, so no annealing runs. `unstick`,
  a grid search, still runs on a violation.

Flows: 1 is today's annealing from a random start. 2 is (a), (b) and annealing at 0.03 × T0. 3 is (a), (b) and (c).

| design | flow | place s | HPWL | ovf | max cell | ovf+pads | violations (5 samples) |
|---|---|---|---|---|---|---|---|
| ht4 | 1 | 193 | 1431 [1323-1446] | 10.2 | 0.66 | 31.0 | 0 |
| ht4 | 2 | 155 | **1226 [1199-1237]** (−14 %) | 9.4 | 0.59 | 23.0 | 0 |
| ht4 | 3 | **85** | **1238 [1191-1260]** (−13 %) | 9.4 | 0.59 | 23.3 | 0 |
| synth24 | 1 | 62 | 152 [135-185] | 0 | 0 | 0.81 | 0 |
| synth24 | 2 | 84 | **103 [100-111]** (−32 %) | 0 | 0.03 | 0.50 | 0 |
| synth24 | 3 | **26** | **102 [98-103]** (−33 %) | 0 | 0 | 0.49 | 0 |
| synth60 | 1 | 85 | 1258 [1174-1518] | 79 [43-86] | 1.68 | 259 | 0 |
| synth60 | 2 | 123 | **888 [882-924]** (−29 %) | 80 [60-128] | 2.02 | 270 | 0 |
| synth60 | 3 | **65** | **888 [879-904]** (−29 %) | 85 [76-92] | 2.13 | 268 | 0 |
| synth150 | 1 | 113 | 4784 [4301-5827] | 298 [198-538] | 1.75 | 820 | 0 |
| synth150 | 2 | 174 | **2550 [2535-2650]** (−47 %) | 364 [263-428] | 2.77 | 950 | 0 |
| synth150 | 3 | 162 | **2461 [2437-2647]** (−49 %) | 352 [270-483] | 2.79 | 965 | 0 |
| synth24 | 2 + rudy | 103 | 105 | 0 | 0 | 0.29 | 0 |
| synth60 | 2 + rudy (first parameter set) | 193 | 965 (−23 %) | **54** (−31 %) | 1.62 | **181** (−30 %) | 0 |
| synth150 | 2 + rudy (2 samples) | 249 | 2685 (−44 %) | 354 | 2.49 | 847 | 0 |
| ht4 | 2 + rudy (3 samples) | 195 | 1244 | 9.4 | 0.59 | 23.1 | 0 |

Per stage. Each figure is the mean over the 4 placer seeds of a sample, then the median over the samples. Overlap
counts pairs with at least one movable part. The fixed/fixed overlap of ht4 (18.4 mm²) is constant and left out.

| design | stage | s | HPWL | ovf | overlap mm² |
|---|---|---|---|---|---|
| ht4 | global | 4.2-4.5 | 1210 | 9.3 | 14 |
| ht4 | legalize | 0.7-0.9 | 1212 | 9.2 | 12-14 |
| ht4 | anneal (flow 2) | 28 | 1242 | 9.3 | 0 |
| ht4 | detailed (flow 3) | 4.0 | 1187 | 9.3 | 6.3 |
| synth24 | global / legalize | 5.1-5.4 / 0 | 117 | 0 | 0 |
| synth24 | anneal / detailed | 14.6 / 1.1 | 108 / 109 | 0 | 0 |
| synth60 | global / legalize | 10.8-11.8 / 0 | 952 | 173 | 0 |
| synth60 | anneal / detailed | 18.8 / 4.2 | 900 / 898 | 90 / 99 | 0 |
| synth150 | global / legalize | 23-27 / 0 | 2631 | 684 | 0 |
| synth150 | anneal / detailed | 18.8 / 11.9 | 2599 / 2524 | 365 / 377 | 0 |

Findings:
- **HPWL.** Global placement alone beats a full random-start anneal on all 4 designs, by 13 % to 49 %. The gain grows
  with the part count. The spread across seeds also shrinks: ht4 [1199-1237] against [1323-1446].
- **Flow 3** gives the same HPWL as flow 2 in half the time or less on 3 designs (85 / 26 / 65 s against 155 / 84 /
  123 s). It is about even on synth150. The global stage already costs 23-27 s there, and `unstick` and the
  per-seed setup dominate the rest.
- **Legalization** is the weak stage. On boards with many fixed parts and regions (ht4), push-apart leaves 12-14 mm²
  of overlap. Detailed placement removes only half of it, and `_finish` and `unstick` clear the rest. A real
  row-free legalizer (Tetris/Abacus against fixed obstacles) is the next step.
- **Congestion.**
  - The shorter wire is packed denser. On the congested 2-layer boards the RUDY overflow does not improve, and gets
    worse on synth150: +22 % overflow, largest cell 2.8 against 1.75.
  - On ht4 and synth24 it does not change, or improves.
  - The HPWL win therefore holds on all designs. The routability win is likely on ht4 and synth24 and uncertain on
    synth60 and synth150.
  - Flow 2 + RUDY pad blockage is the combination that improves HPWL and overflow together. On synth60 that was
    measured with 5 samples: −23 % HPWL and −30 % overflow with pads. On synth150 it rests on 2 samples only.
- **Narrow corridors.** No change on any design. On ht4 the 2 narrow corridors are between fixed parts only.

### Idea 3: macro-first with the layout blocks (`HWFLOW_MACRO_FIRST`)

**Binding.** The bound design is the copy `projects/home-thermostat-4-blocks/`, so the other A/B runs keep the
original. `hwflow check` is clean: 0 errors, and the 2 guard-FET value warnings that were there before.

| block | bound | note |
|---|---|---|
| ads1115_3ch_rc (U3, C11, R13, C14, R11, C12, R12, C13) | yes, placer places it | At U3's fixed place (rot 0), R11-R13 come within 0.3 mm of the board edge. U3 and C11 lose their fixed place in the copy. |
| pfet_switch_rc (M4, R41, C23, R42) | yes | |
| xtal32k_3215 (Y1, C9, C10) | yes | |
| pfet_vbat_divider (M3, R15, R16, C15) | yes | |
| max17227a_boost (U2, L1, C3, C4, C5, R40) | no | At U2's fixed place, its block courtyard (a box over parts and copper) overlaps the mcu block courtyard by 1.6 × 6.8 mm: a fixed/fixed placement violation. |
| rev_guard_2pfet, esp32c3_mini_decoupling | already bound | |
| U4/U5 (DRV8837) with their parts, K1, K2 | not bound | Back side, and blocks are front side only. |

The block copper closes 5 nets that the router then skips: AIN_NTC1, AIN_NTC2, AIN_REF, VBST_G and VDIV_TOP
(`blocks.closed_nets`). XTAL_P/N are ports of the xtal block, so they are still routed.

**Schedule.**
1. 40 % of the moves anneal the macros alone, on a placer built from the fixed parts and the macros, so the small
   parts are out of the cost.
2. Each small part goes to the mean of the pads it shares a non-ground net with. 40 % of the moves anneal the small
   parts with the macros held.
3. 20 % of the moves anneal everything at a low temperature.

| design | variant | place s | HPWL | ovf | ovf+pads | violations (5 samples) |
|---|---|---|---|---|---|---|
| ht4-blocks | today's placer | 220 | 1430 [1394-1504] | 11.3 | 20.2 | **6 (one sample, 559 s in `unstick`)** |
| ht4-blocks | macro-first (first parameter set) | 307 | 1389 [1321-1423] | 9.6 | 22.8 | **0** |
| ht4 | today's placer | 193 | 1431 | 10.2 | 31.0 | 0 |
| ht4 | macro-first | 139 | 1324 (−7 %) | 9.4 | 28.1 | 0 |
| synth24 | macro-first | 57 | 113 (−26 %) | 0.01 | 1.7 (vs 0.8) | 0 |
| synth60 | macro-first | 93 | 1029 (−18 %) | 144 (+82 %) | 430 (+66 %) | 0 |
| synth150 | macro-first | 125 | 3739 (−22 %) | 680 (+128 %) | 1647 (+101 %) | 0 |

- **With layout blocks.** Large rigid blocks are hard for today's annealer: one ht4-blocks sample ended with 6
  violations after a long `unstick`. Macro-first places the blocks on an empty board, and all its samples came out
  clean.
- **Without blocks.** It lowers HPWL by 7-26 %, but much less than global placement does. On the dense boards it
  roughly doubles the overflow, because the small parts crowd around their macros.
- **Routing.** Not measured: there was no time for a routed run of the bound design.
- **Verdict.** The win holds only for designs with layout blocks, which is one design here. It is not a generic win.

### Routing check

**synth60 (KRT, saved placements, no repair).**
- Ten routes ran in parallel: flows 1, 2 and 3, rudy and 2 + rudy, at seed0 1 and 5.
- None finished within 35-55 min. synth60 is a hard 2-layer board: every width class goes through 4 net orders and
  a neck-down.
- They were stopped so the final route could run. The only signal is how far each one got in the same wall time:
  flows 2 and 3 had written 18-29 router pass boards, flow 1 16-17, rudy 15-16. That is not a result.

**home-thermostat-4, the one final route.** Flow 2 (`HWFLOW_ANALYTIC=1`, seed0 1, ANALYTIC_T0 of 5) against the
baseline above.

| | baseline (flow 1) | flow 2 |
|---|---|---|
| attempt 1: placement | 42-56 s idle | 69 s |
| attempt 1: route mains / rest | 352 s, **AC_ROOM1 open** / 237 s | **45 s, 0 open** / 383 s |
| attempt 1: repair | 4 → 4, 120 s | 4 → 3, 149 s |
| attempt 1: unrouted / DRC / vias / track | 4 / 4 / 97 / – | 3 / 3 / 74 / 1265 mm |
| attempts 2-4: unrouted + DRC | 1+1, 1+1, 1+1 | 2+2, **0+0**, **0+0** |
| attempts 2-4: vias | 92, 97, 97 | 86, 96, 77 |
| attempts 2-4: track mm | 1421 (kept) | 1226, 1401 (kept), 1214 |
| attempts 2-4: rest routing | 282-408 s | 635-1109 s (see the note on load) |
| attempts 2-4: repair | 3 rounds each, 78-86 s | 3 rounds 171 s; 1 round 6 s; 1 round 19 s |
| router retries, kept board | 12 net orders, 4 neck-downs | 9 net orders, 3 neck-downs |
| layout verdict | **FAIL** (AC_ROOM1 unrouted, 1 DRC) | **OK, 0 unrouted, 0 DRC, build verdict PASS** |
| layout wall time | 1466 s | 2161 s |

- **Mains.** The mains route group (all fixed parts) closed AC_ROOM1 in 45 s, where the baseline failed after
  323-352 s in both baseline runs. The placements explain it. The baseline anneal puts SELV parts in the
  26-30 mm band next to the barrier region in every attempt: D3, R15, M3, R8 and R10 at x 26.8-29.9. Flow 2 leaves
  that band empty in 3 of 4 attempts. This is a plausible cause, not a proven one. The group cache shares attempt
  1's mains result with attempts 2-4, so this is one mains route against two.
- **Load.** Attempts 2-4 of the flow-2 run shared the machine with the synth150/synth24 proxy runs (about 10
  processes), and the baseline did not. Their routing times (635-1109 s) are not comparable. Attempt 1 is: its rest
  group took 383 s against 237 s, which is slower.
- **The rest.** Track length is 1214-1401 mm against 1421 mm. Vias are 74-96 against 92-97. Two of four attempts came
  out clean after one short repair round.
- **Caveat.** This route used the first parameter set (T0 = 5 mm, γ 4 → 0.5 mm, absolute). The board-derived set
  gives nearly the same values on ht4 (T0 = 5.3, γ 2.9 → 0.37 mm), and its proxies are within the seed spread.
- **Other code.** `pcb.py` was changed in this worktree by a parallel router experiment. That change is behind
  `HWFLOW_GROUTE`, which was off in both runs.

### What worked, what did not, and why

- **Global placement (a) works on every design.** HPWL drops 13-49 % and the spread across seeds shrinks, because
  the analytic solve finds the net topology that random-start annealing only samples. On ht4 its single routed run
  was the first clean layout of this design. It also used fewer vias and less track.
- **Detailed placement (c) is a cheap replacement for the anneal.** It matches flow 2's HPWL in half the time on 3 of 4
  designs. It looks at no congestion, and on ht4 it needs `unstick` after it, because legalization (b) is weak
  against fixed parts.
- **Legalization (b) is the weak link.** The current push-apart was written to fix small overlaps after an anneal.
  It does not legalize a dense analytic placement among fixed parts.
- **Congestion stays a problem on dense 2-layer boards.** Nothing in (a) or (c) sees routing capacity, so shorter
  wire is packed tighter. RUDY with pad blockage (idea 1) is the part that adds capacity awareness. It costs 7-13 %
  HPWL alone, about 5 % when combined with flow 2, and anneal speed.
- **Macro-first is a fix for layout blocks, not a generic placer.** It helps rigid blocks and hurts congestion on
  dense boards.

### Recommendation

- **Make `HWFLOW_ANALYTIC` (flow 2) the default**, after one more routed check on a second design. Its proxy win holds
  on all 4 designs, and on ht4 it gave the only clean routed layout.
- **Keep `HWFLOW_ANALYTIC_DP` (flow 3) as the fast option**, for area search and many attempts. It should become the
  default once a proper legalizer replaces push-apart.
- **Keep `HWFLOW_RUDY_PADS` off.** Turn it on together with flow 2 for dense 2-layer boards, where it trades a little
  HPWL for 30 % less overflow. That needs a routed confirmation first.
- **Keep `HWFLOW_MACRO_FIRST` off.** Use it only for designs with layout blocks, where it removed the stuck-block
  violations.
- **Not yet shown:**
  - routing on a synthetic board: the synth60 routes did not finish;
  - a second seed set for the final route;
  - routing of the bound-block design.

## Global and detailed routing

Idea: route like an ASIC flow. A global router on coarse g-cells (FastRoute / CUGR style, PathFinder negotiated
congestion) runs first. A detailed router (KiCadRoutingTools, KRT) then follows its guides. Idea 6 is pin access
analysis in the style of TritonRoute: for each pad, which directions a track of the net's width can leave it in.

Code: `src/hwflow/globalroute.py` (model, router, pin access, CLI `python -m hwflow.globalroute <proj> [--board B]
[--access] [--pitch P]`), `tests/test_globalroute.py`, and the harness `scripts/groute_bench.py` (prep / route /
predict). The module is generic. It contains no design names or design constants; every input comes from the
board's rules and placement through `corridor.Model`. The hook in `pcb.krt_route` is off by default:

| switch | effect |
|---|---|
| `HWFLOW_GROUTE=1` | global route of each `krt_route` input; every pass first tries `--ordering original` with `--nets` sorted most constrained first. The order takes the first of the `router_retries` tries, and the other orders follow. |
| `HWFLOW_GROUTE_LAYERS=1` | also: a net the global route keeps on one layer with no via routes in a pass restricted to that layer (`layer_args`) |
| `HWFLOW_GROUTE_ACCESS=1` | the global route starts each net from its pin access points |
| `HWFLOW_GROUTE_PITCH`, `HWFLOW_GROUTE_ITERS` | override the g-cell side and the PathFinder iteration cap |

`repair.py` needs no hook of its own, because `repair.reroute` calls `krt_route`.

### Model and parameters

- **G-cells.** The side is `TRACKS_PER_CELL (3) x (track + clearance)` of the board, clamped to 0.75-2.5 mm and
  rounded to 0.05 mm. That is 1.35 mm for 0.25/0.2 mm rules. Every copper layer gets a grid, and a via edge joins
  any two layers of a cell (a through via).
- **Planar capacity.** It is measured on the boundary line between two cells, sampled every 0.025 mm. Each free run
  f holds `floor((f + clearance) / (track + clearance))` tracks. Obstacles, grown by the clearance:
  - pads on that layer (a through-hole pad is on every layer) and non-plated holes;
  - keep-out rule areas that forbid tracks on the layer;
  - cuts and the outline (the 0.3 mm edge inset).

  A boundary closed by keep-outs, cuts or the outline even at the minimum track is impassable. A boundary closed only
  by pads has capacity 0, so any use is overflow, because the net's own pads are part of the obstacles there.
- **Per-net rules.** These become forbidden cells: board.isolation `mm` (member vs non-member pads), `between_mm`
  (member vs other members' pads), `.kicad_dru` disallow rules that name the net, conductive isolation, and
  `nets.<n>.layers` (the other layers are forbidden). A cell is forbidden only when the region covers it whole.
  Nets with the same rule signature share one mask.
- **Via capacity.** Via sites in the cell (centres at via_dia/2 + clearance from every pad, inside the outline,
  outside via keep-outs), divided by (via_dia + clearance)^2.
- **Demand.** A net uses `(w + clr) / (track + clr)` units of an edge, so wide nets use more. The demand is capped
  at an empty boundary's capacity, because a track wider than a g-cell fills the boundary. Edges that touch a cell
  of one of the net's pad centres are pin access and count no usage.
- **Router.** Each net is a Steiner tree grown with Prim's method: a multi-source A* from the tree to the nearest
  terminal, in the terminals' bounding box + 6 cells (the full board as fallback). PathFinder then rips up only the
  nets on overflowed edges. The cost of an edge is `(1 + history) * (1 + pres * overflow)`, with pres 0.5 x 1.5 per
  iteration, history += overflow, and at most 30 iterations. A via costs 4 planar steps. These are documented
  defaults, not tuned per design.
- **Output per net.** Its g-cells (the route guide), layers, vias, overflow, and a criticality: overflow, then peak
  use/capacity, then detour.
- **Pin access.** For each pad, 8 directions are tried on each of its copper layers. A centre line of the net's
  width, and separately of the board minimum width, runs from the pad centre to 0.3 mm beyond the pad's edge. It
  must stay in the configuration space: the outline inset by w/2, minus foreign pads and holes grown by the pair's
  clearance + w/2 (net class, isolation, between_mm), minus the net's keep-outs. This is `partescape._Part.free`
  carried from the part's own pads to the whole board. An SMD pad with no direction at either width is
  `no_access`. With access on, the global router starts each pad from the g-cells of its valid directions.

### Validation set

Six designs:

| design | parts / routed nets | board | layers | density |
|---|---|---|---|---|
| home-thermostat-4 | 39 routed nets | 88 x 46 mm | 4 | sparse, mains isolation, between_mm, layer-restricted nets, keep-outs, notches |
| synth60 s1, s2, s3 (make_synth_board seeds 1-3) | 60 / about 80 | 63 x 48 mm | 2 | medium |
| synth60 dense d4 (seed 4, board sides x 0.85) | 60 / 76 | 54 x 41 mm | 2 | high |
| synth150 b7 (seed 7) | 150 / 170 | 85 x 64 mm | 2 | large, poorly placed (short placer run): KRT fails 2/3 of the nets |

The synthetic boards were placed with the hwflow placer (1 seed: 20000 iterations for synth60, 4000 for synth150).

KRT ground truth for each design:

- thermostat: its last full layout (`build/krt.log` and REPORT notes).
- s1-s3: full `krt_route` (all passes, neck-downs, retries). The truth is the nets open after the first try of the
  power and signal passes, and the necks from the `krt_route` notes.
- d4, b7: one KRT call over every routed net at the board track width (default order). This is cheaper: a full
  `krt_route` takes about 50 min on synth60 on the shared machine, and the single call on b7 already took 40 min.

The machine was shared with the placer experiment (load about 20 on 16 cores), so absolute router times are
inflated. Two router calls in the s1 and s2 `HWFLOW_GROUTE=1` and `layers` runs were killed from outside
(SIGTERM, "failed (exit -15)").

### Speed

The target was under 30 s on home-thermostat-4.

| design | global route (derived pitch) | + pin access start | pin access alone | 0.75 mm pitch |
|---|---|---|---|---|
| home-thermostat-4 | 1-3 s | 4.5 s | 1.8 s | 9-12 s |
| synth60 s1-s3, d4 | 1.0-1.7 s | 2.1-4.9 s | 1.1-2.0 s | 1.2-4.9 s |
| synth150 b7 | 4.5 s | 9.2 s | 1.0-4.5 s | 12-27 s |

On the thermostat, the build (raster capacity, masks) takes 0.8-1.8 s and PathFinder 0.15 s (6 iterations). At a
0.5 mm pitch PathFinder takes about 1 min (python A*), so cells finer than about 3 tracks are not practical.

### (a) Feasibility: does global-route overflow predict the nets KRT fails?

Nets the global route flags, true positives in brackets:

| design | nets KRT fails | GR, derived pitch | GR + access start | GR 0.75 mm | GR 0.75 mm + access |
|---|---|---|---|---|---|
| home-thermostat-4 | 7 | 0 | 0 | 4 (0): mains nets | 1 (1): VIN_BST |
| synth60 s1 | 18 | 0 | 0 | 0 | 0 |
| synth60 s2 | 15 | 0 | 0 | 0 | 0 |
| synth60 s3 | 22 | 0 | 0 | 0 | 0 |
| synth60 dense d4 | 19+ | 0 | 0 | 0 | 0 |
| synth150 b7 | 113 | 0 | 0 | 0 | 0 |

The global route does not predict KRT failures on any design. Recall is 0, apart from 1 of 7 on the thermostat at
0.75 mm. At g-cell scale these boards have plenty of channel capacity, even the dense one. KRT fails where a net
leaves a pad: QFN and SOIC pins, or a wide power net at a small pad. The global model counts that as pin access.
On the thermostat a fine grid flags the four mains nets. That is a real constraint, since the corridor proof also
says "a neck may be needed" for AC_L and AC_ROOM1, but KRT routes those nets, so the flags are false positives.
Inside the nets that pin access flags, GR criticality does not separate the failures from the rest (AUC 0.51 on
s1). Starting the global route from access points changes no prediction, except VIN_BST at 0.75 mm on the
thermostat. **No win.**

### Idea 6: pin access as a predictor

A net is flagged when one of its pads has at most 1 of the 8 directions open at the net's width:

| design | flagged / routed nets | precision | recall | base rate | neck-down recall | 0 directions at net width |
|---|---|---|---|---|---|---|
| home-thermostat-4 | 11 / 37 | 0.55 | 0.86 (6/7) | 0.19 | 0.75 (3/4) | SW_BOOST (necked) |
| synth60 s1 | 32 / 80 | 0.56 | 1.00 | 0.22 | 1.00 (8/8) | +3V3 (necked) |
| synth60 s2 | 31 / 75 | 0.35 | 0.73 | 0.20 | 0.73 | +3V3 (necked) |
| synth60 s3 | 33 / 77 | 0.64 | 0.95 (21/22 named) | 0.29 | 1.00 | +3V3 (necked) |
| synth60 dense d4 | 30 / 76 | 0.63 | 1.00 (19/19 named) | 0.25 | - | +3V3 |
| synth150 b7 | 63 / 170 | >= 0.24 (*) | 0.68 (15/22 named) | 0.66 | - | +3V3 |

(*) KRT names only 22 of the 113 open nets on b7 ("+N more"), so precision there is a lower bound. The board is
saturated (base rate 0.66), so lift cannot be measured on it.

The win holds on the five designs where it can be measured. On the five that are not saturated, recall is 0.73-1.0
and precision 0.35-0.64, which is 1.7-2.9 times the base rate, in about 2 s (4.5 s at 150 parts) before any
routing. On the saturated b7, recall over the named failures is 0.68. "0 directions at net width, at least one at the board minimum" picked exactly one
net per design, and that net needed a neck-down every time it was routed (precision 1.0; it covers 1 of the 4-19
necks per design). `no_access` (an SMD pad that no track leaves at any width) fired only on QFN exposed pads (GND,
a via-in-pad pocket, correct). Before a fix to the search window it also fired on 9.5 mm battery-holder pads.

### (b) Net order and layer hints for KRT

Unrouted nets after one KRT try with each net order, on the same input board:

| design | KRT default (MPS) | global-route order | pin-access order (fewest directions first) |
|---|---|---|---|
| synth60 s1 (signal pass) | 17 | 20 | 17 |
| synth60 s2 (signal pass) | 17 | 18 | 17 |
| synth60 s3 (signal pass) | 24 | 26 | 27 |
| synth60 dense d4 (all nets, one call) | 24 | 25 | 19 |
| synth150 b7 (all nets, one call) | 113 | 101 | 99 |

Full `krt_route`, baseline against `HWFLOW_GROUTE=1` (same number of tries per pass):

| design | unrouted base / groute | router calls base / groute | time s base / groute | vias base / groute |
|---|---|---|---|---|
| synth60 s1 | 4 / 0 | 32 / 16 | 2991 / 2110 | 138 / 137 |
| synth60 s2 | 0 / 14 (*) | 15 / 33 | 2124 / 3090 | 190 / 190 |
| synth60 s3 | 0 / 0 | 15 / 15 | 2114 / 2116 | 123 / 116 |
| median | 0 / 0 | 15 / 16 | 2124 / 2116 | 138 / 137 |

(*) In s2 the neck-down pass lost its `--ordering inside_out` try to an outside kill. That try routed all 18 nets
in the baseline.

`HWFLOW_GROUTE_LAYERS=1` (s1): 20 unrouted (base 4), 8 vias (base 138). The signal pass left 48 of 74 nets open.

- **The global-route order lost on four designs and won on one.** It lost on s1, s2, s3 and d4 (single tries: 17 vs
  20, 17 vs 18, 24 vs 26, 24 vs 25). It won only on the saturated b7 (101 vs 113 open), where any change of order
  moves a large number.
- **The full-flow medians are equal.** s1 improved and s2 got worse, both by run-to-run KRT variance and the
  shifted retry list, not by the hint. The hint also costs one of the retries: `--direction backward` drops out.
- **The pin-access order is mixed.** It is better on d4 (19 vs 24) and b7 (99 vs 113), equal on s1 and s2, and
  worse on s3 (27 vs 24). That is no consistent win, but it is the best candidate for an extra retry order.
- **The layer hints hurt badly.** On a 2-layer board with SMD parts on F.Cu, "the global route keeps the net on
  one layer" means F.Cu, and forbidding B.Cu takes away KRT's way around.

### (c) Guides

KRT cannot restrict a net to a region within one multi-net call. `--keepout` (polygons on a User layer) applies to
every net of the call. `--guide-corridor` is one waypoint polyline for every routed net, meant for a single net. A
per-net guide would need one KRT call per net (about 80-200 calls of tens of seconds each). The other way is a
per-pass keep-out, the board minus the union of the pass's guides plus a margin, but on these boards that union
covers nearly the whole board, so it restricts nothing. Skipped.

The full home-thermostat-4 route was not run: the synthetic results favour neither (b) nor (c).

### What worked, what did not

- **Worked:** a generic global router (every rule type through the generic inputs; unit tests for slit capacity,
  wide nets, keep-out barriers, single-layer nets, the derived pitch) that runs in 1-3 s on home-thermostat-4 and
  2 s on the synthetic boards.
- **Worked, on every design where it can be measured:** pin access. It predicts KRT failures and neck-downs before routing, with recall
  0.73-1.0 and 2-3x lift. "0 directions at net width" is a precise neck-down predictor.
- **Did not work:** global-route overflow as a predictor of KRT failures. On these boards the bottleneck is pin
  escape, not channel capacity. A finer grid adds false positives (wide isolated nets) before true ones, and its
  run time grows quickly.
- **Did not work:** the global-route net order (it wins on 1 of 5 designs) and the layer hints (harmful). The
  pin-access order is mixed (2 wins, 2 ties, 1 loss). Guides are not possible with KRT's interface.

### Recommendation

- Do not put the global router in the flow. Keep `globalroute.py` behind `HWFLOW_GROUTE` as an experimental
  feasibility view. It is worth a second look only on boards where channel capacity is the limit (BGA fan-out, many
  wide nets, 1-layer routing).
- Adopt pin access as a pre-route check, generic and cheap:
  - report the pads with 0 directions at net width;
  - send those nets through `krt_route`'s existing narrow-first path, the corridor proof's `necked` set, which skips
    a full-width pass and its retries, each a router call of minutes;
  - list pads with at most 1 direction as placement risk (spread or rotate the part), and feed that list to the
    placer's congestion term.

  Next step: measure the router calls the `necked` change saves on these designs, and try the pin-access order as
  one more `KRT_VARIANTS` entry (after MPS, not before) for the passes that still leave nets open.

## Combined flow (`HWFLOW_FLOW=asic`)

Experiment of 2026-10-23, same branch. One flow built from the pieces above, off by default: env `HWFLOW_FLOW=asic`
or `placement: {flow: asic}` in design.yaml. Code: `src/hwflow/asicflow.py` (stages, density maps, pin access term,
router helper), hooks in `Placer.run`, `gplace.Global.run` (`feedback`, `infl`), `Placer.legalize(refs=)` and
`pcb.krt_route`. Tests: `tests/test_asic_ideas.py` (incremental pin access term with undo, walled pad, stage list,
inflation). Harness: `scripts/asic_flow_bench.py` (one proxy sample per process, `--table` for the medians). Nothing
in it names a design; every input is the board's rules and placement.

```
 placement (per placer seed)                                     routing (pcb.krt_route, every call)
 ---------------------------                                     -----------------------------------
 1 macro      blocks / units >= 10x median courtyard?            5b pin access (globalroute.pin_access, 0.1-0.7 s)
              yes: mixed-size GP -> legalize macros ->              nets with a pad with 0 exits at its width but
              hold them -> GP of the rest                           >= 1 at the board minimum -> `necked` set
 2 global     L-BFGS analytic GP, 8 rounds; after rounds            (narrow-first pass, no full-width pass)
              2, 4, 6 a congestion map inflates courtyards       6  KRT passes; each pass tries first
              (a) RUDY - pad blockage   (b) PathFinder g-cells      `--ordering original` on the pin-access order
 3 legalize   push-apart (Placer.legalize)                          (fewest exits first), then the usual orders
 4 detailed   local moves (Placer.detailed), then a short           (same number of tries); GR layer hints off
              anneal: 0.25 x iters at 0.03 x T0, no ramp
 5a access    placer term: 3 per pad with <= 1 of 8 exits
              at its net width (6 with none), in 4
```

Stage switches (all on by default inside the flow): `HWFLOW_ASIC_MACRO`, `HWFLOW_ASIC_DP`, `HWFLOW_ASIC_ANNEAL`,
`HWFLOW_ASIC_ACCESS` (placer term), `HWFLOW_ASIC_NECK`, `HWFLOW_ASIC_ORDER`; `HWFLOW_ASIC_DENSITY` = `rudy`
(default), `groute` or `none`; `HWFLOW_ASIC_GR_TARGET` (default 1: only overflow inflates). The route cache keys
include these knobs.

Parameters (documented defaults, not tuned per design):

| parameter | value |
|---|---|
| macro | a layout block, or a unit with a courtyard >= 10 x the median movable one |
| feedback | after GP rounds 2, 4 and 6 of 8 |
| inflation | per courtyard: mean of max(1, demand / capacity) over its cells, at most 2x area; all inflation together at most 25 % of the free board area; never shrinks |
| RUDY map (a) | 1 mm cells, capacity minus pad blockage (each pad grown by half a pitch), as `HWFLOW_RUDY_PADS` |
| PathFinder map (b) | `globalroute.build` + 10 PathFinder iterations on the current GP; per g-cell the largest use / capacity of its edges and via edge |
| short anneal | 0.25 x the per-seed iterations, start 0.03 x 2 max(W, H), the existing terms (corridor, isolation, regions, plain RUDY); no refine pass after |
| pin access term | rectangle model of `globalroute.pin_access`: 8 rays from the pad centre to 0.3 mm + width beyond the pad corner radius, 5 samples each, foreign pads grown by the larger class clearance + w/2, board rectangle inset by copper_edge + w/2; best layer counts; incremental with undo |

The pin access term ignores isolation distances, keep-outs and outline notches (the corridor term already covers
the constrained nets). The router's pin access is the exact shapely model.

### Proxies

Each sample: `placer.place(seeds=4, iters=30000)`, 5 samples per cell (seed0 1, 5, 9, 13, 17), measured with every
switch off: HPWL, RUDY overflow without and with pad blockage, global-route overflow at the derived pitch (shapely
model, after placement), pads with <= 1 exit at their net width, pads with 0 exits (and >= 1 at the minimum),
placement violations (sum over the 5 samples). Medians, [min-max]. 12 samples ran in parallel on 16 cores and another
worktree ran ngspice and builds at the same time (load 12-20), so times compare only within a design.

Designs: ht4 (`projects/home-thermostat-4`, 4 layers, 41 movable), sensor_preamp (`examples/`, 13 parts, at its
declared 40 x 30 mm board: no area search in the proxies), synth24 (`make_synth_board.py --profile synth60 --parts 24 --seed 11`), synth60
(`evals/bench/synth60`).

| design | variant | place s | HPWL | ovf | ovf+pads | GR overflow | pads <= 1 exit | 0-exit pads | violations |
|---|---|---|---|---|---|---|---|---|---|
| ht4 | baseline | 168 | 1379 [1299-1496] | 9.2 | 42.6 [23.3-59.3] | 0 | 15 | 1 | 0 |
| ht4 | asic, density (a) RUDY | 300 | **1235 [1202-1306]** | 9.4 | **22.7** [18.5-31.6] | 0 | 14 | 1 | 0 |
| ht4 | asic, density (b) PathFinder | 254 | 1235 (identical placements) | 9.4 | 22.7 | 0 | 14 | 1 | 0 |
| ht4 | asic (a), no pin access term | **102** | 1214 [1192-1280] | 9.3 | 22.3 [20.5-23.8] | 0 | 15 | 2 | 2 |
| sensor_preamp | baseline | 47 | 72.6 [70-101] | 0 | 1.0 | 0 | 0 | 0 | 0 |
| sensor_preamp | asic (a) | 52 | **56.9** [51.5-65] | 0 | 1.24 | 0 | 0 | 0 | 0 |
| sensor_preamp | asic (b) | 57 | 55.7 [51.1-66.4] | 0 | 1.26 | 0 | 0 | 0 | 0 |
| sensor_preamp | asic (a), no access term | 14 | 56.9 (identical) | 0 | 1.24 | 0 | 0 | 0 | 0 |
| synth24 | baseline | 44 | 152 [135-188] | 0 | 1.02 | 0 | 6 | 6 | 0 |
| synth24 | asic (a) | 97 | **103** [99-106] | 0 | **0.43** | 0 | 6 | 6 | 0 |
| synth24 | asic (b) | 99 | 105 [100-108] | 0 | 0.42 | 0 | 6 | 6 | 0 |
| synth24 | asic (a), no access term | 48 | 103 (identical) | 0 | 0.43 | 0 | 6 | 6 | 0 |
| synth60 | baseline | 80 | 1258 [1174-1518] | 79 | 259 [177-289] | 0 | 39 | 6 | 0 |
| synth60 | asic (a) | 266 | **947** [932-1072] | **54** | **189** [163-285] | 0 | 39 | 6 | 0 |
| synth60 | asic (b) | 343 | 854 [837-889] | 71 | 242 [208-286] | 0 | 39 | 6 | 0 |
| synth60 | asic (a), no access term | 151 | 1064 [930-1070] | 58 | 191 [166-277] | 0 | 39 | 6 | 0 |

Per-stage contribution on the proxies: the median change when the stage is turned off, against asic (a).

| stage off | ht4 | sensor_preamp | synth24 | synth60 |
|---|---|---|---|---|
| macro (`MACRO=0`) | no macro on this board | no macro | HPWL 0, ovf+pads 0.43 -> 0.84 | HPWL +11 %, ovf+pads -5 % |
| routability density (`DENSITY=none`) | no change (0 courtyards inflated: the overflow is over fixed parts) | HPWL -2 % | HPWL +1 %, ovf+pads 0 | HPWL **-10 %**, ovf+pads **+28 %**, ovf +31 % |
| density (b) instead of (a) | identical to none | identical to none | identical to none | identical to none |
| local moves (`DP=0`) | HPWL 0 | +3 % | +6 % | +13 % |
| short anneal (`ANNEAL=0`) | HPWL 0 | -8 % (the anneal hurts) | 0 | +13 % (*) |
| pin access term (`ACCESS=0`) | flagged pads 14 -> 15, 0-exit pads 1 -> 2, 2 of 5 samples with an overlap left; place time **-66 %** | no flagged pad, no change, -73 % time | no change (the 6 flagged pads are +3V3 at QFN pins), -50 % time | no change in flagged pads, -43 % time |

(*) synth60 samples are bimodal (about 935 or about 1065 for every asic variant), so a 5-sample median moves by
13 % on one sample; read the synth60 HPWL rows with that spread.

**PathFinder feedback against RUDY.** The global router found no overflow at any feedback point on any design: 0
of 60-120 feedback calls inflated a courtyard, so variant (b) gives exactly the placements of `DENSITY=none`, at
0.3-2.5 s per call. This is the result of the earlier routing experiment again: at g-cell scale these boards have
channel capacity to spare, and the bottleneck is the pin escape that the global model counts as pin access. With
the target lowered to 70 % use (`HWFLOW_ASIC_GR_TARGET=0.7`, not a spec value, run to give it a chance) it inflates
on the synthetic boards and gives synth60 HPWL 932 / ovf+pads 219: less than RUDY (189), and nothing on ht4.
RUDY with pad blockage is the density source that acts, and only where movable parts sit in congested cells
(synth24, synth60, sensor_preamp; not ht4).

### Full routing

`uv run hwflow layout <proj> --auto` (4 attempts, seed0 1), one baseline run and one run of the best combined variant
(asic, density (a), every stage on) per design, one at a time. Comparability caveat: the baseline ht4 run met load
15-20 from another worktree, the combined run load 3-15. Wall times are therefore biased towards the combined flow;
router tries, repair rounds, vias, track, DRC and unrouted are not.

| home-thermostat-4 | baseline | combined (a) | combined (a), no access term (ablation) |
|---|---|---|---|
| attempts run / first clean | 4 / 3 | 3 / 3 | 2 / **2** |
| layout wall time | 1310 s | **750 s** | **733 s** |
| CPU time of the command | 2996 s | **2314 s** | 2109 s |
| placement per attempt | 46-54 s | 82-105 s | 31-60 s |
| route group rest per attempt | 217-442 s | **154-262 s** | 226-281 s |
| repair, attempt 1 | 3 -> 1 in 3 rounds, 148 s | 5 -> 2 in 3 rounds, 36 s | 5 -> 3 in 3 rounds, 43 s |
| repair, kept attempt | 2 -> 0 in 1 round | 1 -> 0 in 1 round | none needed |
| router tries, kept board | 12 | **10** | 12 |
| neck-downs, kept board | 3 | 3 (SW_BOOST narrow first) | 3 |
| kept board: unrouted / DRC / vias / track | 0 / 0 / 98 / 1491 mm | 0 / 0 / **78** / **1279 mm** | 0 / 0 / **73** / **1197 mm** |
| track, all attempts | 1414-1525 mm | 1275-1288 mm | 1197-1334 mm |
| verdict | OK | OK | OK |

| sensor_preamp (area search on) | baseline | combined (a) |
|---|---|---|
| layout wall time | 243 s | **141 s** |
| final board | 18 x 13 mm | 18 x 13 mm |
| repair | 1 -> 0 in 1 round | **none needed** |
| unrouted / DRC / vias | 0 / 0 / 7 | 0 / 0 / **3** |
| verdict | OK | OK |

Router stages on ht4:
- **Pin-access necked (5b).** It flagged one net, SW_BOOST (0 exits at 0.6 mm, the boost switch node at a
  small pad), in every call. SW_BOOST then skipped the full-width pass and closed with one pad-entry neck-down call;
  the baseline routed it in the shared 0.6 mm pass, whose 4 tries all left nets open. On sensor_preamp it flagged
  nothing. Its cost is 0.1-0.7 s per `krt_route` call.
- **Pin-access order (6).** In every pass of every attempt the `--ordering original` try on the pin-access order
  left exactly as many nets open as the default order after it. It changed no result, and it pushes `--direction
  backward` out of the tries.

### Win criteria

- **Combined flow against today's flow: a win on both routed designs.** On ht4 the router used 10 tries instead of 12
  on the kept board, the first attempt's repair took 36 s instead of 148 s, and the kept board has 20 % fewer vias
  and 14 % less track. On sensor_preamp no repair was needed (baseline: 1 round), with 3 vias instead of 7. DRC,
  unrouted and the verdict are the same (0, 0, OK). Wall time fell on both (750 s vs 1310 s, 141 s vs 243 s), with
  the load caveat on ht4. One run per design: the number of attempts to the first clean board (3 in both on ht4) did
  not improve.
- **Per stage:**
  - Global placement and the local moves carry the win: HPWL -10 % to -32 % against the baseline, and the earlier flow-2
    route.
  - RUDY density (a) acts only on the dense synthetic boards (synth60: -22 % overflow with pads for +11 % HPWL). It
    had no effect on either routed design, so the routed win does not depend on it, and it is not proven on routing.
  - PathFinder density (b): no effect at all (no overflow).
  - Macro: no macro on the two routed designs; mixed on the synthetic ones.
  - Short anneal: neutral to harmful except on synth60.
  - Pin access placer term: no gain on any proxy, 2-3x placement time, and on ht4 the ablation without it routed as
    well or better (clean at attempt 2 with no repair, 73 vias). Not a win.
  - Pin-access necked: a plausible saving of one width-class pass for one net on ht4, too small to isolate in tries.
  - Pin-access order: no effect.

### Recommendation

- **Make `HWFLOW_FLOW=asic` the default, trimmed**: global placement with RUDY-plus-pad-blockage density feedback,
  legalization, local moves, the short anneal, and the pin-access `necked` routing.
  - Turn the pin access placer term off by default (`ACCESS`). It triples placement time for no measured gain.
  - Drop the pin-access net order (`ORDER`). It never changed a result and costs a retry order.
  - Drop the PathFinder density source. It never fired on these boards.
  - Do it after one more routed seed set (seed0 17) on ht4 under equal load, because each routed comparison here
    is a single run.
- **Keep macro-first inside the flow.** It is a no-op without macros and helps the layout-block case of the earlier
  experiment, but it is not yet routed on a design with blocks.
- **Next:**
  - a real legalizer: ht4 still leaves 2-11 mm2 of overlap after detailed placement, cleared by the anneal and
    `_finish`;
  - the PathFinder map only on boards where channel capacity is the limit (BGA fan-out, 1-layer boards).
