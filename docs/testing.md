# Testing

The engine tests are deterministic and never call an LLM. They run with pytest. The agent evaluations in `evals/` are a
separate thing (see "Evals harness").

## Tiers

| tier | command | runtime | what it runs |
|---|---|---|---|
| fast | `uv run pytest -q -m "not slow"` | 6 min 41 s (518 tests; the layout corpus adds 71 s) | everything except tests marked `slow` |
| slow | `uv run pytest -q -m slow` | 50 min (37 tests; routing the layout corpus is 35 min of it) | end-to-end builds and router or DRC runs over 15 s |
| full | `uv run pytest -q` | about 57 min | both tiers; run it before a merge |

`testpaths` is `tests` and `harness/tests` (root `pyproject.toml`). The platform service has its own pytest config and
its tests run from `services/platform` (`testpaths = ["tests"]`), not from the root command.

External tools. A test that needs one skips when it is missing, so a bare checkout still passes:

| tool | needed by | skip guard |
|---|---|---|
| `kicad-cli` and the KiCad libraries | DRC, ERC, schematic and board export, full builds | `HAVE_KICAD`, `tools.kicad_cli` |
| `ngspice` | SPICE, Monte Carlo, post-layout | `HAVE_NGSPICE`, `HAVE_TOOLS` |
| KiCadRoutingTools (`tools/`) | KRT routing, repair, blocks | `needs_router` (checks `py_router/route.py`) |
| `gcc` | firmware host tests | `pytest.skip` in `tests/test_fw.py` |
| `node` | web UI script check | `pytest.skip` in `tests/test_ui.py` |

### CI and pre-commit

`.github/workflows/ci.yml` has three jobs:

- `lint`: `ruff check` on src, harness, tests, evals, ui, scripts, then `lint-imports` (contracts in `docs/import-graph.md`).
- `test`: installs KiCad 10 and ngspice, then runs the fast tier. The Freerouting jar and the KRT binary are not
  installed, so router tests skip.
- `slow`: nightly (03:17 UTC), on demand, and on pull requests that touch `src/hwflow`, `tests`, `examples`, `library`,
  `blocks`, `uv.lock`, `pyproject.toml` or the workflow. It installs Freerouting (Java 25) and runs `pytest -m slow`
  (exit code 5, nothing ran, is accepted). KRT tests still skip there.

`scripts/pre-commit` (copy it into the hooks folder to enable): when `src`, `tests`, `evals`, `ui` or `scripts` changed
it runs `ruff check`, `lint-imports`, and, when `src` or `tests` changed, `pytest -q -x -m "not slow"`.

## Test files

Counts are approximate (`def test_` per file). "Synthetic" means the test builds its board or design in code.
"Real" means it reads `examples/sensor_preamp` (the only folder in `examples/`) or another checked-in project.
Files with a `slow` mark are noted.

### Core and design

| file | covers | data |
|---|---|---|
| `test_core.py` | design model, board, nets, placement and routing primitives (36 tests; 2 slow) | mostly synthetic, some real |
| `test_design_save.py` | design edits round-trip through the YAML parser | synthetic |
| `test_floorplan.py` | floorplan field, arithmetic, quick check, placer region term | synthetic |
| `test_budgets.py` | requirement budgets: validation, track widths, measured copper | synthetic |
| `test_settings.py` | env names, defaults, call-time reads; env reads only in `settings.py` | none |
| `test_import_shims.py` | old module paths are aliases of the moved modules | none |
| `test_artifacts.py` | content-addressed store: atomic writes, hash check | synthetic |
| `test_min_width.py` | minimum track width rules | synthetic |

### Simulation (SPICE)

| file | covers | data |
|---|---|---|
| `test_simrobust.py` | convergence fallback, floating-node leaks, scoped analyses | synthetic and real |
| `test_sim_failure_reporting.py` | model failures stay bounded and distinct from a clean audit | synthetic |
| `test_em.py` | pure-Python parts of the optional openEMS stage | synthetic |

### Schematic

| file | covers | data |
|---|---|---|
| `test_sch.py` | generated schematics keep content on the sheet | synthetic |

### Layout: place, route, board

