# Baseline: home-thermostat, UI run of 2026-10-19/20

One complete `/hw-design` run through the web UI (`ui/server.py`, `claude -p`, model `opus` for the
orchestrator, subagent tiers from `.claude/agents/*.md`), with the lean-context changes of the `token-opt`
branch. Use it to compare later runs on the same brief.

Result: build PASS, 21 of 21 requirements verified, SPICE 163/163 before and after layout, DRC 0 errors
(230 V clearance and creepage rules active), 4 layers, two 62 x 40 mm boards on a 130 x 40 mm panel,
BOM 27.94 USD per unit at 10 units, six product-person rounds. Details: `metrics.json`, `project/SUMMARY.md`.

## Contents

| path | what |
|---|---|
| `project/brief.md` | the brief the agent got |
| `project/inbox/` | the questions of rounds 1 to 6 and the answers (`answers.md`) |
| `project/requirements.yaml`, `design.yaml`, `architecture.md`, `conflicts.md`, `state.md`, `SUMMARY.md` | final agent output |
| `project/lib/`, `project/models/` | footprints and SPICE models, so the design can be rebuilt |
| `project/REPORT.md`, `status_summary.json`, `bom.csv` | final build report, score card and trace, BOM |
| `metrics.json` | outcome, tokens by model and by agent, wall time, event counts |
| `run/usage.jsonl` | per-turn usage logged by the UI (the first lines are cumulative, see `docs/token-economy.md`) |
| `run/ui_history.jsonl.gz` | every UI event of the run |
| `run/engine-changes-during-run.patch` | hwflow edits made by the agent during the run, not committed when the run ended |

## What was measured

222.8 M tokens, 97.7 % of them cache reads. Opus 104.8 M, Sonnet 118.0 M, Haiku not counted (WebFetch
summaries are not in the transcripts).

| agent | launches | turns | M tokens |
|---|---|---|---|
| hw-layout (Sonnet) | 5 | 641 | 83.4 |
| hw-circuit (Opus) | 7 | 458 | 60.6 |
| hw-parts (Sonnet) | 2 | 242 | 34.6 |
| orchestrator (Opus) | | 168 | 27.4 |
| hw-architect (Opus) | 2 | 70 | 8.2 |
| hw-reviewer (Opus) | 3 | 86 | 7.8 |
| hw-requirements (Opus) | 3 | 23 | 0.5 |

Wall time: the first and last message are 10.4 h apart. That includes pauses (KiCad installs, waiting for
answers, two builds killed when a turn ended). The sum of the agent turns is 6.3 h.

## Code state

The run started on branch `token-opt` at commit `174264c` (about 14:28 local time). During the run the
code changed in three ways, so the baseline is not a clean checkout:

1. `f7507bc`: `run_krt` resolves the work directory (the router runs with another cwd).
2. Documentation and backlog commits only (no engine change).
3. Edits by the agent at the end, committed afterwards as `9dc21e4` (the same change is in
   `run/engine-changes-during-run.patch`): the post-layout transient netlist keeps only the exact DC resistance
   between the pads of each net, with no trace capacitance (`postlayout.py`, on by default,
   `board.post_tran_lump: false` turns it off); the `board.drc_rules` custom DRC rules (`pcb.py`); and
   `docs/design-yaml.md`. Tests: `tests/test_postlayout.py`.

A replay from `9dc21e4` or later includes these edits. A replay without them hits the 600 s ngspice timeout in post-layout, as this run did before the edits.

Tools: Claude Code 2.1.291, KiCad 10.0.7 (`/usr/bin/kicad-cli`), ngspice-42, uv 0.10.10, KiCadRoutingTools
router. UI command line: `claude -p ... --model opus --setting-sources project,local --strict-mcp-config
--tools Bash,Read,Write,Edit,Agent,Skill,WebSearch,WebFetch --permission-mode acceptEdits`, with
`CLAUDE_CODE_AUTO_COMPACT_WINDOW=400000`.

## Replay

1. Pick the code to test (a branch with the backlog items applied) and commit it.
2. `uv run python ui/server.py`, open the UI, create a project with a new name (for example
   `home-thermostat-b`) and paste `project/brief.md`. Model: `opus`.
3. Answer each round from `project/inbox/answers.md`. The agent may ask different questions or in a
   different order: match them by topic. For a question that has no match, answer "No preference, use your
   judgment." and write it down.
4. When the state is `done`:

```
uv run python scripts/usage_tally.py home-thermostat-b --compare evals/baselines/home-thermostat-2026-10-19/metrics.json
uv run hwflow trace projects/home-thermostat-b
uv run hwflow build evals/baselines/home-thermostat-2026-10-19/project   # rebuilds the baseline design
```

5. Compare: tokens by agent (`usage_tally.py`), build verdict and requirement count, number of product
   questions and rounds, the number of build calls and the wall time of the agent turns.

## Limits

- One run, no repeat: the agent is not deterministic, so a difference of a few tens of percent in tokens
  can be noise. Repeat the replay before trusting a small gain.
- The product person here was a human who answered from the UI, so the answers are not an automatic persona.
- Builds killed at the end of a turn (backlog 50) and the hand-made layout loop (backlog 23, 25, 40) are
  in these numbers.
