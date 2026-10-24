# Settings

Every switch and path of the engine is one typed object, `hwflow.settings.Settings` (pydantic-settings). Get it with
`get_settings()`: it reads the environment at call time and caches nothing, so tests can `monkeypatch.setenv`. No module
outside `settings.py` reads `os.environ` (`tests/test_settings.py` fails if one does). The only exceptions are
subprocess environments that pass the parent environment to a child on purpose: `kicad.py` (kicad-cli), `pcb.py` (the
router) and `cli.py` (the openEMS re-exec).

The resolved settings are saved in `build/status.json` under `settings` for each build. The layout, repair and router
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

Package data (`models/generic.lib`, hashed source files) stays relative to the package. No setting holds a secret;
`Settings.snapshot()` is where one would be dropped.