| file | covers | data |
|---|---|---|
| `test_placer.py` | RUDY congestion term, incremental placer cost (22 tests); 2 tests use the stored thermostat baseline | synthetic, baseline project |
| `test_layout_flow.py` | the flow in `docs/layout-flow.md`: macro stage, global placement, density feedback | synthetic |
| `test_corridor_placer.py` | corridor term of the placer | synthetic |
| `test_routing.py` | isolation keep-outs for a router pass, neck-down widening | synthetic |
| `test_router_rules.py` | per-net router rules (11 tests; 4 slow; needs router) | real |
| `test_regionroute.py` | hierarchical routing: partition, cache key, one real router run | synthetic, needs router |
| `test_seedroute.py` | seed route for nets the router leaves open | synthetic, needs router |
| `test_repair.py` | repair loop (needs router; one test needs `kicad-cli`) | real |
| `test_legality.py` | placement legality gate: `Placer.settle`, `legality.placement_verdict`, `stage_layout` stops before the router, `hwflow floorplan` fails on an illegal quick placement | synthetic |
| `test_routability.py` | routability predictor (report only): raster primitives, window cuts, exact bounds, verdict thresholds | synthetic |
| `test_roads.py` | roads first: classify, plan, reservation boxes, placer exemptions, preseed on the board | synthetic |
| `test_portfolio.py` | layout portfolio (off by default): score order, strategy pruning, shared placement, grace stop, fallback knobs, area-search order; stubbed attempts | synthetic |
| `test_repair_budget.py`, `test_repair_neighbors.py` | repair budget; freeing nearby copper | synthetic |
| `test_initial_route_budget.py` | initial routing keeps the search budget | synthetic |
| `test_prune.py` | removal of dangling router leftovers | synthetic |
| `test_partescape.py` | escape proofs of a single part (1 slow) | synthetic |
| `test_barrier.py`, `test_corridor.py`, `test_cutcap.py` | channel, corridor and cut-capacity proofs | synthetic |
| `test_cut_and_layers.py` | cut regions and per-net layers (12 tests; 2 slow) | real with edits |
| `test_keepout.py`, `test_padiso.py` | conductive keep-outs and pad isolation (keepout: 1 slow) | synthetic |
| `test_blocks.py` | layout blocks: binding, rigid placement, extraction (1 slow; needs router) | synthetic |
| `test_layout_guards.py` | guards from the thermostat-3 run (needs router) | real with edits |
| `test_layout_perf.py` | reproducible board text, route cache, parallel `--auto` attempts (stubs) | synthetic, stubs |
| `test_diagnose.py` | layout diagnosis and when the cycle stops | synthetic |
| `test_layout_corpus.py` | the layout corpus (below): pre-routing estimates, re-placement, the cutout extractor, two lesson tests; slow: dense checks and KRT routing of every case | real cutouts, open-hardware boards |
| `test_kicad_import.py` | KiCad board importer: rules, nets, back-side footprints, outline cuts, written pads equal the source, human copper | synthetic board |
| `test_oshw_corpus.py` | open-hardware cases: provenance, license file, NOTICE, size, index | `oshw_*` cases |

### Verify and post-layout

| file | covers | data |
|---|---|---|
| `test_postlayout.py` | transient netlist reduced to pads with DC resistances | synthetic |
| `test_drc_fast.py` | fast DRC path (needs `kicad-cli`) | real |
| `test_trace.py` | verification never promotes plans, stale files or simulation to bench proof | synthetic |
| `test_exec_relative.py` | `execute_stage` with relative paths (needs KiCad) | real |

### Pipeline

| file | covers | data |
|---|---|---|
| `test_pipeline.py` | end-to-end build of the reference design (2 slow; needs router) | real |
| `test_cold_equals_cached.py` | cold build equals a cached build (1 slow; needs KiCad, ngspice) | real |
| `test_stage_specs.py` | stage registry and under-declaration guard (1 slow) | real |
| `test_buildplan.py` | planner: stage order, `until`, hard-failure stop | synthetic |
| `test_runs.py`, `test_run_cmd.py` | run ids, state machines, manifest; `hwflow run status|wait` | real, synthetic |
| `test_status_concurrency.py` | concurrent writers of `build/status.json` | synthetic |
| `test_hooks.py` | engine works without `hwharness`; hook coupling | real |
| `test_ui.py` | web UI server logic, no browser, no `claude` process | synthetic |

