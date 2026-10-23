# Token economy of headless /hw-design runs

This document is about the `claude -p` sessions that hwflow starts, not about interactive use. The three callers are `evals/harness.py`, `ui/server.py`, and `scripts/hw-run.sh`. On the subscription, the usage window counts tokens, so `cost_usd` is only a proxy.

The status is branch `backlog-impl` (on top of `token-opt`). The baseline run (`evals/baselines/home-thermostat-2026-10-19/`) cost 222.8 M tokens, 97.7 % cache reads. The backlog items and their expected effect per agent are in `docs/backlog-result.md`. No eval run has been done on the new code yet; the user replays the baseline brief.

## 1. Where the tokens go in a headless run

A designer call resumes one session again and again (`--resume <sid>`). Every API request in it resends three things:

1. The fixed prefix: the system prompt, tool schemas, skills, agents, and CLAUDE.md. `/context` measured this on this machine (Claude Code 2.1.291, Sonnet):

   | | default `claude -p` | lean flags (this branch) |
   |---|---|---|
   | fixed prefix | 18.5k tokens | 9.3k tokens |
   | skills | 9.9k (GSD, Vercel, Notion... from user settings) | 2.4k (hw-design + built-ins) |
   | custom agents | 3.6k (33 GSD agents + 6 hw-*) | 0.5k (6 hw-*) |
   | MCP tools and instructions | 2.8k (Claude Docs, Drive, Notion, Chrome) | 0 |
   | context window / auto-compact | 1M / about 967k | 400k / about 367k |

   The global plugins in `~/.claude` have nothing to do with hardware design, but every headless request carried them. The GSD PostToolUse hooks also ran on every tool call.
2. The conversation so far. With the default 1M window, a long resume chain (stages 1 to 8, plus "continue" calls after every product-person round) grows to hundreds of thousands of tokens before compaction. Each request re-reads all of it, mostly as cache reads. This is the largest term. A 400k cap bounds it.
3. New content: tool results (build reports, lookups, WebFetch of datasheets) and the model's output.

The hwflow lookups are already bounded: `lib sym` returns at most 30 lines, `lib fp` at most 40, and `parts` defaults to 15. `build` prints a compact report. The largest new-content items are the WebFetch datasheets (tens of kB each, fetched by hw-parts and again by hw-reviewer, which runs at least twice) and the duplicate report read: the skill said "read `build/REPORT.md`" after a build that had already printed it.

## 2. Who uses which model (headless)

| caller | role | model before | effort before | after (this branch) | reason |
|---|---|---|---|---|---|
| harness `run --models` | orchestrator (main session, stages 6 and 8 in-thread) | `opus` default, `sonnet` in A/B | from user settings: Opus 5.5 `medium`, others `high` (top-level `effortLevel`); a parent session's `CLAUDE_EFFORT` leaked in | unchanged model, model default effort; `HWFLOW_EFFORT` overrides | candidate for `sonnet`: it routes, keeps `state.md`, and reads reports, and its context is the longest-lived, so the per-token price matters most here. Decide by eval (section 5) |
| harness product person | answers from a fact list | `sonnet` (`HWFLOW_PM_MODEL`) | user default | `sonnet`, `--effort low` (`HWFLOW_PM_EFFORT`) | a lookup in a short text needs no deep thinking; already no tools. Try `haiku` only after checking that rule 1 (hard limits) still holds |
| `ui/server.py` | orchestrator | dropdown | user settings | dropdown, lean flags | same as harness |
| `scripts/hw-run.sh` | orchestrator | `opus` | user settings | `opus`, lean flags | same |
| subagent hw-requirements | interview, conflict arithmetic | opus | inherited | opus / `medium` | conflicts are 15 of 100 points; judgment-heavy but a short context |
| subagent hw-architect | budgets with arithmetic | opus | inherited | opus / `medium` | one pass, small context; errors propagate to every later stage |
| subagent hw-parts | search, lookups, datasheet transcription | opus | inherited | **sonnet** / `medium` | many tool calls and little reasoning; check/ERC/pin tools catch mapping errors. Now also writes `notes/datasheets/<mpn>.md` |
| subagent hw-circuit | schematic, SPICE debug loop | opus | inherited | opus / `medium` | the hardest reasoning and the most turns; A/B `sonnet`/`high` later |
| subagent hw-reviewer | adversarial review | opus | inherited | opus / **`high`** | read-only, one pass, highest value per token; it reads datasheet notes first |
| subagent hw-layout | knob turning against DRC/post-layout report | opus | inherited | **sonnet** / `medium` | mechanical loop; the placer, router, and area search are deterministic in hwflow |

