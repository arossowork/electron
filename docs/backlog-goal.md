Goal: implement the open items of the hwflow improvement backlog, in this repo, on branch `token-opt` (worktree `/home/arosso/Personal/electron-token-opt`), and prove each one with a deterministic test or a measured number.

## Context

`hwflow` (src/hwflow/) is a deterministic EDA engine. Claude Code runs the design flow (`/hw-design`, agents in `.claude/agents/`, skill in `.claude/skills/hw-design/`) through `claude -p`, started by `ui/server.py`, `evals/harness.py` or `scripts/hw-run.sh`. One full run was done on 2026-10-19/20 (project home-thermostat, through the web UI) and saved as a baseline:

- `evals/baselines/home-thermostat-2026-10-19/` (README.md has the numbers, the code state and the replay steps)
- `docs/token-improvements.md`: the backlog, items 1 to 59, with evidence and the file to change. Status is `open`, `applied` or `rejected`. This is your task list.
- `docs/token-economy.md`: how the headless sessions spend tokens; keep it in sync with what you change.
- `scripts/usage_tally.py`: token and turn tally of a run, with `--compare <baseline metrics.json>`.

The baseline cost 222.8 M tokens (97.7 % cache reads) and about 6.3 h of agent turns. Where it went: hw-layout 83 M (641 turns, a hand-made loop of about 30 layout builds), hw-circuit 61 M, hw-parts 35 M, orchestrator 27 M. The goal behind the backlog is a run with the same result in fewer tokens and less wall time.

## Rules

- Read `CLAUDE.md` first. Its rules about `design.yaml` and `build/` bind the design agents. You are changing the tool and the prompts, so you edit `src/`, `tests/`, `docs/`, `ui/`, `scripts/`, `.claude/`.
- Do not edit anything under `projects/` or `evals/baselines/`. Use a copy in the scratchpad when you need a project to build.
- One commit per item, or per small group of items that share a file. The message names the item numbers. Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- After each commit, set the item's status in `docs/token-improvements.md` to `applied` with the commit hash, or to `rejected` with the reason. Do not mark an item applied without a test or a measurement that shows the effect.
- Tests: `uv run pytest -q tests/test_core.py tests/test_em.py tests/test_postlayout.py` after every change (about 70 s). Add tests for new behaviour in `tests/`. Run `tests/test_pipeline.py` only at the end of a group, in the background.
- Regression check on real data: rebuild the baseline design from a copy (`cp -r evals/baselines/home-thermostat-2026-10-19/project <scratch>/p`, then `uv run hwflow build <scratch>/p --reuse`). The expected result is PASS, 21/21, SPICE 163/163, DRC 0 errors. A change that moves those numbers needs an explanation in the commit.
- Do not run long jobs in the foreground of an agent turn: use a blocking call with a timeout, or run in the background and poll. A build that is started and left behind when your turn ends gets killed (backlog 50).
- Anything that changes cost or a product decision for the user (layers, BOM, board size, a model change for a design agent) is not yours to decide. Write the proposal in `docs/` and stop for the user.

## Order of work

Do the groups in this order. Inside a group, do the items with the smallest change first.

1. **Wrong or misleading reports (hwflow, deterministic).** Items 20 (FAIL verdict with 0 failing measures), 19 (dbg probes out of the verdict), 22 and 42 (say why an analysis has no value; per-analysis `timeout_s`), 41 (retry once on an ngspice signal crash), 55 (keep seed, values and log of failing Monte Carlo runs), 43 (keep stale results with a marker), 39 (report the minimum distances that requirements depend on), 18 (build history), 58 (design hash ignores comments), 54 (trace checks that evidence files exist), 17 (a `Q` reference with a MOS model fails `check`), 46 (post-layout probe node mapping), 56 (footprint against package).
2. **Time and tokens inside hwflow.** Items 23 and 25 (`layout --auto`: placement, routing, DRC and area search in one call, with retries of the router in a different net order, item 40; returns only the final numbers), 27 (skip a build when the design hash is unchanged; Monte Carlo once per stage), 26 and 47 (validate the post-layout lumping against the full network on one analysis, exempt nets declared fast, state the scope in the docs and the report), 8 (`build --brief`: print only what changed).
3. **Constraints as data.** Items 52 and 53 (a `stack:` and `keepouts:` section in `design.yaml`, drawn and checked by hwflow, including part height against the gap), 38 (generate the DRC rule file from the design), 59 (one gerber, BOM and position set per board), 44 (a `check` rule for changes of layers, board size or BOM cost against the last approved baseline), 48 (signal-integrity screen without SPICE). Write a short design note for 52, 53 and 59 first and ask the user to confirm the field names before you implement.
4. **Agent prompts and skills.** Items 24 (realism check for every testbench scenario), 57 (delta review after a late change), 37 (a decision that needs the product person becomes `inbox/questions.json`), 50 (the agent waits inside the turn for builds it started), 1, 2, 3, 9, 16, 28, 29, 30 (datasheets as text, fetch once, batched lookups, caps on searches, compact briefs, fewer rounds), 4 (`maxTurns` per agent, from the measured turn counts in the baseline README). Keep each prompt change small and say in the commit what behaviour it targets.
5. **UI.** Items 32 (render Markdown), 33 (group and collapse tool events, page the history), 34 (queue a message while the agent works), 35 (usage per turn and per subagent, live), 36 (questions count), 51 (status from the process, not the first result).
6. **Measurement.** Items 12 and 14: `scripts/usage_tally.py` exists; add the per-stage wall time from the UI history, and the cache write against read check for `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`.

Leave these `open` and say why in your summary: 5, 6, 7, 11, 31 (model, effort and session-split choices need an eval run on the new code, which the user starts), 10, 13, 49 (need investigation or an install on this machine; report what you find in a note under `docs/`), 15 (rejected).

## Done when

- Every item you touched has a status, a commit hash or a reason in the backlog.
- All the fast tests pass and the baseline design rebuilds with the same result.
- `docs/token-economy.md` describes the flags and behaviours as they are now.
- You leave a table in `docs/backlog-result.md`: item, commit, test or measurement, expected effect on tokens or time. The user will replay the baseline brief through the UI from the final commit and compare with `scripts/usage_tally.py --compare`, so state which items you expect to move which agent's numbers.
