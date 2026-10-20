# Layout performance: result (branch `layout-perf`)

Goal: `docs/layout-perf-goal.md`. Base: `backlog-impl` at `71b7830`. Baseline: the 2026-10-19 home-thermostat run
(`evals/baselines/home-thermostat-2026-10-19/`), where hw-layout took about 4.3 h of wall time (2.8 h of it waiting on
tools), 641 turns and 83 M tokens, and the last layout session made 35 full rebuilds in 40 min, switching between
the same 1 to 3 errors. `scripts/run_timing.py` gives these numbers from the transcripts.

## Items

| item | commit | test or measurement | before | after |
|---|---|---|---|---|
| L1 router sees the DRC rules | 8646f35 | `tests/test_router_rules.py`; baseline rebuild, DRC errors by type | 19 clearance, 8 copper_edge_clearance, 7 creepage, 4 unconnected (router reported 0 failures; it got `--clearance 0.15` for the 2.5 mm mains class and no rule files) | 0, 0, 0, 0 |
| fix: quoted pad numbers and layers | c7bd6ff | `test_board_writes_quoted_pad_numbers_and_layers`; sensor_preamp `--until layout` | 7 unconnected track-to-pad (the router saw no pads on U1/U2 because system footprints write `(pad 1 ...)` unquoted); repair could not fix them (7 -> 7) | 0 DRC errors |
| L2 repair loop | f3ba6c5 | `tests/test_repair.py` (repair one net without touching others, reject a worse round, stitch a GND island); damaged baseline board | a full rebuild per error, about 50 to 60 s, not monotonic | 20 -> 2 errors in 1 round, 6 nets re-routed, 9.7 s per round with `drc_fast` |
| L4 overhead | 3ec031c | `tests/test_drc_fast.py`; profile of one baseline `stage_layout` | DRC 14.5 s per check | `drc_fast` 2.5 s, same errors except creepage (used per repair round; full DRC once on the kept board). One full layout stays at about 50 to 60 s: target of 20 s not reached, see below |
| L6 post-layout cache | 7b43efe | `test_analysis_cache_runs_ngspice_once_per_netlist`; baseline post stage | 20.7 s cold, every post analysis re-simulated after any layout change | unchanged 2.2 s; layout change on a net no analysis sees 2.1 s; only analyses whose netlist changed re-run |
| L7 hw-layout prompt | 31ac500 | prompt diff | manual loop of coordinate nudges | `build --until layout --auto --brief`, no coordinate tuning for routing errors, at most 3 placement edits for named non-routable errors, then escalation with an error list and a PNG; `maxTurns` 300 -> 80 |
| L3 reproducible layout | 7d5bb7e | `tests/test_layout_perf.py` (reproducible board text, cache hit skips the router, new key on a rule change) | board UUIDs were uuid4, so no two builds were equal; `--reuse` always re-routed | route cache under `build/routecache/`; second thermostat `--auto --reuse` 28 s (cache hit, mostly the full DRC); boards identical after UUID normalization |
| L5 parallel `--auto`, early stop | 7d5bb7e | `tests/test_layout_perf.py` (deterministic pick, early stop, no pool when attempt 1 is final, per-attempt dirs) | thermostat `layout --auto` 190 s, 4 sequential attempts ending with the same 2 errors on fixed parts | 74 to 89 s, 1 attempt ("stopped: remaining errors are on fixed parts"); attempt 1 runs alone, the pool starts only if it is not final |
| L14 keep-out check independent of `fp-lib-table` | 694aaf7 | `tests/test_keepout.py` | KiCad drops footprint and pad keep-out errors when the keep-out footprint's library resolves; hwflow always ran that way | own check (0.15 s), adds exactly BT1 and U2 on the baseline; off by default (`HWFLOW_KEEPOUT_STRICT=1`), proposed field `board.keepout_check: strict` |

## Regression gate (first goal)

Commit `7d5bb7e`, two `hwflow build <copy> --auto` of the baseline from scratch (108 s and 124 s each, full build
including SPICE): both PASS, 21/21 requirements, SPICE 163/163, DRC 0 errors, 0 unrouted, 94 vias, 529 segments.
The two boards are identical after normalizing the UUIDs that `kicad-cli drc --save-board` assigns. DRC here
is hwflow's default (keep-out strict off), see L14.

## L4: why one full layout is not 20 s

Profile of one baseline `stage_layout` (61.6 s on a loaded machine, 49 to 53 s on a quiet one): placer 0.1 s; four
router passes 42.8 s; `kicad-cli` DRC 18 s.

- The router passes are routing work, not process start-up. About 3.5 s per pass is fixed cost: imports 0.7 s, parse
  and obstacle map 1.2 to 1.7 s, and the pour-guard refill 1.2 s. The 0.25 mm pass is 24.5 s, of which phase-3 tap
  routing is 11.7 s, including 7.9 s of plane-fragility refresh.
- These options were measured and rejected because each cost routing quality:
  - one invocation for all width classes: 101 s and 2 failed nets
  - merging the two narrowest classes: 2 unrouted
  - the plane-fragility knobs: 2 to 17 unrouted
- DRC time is the creepage rule, not zone refill. KiCad evaluates creepage over every item pair: the DRC takes 2.5 s
  without the rule and 17.6 s with it alone. Adding `(layer outer)` to the rule does not change the time (19.2 s in
  both cases, same errors).
- Reaching 20 s needs a change in the router (cheaper fragility refresh) or in KiCad. Both are outside this work.

What a layout costs the agent now is one full layout plus repair rounds of about 10 s each, instead of a full
rebuild for every error.

## Finding: the baseline's DRC verdict depends on `fp-lib-table`

On the baseline design, the courtyard of BT1 (2xAAA holder, fixed at 71.2, 29.65) spans x 69.7 to 127.2 and y 17.35 to
41.95, so it covers both `KO_STACK` keep-out areas (x 77 to 96 and 101 to 120, y 25.8 to 40, `footprints
not_allowed`). KiCad 10.0.7 reports `items_not_allowed` for BT1 and U2 when the board is checked alone, and reports
nothing when `fp-lib-table` sits next to the board, which is how hwflow always runs DRC. The original run's
"DRC 0 errors" came from this.

Whether this is a defect is a design question. The keep-out is meant to keep front-board copper away from the rear
230 V terminals, and BT1 sits on the front side. The baseline is not edited. For the user: either the keep-out
should forbid only copper (tracks, vias, pads, pour), or BT1 must move.
