# Backlog result

Branch `backlog-impl`, on top of `cc9a5b2`. Code state of the last engine, prompt and UI change: `990b77c`. The commit "Backlog: statuses, token-economy sync, result table" follows it and changes only docs and one line of SKILL.md (`git log -1` on the branch gives its hash). Statuses per item are in `docs/token-improvements.md`; flags and behaviours are in `docs/token-economy.md`.

Baseline: 222.8 M tokens (hw-layout 83.4, hw-circuit 60.6, hw-parts 34.6, orchestrator 27.4, hw-architect 8.2, hw-reviewer 7.8, hw-requirements 0.5), 97.7 % cache reads, 6.3 h of agent turns. Tokens scale with turns times context, so the expected effects below are fewer turns, shorter reports and fewer rebuilds. They are expectations, not measurements of a new run.

"Test" is a deterministic test in `tests/` unless it says measurement. Prompt and UI items have no deterministic test; the evidence is the diff and the replay.

## Table

| item | commit | test or measurement | expected effect and agent it should move |
|---|---|---|---|
| 8 build --brief | cef43d9 | tests; unchanged rebuild prints 3 lines | shorter loop output in every re-read context. hw-layout, hw-circuit |
| 27 skip unchanged stages, Monte Carlo once | cef43d9 | tests; baseline unchanged rebuild 72 s to 0.5 s | fewer builds that rerun SPICE. hw-layout, hw-circuit |
| 23, 25 layout --auto | 194610e | test; measurement: 46 s, one call, on the baseline | replaces the hand-made loop of about 30 builds (641 turns). hw-layout |
| 40 router net-order retries | 3dc01af | test | fewer manual router retries. hw-layout |
| 26 post-layout lumping, reuse of pre-layout results | 1ea88ed, 194610e | measurement: post 18.3 s to 12 s on the baseline | shorter post-layout waits. hw-layout, hw-circuit |
| 47 lumped against full network | 194610e, d29bda7 | test on the example; reverse_tran max difference 3.8 % | fewer disputes about post-layout results. hw-layout |
| 38 board.isolation | 5139366 | test; baseline 230 V rules reproduced | removes the hand-run kicad-cli check. hw-layout |
| 20 FAIL verdict with 0 failing measures | fce0a48 | test | fewer wrong debug turns. hw-circuit |
| 19 dbg measures out of the verdict | 337c2df | test | hw-circuit |
| 22, 42, 41 reasons, timeout_s, crash retry | 1ea88ed | test | fewer turns spent on n/a measures and flaky crashes. hw-circuit |
| 55 failing MC runs kept | 5ce69dc | test | hw-circuit |
| 58 design hash ignores prose | 14184bd | test | fewer needless reruns. hw-circuit, hw-layout |
| 17, 56 model type and package checks | b357d7b | test | errors found at check, not in SPICE. hw-circuit, hw-parts |
| 43, 18 stale marker, build history | 255a395, d29bda7 | test | hw-circuit, orchestrator |
| 46 post-layout probe node mapping | 7bd9df4 | test | hw-layout, hw-circuit |
| 39 minimum distances in post-layout | 2f0a6c4 | test | hw-layout |
| 44 hwflow approve | 4917eaa | test | catches unapproved layer, size or cost changes. orchestrator |
| 48 SI screen | 3444d5a | test; about 1 s on the baseline | hw-layout |
| 54 trace evidence files | fbcad49 | test | report only; verdict unchanged |
| 24 realism check per testbench scenario | 558567a | prompt diff | hw-circuit, hw-reviewer |
| 37 questions.json for decisions | 6981b83 | prompt diff | orchestrator |
| 50 wait inside the turn for builds | 6981b83, ea5dcc9 | prompt diff | no killed builds, no rework. orchestrator |
| 57 delta review | 6981b83, ea5dcc9 | prompt diff | smaller second review. orchestrator, reviewer |
| 9 compact briefs | 6981b83 | prompt diff | orchestrator, architect |
| 28 parallel parts groups | 6981b83 | prompt diff | wall time. hw-parts |
| 1 datasheets as text | ea5dcc9 | prompt diff | no page images in context. architect, hw-parts |
| 2 one fetch, notes/datasheets | ea5dcc9 | prompt diff | architect |
| 3 about 5 searches per stage | ea5dcc9 | prompt diff | architect |
| 16, 30 batched lookups | ea5dcc9 | prompt diff | fewer turns (147 in the baseline). hw-parts |
| 29 requirements asks in round 1 | ea5dcc9 | prompt diff | fewer rounds. requirements |
| 4 maxTurns | c46e850 | frontmatter diff | guard only |
| 32, 33, 34, 35, 51 UI | 4c82f50 | not tested in a browser | no token effect; 34 saves restarts |
| 12, 14 usage tally | a0ba0d2 | measurement: 45 cache reads per write; 5 min and 1 h TTL equal within 0.1 % (`docs/cache-ttl-note.md`) | none; the TTL is not a lever |
| 52, 53, 59 | ebbb663 | design note only | waits for field names |
| 21 | earlier | | |

## Left open and why

- 5, 6, 7, 11, 31: model, effort and session-split choices need an eval run on the new code, which the user starts.
- 10, 13, 49: need investigation of the `lib sym` output, a Bash output log check, or an openEMS install.
- 36: could not reproduce. The UI now shows the count and tolerates odd shapes; the bug is not found.
- 45: `scripts/usage_tally.py` exists; calling it at the end of a run is not wired.
- 52, 53, 59: `docs/design-note-stack-keepouts-boards.md` waits for the field names (`boards:`, `stack:`, `keepouts:`).
- 26: the larger time step outside the measured window is not done.
- 15: rejected (little value for this flow).

## Known caveats

- `tests/test_pipeline.py` fails 2 of 2 with `7 == 0`, also on unmodified source.
- `maxTurns` for hw-layout and hw-circuit are estimates; per-launch maxima were not measured for them.
- The UI was not clicked through in a browser. Item 35 assumes `parent_tool_use_id` in the stream, untested on a real stream.
- The worktree has no `tools/`: use `KRT_DIR=/home/arosso/Personal/electron-token-opt/tools/KiCadRoutingTools`.
- Item 54 only reports; it does not change the verdict.

## Replay

1. Commit the code to test (this branch, final commit as above).
2. `uv run python ui/server.py`, open the UI, create a project with a new name (for example `home-thermostat-b`), paste `evals/baselines/home-thermostat-2026-10-19/project/brief.md`, model `opus`.
3. Answer each round from `project/inbox/answers.md`, matching by topic. For an unmatched question answer "No preference, use your judgment." and write it down.
4. When the state is `done`:

```
uv run python scripts/usage_tally.py home-thermostat-b --compare evals/baselines/home-thermostat-2026-10-19/metrics.json
uv run hwflow trace projects/home-thermostat-b
uv run hwflow build evals/baselines/home-thermostat-2026-10-19/project   # expect PASS, 21/21, SPICE 163/163, DRC 0
```

5. Compare tokens by agent, verdict and requirement count, product questions and rounds, build calls and agent wall time (`usage_tally.py --history`). Expected movers: hw-layout (23, 25, 40, 27, 8, 38), hw-circuit (20, 19, 22, 42, 24, 27, 41, 55, 58, 17), hw-parts (1, 16, 28, 30, 56), orchestrator (37, 50, 57, 9), architect (1, 2, 3, 9). One run is noisy: repeat before trusting a gain of a few tens of percent.