### Firmware

| file | covers | data |
|---|---|---|
| `test_fw.py` | contract checks, generated headers, gcc host build (22 tests) | one small synthetic design; one test reads `projects/` |

### Harness and evals

| file | covers | data |
|---|---|---|
| `harness/tests/test_protocol.py` | product-person protocol and hooks (1 slow) | real |
| `harness/tests/test_evals_cards.py` | scorer parity: local adapter and platform adapter give identical cards | stored baseline in `evals/baselines/` |

### Platform service (`services/platform/tests`, 40 files, about 290 tests)

API, auth, RLS, runs, outbox, relay, workflows (Temporal), agent sandbox and tools, S3 storage, BYOK, crypto,
observability, OpenAPI contract, eval activities. They are separate from the engine tests and use fakes (`wf_fakes.py`)
or the compose services. Tests marked `integration` need Postgres, SeaweedFS (S3) and NATS and skip when those do not
answer. Temporal tests skip without `temporalio` or the dev server. See `docs/platform-dev.md`.

## Markers and fixtures

| marker | defined in | meaning |
|---|---|---|
| `slow` | root `pyproject.toml` | end-to-end builds and router or DRC runs over 15 s |
| `integration` | `services/platform/pyproject.toml` | needs compose Postgres, S3 and NATS |
| `anthropic_smoke` | `services/platform/pyproject.toml` | real Anthropic API; collected only with `-m anthropic_smoke` (or `HWPLATFORM_AGENT_SMOKE=1`) and a credential in the environment; never in CI |

Fixtures:

- `tests/conftest.py`: `_private_cas` (autouse, session). It points `HWFLOW_CAS_DIR` at a temporary folder so tests do
  not touch `~/.cache/hwflow/cas`. This is the only shared engine fixture; most files build their own boards and
  designs with local helpers and `tmp_path`.
- `services/platform/tests/conftest.py`: Postgres (owner and app roles, schema reset once per session), S3, NATS and
  relay-role fixtures, and `pytest_ignore_collect` for the smoke files.

## Layout corpus

`tests/fixtures/layout_corpus/` holds 15 small projects cut out of real placed boards with `hwflow cutout` and 20 open-hardware boards (below)
(`src/hwflow/layout/place/cutout.py`), each with the outcome measured on main in `expected.yaml`. How to add a case,
run it and update `expected.yaml`: `tests/fixtures/layout_corpus/README.md`. Tests: `tests/test_layout_corpus.py`.

- Fast tier (no router), 35 tests in 71 s on this machine: per case, placement legality of the fixed placement, fixed-part findings,
  corridor margins (+-0.02 mm), RUDY congestion (5 %), pin access (necked nets, pads without exit) asserted as
  recorded; `hwflow check` where it took at most 15 s when measured (`tiers.check: fast`); a re-placement from
  scratch of up to 20 free parts with no more violations than recorded; the extractor on a case (cut of a cut);
  two lesson tests (keep-out zone follows the part, round cut is not a box).
- Slow tier: `hwflow check` on the 6 dense cases with a scaling budget (max(120 s, 5 x measured)), and a real
  `hwflow layout` of every case, all cases in parallel (`LAYOUT_CORPUS_JOBS`, default cpu/4), 35 min with 8 jobs. A case
  fails when it is worse than recorded: more unrouted items, DRC errors, placement violations or open nets, more
  clearance or creepage DRC errors, an isolation row (copper of other nets inside the isolation distance, any layer)
  that was ok and is not, ok that became not ok. Counts above 0 get a slack of max(2, 15 %), because the repair
  loop runs on a wall-time budget and a loaded machine leaves a few more items open (measured: ht4_s1_k2in 3 -> 5,
  kb_audio 84 -> 96 unrouted on the same code); a count of 0, the DRC clearance and creepage counts and the isolation
  rows have none. A better result passes and is printed.
