# Real-board layout benchmark (PCBWorld D3-A)

The layout benchmark (`scripts/layout_bench.py`) also runs on real open-source boards. The boards are the D3-A set (99 small boards) of [PCBWorld](https://github.com/LGAI-Research/PCBWorld) (LG AI Research, arXiv 2607.05915, BSD-3), which takes them from the [PCBench](https://github.com/PCBench/PCBench) corpus (MIT; each board keeps the license of its source repository, recorded in `pcbench.json` per board and in the manifest). No board file is in this repository. `prep` clones both repositories at pinned commits into `build/bench/pcbench/ext/` and rebuilds the set there.

```
uv run python scripts/pcbench.py prep                                   # ~3 min: clone, KiCad conversion, DRC filter, import
uv run python scripts/layout_bench.py --pcbench-only --pcbench-mode both \
    --compare evals/bench/pcbench/baseline.json                         # ~70 min (route ~6, place ~60)
uv run python scripts/layout_bench.py --pcbench                         # route mode beside the usual design set
uv run python scripts/pcbench.py prep --set d3b                         # D3-B (10 medium boards) -> designs_d3b/; d3c: hard
uv run python scripts/layout_bench.py --pcbench-only --pcbench-set d3b --pcbench-mode place --workdir build/bench/w_b
uv run python scripts/layout_bench.py --pcbench-only --pcbench-set d3b --pcbench-mode replay --placements build/bench/w_b
```

`--pcbench-set d3a|d3b|d3c` picks the set (default d3a). `replay` takes the attempt-1 placement of an earlier place-mode
run (`--placements` its work dir; `--placement-attempt kept` for the kept one), fixes every part there and routes it:
the router alone on hwflow's placement, for router A/B runs (docs/research/router-ab.md).

`prep` needs `kicad-cli` and KiCad's `pcbnew` Python module in the system interpreter (`PCBNEW_PYTHON`, default `/usr/bin/python3`). PCBWorld's scripts run there in a child process, never in the hwflow venv.

## Import

1. PCBWorld's own chain, steps 0 and 1 (`tools/datagen/pcbench_prep`), converts each board to the current format and keeps only the boards whose original routing passes KiCad DRC under their own rules, so every kept board is routable. PCBWorld pins KiCad 9.0.8; with KiCad 10, 95 of the 99 boards pass (out: 0008, 0036, 0078, 0080). PCBWorld's published fair-95 list is close to this set but not identical.
2. `hwflow.layout.board.kicad_import.import_board` (the importer of the oshw corpus cases) turns each board into a design: every part `fixed` at its original position, net classes as net widths and clearances, the outline as floorplan cut regions (non-rectangular outlines included), and `free`, the parts a placer may move (everything but connectors, switches and mechanical parts). The board's own copper and the HPWL of its placement are the human reference in `pcbench.json`.

PCBWorld's step 2 (per-net guide widths) parses KiCad 9 net ids and fails on KiCad 10 files; it is not run, and the net classes give the widths.

## Two modes

**route** (`pcb:<id>`): every part at the human placement, so it measures the router and the repair loop. A human placement can break hwflow's placement rules, so it is routed anyway (`HWFLOW_ROUTE_ILLEGAL=1`, as in the oshw cases). The metrics follow PCBWorld's RQ2 table, and the printed table puts hwflow beside its published KiCadRoutingTools and Freerouting rows:

| metric | rule |
|---|---|
| rout. | share of the required pad-to-pad connections that are routed, mean over all boards (a timeout counts 0) |
| clean | share of boards fully routed with DRV 0 |
| DRV | DRC errors other than unconnected, mean over the fully routed boards. An error that names fixed parts only (courtyards, a pad at the board edge, pad to pad in one footprint) comes with the original placement: it is counted as `fixed_errors`, not as DRV (`repair.fixed_error`) |
| WL, vias | track length (mm) and vias, mean over the fully routed boards |
| time_s | the whole layout stage per board (router, DRC, repair). PCBWorld's time is router time only: compare it with `router` |

**place** (`pcbp:<id>`): the `free` parts are released (no x, y, rot), and the rest stay at the human placement. The whole flow places and routes; the result is compared with the human board.

| metric | rule |
|---|---|
| legal | share of boards whose placement has no violation that names a released part |
| inherited | boards whose only violations are between fixed parts (from the human board, typically a connector pad within 0.3 mm of the edge or overlapping connector courtyards): not counted against the placer. Every board is routed (`HWFLOW_ROUTE_ILLEGAL=1`), so these still give routability and DRV |
| rout., clean, DRV | as in route mode; a board with a placer violation is never clean |
| HPWL, track length | hwflow / human, median over the clean boards. HPWL is over pad centres, the human copper from `kicad_import.human_copper` |
| vias | hwflow minus human, median over the clean boards |

## Limits

- hwflow's own 0.3 mm copper-to-edge clearance and its placer courtyard margin apply. Human boards often put pads closer to the edge, and many D3-A boards are small and dense (courtyards with the placer margin above 100% of the board face). Placer violations on those boards say that hwflow's rules are stricter than the human design, not only that the placer is weak.
- Keep-out zones, graphic copper and non-ground zones are not carried over.
- One run per board and mode. The set (95 boards) is the spread; per-board numbers move between runs.

`manifest.json` in the work dir gives the reason for every skipped board. `baseline.json` holds both summaries and the per-board rows of the last accepted run.
