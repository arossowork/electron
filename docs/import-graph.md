# Packages and import contracts of `hwflow`

The engine is a modular monolith: bounded-context packages with enforced layers (issue #5, ADR 0001 section 1).
`uv run lint-imports` checks the contracts in `pyproject.toml` and runs in CI (job `lint`). `uv run python
scripts/import_graph.py` prints the module graph (module-level and lazy imports) and its cycles; re-run it after a
refactor.

## Packages

Layers, from the top (a package imports only from packages below it):

| Package | Content | Knows |
|---|---|---|
| `hwflow.pipeline` | stages, `spec` (StageSpec, cache keys, stage cache), `exec` (execute_stage in a scratch dir), runner, status, report, trace, hooks (`hwflow.hooks` entry points) | everything below |
| `hwflow.verify` | `postlayout` (extraction, post-layout SPICE), `em` (openEMS), `checks` (the whole `hwflow check`), `blocks` (block authoring: `extract`, `verify`) | layout, sim, core |
| `hwflow.layout` | `checks` (layout part of `hwflow check`), `feasibility` (registry of pre-layout proofs), `diagnose`; sub-packages `route`, `place`, `board` | sim, core |
| `hwflow.sim` | `spice`, `simrobust`, `optimize`, `budgets` | core |
| `hwflow.schematic` | `sch` (KiCad schematic writer) | core |
| `hwflow.core` | `design`, `units`, `sexpr`, `libs`, `parts`, `blocks` (library, bindings, copper), `floorplan` (the `floorplan:` field as data), `geometry` (Part, Rigid, rot, copper layers), `settings` | nothing |
| `hwflow.fw` | HW/SW contract to firmware headers and skeletons | core only |

`sim` and `schematic` are siblings and do not import each other. `hwflow.cli` and `hwflow.commands` sit above
`pipeline`; nothing below imports `cli`. `hwflow` never imports `hwharness`, `hwplatform`, `ui`, `evals`.

Inside `hwflow.layout`, from the top:

| Sub-package | Content | Knows |
|---|---|---|
| `diagnose` | layout diagnosis | everything below |
| `checks`, `feasibility` | layout checks of `hwflow check`; proof registry (modules loaded by name) | route, place, board |
| `route` | `krt` (KiCadRoutingTools and Freerouting drivers, retries, necked routing), `seedroute`, `regionroute`, `repair`, `prune` | place, board |
| `place` | `placer`, `gplace`, `asicflow` (the layout flow), `floorplan` (early check, report), and the geometric pre-route proofs the placer uses: `corridor`, `barrier`, `cutcap`, `padiso`, `partescape`, `pinaccess` | board |
| `board` | `pcb` (board writer and reader, net classes, isolation rules), `routing` (rule areas, widening: edits to a board file), `keepout` (footprint keep-out DRC), `kicad` (kicad-cli) | core |

### Where the placement differs from the first plan

- `budgets` is in `sim` (it solves resistor networks and is read by the placer proofs, `postlayout` and the check), not
  in `layout`.
- `corridor`, `barrier`, `padiso`, `partescape`, `pinaccess` are in `layout/place`, not `route`: the placer calls the
  corridor proof, and the proofs read the floorplan, the keep-outs and `Part`. Router drivers are in `route`.
- `routing.py` (board-file edits around each router pass) and `keepout.py` (DRC supplement) are in `layout/board`: the
  writer and the DRC wrapper use them, and the placer needs their constants.
- `floorplan.py` is split: the data model (`parse`, `validate`, `cuts`, `outline`, regions, conductive keep-outs) is
  `core/floorplan.py`, the check that places parts (`check`, `report`, `run`, `fit_anywhere`) is `layout/place/floorplan.py`.
- `pcb.py` is split: the router drivers (`run_krt`, `krt_route`, `route_class`, `import_ses`, `router_version`,
  `KRT_VARIANTS`, ...) are `layout/route/krt.py`.
- `design.check` is split: netlist and library checks stay in `core.design.netlist_findings`, layout checks are
  `layout/checks.py`, and `verify/checks.py:check` runs them with budgets, feasibility, decoupling, blocks and the SPICE
  checks in the old order. SI parsing is `core/units.py` (`design` re-exports it).
- `blocks.py`: `extract` and `verify` (they need the board reader, the placer, KiCad and the extraction) are
  `verify/blocks.py`; everything `design.load`, the placer and the board writer use stays in `core/blocks.py`.
- `fw` is a package (`hwflow/fw/__init__.py`), so `hwflow.fw` keeps its API without an alias.

## Cycles

Module-level imports have no cycle. Lazy (function-level) imports used to tie 18 modules into one group; that group and
every cross-package cycle are gone: `design` no longer imports layout or sim, `pcb`/`placer`/`floorplan`/`blocks`
share `core.geometry` and `core.floorplan`, the private names other modules reached into are public
(`repair.parse_item`/`thermal_keys`, `postlayout.ellipk`/`zone_fills`/`edge_dist`, `sim.budgets.leg_r`,
`spice.subst_nets`/`netlist_cache_key`/`NUM_PATTERN`, `simrobust.leak_ohm`, `corridor.corridor_nodes`/`corridor_connects`/
`corridor_finding`, `barrier.barrier_nodes`/`barrier_forced`/`barrier_label`, `keepout.rule_areas`/`node_points`,
`placer.part_pad_box`, `design.natkey`), `RHO_CU` lives in `sim.budgets`, `ESCAPE_PINS` in `partescape`.

Still cyclic through lazy imports, each inside one package (the layer contracts allow it):

- `layout.place`: `asicflow` <-> `gplace`, `asicflow` <-> `placer` (the placer runs the flow stages, the stages use
  the placer's cost model), and through them `barrier`, `corridor`, `pinaccess`.
- `sim`: `spice` <-> `simrobust` (`run_analysis` hands a non-converging or scoped analysis to `simrobust`).

## Contracts (`pyproject.toml`, `[tool.importlinter]`)

1. `hwflow` imports nothing from `hwharness`, `hwplatform`, `harness`, `ui`, `evals`.
2. No engine package imports `hwflow.cli`.
3. Layers: `pipeline` > `verify` > `layout` > `sim | schematic` > `core`.
4. `hwflow.fw` imports none of sim, schematic, layout, verify, pipeline, commands, cli.
5. Layout layers: `diagnose` > `checks | feasibility` > `route` > `place` > `board`.
6. `pipeline` and `commands` do not import `cli`.
7. The contract modules the platform imports (`hwflow.artifacts`; `hwflow.ids`, `hwflow.runs` with #7) are
   stdlib-only: no engine package, no yaml/pydantic. They sit outside the layers (any layer may import them).

There are no `ignore_imports`. A layer violation is fixed by moving the shared code down, not by an exception.

## Compatibility aliases (remove by 2026-12-31)

The old flat module paths still import: `hwflow/<name>.py` is a three-line module that replaces itself in
`sys.modules` with the moved module, so `import hwflow.pcb as p` gives `hwflow.layout.board.pcb` itself and
`monkeypatch.setattr(hwflow.spice, ...)` acts on the real module. Names that moved out of a module are copied back
onto it by its alias (`pcb.krt_route`, `blocks.extract`/`verify`, `design.check`). The engine does not import through
the aliases (`tests/test_import_shims.py`). Remove them after the M1 release, no later than 2026-12-31.

| Old path | New module |
|---|---|
| `design` `sexpr` `libs` `parts` `blocks` `settings` | `core.<name>` |
| `spice` `simrobust` `optimize` `budgets` | `sim.<name>` |
| `sch` | `schematic.sch` |
| `pcb` `kicad` `routing` `keepout` | `layout.board.<name>` |
| `seedroute` `regionroute` `repair` `prune` | `layout.route.<name>` |
| `feasibility` `diagnose` | `layout.<name>` |
| `placer` `gplace` `asicflow` `floorplan` `cutcap` `corridor` `barrier` `padiso` `partescape` `pinaccess` | `layout.place.<name>` |
| `postlayout` `em` | `verify.<name>` |

## CI

`.github/workflows/ci.yml`: `lint` (ruff, lint-imports) and `test` (`pytest -m "not slow"`, with KiCad 10 and ngspice
installed on the runner) on push and pull request to main. The nightly `slow` job runs `pytest -m slow`; the router
(`tools/KiCadRoutingTools`) and Freerouting are not on the runner, so tests needing them skip, and "no tests ran" (exit
5) is not a failure. `scripts/pre-commit` runs the same fast tier locally.