Not tiered further: Haiku 4.5 for a subagent. It has a 200k window and no tool search, so its full tool schemas load (a smoke call measured about 20k tokens of prefix), and hw-parts needs judgment on alternatives.

## 3. Changes in this branch

All of them apply to every headless call, because the three callers share `harness.designer_args()` and `harness.headless_env()`; `hw-run.sh` mirrors them.

| change | where | effect |
|---|---|---|
| `--setting-sources project,local --strict-mcp-config --tools Bash,Read,Write,Edit,Agent,Skill,WebSearch,WebFetch` | `designer_args` | fixed prefix 18.5k to 9.3k tokens on every request; no user hooks. User settings have no allow rules, and `--permission-mode acceptEdits` was already set, so permissions do not change |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW=400000` (override: `HWFLOW_AUTOCOMPACT_WINDOW`) | `headless_env` | the env var takes precedence over every settings file and works for any model. `/context` confirmed a 400k window |
| `"autoCompactWindow": 400000` | `.claude/settings.json` | the same for interactive sessions in the repo, for every model (the user setting covered only Opus 5.5) |
| `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL=1h` | `headless_env` | measured on the baseline (`docs/cache-ttl-note.md`): 45 cache reads per written token; 1h and 5 min cost the same within 0.1 % (29 gaps over 5 min repay the higher write price). Not a token lever; the number of turns is |
| drop the parent session's `CLAUDE_EFFORT`, `CLAUDE_CODE_*SESSION*`, and messaging variables | `headless_env`, `hw-run.sh` | a harness started from inside Claude Code no longer forces its effort on the agents |
| subagent `model`/`effort` tiers | `.claude/agents/*.md` | section 2 |
| PM `--effort low` | `_claude_once` | cheaper answers |
| "the build prints the report; do not read REPORT.md again" | SKILL.md, hw-circuit, hw-layout | one report fewer per build iteration, and that report is re-read on every later request |
| datasheet notes `notes/datasheets/<mpn>.md`, reviewer reads notes first | hw-parts, hw-reviewer | WebFetch once per datasheet, not once per stage and review |
| `tokens` and `tokens_by_model` in `meta.json`, `score.json`, and aggregate `tokens_k` | harness | measure: input, output, cache read, and cache write per trial and per model (subagents included, from `modelUsage`) |

`modelUsage` in the JSON result of a resumed session is cumulative (checked against the session transcripts: the second turn reported the running total of both turns, subagents included), while `usage` covers only the main thread of one call. The harness and the UI log the per-turn difference (`model_usage_delta`).

Keep the flags identical on every `--resume` of a session: a changed model or tool set misses the prompt cache. A session that was started before this branch keeps its recorded system prompt. Start new trials to see the effect.

## 4. Tools and projects, and how they would apply to headless runs

A tool helps a headless run only if it works without a person: a CLI flag, an env var, a project hook (project settings still load), or a change in the prompt or hwflow.

| tool | applies headless? | verdict |
|---|---|---|
| Native: `--setting-sources`, `--strict-mcp-config`, `--tools`, auto-compact window, subagent `model`/`effort`, cache TTL | yes | done in this branch |
| Native: `maxTurns` in agent frontmatter, `--max-turns` | yes | add after measurement, as a guard against runaway loops. A wrong value stops a good run, so take it from the measured turn counts |
| Native: `BASH_MAX_OUTPUT_LENGTH` (default 30000 chars) | yes, env | a cheap cap. hwflow output is already short, so it is low value. Set it to 15000 if a log shows a large Bash result |
| [ccusage](https://github.com/ryoppippi/ccusage) | yes, reads `~/.claude/projects/*.jsonl` after the run | use it for daily and 5h-window views (`npx ccusage@latest session --json`); zero context cost. Not installed |
| [RTK](https://github.com/rtk-ai/rtk), a PreToolUse hook that compacts Bash output | possible: a project hook loads under `--setting-sources project` | low value here: it has no filter for `hwflow`, whose output is already compact. The same idea applied at the source is cheaper (section 5) |
| [context-mode](https://github.com/mksglu/context-mode), an MCP output sandbox | would need `--mcp-config` | no: it adds MCP schemas to every request, and `--strict-mcp-config` is the saving |
| caveman / terse-output skills | yes, via the agent prompt or `--append-subagent-system-prompt-file` (a hidden flag in 2.1.291) | output is a small share; the agents already return at most 10 to 15 lines |
| Serena, Repomix | — | not for this flow: the agent reads YAML and CLI tables, not a large code base |
| claude-code-router | — | needs a custom base URL, which turns off tool search; native per-agent `model` covers the need |
| OpenTelemetry (`CLAUDE_CODE_ENABLE_TELEMETRY=1`) | yes | not needed: the JSON result already carries `usage` and `modelUsage`, which the harness now records |

## 5. Next steps, ranked by expected saving

1. Measure first. One eval trial per fixture with `--models sonnet,opus --trials 1`, then compare `tokens_k` and `tokens_by_model` with a run on `main`. Without a baseline the shares in this document are estimates.
2. One fresh session per stage group, not one resume chain. For example, the harness could start stage 7 (layout) in a new session that reads `state.md`. The 400k cap bounds the chain, but a fresh session starts at about 10k tokens.
3. Orchestrator on `sonnet` with the tiered subagents (`--models sonnet`). It is the longest context, so it is the largest lever, if the score holds (eval tolerance is 5 points).
4. hwflow: a `build --brief` mode that prints only changed lines against the previous `status.json`, for the circuit and layout loops.
5. `maxTurns` per agent from the measured turn counts.
6. If hw-circuit on `sonnet`/`high` scores the same as `opus`/`medium`, move it too.

## hwflow flags

Engine behaviour added for the backlog (groups 1 to 3). Each one cuts report noise or a rebuild that an agent turn would otherwise pay for.

| flag or behaviour | what it does | backlog |
|---|---|---|
| `build --brief` (also `check`, `sch`, `sim`, `layout`, `post`, `fab`) | prints the verdict and only the report lines that changed against the previous `REPORT.md` (`+` new, `-` gone). An unchanged rebuild prints 3 lines instead of the full report | 8 |
| stage skipping in `build` | a stage that passed last time with the same inputs (parsed design, model files, project libraries, hwflow code) is skipped; once a stage runs, all later ones run. `--rebuild` forces every stage. Unchanged baseline rebuild: 72 s to 0.5 s | 27 |
| Monte Carlo once | `--mc N` reuses the last Monte Carlo when the inputs and N are unchanged | 27 |
| `layout --auto [--attempts 4]`, `build --auto` | placement, routing, area search and DRC in one call; new placer seeds per attempt until 0 DRC errors, 0 unrouted and no placement violations; every attempt kept in `build/auto/`; prints one summary line | 23, 25 |
| `board.router_retries` (default 3) | KiCadRoutingTools routes a width class again in another net order when nets stay unrouted, keeps the best | 40 |
| `timeout_s` per analysis (default 600) | bounds one ngspice run. Each analysis gets `status` ok, timeout, crash, aborted or error with its run time; every n/a measure says why, including a node missing from the netlist | 22, 42, 46 |
| crash retry | an ngspice signal exit or heap error is retried once and reported as flaky | 41 |
| measure messages | ngspice `measure ... out of interval` on an analysis whose measures all pass is a warning; a sim FAIL names the failing measures or the analysis with the error | 20 |
| `dbg_*` measures, `debug: true` | listed apart; never counted in pass/total, verdict, trace, post-layout deltas or Monte Carlo yield | 19 |
| `build/mc_failures/` | every failing Monte Carlo run: seed, run index, sampled values, rows, ngspice log; unfinished runs stay out of the min/max spread | 55 |
| stale results | kept in `REPORT.md` with `(stale)` and `stale: ...` in the verdict; post and fab are also stale when the layout ran after them; trace and score use fresh results only | 43 |
| history | every build appends one line to `build/history.jsonl` and keeps its report in `build/history/<timestamp>.md` (last 50) | 18 |
| design hash | parsed `design.yaml` (comments, whitespace and the in-memory layout resize do not count) and `requirements.yaml` without prose fields | 58 |
| evidence check | `trace` and the report list requirement evidence files that are missing or empty (`req_evidence_missing` in the score; verdict unchanged) | 54 |
| `package:`, `package_pads`, `package_pitch_mm` | `check` compares the datasheet package with the footprint (name: warning; pad count, pitch: error); a `Q` with a MOS model or an `M` with a BJT model is a check error | 56, 17 |
| `board.isolation` | net group (glob or class), clearance and creepage, `req`: generated KiCad rules in the layout-stage DRC; the layout line gives the measured distance with its limit and the violations; `trace` verifies the `req` from it. Replaces the hand-run `kicad-cli` check with a `.kicad_dru` in `.scratch/` | 38 |
| `hwflow approve`, approval check | `approve` records layers, board size and BOM cost in `build/approved.json`; `check` then makes a change of layers, a size change above `approval.size_mm` or a cost rise above `approval.cost_pct` an error, or a warning when `conflicts.md` (`change: <key>`) or an inbox question (`"change": "<key>"`) records it. The check stage is not skipped when the approval findings change | 44 |
| SI screen (`si` findings) | for nets with `edge_ns`: delay of the longest copper path against t_r/6, vias and branch points, length without a reference plane, decap loop inductance at 0.35/t_r against `board.si_pdn_z_ohm`. No SPICE, about 1 s on the baseline board | 48 |
| distances | post-layout reports the clearance of each `board.drc_rules` net group to every other net and the tightest decap paths, with limits | 39 |
| lumping scope | the report states what lumped post-layout transients check; nets with `class: fast`, `edge_ns` or `post_full` keep the full network; `board.post_lump_check: <analysis>` compares lumped and full on one analysis | 26, 47 |

## Agent prompts and UI (backlog groups 4 to 6)

| behaviour | where | backlog |
|---|---|---|
| datasheets read as text (`pdftotext`), one fetch per datasheet, facts in `notes/datasheets/<mpn>.md`; about 5 searches per stage; lookups batched in one Bash call | hw-architect, hw-parts, hw-reviewer | 1, 2, 3, 16, 30 |
| hw-requirements asks everything it can in round 1 | hw-requirements | 29 |
| realism check for every testbench scenario | hw-circuit, hw-reviewer | 24 |
| a decision for the product person goes to `inbox/questions.json` with options; compact briefs to subagents; parallel `hw-parts` per block group; delta review mode after a late change | SKILL.md | 37, 9, 28, 57 |
| the agent waits inside its turn for builds it started (blocking call with timeout) | SKILL.md, agents | 50 |
| `maxTurns` per agent | `.claude/agents/*.md` | 4 |
| UI: Markdown rendering, grouped tool events, history paging, message queue and interrupt while the agent works, live usage per subagent (needs `parent_tool_use_id` in the stream, untested on a real one), questions count, status from the process exit | `ui/` | 32 to 36, 51 |
| `scripts/usage_tally.py`: tokens by agent, `--compare`, `--history` for wall time per stage, `--cache-ttl` for write against read | scripts | 12, 14 |

Section 5 ranks the remaining levers: items 5, 6, 7, 11 and 31 (model, effort, session split) wait for an eval run by the user.
