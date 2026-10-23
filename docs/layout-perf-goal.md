Goal: make the hwflow layout stage converge on its own, quickly and reproducibly, then prove it with a full UI run of `home-thermostat-3` driven through Claude in Chrome, compared against the 2026-10-19 baseline.

## Setup

- New worktree `/home/arosso/Personal/electron-layout`, branch `layout-perf`, created from `backlog-impl` (HEAD `71b7830`). Copy `tools/KiCadRoutingTools` from `/home/arosso/Personal/electron-backlog/tools/` (it is untracked). Never touch `main` or the other worktrees.
- This session is the orchestrator. It does not write engine code itself: it spawns Sonnet subagents (`model: sonnet`) for each work item, reviews each diff, runs the tests and commits. The session runs with a 400k autocompact window; the UI run gets the same from `evals/harness.py` (`CLAUDE_CODE_AUTO_COMPACT_WINDOW`).
- Subagents run one after another when they touch the same files (`pcb.py`, `cli.py`). They run in parallel only when their files are disjoint (for example L6 against L1).

## Evidence (from the 2026-10-19 baseline)

- hw-layout: about 4.3 h wall, about 2.8 h of it waiting on tools, 641 turns, 83 M tokens. The last layout session made 35 full rebuilds in 40 min and kept switching between the same 1 to 3 errors (GND zone islands, `RLY1_IN1` unconnected, via-to-track clearance).
- `run_krt` passes `--clearance 0.15` to every router pass, the 2.5 mm mains net class included. The router log says `design_rules: count 0`, and it warns that no `.kicad_pro` sits next to the input board.
- Rebuilding the final PASS `design.yaml` on `backlog-impl` (plain `stage_layout`) gives 40 DRC errors (19 clearance, 8 board/slot edge, 7 creepage, 4 unconnected, 2 items not allowed). The router reported 0 failures.
- One `stage_layout` on the baseline takes 53 s: placer 0.1 s, 4 router passes 37 s (core routing only 2.9 s), `kicad-cli` DRC with zone refill 15 s.

## Work items

One commit per item. The message names the item (L1 to L7). Trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Every item needs a deterministic test in `tests/` or a measured number on the baseline copy.

1. **L1: the router sees the DRC rules.** Before every router pass, write the `.kicad_pro` and `.kicad_dru` that DRC uses next to the pass input: net class clearances and widths, `board.isolation` rules, edge and slot clearance, keep-outs and creepage where the router supports them. Pass a clearance per class instead of one global value. Test: a fixture with a 2.5 mm class net routes with 0 clearance errors in DRC. Measurement: routing the baseline design once gives 0 `clearance`, `copper_edge_clearance` and `creepage` errors (today 19, 8 and 7). If the router cannot honour a rule type, record it in `docs/` and leave that rule to item L2.
2. **L2: repair instead of a rebuild.** After DRC, rip up only the nets named in the errors (and nets within a small radius of them), then re-route them with `--keep-input-copper`. Repeat for at most N rounds and stop when a round brings no improvement. Never accept a round with more errors than the one before. Fix GND zone islands with stitching vias, or remove the islands when that is allowed. `layout --auto` uses the repair loop before it falls back to a new placement seed. Test: a board with a broken net is repaired without changing the copper of the other nets. Measurement: time of one repair round on the baseline (target 10 s or less).
3. **L3: reproducible layout.** Cache the routed board under a hash of placement, rules, netlist and router version, and make `--reuse` reuse that routing, not only the placement. Test: building the same input twice gives byte-identical `.kicad_pcb` files (or identical copper after normalizing the timestamps and UUIDs) and the same DRC result.
4. **L4: less fixed overhead.** Run the router passes in one process if KiCadRoutingTools allows it (as a library, or one call that takes every width class), and avoid a zone refill per pass. Inside the repair loop, use a fast in-process pre-check. Run the full `kicad-cli` DRC once per accepted candidate. Measurement: one full layout of the baseline at 20 s or less (today 53 s). If the target is not reachable, report the measured breakdown and why.
5. **L5: parallel `--auto` attempts.** Run placement and routing attempts in a process pool with cpu-1 workers. The pick stays deterministic: the same winner whatever the completion order. Measurement: wall time of `layout --auto` on the baseline, before and after.
6. **L6: post-layout cache.** Cache each analysis result under a hash of its netlist text, so the post stage never re-simulates an analysis whose netlist did not change. This builds on items 26 and 27. Measurement: an unchanged post rebuild takes under 5 s, and a post rebuild after a layout-only change runs only the post analyses.
7. **L7: hw-layout prompt.** The agent runs `layout --auto`, which repairs on its own. It escalates only residual errors that need judgment (a part that cannot meet creepage where it is, a board size change), with the error list and a rendered PNG. It does not hand-tune coordinates. Set `maxTurns` for hw-layout from the new measured numbers. Keep the change small and say in the commit which behaviour it targets.

