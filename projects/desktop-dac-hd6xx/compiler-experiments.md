# Compiler baseline: desktop DAC and HD6XX amplifier

This board is a reference case for the proposed DSL-to-PCB compiler. First capture what unchanged `hwflow` does at commit `ca10fa8e0b9d67745787734b979e784f4422a3ec`; then compare any compiler change against the same design and toolchain. Circuit capture, source checks, schematic/ERC and a real-footprint feasibility run are now recorded in [notes/circuit-baseline.md](notes/circuit-baseline.md). The original model blocker is repaired and the qualified power/protection checks now pass. The first bounded current-engine routing run reached the 750 s outer timeout before a final result; the complete matched four-case baseline/candidate comparison remains **NOT CHECKED**. The separate [architecture-only baseline](notes/floorplan-baseline.md) records the placeholder floorplan and its negative fit check.

## Freeze the input

Record the design commit or file hash, board size and layer count, component/net counts, KiCad and router versions, container image digest, CPU/worker settings, and the exact command for each run. Keep USB, audio, power connectors, and mechanical mounting positions fixed where the design specifies them. Keep the remaining blocks and passives movable unless a documented electrical or mechanical constraint requires otherwise. Record the fixed/movable counts and the chosen coordinates; do not change them between baseline and candidate runs.

Run each case in its own copy under `projects/` so its generated `build/` data cannot affect another case. Keep the source project untouched. Use the same machine, tool versions, layout budget, worker count, board constraints, and acceptance criteria for both implementations. Save command output, elapsed wall time, `build/REPORT.md`, `build/status.json`, `build/FLOORPLAN.md`, and the generated board for inspection. A timeout or crash is a measured failure, with its elapsed time and last stage recorded.

## Four runs

These circuit/build/routing cases begin only after circuit capture and real footprint verification. The current design has cleared its stated model-level circuit gate. Physical bench qualifications remain open; a feasibility result does not substitute for completed routing.

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
| Cold | NOT CHECKED | NOT CHECKED | — | NOT CHECKED | NOT CHECKED | Matched controlled comparison not yet run |
| Warm | NOT CHECKED | NOT CHECKED | — | NOT CHECKED | NOT CHECKED | Matched controlled comparison not yet run |
| Local edit | NOT CHECKED | NOT CHECKED | — | NOT CHECKED | NOT CHECKED | Matched controlled comparison not yet run |
| Impossible fit | NOT CHECKED | NOT CHECKED | — | NOT CHECKED | NOT CHECKED | Matched controlled comparison not yet run |

## Findings during setup

On this baseline, `trace()` treats nonempty evidence prose for inspection/test requirements as coverage; even text saying “NOT CHECKED” can appear verified. This project therefore leaves `evidence` empty until a real result exists. The separate evidence patch now requires an explicit status, a current evidence hash and a nonempty project-local artifact; absent, failed and stale cases have regression coverage. See [verification-baseline.md](notes/verification-baseline.md). Do not count document text as proof.

The original container build also selected the newest KRT binary despite pinned source, then failed on a version mismatch without Cargo. The environment-only correction pins the matching release; see the measured setup and floorplan results in `notes/floorplan-baseline.md`.

Verified compiler corrections include explicit evidence semantics, clearance-preserving placement snapping and complete schematic sheet sizing. The simulation/protection work also exposed and repaired explicit PSpice parsing, incomplete operating-point audit reporting, timeout-log loss and query-URL round trips; see [simulation-build-repair.md](notes/simulation-build-repair.md). The source-specific ERC short was a custom symbol geometry mistake, fixed in the project library; it was not attributed to the generator. `BOM cost: $0.00` currently accompanies a list of missing prices and must be read as **unpriced**, not a zero-cost estimate. A future report improvement should represent incomplete cost directly.

## Current-engine PCB attempt

The validated electrical checkpoint source is SHA-256 `556b89df64223c63651a788429b29bce4f98e7e82559a325971bd939ba9d63c1`: 238 parts, 133 connected nets, 703 assigned pins, four fixed connectors, 234 movable parts, 140 × 80 mm and four layers. The first attempt preceded the routing-budget correction; the simulator/audit and YAML corrections are recorded in commits `a5ca1b5` and `2406fc5`. The first actual route used two attempts/two workers, a 600 s engine budget and a 750 s outer timeout; it reached the outer timeout after 750.4 s, having started only attempt a1. Its result is recorded separately in [layout-validation.md](notes/layout-validation.md) and `verification/layout-*.json`.

This run begins with an already generated and verified schematic, simulation results and floorplan. It is a cold routing attempt, not the complete fresh-copy cold build specified in the four-case table. It cannot establish a routing speedup against unchanged engine `ca10fa8e`; that comparison is still outstanding. The first run used the validated electrical checkpoint unchanged; subsequent physical-rule corrections are listed below. Fixed anchors remain unchanged.

That attempt exposed two compiler failures: the quick floorplan ignored fixed-part region conflicts, and the initial routing pass did not receive the layout deadline. Measured connector geometry justified padding the DAC region left edge from 38 to 37.6 mm and the power region top edge from 2 to 0.7 mm. Board size, connector positions, USB reserve and the 8 mm power/analog/DAC gap are unchanged. `verification/circuit-revision-equivalence.json` proves the electrical inputs are identical to the validated checkpoint. Two narrowly scoped rules also permit the intrinsic 0.15 mm pad gaps inside U204 and U336; every pad-to-track, pad-to-via and other-footprint clearance remains 0.20 mm. The corrected layout-source hash is `8d99069b290cd461d95c22192f326537330d9ada447b19f2e4fa2150d384b96f`; keep the runs separate rather than claiming an unchanged-input routing speed comparison.

## Selective repair measurements

The current layout source `8d99069b…b96f` retains every electrical input and fixed anchor. A recorded harness rebuilds archived copper through the engine, verifies all 703 assigned pin nets, all 238 placements and the board/rules, then invokes the existing local-repair API. It does not insert a route-cache entry or claim the CLI layout stage passed.

| Run | Search / wall time | Retained full-DRC result | Interpretation |
|---|---|---|---|
| First selective repair | 300.0 / 323.391 s | 6 → 3 open connections; zero other copper errors | Three ground islands connected; three signal nets remain open |
| Neighbor-expansion follow-up | 300.0 / 318.370 s | 3 → 3 open connections; zero other copper errors | Actual second round freed 12 nets, timed out and was rejected; no demonstrated connectivity improvement |

Both runs preserved all component positions, board dimensions, assigned pin nets and source bytes. The kept follow-up board has no dangling-copper warnings after pruning, but has 398 silk warnings, 78 NC-name/Datasheet parity warnings and nine decoupling-path warnings. Thirteen declared-current nets receive a width screen; copper-region containment and return-path/SI are not checked because the required net envelopes and edge-rate inputs are absent. These are partial routing outcomes, not whole-product success or a matched speedup result.

The observed local-repair failure justified a small compiler fix: missing-connection endpoints now feed the existing neighbor search on retries. First-round behavior, the 12-net cap, width/layer checks and acceptance/rollback are unchanged. Commit `27f00af` has independent review and eight focused regressions; the final fast suite is 327 passed, 2 skipped, 14 deselected. A clean baseline is still needed before the warm/local-edit comparison in the table above.
