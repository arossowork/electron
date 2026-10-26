# Layout regression corpus

Small standalone projects cut out of real placed boards (the keyboard v2 and the home-thermostat runs) and a few
synthetic or deliberately over-full boards. They check placer, routability-predictor and router changes in seconds
(fast tier) or minutes (slow tier) instead of a multi-hour build. Each case has:

| file | content |
|---|---|
| `design.yaml` | the cut design: every part `fixed: true` at its source placement, moved to the window origin; nets, net classes, isolation groups, floorplan regions clipped to the window, keep-out footprints, rules, blocks, budgets whose parts are all kept |
| `lib/` | the project-library symbols and footprints the case uses (KiCad and `library/` parts come from the normal search path) |
| `cutout.yaml` | source project and placement, selection, window and offset (source board mm), border terminals (`TPB*`: net, nearest kept pad, nearest dropped pad), `free` (parts the source placer could move), neighbours the window cut through (dropped) |
| `expected.yaml` | the measured outcome on main: `fast` (check, placement violations, corridors, RUDY, pin access), `replace`, `route` (routed result of `hwflow layout`), `tiers`, and `source`/`reproduces` notes |

No build outputs live here: every run works in a scratch copy.

## Running

```
uv run pytest -q tests/test_layout_corpus.py -m "not slow"   # fast tier: no router
uv run pytest -q tests/test_layout_corpus.py -m slow          # dense-case checks and real KRT routing of every case
uv run python scripts/layout_corpus.py summary [CASE ...] --jobs 6   # table: case, expected, actual, time
```

The slow tier routes all cases in parallel (`LAYOUT_CORPUS_JOBS`, default cpu/4; `LAYOUT_CORPUS_TIMEOUT` per case,
default 5400 s). A case fails when it gets worse than `expected.yaml`: more unrouted nets, DRC errors or placement
violations, or not ok where it was ok. Counts above 0 get a slack of max(2, 15 %) (`slack()` in the script: the
repair loop is wall-time bounded, so results vary under load); 0 counts, clearance/creepage DRC and isolation rows are
strict. A better result is printed and passes. `summary` exits 1 on a regression and
`--json FILE` writes the rows, so another branch can compare against the same table.

## Adding a case

```
uv run hwflow cutout <project> --out tests/fixtures/layout_corpus/<case> --region <name | x0,y0,x1,y1> \
    [--parts 'U1,C*'] [--nets 'USB_*'] [--placement <placement.json>] --name <case>
uv run python scripts/layout_corpus.py measure <case> --fast --route --write
```

Then add `source` (the original failure, with its numbers) and `reproduces` (does the cutout show the same failure
mode, measured) to `expected.yaml`. Drop the case when it does not reproduce and is not a control. Keep a case small
(tens of parts, a few hundred kB). `hwflow cutout` takes the parts centred in the region, plus every part fully
inside the window (bounding box + 3 mm frame); a net that leaves the window ends on a fixed test-point pad on the
border where the line from its nearest kept pad to its nearest dropped pad crosses it. The frame grows by 2 mm (at
most 3 times, without taking more parts in) until every terminal meets the clearance, isolation and region rules.

## Updating expected.yaml

When a change improves a case (fewer unrouted nets, DRC errors or violations), record it:

```
uv run python scripts/layout_corpus.py measure <case> --route --write     # routed outcome
uv run python scripts/layout_corpus.py measure <case> --fast --write      # after a deliberate change of the estimates
```

The placement legality gate (docs/layout-flow.md) stops a layout on a placement with violations before the router:
`kb_audio`, `kb_audio_dig`, `kb_hub` and `sp_overfull` (all fixed parts, violations in the cut placement) record that
stop (`route.blocked`, `route.placement_verdict`: architecture), with main 30fa4c4's routed numbers kept under
`route_routed` (`HWFLOW_ROUTE_ILLEGAL=1` routes them that way).

Commit the new `expected.yaml` with the change that caused it, and say in the commit message which numbers moved.
Routing times are wall times on a shared machine (load noted in `expected.yaml`); they are not asserted.
