# Layout flow

hwflow has one layout flow. `hwflow layout <proj>` and `hwflow build <proj> --until layout` run it, and `--auto`
repeats it over placer seeds with repair and area search. Every stage comes from the experiments in
`docs/asic-ideas.md`, and each stage was kept because it improved results there. Nothing in the flow names a design.
Every input comes from the board rules, the floorplan and the placement.

Code: `src/hwflow/layout/place/asicflow.py` (stage order, density map, pin-access hint), `placer.py` (cost terms, legalization,
detailed moves, anneal), `gplace.py` (global placement), `pinaccess.py`, `krt.krt_route` and `regionroute.py`,
`repair.py`, `prune.py` (routing). Tests: `tests/test_layout_flow.py`.

## Placement (per placer seed, `placer.place` → `Placer.run` → `asicflow.place`)

1. **Macro stage** (`HWFLOW_ASIC_MACRO`). A layout block, or a unit with a courtyard of at least 10 × the median
   movable one, is a macro. When the design has macros, global placement first places every unit. The macros are
   then legalized against each other and the fixed parts and held in place. A second global placement then places
   the other units. A design without macros skips this stage.
2. **Analytic global placement with routability feedback** (`gplace.Global`, `HWFLOW_ASIC_DENSITY`). L-BFGS-B
   runs on log-sum-exp wirelength plus a smoothed courtyard-overlap density, in 8 rounds of rising density weight.
   Pads of fixed parts are anchors. Rule areas and exclusive regions are blockages, and the board, edge and region
   boxes are clamps. After rounds 2, 4 and 6, a RUDY map of the current solution is built on 1 mm cells. It compares
   wire demand with cell capacity minus pad blockage, where each pad is grown by half a track pitch. The courtyards
   over overflowing cells are then inflated. A courtyard can grow to at most 2 × its area. All inflation together
   is at most 25 % of the free board area, and inflation never shrinks.
3. **Legalization** (`Placer.legalize`). Overlaps are pushed apart along the axis of least overlap. Parts are then
   clamped into the board and into their rectangular regions.
4. **Detailed local moves** (`Placer.detailed`, `HWFLOW_ASIC_DP`). These are greedy moves in the OpenDP style:
   shifts of 0.25 to 2 track pitches, the three other rotations, swaps of similar-size units, and 3-unit window
   reordering. At most 4 passes run.
5. **Short low-temperature anneal** (`HWFLOW_ASIC_ANNEAL`). It runs 0.25 × the per-seed iterations, starting at
   0.03 × 2 max(W, H), with no weight ramp. It uses the placer's existing terms: wire, overlap, bounds, edge,
   decoupling loops, groups, plain RUDY congestion, keep-outs, isolation, separation and the corridor term. The
   floorplan regions and budgets act through those terms. The refine pass of the old flow does not run.

After the stages, `place` keeps the best seed. `unstick` (a grid search for a part stuck on a violation) still runs
on a violation: over the part's floorplan region (else the board), at most `placer.UNSTICK_POINTS` grid points per
rotation (a board up to about 58 x 58 mm keeps the 1.5 mm step) and the `placer.UNSTICK_UNITS` worst parts per round.
The layout budget (`HWFLOW_LAYOUT_BUDGET_S`) reaches placement: `stage_layout` gives `place` half of the time left;
past it no new seed starts and `unstick` stops.
Then `Placer.settle` runs on what is left (`HWFLOW_SETTLE=0`: off). Settle takes every unit in a violation
out and puts it back, largest first, on the free spot nearest to where it was, in the four rotations, on an occupancy
raster of the board (courtyards per side, a through-hole part on both, keep-outs, the board margin, the copper-edge
distance of the pads, rectangular and polygon floorplan regions). When a unit finds no spot it repacks every unit
(near, then from the corner) and keeps the result with the fewest violations; it is never worse than its input.
With the routability ranking on (`HWFLOW_ROUTAB_RANK=1`), `place` keeps, among the legal seeds, the one with the
fewest predicted hard nets.

## Placement legality gate

A placement with a violation (courtyard overlap, a part off the board or in a keep-out, a floorplan region broken,
isolation) is invalid. `stage_layout` never routes it: it stops in seconds with `legality.placement_verdict`
(`layout/place/legality.py`, also `diagnose.placement_verdict`), which names the fill of each board face and of each region the violations touch (placer courtyards with their margin,
over the area inside `margin_mm`), and the board or region that would hold them. Above `PLACE_FILL_MAX` (95 %) it
sends the build back to architecture; under it, the area allows a legal placement and the verdict is a placer
failure (layout). `hwflow floorplan` fails the same way on an illegal quick placement. `HWFLOW_ROUTE_ILLEGAL=1`
routes it anyway, for debugging only.

