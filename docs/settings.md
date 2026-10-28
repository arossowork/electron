# Settings

Every switch and path of the engine is one typed object, `hwflow.settings.Settings` (pydantic-settings). Get it with
`get_settings()`: it reads the environment at call time and caches nothing, so tests can `monkeypatch.setenv`. No module
outside `settings.py` reads `os.environ` (`tests/test_settings.py` fails if one does). The only exceptions are
subprocess environments that pass the parent environment to a child on purpose: `kicad.py` (kicad-cli), `pcb.py` (the
router) and `commands/em.py` (the openEMS re-exec).

The resolved settings are saved in `build/status.json` under `settings` for each build. A stage runs under its
run's snapshot: `Settings.from_snapshot(snap)` takes the layout, repair and router groups from it and tools and paths
from this machine, and `with settings.use(s):` makes `get_settings()` return it (also in threads started inside). The layout, repair and router
groups are folded into the route and region cache keys (`Settings.key_dump()`); paths and tool locations are not.

| Setting | Env var | Default | Meaning |
|---|---|---|---|
| `layout.flow` | `HWFLOW_FLOW` | none (design `placement.flow`, then `asic`) | Layout flow; `legacy` is plain annealing |
| `layout.asic[<name>]` | `HWFLOW_ASIC_<NAME>` | all stages on | `0`, `false`, `no`, `off` turns a stage off (DENSITY, ANNEAL, NECK, ...) |
| `layout.keepout_strict` | `HWFLOW_KEEPOUT_STRICT` | on | Strict keep-outs; off with `0`, `false`, `no`, `off` |
| `layout.neckdown` | `HWFLOW_NECKDOWN` | on | Neck-down widening; off only when exactly `0` |
| `layout.seedroute` | `HWFLOW_SEEDROUTE` | on | Seed routes for corridor nets; off only when exactly `0` |
| `layout.seed0` | `HWFLOW_SEED0` | 1 | First placer seed (A/B runs) |
| `layout.budget_s` | `HWFLOW_LAYOUT_BUDGET_S` | 3000 | Wall time of `layout --auto` |
| `layout.auto_max_errors` | `HWFLOW_AUTO_MAX_ERRORS` | `repair.max_errors` | Errors above which `--auto` starts no other attempt |
| `layout.settle` | `HWFLOW_SETTLE` | on | `Placer.settle` legalizer after the placement; off only when exactly `0` |
| `layout.eco` | `HWFLOW_ECO` | on | Incremental (ECO) placement: attempt 1 starts from `build/placement.good.json`, the last clean layout (docs/layout-flow.md); off only when exactly `0` |
| `layout.route_illegal` | `HWFLOW_ROUTE_ILLEGAL` | off | Route a placement with violations anyway (debugging only; the legality gate stops it) |
| `layout.routab_predict` | `HWFLOW_ROUTAB_PREDICT` | on | Routability prediction (a report) and its run log `build/routab/`; off only when exactly `0` |
| `layout.routab_rank`, `layout.routab_feedback`, `layout.routab_retry` | `HWFLOW_ROUTAB_RANK`, `_FEEDBACK`, `_RETRY` | none (design `placement.routability_rank` etc., then off) | Prediction-driven seed ranking, density feedback, router retry policy (docs/layout-flow.md) |
| `layout.roads` | `HWFLOW_ROADS` | none (design `placement.roads`, then `0`) | Roads first: `route` routes the planned roads first, `1` also reserves them in placement |
| `layout.roads_power` | `HWFLOW_ROADS_POWER` | off | Every power-class net is a road too (experiment) |
| `layout.place_escape`, `layout.place_escape_infl`, `layout.place_escape_rot` | `HWFLOW_PLACE_ESCAPE`, `_INFL`, `_ROT` | none (design `placement.escape`, `escape_infl`, `escape_rot`, then off) | Escape-aware placement prototype (layout/place/escape.py): slot term, demand inflation + channel check, rotation forward check |
| `layout.place_escape_w` | `HWFLOW_PLACE_ESCAPE_W` | 10 | Weight of the escape slot term (mm of wire per blocked pad) |
| `layout.place_escape_conn` | `HWFLOW_PLACE_ESCAPE_CONN` | 1 | Share at which parts that share a (non-ground) net block each other's escape slots |
| `layout.krt_rotate` | `HWFLOW_KRT_ROTATE` | 0 | The router's net-order retries start from `KRT_VARIANTS[k]` |
| `layout.pour_stitch` | `HWFLOW_POUR_STITCH` | off | Join a split GND pour after routing: vias in the fill, in exposed pads, pad stubs to a via, stubs that displace routed nets (layout/route/pourstitch.py) |
| `layout.firstpass` | `HWFLOW_FIRSTPASS` | none (design `placement.firstpass`, then off) | Routable first pass: `1` (every step) or a list of `roads`, `hot`, `quick` (layout/route/firstpass.py) |
| `layout.firstpass_quick_s`, `layout.firstpass_hot_w` | `HWFLOW_FIRSTPASS_QUICK_S`, `_HOT_W` | 240, 4 | Wall cap of the quick route; weight of the hotspot placer term |
| `layout.fast` | `HWFLOW_FAST` | off | Fast preset: `pour_stitch` on, `place_escape=1`, `roads=route`, each unless given explicitly (docs/layout-flow.md "Fast preset") |
| `layout.portfolio` | `HWFLOW_PORTFOLIO` | off | Attempt 1 of `--auto` as the layout portfolio (pipeline/portfolio.py) |
| `layout.portfolio_workers` | `HWFLOW_PORTFOLIO_WORKERS` | 0 (CPU count - 2) | Portfolio strategies at once |
| `layout.portfolio_grace` | `HWFLOW_PORTFOLIO_GRACE` | 1.5 | Running strategies get this x the first converged one's time; `inf` waits for all |
| `repair.max_errors` | `HWFLOW_REPAIR_MAX_ERRORS` | 40 | Above this many errors, local repair is skipped |
| `repair.budget_s` | `HWFLOW_REPAIR_BUDGET_S` | 120 | Wall time of the whole repair |
| `repair.max_stall` | `HWFLOW_REPAIR_MAX_STALL` | 2 | Rounds in a row without improvement before repair stops |
| `router.env_knobs` | `KICAD_*`, `KRT_*` (not `KICAD_CLI`) | none | Passed to the router process; part of the cache key |
| `tools.kicad_cli` | `KICAD_CLI` | PATH `kicad-cli`, then the macOS app | kicad-cli binary |
| `tools.java` | `JAVA` | PATH `java`, then Homebrew openjdk, then `java` | Java for Freerouting |
| `tools.ngspice` | `HWFLOW_NGSPICE` (new) | PATH `ngspice`, then `/opt/homebrew/bin/ngspice` | ngspice binary |
| `tools.openems_python` | `HWFLOW_OPENEMS_PYTHON` | `~/opt/openEMS/venv/bin/python` | Python with the openEMS bindings |
| `tools.em_reexec` | `HWFLOW_EM_REEXEC` | off | Set by hwflow for its own child; stops re-exec loops |
| `tools.em_verbose` | `HWFLOW_EM_VERBOSE` | 0 | openEMS verbosity |
| `paths.root` | `HWFLOW_ROOT` | the repository root | Base of the data dirs below |
| `paths.blocks_dir` | `HWFLOW_BLOCKS_DIR` | `<root>/blocks` | Layout block library |
| `paths.library_dir` | `HWFLOW_LIBRARY_DIR` | `<root>/library` | Shared symbols and footprints |
| `paths.tools_dir` | `HWFLOW_TOOLS_DIR` | `<root>/tools` | Bundled tools |
| `paths.krt_dir` | `KRT_DIR` | `<tools_dir>/KiCadRoutingTools` | KiCadRoutingTools checkout |
| `paths.freerouting` | `FREEROUTING` | `<tools_dir>/freerouting-2.5.0.jar` | Freerouting jar |
| `paths.kicad_share` | `KICAD_SHARE` | first of `/usr/share/kicad`, `/usr/local/share/kicad`, the macOS app | KiCad libraries (`symbols/`, `footprints/`) |
| `paths.ref_dirs` | `HWFLOW_REF_DIRS` | `<root>/examples` and `<root>/projects` (`os.pathsep`-separated) | Reference designs for `hwflow ref search`: one `design.yaml` per sub-folder |
| `paths.oshw_index` | `HWFLOW_OSHW_INDEX` | `<root>/oshw/index.json` | External open-hardware index for `hwflow ref search`; a missing file is skipped |
| `paths.cas_dir` | `HWFLOW_CAS_DIR` | `~/.cache/hwflow/cas` | Local artifact store (`LocalCAS`) and stage cache (`keys/<stage>/<key>.json`) |

Package data (`models/generic.lib`, hashed source files) stays relative to the package. No setting holds a secret;
`Settings.snapshot()` is where one would be dropped.