- `uv run python scripts/layout_corpus.py summary --jobs 6` prints case, expected, actual and time for another branch.
- The placement legality gate stops `kb_audio`, `kb_audio_dig`, `kb_hub` and `sp_overfull` before the router (their
  fixed placements have violations, verdict architecture); their `route` rows record the stop, the routed numbers of
  the table below are kept as `route_routed`. So these four no longer exercise the router in the slow tier
  (`HWFLOW_ROUTE_ILLEGAL=1` routes them).

| case | source (region) | parts / nets | routed on main (wall s) | reproduces |
|---|---|---|---|---|
| kb_usb_c | keyboard v2 usb_c | 37 / 42 | 30 unrouted, 17 nets open (1294) | yes: 5 of the 6 SuperSpeed nets of the source run open |
| kb_hub | keyboard v2 hub | 132 / 89 | 160 unrouted, 69 open (1592) | yes, stronger (source 43 of 48) |
| kb_audio_dig | keyboard v2 audio_dig | 127 / 87 | 151 unrouted, 65 open, 7 placement violations (684) | yes, stronger (source 27 of 39) |
| kb_back_conn | keyboard v2 back_conn | 94 / 56 | 49 unrouted, 21 open (826) | yes (source 14 of 33) |
| kb_audio | keyboard v2 audio | 183 / 86 | 84 unrouted, 28 open, 42 placement violations (1991) | yes: dense-strip violations and open nets (source 25 of 66) |
| kb_keys | keyboard v2 key matrix slice | 20 / 13 | clean (15) | control |
| ht4_s11_mains | home-thermostat-4 seed 11, 230 V corner | 13 / 10 | AC_ROOM1 open (617) | yes, the same single net |
| ht4_s1_mains | home-thermostat-4 seed 1, 230 V corner | 13 / 10 | clean, isolation 6.00 mm (52) | passing twin |
| ht4_s1_k2in | home-thermostat-4 seed 1, parts on K2_IN1 | 49 / 25 | 3 unrouted: GND, K2_IN2 (537) | partly: a sibling net, not K2_IN1 |
| ht3_mains | home-thermostat-3 round 8, 230 V corner | 22 / 14 | clean, isolation 6.00 mm (57) | positive control |
| ht1_mains | home-thermostat v1, relays and terminals | 18 / 13 | 20 creepage DRC (30) | yes (source 21 creepage) |
| ht1_battery | home-thermostat v1, battery switch | 79 / 35 | 15 unrouted, the source's 8 open nets exactly (940) | yes |
| s60_escape | synth60, whole board | 60 / 78 | 32 unrouted, 24 open (1885) | yes (source 31) |
| sp_control | sensor_preamp on 18 x 13 mm | 11 / 7 | clean (24) | control |
| sp_overfull | sensor_preamp on 13 x 10 mm | 11 / 7 | 11 unrouted, 6 placement violations (344) | deliberately over-full |

Wall times were measured with 7 cases routing in parallel on a 16-core machine shared with other jobs. Not extracted:
the home-thermostat-3 state with K1/F1 and BT1/BT2 overlaps and 4 unrouted at 74 % fill (no placement of it is kept;
`ht3_mains` is the later fixed state). main's corridor estimate gives AC_ROOM1 the same -1.0 mm margin on the failing
seed 11, the passing seed 1 and the routed ht3 board, so the corpus records it but cannot assert the >= 1.7 mm
margin of a fixed state.

### Open-hardware boards (`oshw_*`)

20 whole boards of open-hardware KiCad projects (6 mains relay/SSR boards, 4 battery IoT nodes, 3 USB-C power
boards, 2 small MCU boards, 2 dense 4-layer boards, 3 analog front ends; licenses MIT, Apache-2.0, BSD-1-Clause,
CERN-OHL-P/W-2.0, CC-BY-SA-4.0, GPL-3.0; authors, URLs and commits in `tests/fixtures/layout_corpus/NOTICE`). The
importer `hwflow.layout.board.kicad_import` (tests: `tests/test_kicad_import.py`) keeps the human placement, the pads
and nets, the `.kicad_pro` net classes and the outline; `scripts/oshw_corpus.py` clones (shallow, nothing executed),
imports, records the human reference and writes `oshw/index.json` (name, URL, license, MCU, blocks found, MPNs,
layers, size: input of reference-design retrieval). Routing a case is a routing-only benchmark of the human placement
(the case's `env`: `HWFLOW_FAST=1`, `HWFLOW_ROUTE_ILLEGAL=1` where the human placement breaks the placer's rules).
Fidelity: the human tracks, vias and zones written on the imported board leave no unconnected item on 18 boards; the
other two are open at the source too. `tests/test_oshw_corpus.py` checks provenance, the license file, the NOTICE
entry, the size (<= 1.5 MB per case, 3.3 MB in all) and the index. Fast tier: the 20 cases add 93 tests and about
50 s; `hwflow check` of `oshw_pico_ice` and `oshw_riegel` (35-42 s) is slow tier.

