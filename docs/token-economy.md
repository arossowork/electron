# Token economy of headless /hw-design runs

This document is about the `claude -p` sessions that hwflow starts, not about interactive use. The three callers are `evals/harness.py`, `ui/server.py`, and `scripts/hw-run.sh`. On the subscription, the usage window counts tokens, so `cost_usd` is only a proxy.

The status is a branch `token-opt` with the changes below. Only short smoke calls (Haiku, a few seconds) were run. No eval run or hwflow build has been run against these changes yet.

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
| `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL=1h` | `headless_env` | a subagent idle through a long build (routing, `--mc 100`) does not rewrite its whole context after 5 minutes. Trade-off: a 1h cache write is priced higher than a 5 min write, so measure it |
| drop the parent session's `CLAUDE_EFFORT`, `CLAUDE_CODE_*SESSION*`, and messaging variables | `headless_env`, `hw-run.sh` | a harness started from inside Claude Code no longer forces its effort on the agents |
| subagent `model`/`effort` tiers | `.claude/agents/*.md` | section 2 |
| PM `--effort low` | `_claude_once` | cheaper answers |
| "the build prints the report; do not read REPORT.md again" | SKILL.md, hw-circuit, hw-layout | one report fewer per build iteration, and that report is re-read on every later request |
| datasheet notes `notes/datasheets/<mpn>.md`, reviewer reads notes first | hw-parts, hw-reviewer | WebFetch once per datasheet, not once per stage and review |
| `tokens` and `tokens_by_model` in `meta.json`, `score.json`, and aggregate `tokens_k` | harness | measure: input, output, cache read, and cache write per trial and per model (subagents included, from `modelUsage`) |

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