## Regression gate (before the UI run)

- `uv run pytest -q tests/test_core.py tests/test_em.py tests/test_postlayout.py` passes, and `tests/test_pipeline.py` passes once at the end.
- Copy the baseline project to the scratchpad and run `uv run hwflow build <copy> --auto` twice. Both builds: PASS, 21/21 requirements, SPICE 163/163, DRC 0 errors, 0 unrouted, identical boards. If the baseline cannot reach PASS without a design change, report why and do not edit the baseline.
- Write `docs/layout-perf-result.md`: item, commit, test or measurement, before and after numbers.

## UI run: home-thermostat-3

- Start `uv run python ui/server.py` from the new worktree, in the background, and open `http://127.0.0.1:8765` with Claude in Chrome. Create project `home-thermostat-3`, model `opus` (the same as the baseline orchestrator), and paste the brief below.
- Answer each question round in the UI from `evals/baselines/home-thermostat-2026-10-19/project/inbox/answers.md`, matched by topic. The brief and the v2 relay answer take precedence over the old answers: 2×AAA directly, a small relay (solid state allowed), the 3-module box as a hard constraint, priority size, then price, then battery life. For a question that has no match, answer "No preference, use your judgment." Log every question and the answer given in `docs/replay-v3-answers.md`, and mark the improvised ones.
- Wait inside the turn with Monitor or blocking polls on the project state. Do not leave the run unattended at the end of a turn. If the browser tools fail 2 or 3 times, stop and ask the user.
- When the state is `done`: `uv run python scripts/usage_tally.py home-thermostat-3 --compare evals/baselines/home-thermostat-2026-10-19/metrics.json`, `uv run hwflow trace projects/home-thermostat-3`, and the timing analysis of the transcripts (layout wall time, number of build calls, number of layout rebuilds). Write `docs/replay-v3-result.md` with the comparison table and observations.

Brief for home-thermostat-3:

> A battery-powered thermostat that fits inside a standard Italian 3-module wall box (503 box; look up its inner size and the usable depth behind the cover plate, which is a hard constraint). Two NTC temperature probes on cables shorter than 2 m, no screen, Wi-Fi for remote control (MQTT), and it must keep working offline, with an RTC for the heating schedule. Power: two AAA cells in series, used directly, with protection against cells inserted backwards; target battery life of 1 year or more. There is no mains supply: the device runs only on the batteries. Two small relays (latching or solid state, both fine) switch two 230 V AC thermal valve heads independently, one per room, up to 1.5 A each. Keep reinforced isolation between the 230 V side and the low-voltage side, and keep the Wi-Fi antenna away from the relays and the mains copper. It is a prototype of 10 units, target under 25 USD each. Priorities: size first, then price, then battery life.

## Stop and ask the user

- Any tool change that alters a product result (layers, board size, BOM cost) or needs a new install on this machine.
- A product question during the UI run that affects cost or safety and that neither the brief nor `answers.md` covers.
- Merging `layout-perf` into `main` or `backlog-impl`. That is the user's decision.

## Done when

- L1 to L7 are each committed with a test or a measurement, or rejected with a reason in `docs/layout-perf-result.md`.
- The regression gate passes.
- `home-thermostat-3` reaches `done` with a PASS verdict and every requirement verified, and `docs/replay-v3-result.md` compares it with the baseline: tokens by agent, hw-layout turns and wall time, number of layout rebuilds, build calls, product rounds, verdict.
- Expected results, stated as targets and not promises: hw-layout wall time under 1 h (baseline 4.3 h), fewer than 10 layout builds by the agent (baseline about 70), and hw-layout tokens down by more than half (baseline 83 M).