Measured (scripts/legal_bench.py, 2 seeds x 10000 iterations; violations before settle -> after):
see "Calibration and measurements" below.

## Routability prediction (`layout/place/routability.py`, every legal placement, seconds, no routing)

A deterministic raster model of the copper (numpy and scipy.ndimage only), per copper layer at 0.05 mm or coarser
(at most 3 M cells): pad copper labelled by net, hard areas (outside the outline, track keep-outs, holes), the
Euclidean distance transform of the free space (the corridor half-width map), its ridge (channel centre lines),
connected components, and via sites. `Raster` is the reusable primitive (`.edt`, `.free(li, w, clr)`, `.ridge`,
`.components`, `.via_free`).

- **Window cuts** (the estimate). Every square window of 2, 4 and 8 mm at every 0.5 mm position: the nets with pads
  inside and outside must cross its border (demand, sum of w + c), against the track centre lines that fit along
  the border's free runs on the pad layer plus the other layers up to the via sites inside. The worst ratio per
  0.5 mm cell is the overflow map. A net with a pad at ratio >= `RATIO_HARD` (3.0) is hard. Pads with at most one
  exit (`pinaccess`) and constrained nets whose widest no-neck corridor is less than 0.3 mm wider than the net
  (`corridor.margin`) are listed in the message but do not make a net hard (see the calibration).
- **Exact bounds** (proofs: when one fails, no router can route the placement): channel density across every
  straight line of the board (x and y), a min-cut ring around every part with 8 or more signal pads (inner layers
  limited by the via sites and pads inside), forced corridors (a constrained net whose widest corridor is narrower
  than its width), and 2D packing per floorplan region (area, and a member that fits the region's box in neither
  orientation). A failed bound is reported with the prediction; it does not stop the layout (the
  prediction is a report and a run log, never a gate).
- **Verdict**: 0 hard nets `routable`; under 25 % of the routed nets `marginal`; from 25 % (or a proof) `not
  routable`. `hwflow floorplan` reports it as the check `routability-prediction` (pass when `routable`, else a
  risk, never a FAIL); REPORT.md shows the prediction and, after routing, how it compared with the router.
- **Run log** (a dataset, never in git): each layout run appends (cheap features, outcome) to
  `build/routab/runs.jsonl` and saves the raster channels as `build/routab/run_<n>.npz`; the format is in
  `scripts/routab_bench.py`.

The prediction also drives three options, off by default (see "Calibration and measurements" for why):
`HWFLOW_ROUTAB_RANK=1` (seed ranking above), `HWFLOW_ROUTAB_FEEDBACK=1` (the global placement's density feedback
also inflates the courtyards under escape hot spots: `asicflow.escape_map`) and `HWFLOW_ROUTAB_RETRY=1` (a pass wider
than the board track keeps its first net order, since the neck-down routes its open nets; a board-width pass stops
retrying with more than 3 nets open).

## Routing (every `krt.krt_route` call, including the repair's reroutes)

6. **Pin access** (`pinaccess.pin_access`, `asicflow.necked`, `HWFLOW_ASIC_NECK`). For each pad, 8 directions are
   checked on each of its copper layers. Each check asks whether a track of the net's width, and separately one of
   the board minimum width, can leave the pad in that direction. A net that has a pad with 0 exits at its width, but
   at least 1 at the board minimum, goes into the narrow-first `necked` path. That path skips the full-width pass
   and its retries. The check takes 0.1-0.7 s per call.
7. **KRT detailed routing.** The routing passes run as before: isolation members first, then necked nets, then
   the wider nets. They use the net-order retries (`KRT_VARIANTS`), the neck-down (`HWFLOW_NECKDOWN`), and the seed
   route for corridor nets (`HWFLOW_SEEDROUTE`). The route groups are split by region and cached (`regionroute`).
   After routing, DRC repair (`repair.py`) and pruning of dangling vias and tracks (`prune.py`) run. Nets with
   `nets.<n>.min_width_mm` never neck below that width.
