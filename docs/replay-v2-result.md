# Replay v2 (partial): home-thermostat-v2 on branch backlog-impl

The run was stopped by the user at 14:27 (about 64 minutes of agent time) while stage 3 (parts) was running and
a relay decision was waiting. It never reached `done`, so this is a partial comparison, not a score.
The total is NOT comparable with the baseline's 222.8 M tokens: most stages did not run.

| agent | v2 launches / turns / M tokens | baseline launches / turns / M tokens |
|---|---|---|
| hw-requirements | 3 / 36 / 1.0 | 3 / 23 / 0.5 |
| hw-architect | 1 / 26 / 1.8 | 2 / 70 / 8.2 |
| hw-parts | 6 / 258 / 15.2 | 2 / 242 / 34.6 |
| orchestrator | 0 / 30 / 1.7 | 0 / 168 / 27.4 |
| total | 19.7 M (94.9 % cache reads) | 222.8 M |

## Observations
- Requirements took 2 question rounds before architecture started (baseline: 6 rounds over the whole run).
- The architect used 26 turns in one launch (baseline stage 2: 32 turns).
- hw-parts ran as 6 parallel launches (item 28). In 9 minutes it used 13.6 M tokens; 258 turns in total, already above the
  baseline's 242, and the stage was not finished. Parallel launches may not save tokens; they save wall time. Decide after a full run.
- UI: Markdown rendering, grouped tool calls, the queue button and "agent finished" at process exit worked. A question form
  appears only after the agent process exits (item 51), so the form can lag behind `inbox/questions.json` by about a minute.
  Not yet verified: usage per subagent in the UI (item 35).
- Product questions: 3 rounds seen (4, 4, 1 questions). Round 3 asked for a relay choice with no baseline answer and a cost and
  size impact; the user answered: the 3-module Italian box is a hard constraint, solid state or other options allowed,
  priority size, then price, then battery life (this last message was dropped when the run was stopped).
- Verdict, requirement count and build calls: not available, the design stage did not run.

## To finish the comparison
Resume `home-thermostat-v2` in the UI (send "continue" with the relay answer above) and run
`scripts/usage_tally.py home-thermostat-v2 --compare evals/baselines/home-thermostat-2026-10-19/metrics.json` when the state is `done`.
