# home-thermostat-3: result of the web UI run (branch `layout-perf`)

Run through the web UI (`ui/server.py` in the `electron-layout` worktree), Opus orchestrator, improved brief (2xAAA used directly, reverse-polarity protection, small relays, Italian 503 box, isolation, antenna away from mains). Answers and every operator direction: `docs/replay-v3-answers.md`. Baseline: the 2026-10-19 home-thermostat run (`evals/baselines/home-thermostat-2026-10-19/`).

## Outcome

**Not finished.** The run stopped in stage 7 (layout) after 9 layout builds. That is the "stop when stuck" point: the remaining errors need hwflow or router features, not more attempts. Review 2, sign-off and SUMMARY did not run.

| | home-thermostat-3 | baseline |
|---|---|---|
| verdict | FAIL (layout and post-layout) | PASS |
| requirements | 35/35 verified by a check, 2 failing (R4, R10) | 21/21 |
| SPICE | 91/91 before layout, 88/91 after | 163/163 |
| board | 88 x 46 mm, 4 layers, parts on both faces | 130 x 40 mm, 4 layers, 2 stacked boards |
| layout | 7-8 unrouted, DRC 12 | DRC 0 (with the `fp-lib-table` false pass, see layout-perf-result.md) |
| BOM | about 21.5 USD parts; 33-38 USD per prototype accepted | 27.94 USD |

The product is harder than the baseline: one 88 x 46 mm board in the 503 box instead of two stacked boards, a 6 mm reinforced gap, and parts on both faces.

## Cost and time

| | home-thermostat-3 | baseline |
|---|---|---|
| tokens | 80.5 M | 222.8 M (-64 %) |
| Opus / Sonnet | 56.6 / 23.9 M | 104.8 / 118.0 M |
| hw-layout | 5 launches, 197 turns, 11.1 M, wall 97 min (77 min in tools) | 5, 641 turns, 83.4 M, wall 4.3 h |
| hw-circuit | 4, 225 turns, 31.9 M, wall 96 min | 7, 458 turns, 60.6 M |
| orchestrator wall | 348 min (includes operator pauses at night) | |
| build calls | 57, of which 27 reach layout | |

Numbers from `scripts/usage_tally.py home-thermostat-3 --compare ...` and `scripts/run_timing.py home-thermostat-3`.

The layout agent behaved as the new prompt asks:
- one `--auto` build per cycle, no coordinate tuning;
- an escalation with error counts and a PNG after each build;
- in the baseline, the last layout session alone made 35 rebuilds in 40 min.

The time went into structural problems: board size, the router setting, 230 V zones, track widths.

## What the run found and fixed in hwflow

All commits are on `layout-perf`. Details are in `docs/layout-perf-result.md`, "Fixes found during the home-thermostat-3 run".

- **Router setting `KICAD_RIP_PREEXISTING=0`.** Earlier-pass copper boxed in the narrow pass's pads. Same floorplan: 73 -> 11 unrouted. The baseline is identical either way.
- **Route cache key.** It now includes the router environment and the router-driver code. A stale cache hid the fix until the cache was deleted.
- **`layout --auto` structural stop.** When attempt 1 is far from routable (more than 40 errors), no more attempts run. It saved about 15 min of parallel attempts on the 2-layer board.
- **Footprint zones move with the part.** Standard footprints such as the ESP32 antenna keep-out were placed wrong. The baseline's KO_* parts must move to (0, 0); see the open decisions.
- **`--reuse`.** A placement made for another declared board size is no longer reused, and fixed parts are never moved.
- **Agent prompts:**
  - design to the product number, with no silent margin in a `must`;
  - at most 3 builds per failing measure;
  - escalate a structural stop at once, layers first.
- **`hwflow lib show`.** Pin numbers that mix letters and digits no longer break the sort (the agent's fix).

## What blocks the layout now (tool work, the user decides)

1. **Escape from fine-pitch pads.** The nets of U2 (TPS61299, 0.5 mm pitch: SW_BOOST, VBAT_P, +3V3) stay open even at 0.3 mm. Narrow tracks also cost the R4 margin: the guard drop is 54-60 mV against 50 mV, about 21 mV of it from 30 mOhm of narrow track. hwflow needs a neck-down: narrow only at the pad, full width elsewhere.
2. **Mains creepage to pads.** The router checks the mains class-pair clearance only track to track. A 230 V track passed 1.38 mm from a low-voltage pad (C15), so R10 failed again.
3. **Region placement.** The placer cannot keep a group (the 230 V parts) inside a region, or low-voltage parts outside it. The agent used fixed anchors and a rule area instead.
4. **Placer box test.** Round keep-outs are tested as boxes, which raised a false F1 flag.
5. **Post-layout IR drop.** It reports 0.00 mV on nets that are split into islands. It should report the open net instead.

## Decisions for the user

- Implement 1-3 above (router or hwflow work) and resume the run from `state.md`, or accept the design as is.
- Baseline: place KO_ANT, KO_MAINS, KO_BAND and KO_STACK at (0, 0, 0), or keep the old zone semantics.
- Product answers I made up while you were away, all marked in `docs/replay-v3-answers.md`:
  - 33-38 USD for the prototypes;
  - an 88 x 46 mm board with notches (inside the 503 box);
  - 4 layers;
  - sleep floor 26 uA.
- Merging `layout-perf`.