8. **Pour stitch** (`pourstitch.py`, `HWFLOW_POUR_STITCH=1`, off by default). The router never routes a pour net,
   so a GND pad reaches GND only through the fill of its own layer, and the layers join only at through-hole pads
   and vias. Tracks cut the outer fill into islands around single pads, and a plane with no via floats. Before the
   repair, every pour group other than the main one (most pads), and every pad no fill touches, is joined by the
   first of: a via in the group's fill, a via in an exposed pad (no paste, or two vias wide), a stub of at most
   2.5 mm to a via that lands in the main fill, and once more the same stub where it displaces at most 3 routed
   nets (never isolation members or layer-rule nets), which are routed again within the repair budget. Every via
   and stub keeps the clearances, the isolation distances and the rule areas; a round is kept only with fewer DRC
   errors and no more errors of any type other than unconnected.

## Roads first and the layout portfolio (both off by default)

**Roads first** (`layout/route/roads.py`, a prototype). The "roads" are the nets the rules constrain: isolation
members, `layers` nets, region nets, `min_width_mm`, `impedance_ohm`, tracks of at least 2 x the board track. Each
road with fixed endpoints is planned on the fixed copper (the seed-route search, `seedroute.route_net`) to
max(clearance, creepage) of its isolation group (`roads.Model`). `HWFLOW_ROADS=route` (or `placement: {roads: route}`)
puts the planned copper of the isolation, layer, region and min-width roads on the board before the router
(`roads.preseed`, recorded as seed copper); a road that the final DRC drops (`seedroute.drop_on_drc(copper_only=True)`)
goes back to the router as a normal net (`roads.reroute`). `HWFLOW_ROADS=1` also reserves the roads in placement
(rule areas with exempt parts, zoning feedback). Measured on ht4 seed 11: the four mains nets route in 5 s instead
of 4-7 min and the board routes clean (`docs/asic-ideas.md`, "Roads first"); the reservations gave no measured gain.

**Layout portfolio** (`pipeline/portfolio.py`, `HWFLOW_PORTFOLIO=1`). `layout --auto` keeps the sequential attempts
described above unless `HWFLOW_PORTFOLIO=1`. With it, attempt 1 is a portfolio: the parts are placed once (placer
seeds seed0 .. seed0+3, `build/auto/p_place/`), then several routing strategies run on that placement at the same
time, each in a process session of its own (`build/auto/p_<name>/`; the kept one is copied to `build/auto/a1/`):

| strategy | what differs from base |
|---|---|
| `base` | nothing |
| `preseed` | `HWFLOW_ROADS=route` |
| `roads` | `HWFLOW_ROADS=1` (places for itself, while the shared placement runs) |
| `order_inside_out`, `order_mps_reverse` | the net-order retries start from another `KRT_VARIANTS` entry (`HWFLOW_KRT_ROTATE=1`, `2`) |

A strategy that cannot differ from `base` on the design is left out; `portfolio.SELECT` hooks may prune the list.
After the first converged strategy (0 unrouted, 0 DRC errors, 0 placement violations) no other starts and the
running ones get 1.5 x the winner's time. The kept board is the best by `portfolio.score` (converged, vias, track,
routing time, strategy order). With none converged, the sequential attempts 2..n follow; in `layout_auto` they run
with the best strategy's knobs, in `hwflow build` (one stage per attempt) with the default knobs. A design with
`board.optimize_area` runs `base` first, alone. REPORT.md has a `layout portfolio` table. The strategy knobs are
layout settings, so they are in the route cache keys.