Baseline (main of `hard-rules` + this importer, `HWFLOW_FAST=1`, 2 jobs on a shared machine). Human: the board's own
copper and its KiCad DRC under hwflow's rules (same writer, rules and DRC as the routed case; it counts courtyard
overlaps, parts within 0.3 mm of the edge and other placement-made errors the routed board has too). hwflow: the
routed case (track length without the ground pour, which the human boards also use).

| case | category | Cu | parts / nets | human: track mm, vias, DRC errors | hwflow: unrouted, DRC errors, track mm, vias, s |
|---|---|---|---|---|---|
| oshw_ac_relay | mains | 2 | 17 / 15 | 492, 5, 5 | 0, 3, 279, 11, 7 |
| oshw_espgaggia | mains | 2 | 29 / 34 | 1908, 12, 37 | 0, 37, 1652, 49, 17 |
| oshw_hestiapi_one | mains | 2 | 15 / 28 | 1113, 21, 41 | 0, 29, 1194, 32, 19 |
| oshw_hot_plate_power | mains | 2 | 22 / 25 | 725, 6, 19 (+6 open at the source) | 0, 16, 567, 13, 12 |
| oshw_hue_simple_switch | mains | 2 | 28 / 19 | 404, 27, 0 | 0, 0, 303, 15, 12 |
| oshw_openblind32 | mains | 2 | 20 / 19 | 684, 4, 4 | 0, 4, 647, 15, 121 |
| oshw_b_parasite | battery IoT | 2 | 29 / 19 | 401, 51, 2 | 5, 5, 335, 24, 51 |
| oshw_esp32c3_devkit_lipo | battery IoT | 2 | 53 / 34 | 1096, 88, 10 | 8, 14, 821, 88, 59 |
| oshw_mini_env_sensor | battery IoT | 2 | 32 / 16 | 330, 15, 0 | 1, 1, 206, 18, 84 |
| oshw_rocketbeacon | battery IoT (LoRa) | 2 | 35 / 19 | 192, 51, 8 | 3, 8, 183, 19, 94 |
| oshw_buck_tps54202 | USB-C / power | 2 | 27 / 16 | 96, 19, 0 | 0, 0, 153, 8, 6 |
| oshw_ch32v003_usbpd | USB-C / power | 2 | 22 / 21 | 499, 22, 0 | 0, 0, 378, 29, 21 |
| oshw_powerline_usbc | USB-C / power | 2 | 21 / 13 | 106, 9, 0 | 0, 0, 256, 8, 11 |
| oshw_ch32v003_usb_devboard | MCU | 2 | 25 / 29 | 314, 29, 3 | 2, 2, 264, 20, 152 |
| oshw_rp2040_designguide | MCU | 2 | 36 / 56 | 1815, 32, 32 | 66, 66, 281, 14, 313 |
| oshw_pico_ice | dense 4-layer | 4 | 97 / 95 | 2289, 189, 91 | 45, 102, 2122, 188, 2119 |
| oshw_riegel | dense 4-layer (BGA) | 4 | 97 / 124 | 2419, 157, 96 | 25, 25, 2199, 185, 908 |
| oshw_milliohm_meter | analog | 4 | 41 / 19 | 279, 30, 0 | 0, 0, 332, 9, 12 |
| oshw_ina228_simple | analog | 2 | 24 / 14 | 268, 48, 5 | 0, 0, 165, 3, 6 |
| oshw_eurorack_ecg | analog | 2 | 41 / 30 | 518, 19, 13 | 8, 9, 400, 26, 281 |

