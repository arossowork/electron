# CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL=1h: does it pay off? (backlog item 14)

Measured on the home-thermostat baseline (`evals/baselines/home-thermostat-2026-10-19/`) with

```
uv run python scripts/usage_tally.py --cache-ttl evals/baselines/home-thermostat-2026-10-19/metrics.json \
  --usage evals/baselines/home-thermostat-2026-10-19/run/usage.jsonl \
  --session-id 5afb4eb5-f951-4ac6-b654-0326136cf5d0 --transcripts ~/.claude/projects/<repo>-token-opt
```

## Result

- Cache writes 4.82 M, cache reads 217.7 M: 45 reads per written token (hw-layout 69, hw-parts 64, orchestrator 42,
  hw-circuit 42, reviewer 18, architect 18, requirements 4.4, web-fetch 1.4). `usage.jsonl` (main session only) agrees:
  0.45 M written, 24.4 M read.
- The transcripts show every write as `ephemeral_1h` (4.82 M) and none as `ephemeral_5m`, so the variable took effect.
- Gaps between consecutive messages of one agent: 1,643 within 5 min, 29 between 5 and 60 min, 2 over 60 min.
  A 5-minute cache would have expired in the 29 gaps and re-written the prefix (3.15 M tokens that were read instead).
- Cost in units of the base input price (write 5 min 1.25, write 1 h 2.0, read 0.1): 1 h = 31.40 M, 5 min
  (same traffic) = 31.41 M. The two are equal within 0.1 %.

## Reading

The 1 h setting neither saves nor costs anything on this run: the extra 0.75 per written token (3.6 M) is paid back
by the 29 avoided re-writes (3.6 M). It pays off when a run has more long pauses inside an agent (builds that wait
many minutes, user answers while a subagent is alive) and costs when agents run with short gaps only. The baseline
reached the break-even by chance, so the flag stays as is; it is not a lever for reducing tokens. The real lever is
the number of turns, because each turn reads the whole context (97.7 % of the tokens).

Limits: the 5 min case is a model of the same traffic, not a second run. Subagents of the same type share a
prefix across launches, which the model ignores (it would favour 1 h slightly).

## Wall time per stage (item 12)

`uv run python scripts/usage_tally.py --history <ui_history.jsonl.gz>` prints the time from each Agent launch to the
next launch, status, user or question event. Baseline: Stage 7 layout 127 min (2 launches), post-layout layout fixes
81 min, 3D stack fix 54 min, CS1238 fix 43 min, Stage 4 circuit 42 min (3 launches), Stage 3 parts 27 min,
Stage 2 architecture 23 min. The history only has HH:MM:SS, so the end of a stage is the next event, not the
subagent's last message; the 13 turns add to 8.0 h, against 6.3 h of `duration_ms` in the results, because the
gaps between a result and the next event are included.
