# Compiler baseline: desktop DAC and HD6XX amplifier

This board is a reference case for the proposed DSL-to-PCB compiler. First capture what unchanged `hwflow` does at commit `ca10fa8e0b9d67745787734b979e784f4422a3ec`; then compare any compiler change against the same design and toolchain. No circuit build or routing timing result has been collected yet. The four circuit benchmark cases below remain **NOT CHECKED**. The separate [architecture-only baseline](notes/floorplan-baseline.md) records the placeholder floorplan and its negative fit check.

## Freeze the input

Record the design commit or file hash, board size and layer count, component/net counts, KiCad and router versions, container image digest, CPU/worker settings, and the exact command for each run. Keep USB, audio, power connectors, and mechanical mounting positions fixed where the design specifies them. Keep the remaining blocks and passives movable unless a documented electrical or mechanical constraint requires otherwise. Record the fixed/movable counts and the chosen coordinates; do not change them between baseline and candidate runs.

Run each case in its own copy under `projects/` so its generated `build/` data cannot affect another case. Keep the source project untouched. Use the same machine, tool versions, layout budget, worker count, board constraints, and acceptance criteria for both implementations. Save command output, elapsed wall time, `build/REPORT.md`, `build/status.json`, `build/FLOORPLAN.md`, and the generated board for inspection. A timeout or crash is a measured failure, with its elapsed time and last stage recorded.

## Four runs

These circuit/build/routing cases begin only after circuit capture and real footprint verification. The present placeholder design supports an early floorplan check and an impossible-fit diagnostic; running its empty circuit through a build would not measure placement or routing performance.

| Case | Input and command | What to compare |
| --- | --- | --- |
| Cold | Fresh copy with no generated `build/`; run `uv run hwflow check <case>`, `uv run hwflow floorplan <case>`, then `uv run hwflow build <case> --until layout --auto --brief`. | Total and placement/routing/DRC time; floorplan findings; fixed-position changes; placement violations; DRC errors; unrouted nets; route attempts and group timings. |
| Warm | Without changing that copy, run `uv run hwflow layout <case> --auto --reuse --brief`. | Elapsed time, route-cache hit/miss, fixed positions, copper and DRC result. The route cache can skip routing while full DRC still runs; report the actual hit/miss. |
| Local edit | Start from a separate cold-run copy. Change one documented movable part or block locally without changing its footprint or fixed geometry; run `check`, `floorplan`, and `layout --auto --reuse --brief` again. | Whether unrelated placement and route groups stay stable, which groups reroute, time, DRC, and fixed-position invariance. Record the exact edit; do not assume incremental reuse will occur. |
| Impossible fit | Start from another source copy. Make one named placement region smaller than a required member's courtyard, leaving the circuit unchanged; run `check` and `floorplan`. | The expected result is a **FAIL** identifying the part/region fit conflict before routing. If this geometry is not expressible for the chosen part, record **NOT CHECKED** and choose a different explicit conflict; do not fabricate a proof. |

For each run, classify each gate **PASS**, **FAILED**, or **NOT CHECKED** and retain the actual diagnostic. A gate is **NOT CHECKED** when it was not run or a prerequisite prevented it. A run that starts and times out or crashes is **FAILED**. An early floorplan PASS, including PASS with risks, means only that its feasibility checks passed; it does not establish a routed board. Layout PASS requires the specified fixed positions and regions to hold, zero unwaived DRC errors, and zero unrouted nets. Full product sign-off remains separate: schematic/ERC, post-layout checks, requirements trace, electrical review, and first-article measurements.

One current limitation is worth observing explicitly: a floorplan region can constrain member placement, but `nets:` containment is reported as a post-layout warning when copper leaves the shape; it is not a general hard routing envelope. Conductive keep-outs and declared isolation have stronger routing and layout checks. Report any envelope escape with the named net and excursion, even if the layout verdict is otherwise PASS. Do not infer audio performance, electrical safety, manufacturability, or solver speed from a floorplan result alone.

## Results

| Case | Floorplan | Layout / DRC | Wall time | Fixed positions | Cache / route groups | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Cold | NOT CHECKED | NOT CHECKED | — | NOT CHECKED | NOT CHECKED | Real circuit and footprints pending |
| Warm | NOT CHECKED | NOT CHECKED | — | NOT CHECKED | NOT CHECKED | Real circuit and footprints pending |
| Local edit | NOT CHECKED | NOT CHECKED | — | NOT CHECKED | NOT CHECKED | Real circuit and footprints pending |
| Impossible fit | NOT CHECKED | NOT CHECKED | — | NOT CHECKED | NOT CHECKED | Real circuit and footprints pending |

## Findings during setup

On this baseline, `trace()` treats nonempty evidence prose for inspection/test requirements as coverage; even text saying “NOT CHECKED” can appear verified. This project therefore leaves `evidence` empty until a real result exists. A later compiler change should make evidence state explicit and test absent, failed and stale results. Do not count document text as proof.

The original container build also selected the newest KRT binary despite pinned source, then failed on a version mismatch without Cargo. The environment-only correction pins the matching release; see the measured setup and floorplan results in `notes/floorplan-baseline.md`.
