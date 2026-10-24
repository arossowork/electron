# Circuit checkpoint and measured gates

> Historical checkpoint before the simulation/protection repairs. Current results are in [../SUMMARY.md](../SUMMARY.md), [model-repair.md](model-repair.md), [power-validation.md](power-validation.md), and [layout-validation.md](layout-validation.md).

2026-10-24. This is a circuit/feasibility checkpoint, not the frozen cold/warm/local-edit routing comparison. Source is `design.yaml`; temporary parallel-authoring fragments have been merged and removed.

## Input and environment

- Engine baseline: `ca10fa8e0b9d67745787734b979e784f4422a3ec`.
- Current separate compiler fixes: `22a5c75` (explicit evidence) and `38684a5` (placement rounding). Schematic page sizing is separate commit `3de2d38`.
- Image: `hwflow-audio:ca10fa8e`; digest `cbbf1f8964d7f2193794f5300bd515fd10fabcc2d1beacc053c49550a94ddd3e`.
- KiCad 10.0.7, ngspice 42, Python 3.11.15, uv 0.11.33, KRT 0.23.1.
- Circuit: 235 real components, 128 nets, 683 assigned source pins; four fixed connectors and 231 movable components. One extra placeholder reserves the separately supplied USB module's measured envelope.
- Board: 140 × 80 mm, four layers, 1.6 mm. Four anchors: J100 (42,48,0°), J200 (58,76,90°), J201 (123,76,90°), J300 (124,15,180°). Units mm, top-left origin, y down.
- Source SHA256 after whitespace cleanup: `2f1fcd29d11ebfb6bd4fda8a42a53f2ec6c8954b1c13c1070702994acffdadc3`.

## Results and limits

| Check | Observed result | Meaning |
|---|---|---|
| Integrated source check | PASS; 0 errors, 5 warnings; 12.0 s in final run | Symbols/pads and static feasibility accepted |
| KiCad schematic ERC | PASS; 0 errors, 4 warnings; 2.0 s in final run | No remaining ERC error; not an electrical performance test |
| Exported connectivity | PASS; 683/683 assigned source pins match the KiCad netlist | The schematic represents the source connections |
| Real-footprint floorplan | PASS in 50.6 s on final source; 20,000 moves, 2 seeds | Coarse placement/packing feasibility |
| Direct single-channel OPA1622 operating point | FAILED; timeout 20 s/observed 20.8 s, no valid operating point | Inherited model/solver integration is unresolved |
| Complete audio/control simulation and Monte Carlo | NOT CHECKED successfully | Requires qualified nominal models and scenario tests |
| Routed layout, DRC, post-layout extraction | NOT CHECKED | Circuit simulation/protection gate remains open |
| Complete prototype delivered cost | NOT CHECKED | Unpriced lines are not a zero-cost board |
| Product requirements trace | 0 verified, 0 failing, 10 unverified | No physical product claims have been completed |

The floorplan has 60% estimated front-face fill, power/DAC/analog region fill of 45%/43%/39%, a 29.6 mm depth stack within the 32 mm allowance, and 8 mm power-region separation where 5 mm is required. Seed 2 finished the quick placement with zero violations. Its congestion estimate was 7% of capacity, peak 0.7×, and zero overflow cells. These are heuristic feasibility metrics, not a completed route, measured noise isolation or final enclosure fit. The generated front/back SVG was visually inspected; all populated parts are currently on the front.

The five source warnings comprise four internal supplies after passive/diode/converter paths without a symbol power-output driver, and one warning listing eight floating SPICE islands caused by unmodeled circuitry. Those internal rails were not mislabeled as external connectors just to hide warnings. The four ERC warnings are bidirectional ESS pins 16/25/26/27 tied low in the selected hardware configuration; source-document review remains in the DAC note. The earlier actual short caused by overlapping SN74LVC244A symbol pins 19/17 was corrected and the netlist then independently compared.

Timings overlapped other work, including abandoned simulation containers that were subsequently identified and stopped. They are operational observations, not speedups or a controlled baseline-versus-candidate benchmark. The original placeholder floorplan's 4.9 s result concerns a different input and is not comparable to this real circuit.

## Commands and repeatability

Run inside the pinned image with the worktree mounted at `/workspace`, working directory `/workspace`, network disabled, and `PYTHONPATH=/workspace/src`:

```text
hwflow build projects/desktop-dac-hd6xx --until sch
hwflow floorplan projects/desktop-dac-hd6xx --iters 20000 --seeds 2
kicad-cli sch export netlist --format kicadsexpr --output projects/desktop-dac-hd6xx/build/connectivity.net projects/desktop-dac-hd6xx/build/desktop_dac_hd6xx.kicad_sch
python -m pytest -q -m 'not slow'
```

Exported connectivity was parsed as `(net name (node ref pin)…)` and compared for every source `(ref,pin) -> net` pair. For a true unchanged-engine baseline select `PYTHONPATH=/app/src` and a fresh project copy. The controlled cold/warm/local-edit/impossible-fit routing protocol remains in `compiler-experiments.md`; no route-speed result is claimed here.

Local logs retained during this run: `/tmp/dac-integrated-check.log`, `/tmp/dac-schematic-build.log`, `/tmp/dac-schematic-final.log`, `/tmp/dac-schematic-checkpoint.log`, `/tmp/dac-real-floorplan.log`, `/tmp/dac-floorplan-checkpoint.log`, `/tmp/hwflow-final-fast.log`. Portable model reproduction is in `verification/opa1622-op.cir` and `notes/models.md`.

## Final export and compiler verification

After the reviewed R118 discharge-current correction, the final source check again passed with 0 errors/5 warnings and ERC with 0 errors/4 warnings. Schematic page sizing now yields a 1671 × 297 mm custom page; all 235 component references are present in the exported SVG and the full drawing was visually inspected. The final fast suite after all three compiler fixes passed **293 tests, 2 skipped, 14 deselected, in 94.37 s** (`/tmp/hwflow-final-with-schematic.log`). Independent review found no remaining material finding in these three patches. The source- and ERC-warning meanings are unchanged.

The final floorplan was refreshed after the R118 value correction and passed in 50.6 s with identical fill, depth, congestion and zero-violation results. The earlier 167.3 s run overlapped other work; the difference is not a compiler speedup. Final exported netlist parity and presence of all 235 SVG references were checked again against the final source. All started verification jobs and project simulation containers were closed before this checkpoint.
