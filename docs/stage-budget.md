# Stage time budgets and model routing

Target: thermostat requirements to a print-ready layout and firmware in 6 h of work (product-answer time excluded). The early, agent-driven stages took 1.5 to 2+ h and varied a lot between runs; sign-off rework cost 2+ h in run 4. This note gives the budget per stage, the model of each subagent, and the evidence for both, from the two timed e2e runs:

- run 4: `electron-e2e-4/projects/thermo-e2e-4` (6.8 h, ended `blocked` at sign-off on bench-only and certificate items);
- run 5: `electron-e2e-5/projects/thermo-e2e-5` (stopped at 3.5 h in stage 4).

Sources: `.e2e/run.log`, `state.md`, `uv run hwflow time`, and the session transcripts (`~/.claude/projects/-home-arosso-Personal-electron-e2e-N/<session>.jsonl` and `subagents/agent-*.jsonl` with their `.meta.json`), summed per agent: wall minutes, model minutes (tool result to next message), tool minutes, builds, web lookups, reads.

## Budgets

| stage | run 4 min | run 5 min | budget | where the time went (evidence) | rule now |
|---|---|---|---|---|---|
| 1 requirements | 5 | 21 | 10 | run 5: a "C1 study" (Wi-Fi parts that run straight from 2 x AAA) in stage 1: 22 web searches over 3 requirements agents (5.4, 4.8, 5.8 min), the second hit its turn limit; lead polled 4-5 min for a background agent's files in both runs | no part survey in stage 1 (at most 5 searches, the survey goes to stage 2); `run_in_background: false`, no polling loops |
| 1b partition | 7 | 8 | 8 | one Opus agent, 21-30 messages, 5-7 files read; no waste, model time 4.5-5.1 min | Sonnet (below) |
| 2 architecture | 18 | 20 | 20 | one Opus agent 14-17.5 min, 6-7 floorplan runs of about 10 s | none needed; budget = measured |
| 3 parts | 38 | 53 | 30 | parallel parts groups 14 min (critical path); run 5: parts review returned 20 findings, the fix pass took 21 min (107 messages, 10 web lookups); run 4 needed an extra narrow parts agent (3 min) | fix pass only for `part change` findings, at most 8, 15 min, 3 lookups; the rest to the circuit brief |
| 4 circuit | 48 | 112 | 40 | capture itself 37-44 min (Opus, 22-23 model min; `--until sim` builds take 9-10 s, so builds are not the cost); run 5: floorplan rework by `hw-architect` 58 min, 5 floorplan runs, two single model turns of 19 and 17 min (647k output tokens) | floorplan rework: one agent, two candidates, two runs, 15 min; test with `hwflow floorplan` instead of long reasoning |
| 5 review | 53 | - | 25 | run 4: review 13.6 + fix batch 10.9 + delta review 8.9 + parts swap 3.7 + a second delta review 8.9 min | one delta review in stage 5; later changes go to the sign-off review |
| 7 layout | 82 | - | 45 | four `hw-layout` passes (16, 0.4, 27.7, 19.4 min) plus a contract fix; model time is under 5 min per pass: the time is router builds of 8-10 min each | at the budget, the layout agent escalates with the diagnosis instead of another pass |
| 8 sign-off | 157 | - | 30 | full final review 14.2 min found routing rules (VBAT 0.8 mm, crystal on F.Cu, J1 rule area) that invalidated the layout; final layout pass 20 min; then the lead ran six layout builds itself (about 105 min of `run wait`), one refused as blocked, one with 8 attempts; `+3V3` 0.6 mm made U1 unroutable and was taken back | stage-5 review covers those routing rules; sign-off review is a delta review; the lead runs no layout builds; the two-build cap counts every build |
| 9 firmware | 18 (beside layout) | - | 45 | one Opus agent, 85 messages, 27 host tests; off the critical path | Sonnet (below) |

Sum of the budgets on the critical path (1 to 8, optimize included): 228 min, which leaves about 2 h of the 6 h for one rework loop. Run 4 spent 409 min on the same stages.

## How the budget is enforced

