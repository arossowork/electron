# Simulation/build repairs — 2026-10-24

The DAC circuit exposed three reproducible engine failures. These changes are independent of the board component corrections.

## Explicit PSpice compatibility

`sim.ngbehavior: ps` enables ngspice's supported compatibility parser before it reads the netlist. `native` remains the default; other values are rejected. The generated netlist records the mode, so it changes the simulation cache key and propagates to scoped, Monte Carlo and post-layout runs. The process ignores user initialization files (`-n`); no global simulator configuration is changed.

The original TI libraries now work without manually rewriting their switch or LIMIT functions. [model-repair.md](model-repair.md) records the original failure, vendor hashes, 16 OPA1622 checks and 13 OPA161x/analog-chain checks. The original translated model timed out in the isolated operating-point reproduction after 20 seconds. This is a compatibility repair, not a routing or general simulator speedup claim.

## Audits fail visibly

`sim.derating_timeout_s` bounds the operating-point audit independently; the DAC project sets 30 seconds. Timeout stdout/stderr is retained, and a timed-out run is never cached as a successful result. Nonzero exits, simulator errors/aborts, missing pin voltages or currents, and nonfinite values now fail the audit. Stage simulation then fails and the report says `FAILED`; it cannot silently show an empty successful audit.

Regression cases cover partial timeout logs, parser mode, distinct cache keys, stage/report propagation and absent/nonfinite audit measurements. Independent review caught an intermediate case where currents existed but voltage measurements were absent; the added regression failed before the final check was implemented.

## Design round trips

Saving a datasheet URL containing `?` in a flow-style mapping could produce YAML accepted by ruamel's emitter but rejected by hwflow's PyYAML loader. A local round-trip emitter now quotes those flow scalars while preserving block style, existing quoted values, comments and ordering. Regression coverage includes maps, nested flow sequences and a second save/load cycle.

## Verification

The final fast suite passed **304 tests**, with 2 skipped and 14 slow tests deselected, in 88.25 seconds in the pinned container. Independent follow-up ran 11 focused regressions, a live sensor-preamp audit (10 component rows), and a native/PSpice cache-key comparison. No full slow suite or merge was performed.

The project simulation passed all **14 measures**, including both channels at 0/25/60 °C, AC response at 20 Hz/1 kHz/20 kHz, and output DC. All **50 Monte Carlo trials** met the amplifier-stage output limits. These ideal-rail model results do not qualify the physical power supply, DAC full scale, startup behavior or headphone protection; those have separate evidence and bench gates.
