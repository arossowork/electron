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

## Switches

| switch | effect |
|---|---|
| `HWFLOW_ASIC_MACRO=0`, `_DENSITY=0`, `_DP=0`, `_ANNEAL=0`, `_NECK=0` | turn one stage off, for debugging only. The route cache keys include them. |
| `HWFLOW_FLOW=legacy`, or `placement: {flow: legacy}` in design.yaml | plain annealing from a random start plus the refine pass, with no pin-access hint. **This is a temporary escape hatch, to be removed after two more routed designs confirm this flow.** |
| `HWFLOW_SEED0` | the first placer seed of `--auto` (attempt k uses seed0 + 4k) |
| `HWFLOW_NECKDOWN=0`, `HWFLOW_SEEDROUTE=0` | router fallbacks off |

`placer.STAGES` records each placement stage (seconds, HPWL, overflow, overlap), and `asicflow.FEEDBACK` records
each density feedback. `scripts/asic_flow_bench.py` measures the placement proxies of the flow and its ablations.

## Removed after the experiments

The following were removed from the code (see `docs/asic-ideas.md` for the measurements):
- the PathFinder global router (`globalroute.py`), used as a density source, as a net order, and for layer hints;
- the pin-access placer term and the pin-access net order;
- the separate experiment switches `HWFLOW_ANALYTIC`, `HWFLOW_ANALYTIC_DP`, `HWFLOW_RUDY_PADS` and
  `HWFLOW_MACRO_FIRST`.
