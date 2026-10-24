# Simulation and layout build repairs — 2026-10-24

The DAC circuit exposed reproducible simulator, serialization, floorplan and routing failures. These engine changes are independent of the board component corrections.

## Explicit PSpice compatibility

`sim.ngbehavior: ps` enables ngspice's supported compatibility parser before it reads the netlist. `native` remains the default; other values are rejected. The generated netlist records the mode, so it changes the simulation cache key and propagates to scoped, Monte Carlo and post-layout runs. The process ignores user initialization files (`-n`); no global simulator configuration is changed.

The original TI libraries now work without manually rewriting their switch or LIMIT functions. [model-repair.md](model-repair.md) records the original failure, vendor hashes, 16 OPA1622 checks and 13 OPA161x/analog-chain checks. The original translated model timed out in the isolated operating-point reproduction after 20 seconds. This is a compatibility repair, not a routing or general simulator speedup claim.

## Audits fail visibly

`sim.derating_timeout_s` bounds the operating-point audit independently; the DAC project sets 30 seconds. Timeout stdout/stderr is retained, and a timed-out run is never cached as a successful result. Nonzero exits, simulator errors/aborts, missing pin voltages or currents, and nonfinite values now fail the audit. Stage simulation then fails and the report says `FAILED`; it cannot silently show an empty successful audit.

Regression cases cover partial timeout logs, parser mode, distinct cache keys, stage/report propagation and absent/nonfinite audit measurements. Independent review caught an intermediate case where currents existed but voltage measurements were absent; the added regression failed before the final check was implemented.

## Design round trips

Saving a datasheet URL containing `?` in a flow-style mapping could produce YAML accepted by ruamel's emitter but rejected by hwflow's PyYAML loader. A local round-trip emitter now quotes those flow scalars while preserving block style, existing quoted values, comments and ordering. Regression coverage includes maps, nested flow sequences and a second save/load cycle.

## Simulation checkpoint verification

The fast suite at the simulation checkpoint passed **304 tests**, with 2 skipped and 14 slow tests deselected, in 88.25 seconds in the pinned container. Independent follow-up ran 11 focused regressions, a live sensor-preamp audit (10 component rows), and a native/PSpice cache-key comparison. No full slow suite or merge was performed.

The project simulation passed all **14 measures**, including both channels at 0/25/60 °C, AC response at 20 Hz/1 kHz/20 kHz, and output DC. All **50 Monte Carlo trials** met the amplifier-stage output limits. These ideal-rail model results do not qualify the physical power supply, DAC full scale, startup behavior or headphone protection; those have separate evidence and bench gates.

## Fixed connectors are checked before routing

The quick floorplan previously omitted rule violations involving only fixed components. The real layout then reported two connector courtyards outside their regions after a misleading floorplan PASS. The floorplan now reports these as hard failures, while retaining the existing allowance for connector body overhang outside the board. Copper edge checks, keep-outs and normal placement costs are unchanged. Four regressions cover region conflicts, permitted body overhang and preservation of copper checks.

The project correction pads only the two affected region edges; it does not move any connector or change board dimensions. The original source reproduces both failures, and the corrected source passes with zero movable-placement and zero fixed-rule violations. See [layout-validation.md](layout-validation.md).

## A routing timeout retains completed work

The first real route exceeded its 600 s search budget because the initial route did not receive the deadline. The 750 s outer limit killed it before final DRC, repair and reporting. The same deadline now reaches the initial router, region groups, width passes, ordering retries and area search. An interrupted initial pass keeps its best completed variant, labels the result incomplete, skips further search, and does not cache truncated copper. Final checking and diagnosis have their own bounded time allowance; this remains a search budget, not a strict whole-command timer.

Independent review caught two additional cases before integration: a killed Freerouting process could leave a session file that was still imported, and area optimization could discard the first completed partial candidate. Neither now occurs. Area search prefers an earlier complete candidate, otherwise retaining the interrupted size and accepted copper together. Repair keeps its existing behavior of rejecting a timed-out trial. Regression cases cover retained copper, unfinished-file rejection, dimensions, cache exclusion, group reuse and the one-attempt auto path.

There is still no standalone CLI command to resume arbitrary partial copper. The project uses a small recorded harness around the existing local-repair API to evaluate that path. This is a concrete next compiler interface opportunity; it is not presented as an implemented CLI feature or a measured speedup against the original engine.

## Routing-budget checkpoint verification

After the floorplan and routing-budget repairs, the final fast suite passed **319 tests**, with 2 skipped and 14 slow tests deselected, in **81.55 seconds** in the pinned offline container. The routing changes passed an additional independent review and 11 focused regressions. Engine commits are `64f32d5` (fixed-part floorplan checks) and `5422f97` (routing deadline and completed-copper retention). Exact command output and file hashes are in `verification/engine-fast-tests-routing-budget.log` and `verification/engine-fast-tests-routing-budget.json`. The full slow suite and a merge have not been performed.

## Local repair can free neighbors at open connections

The first selective repair closed three ground islands but left three signals open. Its later rounds repeated the same net set because only clearance errors supplied locations for neighbor expansion; unconnected-pad errors supplied none. The minimal fix adds non-pour endpoint locations to the existing retry search. It leaves the first round unchanged, keeps named nets first, retains the 12-net cap, and preserves the same width/layer checks and rejection of a worse board. It does not guarantee closure.

Eight regression cases cover pad/track/via endpoints, missing positions, ground pours, priority/cap and acceptance/rollback after an unsuccessful first round. The affected suite passed 40 tests, and an independent reviewer reran all eight new cases. The final full fast tier then passed **327 tests**, with 2 skipped and 14 slow tests deselected, in **101.12 seconds**. Commit `27f00af` contains this fix. Current exact code hashes and output are in `verification/engine-fast-tests.json` and `.log`; the full slow suite and merge remain unperformed.