The human routed every board completely; hwflow leaves nets open on 9 of 20. The worst: `oshw_rp2040_designguide`
(66 open on 2 layers, while the routability prediction says routable: KRT fails to escape the 0.4 mm QFN-56 at
0.2/0.2 mm rules, and `hwflow check` reports a pad-escape error for the 0.381 mm power class that the human necked),
`oshw_pico_ice` and `oshw_riegel` (dense 4-layer with power planes the import does not carry: hwflow must route
+3V3 or PWR* as tracks), and a GND left in pour fragments on four small boards. Not imported: copper zones other
than the ground pour, keep-out rule areas, custom `.kicad_dru` rules (isolation of the mains boards is therefore not
asserted), blind vias; the outline is the bounding box plus floorplan cuts.

### Lessons learnt and where they are tested

| lesson (board) | test or case |
|---|---|
| mains net open in a tight 230 V corner, twin routes (ht4 seeds 11 and 1) | `ht4_s11_mains`, `ht4_s1_mains` (slow) |
| SELV copper inside the 6 mm isolation distance, inner layers included (ht3) | isolation rows of the mains cases, `regressions()` (slow) |
| creepage errors on a 4 mm / 6 mm board (ht1) | `ht1_mains` (slow) |
| footprint keep-out zone written at the origin (ht3, a86a3a6) | `test_footprint_keepout_zone_follows_part` |
| placer tests a round keep-out as a box (ht3, F1 vs notch) | `test_keepout_circle_is_not_a_box` |
| route cache key ignores the router environment (ht3) | `tests/test_layout_perf.py` (existing) |
| crash on a KiCad `(at x y unlocked)` footprint (keyboard) | `tests/test_core.py` (`kb_audio` stops at the legality gate now) |
| check takes 54 min with region nets under the corridor proof (keyboard) | `test_check_dense[kb_hub]` and the other dense cases: budget (slow) |
| SuperSpeed lanes and QFN fan-out open (keyboard usb_c, hub) | `kb_usb_c`, `kb_hub`, `kb_audio_dig` (slow); pin access in the fast tier |
| dense front strip: placement violations, no pin exit at the net width (keyboard) | `kb_audio` (fast and slow) |
| part Edge.Cuts slot cut by a window (ht1 panel slot) | extractor: slot becomes a cut region (`ht1_mains`) |
| over-full board | `sp_overfull` |

## Evals harness

`evals/` measures the `/hw-design` agent, which is not deterministic. `harness.py run` drives full agent sessions
(`claude -p`) against fixtures (`evals/fixtures/*.yaml`: brief, hidden spec, planted conflicts). A simulated product person
answers. `harness.py score` then rebuilds each design with `hwflow` and gives a 100-point card. `compare` checks a run against a
baseline (`evals/baselines/`) with a noise tolerance. Pytest differs in three ways: it is deterministic, it needs no model
or credential, and it checks code correctness (pass or fail), while evals check agent quality (a score that moves between
runs). `evals/bench/` holds synthetic boards for placer benchmarks. Details: `evals/README.md`.

## Known gaps

- Router coverage depends on KiCadRoutingTools, which CI does not install. Every `needs_router` test (about 10 files)
  skips in CI, so KRT routing, repair and regionroute are not exercised there. The Freerouting fallback runs in the slow job
  (`test_cold_equals_cached.py`).
- Only one real design (`examples/sensor_preamp`) is built end to end by the suite. No test builds a second,
  different design through sim, layout and post-layout.
- The thermostat baseline test (`test_placer.py`) skips when `evals/baselines/home-thermostat-2026-10-19/project` is absent.
- No test runs the optimizer (`hwflow optimize`) or the openEMS stage; `test_em.py` covers only the pure-Python part.
- No test runs the `/hw-design` agent flow or the subagents; only evals do, and they are manual or nightly on the platform.
- The web UI has server-logic tests only; no browser test.
- No test checks the platform workflows against a real Claude session in CI (`anthropic_smoke` is opt-in).
- Layout wall time and route quality (length, vias) are recorded in the corpus but not asserted; the corpus asserts
  counts (unrouted, DRC, violations, open nets, isolation).
- The corpus slow tier needs KiCadRoutingTools, so it skips in CI like the other router tests.