- `uv run hwflow budget <proj> [--json]` reads the stage marks (`build/flow.jsonl`) and prints the current stage, its minutes against the budget, and `ok`, `near` (80 %) or `over`, with the action. A stage number covers its sub-marks (`3-parts-fix` counts against `3-parts`), so rework spends the budget of the stage it goes back to. `hwflow time --mark <stage>` prints the budget of the stage that starts.
- Overrides: `<proj>/budget.yaml` (`{3-parts: 40, 7-layout: 300}`, for example for the 864-part keyboard) and `HWFLOW_STAGE_BUDGETS="3-parts=40,7-layout=60"`.
- `hwflow time <proj> --mark wait` while the product person answers: no budget, reported apart. The e2e driver writes these marks itself when a turn ends on questions and when it resumes.
- The skill (`/hw-design`, "Stage time budgets") makes the lead check at every checkpoint and brief every subagent with its time box; each agent file has a "Time box" paragraph. At `over` the stage returns its best state with the open items in `state.md`. A budget stops polish (`should`/`nit` fixes, margin, a second cost-down pass, more searches, another review round, hand tuning) and never waives a gate: an open gate at the budget goes to the product person with the numbers, or to `blocked`.
- The e2e driver logs `STAGE ... left after N min (budget B, status)` and `BUDGET <stage> near|over` to `run.log` while the run goes, and adds the budget table to `gates.md`.

## Model routing

| agent | model | why | evidence |
|---|---|---|---|
| hw-requirements | opus | every later number comes from its acceptance criteria and conflict arithmetic; short (4-6 min) | run 4/5 |
| hw-partition | **sonnet** (was opus) | a decision table applied in order plus a contract file that `hwflow fw` checks; little open-ended reasoning | Sonnet run on the run-5 inputs, below |
| hw-architect | opus | construction, floorplan geometry and budgets; a mistake here was the run-5 rework | run 5 rework |
| hw-parts | sonnet (unchanged) | lookups and transcription; the Opus parts review checks it | runs 4/5 |
| hw-circuit | opus | capture and SPICE debugging, the longest reasoning on the critical path | 37-44 min, 190-420k output tokens |
| hw-reviewer | opus, effort high | the independent check | runs 4/5 |
| hw-layout | sonnet (unchanged) | drives `hwflow build --auto`; model time under 5 min per 16-28 min pass | run 4 |
| fw-firmware | **sonnet** (was opus) | off the critical path (beside layout), and gated by its own host tests and `hwflow fw` 0 errors, so a weaker first draft costs a test loop, not a wrong board | run 4: 18 min, 27 host tests |
| helpers (`general-purpose`, `web-fetch`) | sonnet, set in the Agent call | lookups and summary drafts; they inherited Opus in run 4 | run 4 sign-off summary agent, 7.4 min |

Partition test (one `claude -p --agent hw-partition` on Sonnet, the run-5 stage-1b brief and inputs copied to a scratch project; the inputs were the later copies of `requirements.yaml` and `conflicts.md`, so a few numbers differ): 4.3 min and 12 turns against 7.1 min and 30 messages for Opus in run 5. Both wrote 19 functions with the same decisions: reverse protection, step-up and bulk in HW; the relay coil pulse limited in HW from the same arithmetic (a coil held on by a hung MCU empties 1000 mAh in 8.4 h); sequencing, probe faults, the warning and the schedule in SW; no button or LED. Both also wrote the energy budget per line, the bring-up checklist and new conflicts. Sonnet flagged its one unsourced number (a 300 ms wake) as unsourced. The contract is checked by `hwflow fw` once `design.yaml` exists, and the architect and reviewer (Opus) read it.

## Expected saving per stage

Against run 4 / run 5, assuming the rules hold:

| stage | measured | budget | expected saving |
|---|---|---|---|
| 1 requirements | 5 / 21 | 10 | 0 / about 11 min (no survey, one agent, no polling) |
| 1b partition | 7 / 8 | 8 | about 3 min (Sonnet: 4.3 against 7.1 min on the same brief) |
| 2 architecture | 18 / 20 | 20 | 0 |
| 3 parts | 38 / 53 | 30 | 8 / 23 min (fix pass cap, part-change findings only) |
| 4 circuit | 48 / 112 | 40 | 8 / 70 min (floorplan rework capped at 15 min) |
| 5 review | 53 | 25 | about 20 min (one delta review, no parts swap loop before layout) |
| 7 layout | 82 | 45 | 20-35 min (escalate at the budget instead of a third and fourth pass); the floor is the router, 8-10 min per build |
| 8 sign-off | 157 | 30 | about 120 min (routing rules settled in stage 5, delta review, no lead layout builds, two builds at most) |
| 9 firmware | 18, beside layout | 45 | wall 0 (off the critical path); Opus tokens of 85 messages move to Sonnet |

Run 4 would go from 409 min to about 230-260 min of work, and run 5's first 4 stages from 215 to about 110 min. These are expectations from the transcripts, not a measured run: the next timed e2e run measures them (`gates.md` has the budget table).
