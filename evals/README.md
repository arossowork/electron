# Agent evaluations

These evaluations measure the `/hw-design` agent, which is not deterministic, with a scorer that is deterministic. Use them to set a baseline and to prove that a change to a prompt, a skill, a subagent, or `hwflow` makes the agent better and not worse.

## Parts

- `fixtures/*.yaml`: one product per file. Each file has the brief that the agent sees and a `hidden_spec` that only the simulated product person knows. It also has `planted_conflicts` with keyword groups, `expected_requirements`, and area and cost `limits`. If `mode: from_requirements` is set, the interview is skipped and only the engineering stages are tested.
- The product person is a separate `claude -p` session (default model `haiku`, set `HWFLOW_PM_MODEL` to change it). It answers only from the hidden specification, and it follows the preference rules there when the agent proves a conflict.
- `harness.py score` rebuilds every design with `hwflow` from a clean state. It never uses the agent's own build and never asks an LLM.

## Score (100 points)

| item | points | rule |
|---|---|---|
| design_exists | 5 | `design.yaml` exists |
| erc_clean | 10 | zero ERC errors |
| drc_clean | 10 | zero DRC errors |
| routed | 10 | zero unrouted nets |
| sim_pass | 15 | share of SPICE measures that pass |
| post_pass | 10 | share of measures that still pass with copper parasitics |
| req_coverage | 10 | share of expected requirement topics present in the agent's documents |
| req_verified | 10 | share of the agent's requirements that `hwflow trace` shows as verified |
| conflicts | 15 | share of planted conflicts found (all keyword groups present in conflicts, requirements, architecture, or questions) |
| area_ok, cost_ok | 2.5 each | inside the fixture limits (cost needs prices in the BOM) |

The card also records the number of questions, the rounds, the designer calls, the wall time, and `cost_usd`. For a subscription login, `cost_usd` is not billed. It is only an effort measure that you can compare between runs.

## Workflow

1. Run: `uv run python evals/harness.py run --models sonnet,opus --trials 3`.
2. Save the first good run as the baseline: `uv run python evals/harness.py baseline evals/runs/<id>`. Commit `evals/baseline.json`.
3. Change something, run again, and compare: `uv run python evals/harness.py compare evals/runs/<new>`. The result per fixture and model is IMPROVED, same, or REGRESSION. The tolerance is the larger of 5 points and twice the trial spread, so noise from a single trial does not count as a change.
4. If you change the scorer, re-score old runs with `harness.py score evals/runs/<id>`. This needs no agent calls, so old and new runs stay comparable.

Use 3 or more trials per fixture before you trust a difference. Keyword matching for conflicts is strict but simple. Read `conflicts.md` of a trial when a score looks wrong, and improve the keyword groups in the fixture, not the agent prompt.