Why it is off (measured on an 8-core laptop, `scripts/portfolio_bench.py`, `docs/asic-ideas.md` "Layout
portfolio"): no strategy converged where `base` did not, on any design; on ht4 s1 and s11 the portfolio took 2.3-2.6 x
the wall time of the sequential attempts to a converged board (2.5-3.3 x the CPU, about 1.4 GB per lane), and it
never gave a better board. The converged boards came from the fallback to other placement seeds: seed diversity, not
strategy diversity, fixed ht4. On sensor_preamp (area search, base first) it was within 10 % of sequential.

## Escape-aware placement (prototype, off by default)

`layout/place/escape.py`, three switches (each also `placement: {escape, escape_infl, escape_rot: true}`):

1. **Escape slots** (`HWFLOW_PLACE_ESCAPE=1`, weight `HWFLOW_PLACE_ESCAPE_W`, default 10). Per part, once, in
   footprint coordinates: for each pad whose net leaves the part, 4 axis slots, each the box a track of the net's
   width needs from the pad centre to via_dia + clearance beyond the pad hull, grown by the clearance. A slot that a
   foreign pad of the same part enters is dropped (own-blocked). At a placement a slot is blocked by 3 x the share of
   it that foreign copper covers: other parts' pads on a shared layer (grown by the isolation distance), other parts'
   slots of another net (half), rule areas without tracks, the board outside the copper edge. A pad costs its least
   blocked slot; the term is incremental (`Placer._update` / `_undo`) and acts in the detailed moves and the anneal.
2. **Demand** (`HWFLOW_PLACE_ESCAPE_INFL=1`). The density feedback of the global placement adds 2 x the slot depth of
   wire per escaping pad to the RUDY demand, and inflates a unit by 1 + the share of its pads without a free slot. The
   part cost adds a channel check: the pads that leave through one side need ceil(n / 2) pitches of free room in front
   of it (to the nearest foreign pad or the board edge); the shortfall in pitches is added.
3. **Forward check** (`HWFLOW_PLACE_ESCAPE_ROT=1`). Before the global placement each movable part's 4 rotations are
   checked: the courtyard fits its region box, an `edge` part keeps a free slot for every pad on its edge line, a part
   with pads of a net region (e.g. mains) turns them towards that region. Pruned rotations are never tried; a part
   with none left is printed (`[place] forward check: ... has no legal rotation`) and recorded in `escape.FORWARD`.

Measurements: "Escape-aware placement" in `docs/asic-ideas.md`.

## Fast preset

`HWFLOW_FAST=1` (settings `layout.fast`, off by default) turns on, in one variable, the switches chosen for the timed
end-to-end run (integration branch `fast-flow`; measurements of each in `docs/asic-ideas.md`, "Escape-aware
placement" and "Routable first pass", and step 8 for the pour stitch):

| switch | value under the preset |
|---|---|
| `HWFLOW_POUR_STITCH` | `1` |
| `HWFLOW_PLACE_ESCAPE` | `1` (step 1, the slot term; `_INFL` and `_ROT` stay off) |
| `HWFLOW_ROADS` | `route` (roads preseeded and routed first; no reservations in placement) |

A switch set explicitly wins over the preset (`HWFLOW_FAST=1 HWFLOW_POUR_STITCH=0` runs without the stitch). The
preset sets the settings, so the route cache key and `status.json` `settings` show the resolved values. The layout
portfolio stays off.

Why `roads=route` and not `firstpass=roads`: on home-thermostat-4 seeds 1, 5, 9, 13, 17, 21 the preseed-only variant
converged at attempt 1 on 5 of 6 seeds with a mean of 353 s wall / 719 s CPU, against 4 of 6 and 604 s / 1274 s with
firstpass roads (reservations in placement). The placement screen (`HWFLOW_SCREEN`) cost about 3x the CPU for no extra
reliability and stays opt-in.

## Switches

| switch | effect |
|---|---|
| `HWFLOW_ASIC_MACRO=0`, `_DENSITY=0`, `_DP=0`, `_ANNEAL=0`, `_NECK=0` | turn one stage off, for debugging only. The route cache keys include them. |
| `HWFLOW_FLOW=legacy`, or `placement: {flow: legacy}` in design.yaml | plain annealing from a random start plus the refine pass, with no pin-access hint. **This is a temporary escape hatch, to be removed after two more routed designs confirm this flow.** |
| `HWFLOW_SEED0` | the first placer seed of `--auto` (attempt k uses seed0 + 4k) |
| `HWFLOW_NECKDOWN=0`, `HWFLOW_SEEDROUTE=0` | router fallbacks off |
| `HWFLOW_SETTLE=0` | the settle legalizer off (debugging) |
| `HWFLOW_ROADS=route` or `1` (or `placement: {roads: ...}`), `HWFLOW_ROADS_POWER=1` | roads first: the planned roads routed first, and with `1` also reserved in placement; `_POWER` adds every power-class net (experiment) |
| `HWFLOW_KRT_ROTATE=k` | the router's net-order retries start from `KRT_VARIANTS[k]` |
| `HWFLOW_FAST=1` | the fast preset: `HWFLOW_POUR_STITCH=1`, `HWFLOW_PLACE_ESCAPE=1`, `HWFLOW_ROADS=route` (above); an explicit switch wins |
| `HWFLOW_PLACE_ESCAPE=1`, `_INFL=1`, `_ROT=1`, `_W`, `_CONN` (or `placement: {escape, escape_infl, escape_rot: true}`) | escape-aware placement steps 1-3 (prototype, off; above) |
| `HWFLOW_POUR_STITCH=1` | pour stitch after routing (step 8): split GND pours and pads in pour holes joined by vias and stubs |
| `HWFLOW_PORTFOLIO=1`, `HWFLOW_PORTFOLIO_WORKERS`, `HWFLOW_PORTFOLIO_GRACE` | the layout portfolio as attempt 1 of `--auto` (off by default); strategies at once (default CPU count - 2); the grace factor (default 1.5; `inf` waits for all, a reproducible choice) |
| `HWFLOW_ROUTE_ILLEGAL=1` | route an illegal placement (debugging only) |
| `HWFLOW_FIRSTPASS=1` or a list of `roads,hot,quick` (or `placement: {firstpass: ...}`), `HWFLOW_FIRSTPASS_QUICK_S`, `HWFLOW_FIRSTPASS_HOT_W` | routable first pass (experiment, off): roads reserved and routed first, the window cuts as a local placer penalty, a quick truncated route that drives a local placement fix (`layout/route/firstpass.py`; measurements in `docs/asic-ideas.md`, "Routable first pass") |
| `HWFLOW_ROUTAB_PREDICT=0` | the routability prediction and its run log off |
| `HWFLOW_ROUTAB_RANK=1`, `HWFLOW_ROUTAB_FEEDBACK=1`, `HWFLOW_ROUTAB_RETRY=1` (or `placement: {routability_rank, routability_feedback, routability_retry: true}`) | the prediction-driven options (off by default). The route cache key includes them (settings group `layout`). |

Regression corpus: `tests/fixtures/layout_corpus/` (README there) holds cutouts of real placed boards (`hwflow
cutout`) with their measured outcome; `tests/test_layout_corpus.py` checks the pre-routing estimates in the fast tier
and routes every case in the slow tier.

`placer.STAGES` records each placement stage (seconds, HPWL, overflow, overlap), and `asicflow.FEEDBACK` records
each density feedback. `scripts/asic_flow_bench.py` measures the placement proxies of the flow and its ablations.

## Calibration and measurements (routability branch, October 2026)

**Legality** (`scripts/legal_bench.py`, 2 seeds x 10000 iterations; usable fill = placer courtyards over the area
inside the board margin; violations without settle -> with it): sensor_preamp 18 x 13 (0.79) 0 -> 0;
home-thermostat-3 and -4 (0.89) 0 -> 0; synth60 x0.55 (0.87) 1 -> 0 on seeds 1 and 5; synth150 x0.6 (0.89)
22 -> 0. Over-full boards stay illegal and get the architecture verdict: sensor_preamp 16 x 12 (0.99) 2 -> 2,
12 x 9 (1.97) 8 -> 8; synth60 x0.5 (1.07) 20 -> 20, x0.45 (1.34) 18 -> 18; synth150 x0.55 (1.07) 92 -> 92,
x0.5 (1.31) 138 -> 129. Settle inside each seed (before `unstick`) made x0.45 worse (44), so it runs once, after.

**Prediction against KRT** (`scripts/routab_bench.py`, `scripts/routab_eval.py`; one fixed placement per case,
the current routing flow with repair; 13 cases on 6 designs, plus the R8 experiment below in the per-net counts).
The thresholds were chosen on 6 cases (sensor_preamp, synth24, synth60, synth60 x0.85, home-thermostat-4 seeds 1
and 11) and kept for the other 7.

| case | layers | hard nets | max ratio | verdict | open signal nets | unrouted | DRC |
|---|---|---|---|---|---|---|---|
| sensor_preamp 18x13 | 2 | 0 / 6 | 1.3 | routable | 0 | 0 | 0 |
| synth24 | 2 | 0 / 8 | 2.0 | routable | 0 | 0 | 0 |
| synth24 x0.7 | 2 | 0 / 8 | 2.0 | routable | 0 | 0 | 0 |
| home-thermostat-3 | 4 | 2 / 39 | 3.6 | marginal | 0 | 0 | 0 |
| home-thermostat-4 seed 1 | 4 | 2 / 39 | 3.6 | marginal | 1 | 1 | 1 |
| home-thermostat-4 seed 11 | 4 | 2 / 39 | 3.6 | marginal | 1 | 1 | 1 |
| home-thermostat-4, 2 layers | 2 | 2 / 39 | 3.6 | marginal | 1 | 3 | 3 |
| home-thermostat v1 | 4 | 7 / 42 | 4.8 | marginal | 8 | 15 | 49 |
| home-thermostat v1, 2 layers | 2 | 7 / 42 | 4.8 | marginal | 7 | 19 | 38 |
| synth60 | 2 | 47 / 77 | 8.0 | not routable | 23 | 31 | 31 |
| synth60 x0.85 | 2 | 47 / 77 | 8.0 | not routable | 20 | 28 | 28 |
| synth60 x1.3 | 2 | 47 / 77 | 8.0 | not routable | 18 | 22 | 22 |
| synth60, 4 layers | 4 | 47 / 77 | 8.3 | not routable | 18 | 25 | 25 |

What it predicts: the clean boards and the hopeless ones apart (every `routable` case routed clean, every `not
routable` case left 18+ signal nets open); Spearman 0.96 between the hard-net share and the open signal nets (0.85
against unrouted + DRC). Per net (609 nets, 16 % open): hard (ratio >= 3) precision 0.44, recall 0.95; pad access
<= 1 exit precision 0.40, recall 0.92. What it does not: the `marginal` class holds both a clean board and boards
with 1-15 open nets, and the layer count does not change the prediction (the other layers count only up to the via
sites near the pads), while 2 layers did route worse. The honest global router (`global_overflow`, built on the
guide-driven router's PathFinder, which was not adopted, so it is not on main) found almost no overflow on the failing
boards (precision 0.21, recall 0.05 per net): g-cell capacity does not predict KRT, as in
docs/asic-ideas.md. The exact bounds never fired on these cases (worst line ratio 0.20, ring 0.39): they prove only
gross over-subscription. The corridor margin flagged one failure (AC_ROOM1, 0.15 mm on seed 11); with R8 moved
0.8 mm the margin rises to 0.73 mm and AC_ROOM1 still stays open, so the margin is listed, not a hard-net rule.
synth60's failure is caught earlier by `hwflow check` (its +3V3 0.4 mm track cannot leave the QFN pads).

**Builds before (asic-router) and after** (`hwflow build --until layout --auto --attempts 2 --mc 0`; "all" adds
`HWFLOW_ROUTAB_RANK=1 FEEDBACK=1 RETRY=1`; wall times on a shared, loaded machine vary by up to 2x):

| design | run | attempts | wall s | route s | unrouted | DRC | vias | track mm | requirements |
|---|---|---|---|---|---|---|---|---|---|
| sensor_preamp | before / after / all | 1 / 1 / 1 | 737 / 772 / 333 | - | 0 | 0 | 3 | 45.1 | 7/7 |
| sensor_preamp 13 x 10 (over-full) | before / after | 2 / 1 | 757 / 22 | 198 / 0 | 10 / - | 17 / - | 0 / - | 19.6 / - | illegal board routed / stopped: fill 157 %, a board of at least 17 x 13 mm |
| synth24 | before / after / all | 1 / 1 / 1 | 105 / 111 / 145 | 38 / 52 / 53 | 0 | 0 | 4 | 130.1 | - |
| home-thermostat-4 | before / after / all | 2 / 2 / 2 | 1595 / 1228 / 880 | 355 / 256 / 204 | 0 | 0 | 73 | 1197.1 | 35/35 |
| synth60 (`--force`) | before / all | 1 / 1 | 2123 / 926 | 1859 / 519 | 28 / 36 | 28 / 36 | 91 / 100 | 1073 / 1100 | - |

The legality gate turns an over-full board from 13 minutes of routing an invalid placement into a 22 s verdict. On
the routable designs the options changed nothing in the result (same boards). The retry policy cut synth60's route
time 3.6x but left 8 more nets open (the board fails either way, back to parts), so the options stay off.

## Removed after the experiments

The following were removed from the code (see `docs/asic-ideas.md` for the measurements):
- the PathFinder global router (`globalroute.py`), used as a density source, as a net order, and for layer hints;
- the pin-access placer term and the pin-access net order;
- the separate experiment switches `HWFLOW_ANALYTIC`, `HWFLOW_ANALYTIC_DP`, `HWFLOW_RUDY_PADS` and
  `HWFLOW_MACRO_FIRST`.
