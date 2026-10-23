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
