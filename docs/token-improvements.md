# Token improvements backlog

Candidates found while testing the headless path (`token-opt` branch). The run in progress is `projects/home-thermostat`, started through the UI. Nothing in the "open" list is applied yet, so that run stays comparable to its start. Apply after the run, then re-run the same brief and compare.

Status: `open` (not applied), `applied` (in the branch), `rejected` (with the reason).

## Findings from the home-thermostat run (so far)

| stage | wall time | Opus tokens (this stage) | Haiku tokens | note |
|---|---|---|---|---|
| 1 requirements, interview turn | 229 s | 329k | 12k | 9 turns |
| 1 requirements, round 1 answers | 182 s | 329k | 0 | 3 turns |
| 2 architecture | 1446 s | about 3.8M | about 0.39M | hw-architect alone: 32 turns, context 7k to 207k, 3.1M cache reads |
| 3 parts | not logged yet | hw-parts ran on Sonnet: 147 turns, 23.6M cache reads, 328k cache writes | — | 4 datasheet notes written |

Per-turn figures come from `projects/home-thermostat/.usage.jsonl`. The UI process that ran this had the old logging code, so its first lines are cumulative; subtract the earlier turns (`modelUsage` of a resumed session is cumulative, see `docs/token-economy.md`).

What drove stage 2 (transcript `subagents/agent-ad07db*.jsonl`):
- 32 turns on a context that grew to 207k tokens; the cost is turns times context, not new content.
- 9 web searches and 6 web fetches (every fetch is summarized by Haiku, which explains the Haiku share).
- 6 Reads of PDF pages that put 20 page images in context.
- The ESP32-C3 datasheet came from two URLs, then `pdftotext`, then page images.
- Some steps added 18k to 36k tokens with little visible tool output. 23 thinking blocks exist, but their content is hidden, so the cause is unconfirmed.

## Backlog

| # | improvement | where | evidence | expected effect | status |
|---|---|---|---|---|---|
| 1 | Read datasheets with `pdftotext` and `grep`, not Read of PDF pages (images) | hw-architect, hw-parts, hw-reviewer prompts | 20 page images in the stage-2 context | smaller context for every later turn of the agent | open |
| 2 | Fetch each datasheet once from one URL, save the facts to `notes/datasheets/<mpn>.md`; the architect writes them too | hw-architect prompt (hw-parts and hw-reviewer already have it) | ESP32-C3 fetched twice, then read twice more | fewer fetches, fewer Haiku tokens | open |
| 3 | Cap web searches at about 5 per stage; batch independent lookups in one turn | hw-architect, hw-parts prompts | 9 searches, 32 turns | fewer turns on a large context | open |
| 4 | `maxTurns` in agent frontmatter, for example 20 for architect and parts | `.claude/agents/*.md` | 32 turns in one stage | guard against runaway loops; pick the value from measured turns, a low value stops good runs | open |
| 5 | A fresh session per stage group instead of one resume chain; stage 7 starts from `state.md` | `evals/harness.py`, `ui/server.py` | the orchestrator context only grows | bounded context for the longest-lived session | open |
| 6 | Orchestrator on `sonnet`, keep the tiered subagents | UI model dropdown, `--models sonnet` | the main thread re-reads its context every turn | the biggest per-token lever; needs an eval to confirm the score holds | open |
| 7 | hw-circuit on `sonnet` with `high` effort instead of `opus` with `medium` | `.claude/agents/hw-circuit.md` | the circuit loop has the most turns | only if an eval shows the same score | open |
| 8 | `hwflow build --brief`: print only lines that changed against the previous `status.json` | `src/hwflow/cli.py` | each build report is re-read on every later request | smaller reports in the circuit and layout loops | open |
| 9 | Hand architect, parts and reviewer a compact brief instead of letting each re-read `docs/design-yaml.md`, requirements, and the example projects | the skill's delegation text | the architect's first steps `ls`, `cat`, and `sed` of docs and examples | fewer startup tokens per subagent | open |
| 10 | Check the `hwflow lib sym ... \| head` calls: the stage-2 context grew by 36k after one such step | `src/hwflow/libs.py`, hw-parts prompt | one step jumped 149k to 185k | find the real size of the output, or whether it was thinking | open |
| 11 | Find out what the 18k to 36k jumps are (hidden thinking?) by comparing the same stage at `effort: low` and `medium` | `.claude/agents/hw-architect.md` | unexplained growth | decides whether lowering effort is the lever | open |
| 12 | ccusage for the 5-hour window view across runs: `npx ccusage@latest session --json` | measurement only | subscription limits count tokens | no saving by itself | open |
| 13 | `BASH_MAX_OUTPUT_LENGTH=15000` in `headless_env` | `evals/harness.py` | no large Bash result seen so far (largest: 27k chars, an `ls -R`) | cheap cap, low value | open |
| 14 | `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL=1h`: check that it pays off (a 1h cache write costs more) | `headless_env` | applied without measurement | confirm with cache creation versus cache read per stage | applied, unmeasured |
| 16 | hw-parts on `sonnet` took 147 turns (23.6M cache reads, context about 160k) against 32 turns and 3.1M for the Opus architect. Run the same stage with hw-parts on `opus`, and compare the 5-hour usage window as well as raw tokens. Whatever the model, cut turns: batch lookups (`lib sym` / `lib show` / `lib pads` for several parts in one Bash call), `maxTurns`, text-only datasheets | `.claude/agents/hw-parts.md` | stage 3 of home-thermostat; datasheet notes were written (4 files) | the tiering saves nothing if it multiplies the turns; unknown until measured | open |
| 17 | Make hwflow choose the SPICE element from the model type (`NMOS_*`/`PMOS_*` to `M`), or make `hwflow check` fail on a `Q` reference with a MOS model. The `Q2` PMOS in home-thermostat gave an ngspice error that cost agent turns to find. Documented in `docs/design-yaml.md` for now | `src/hwflow/spice.py`, `src/hwflow/cli.py` (`stage_check`) | `QQ2 ... PMOS_LOGIC` on line 18 of the netlist | a clear error at check time instead of a SPICE abort | open (doc line applied) |
| 15 | RTK, context-mode, caveman, Serena, Repomix, claude-code-router | — | see `docs/token-economy.md`, section 4 | little or no value for this flow | rejected |

## How to compare after applying

1. Same brief and same answers, one run on `main` and one on `token-opt` with the changes.
2. Compare per stage: Opus, Sonnet, and Haiku tokens from `.usage.jsonl` (restart the UI first, so it logs per-turn deltas), plus wall time and turn counts.
3. Check the requirement count, the open conflicts, and `hwflow trace` for quality, not only tokens.

## Tally of the stopped run (transcripts, tokens in thousands)

The run was stopped in stage 4, because `kicad-cli` is 7.0.11 on this machine and hwflow needs 8 or newer (`sch erc` and `pcb drc` do not exist in 7). Output tokens are low by an unknown amount (the transcript keeps the first streamed record of each message).

| agent | model | turns | peak context | cache read | cache write |
|---|---|---|---|---|---|
| hw-requirements (3 launches) | opus | 8 + 9 + 6 | 30k / 39k / 26k | 109k / 182k / 96k | 30k / 35k / 22k |
| hw-architect (stage 2) | opus | 32 | 207k | 3,097k | 207k |
| hw-parts (stage 3) | sonnet | 147 | 328k | 23,608k | 328k |
| hw-circuit (stage 4, partial) | opus | 43 | 197k | 5,193k | 197k |
| orchestrator | opus | 23 | 51k | 692k | 51k |

Totals: about 9.9M Opus tokens and 23.9M Sonnet tokens, almost all cache reads. Peak context of a subagent is also its total cache write: it grows by appending and the cache is never rebuilt. No compaction happened (the window was 400k), so the 400k cap did not matter in this run. Context size and turn count are what to attack.

To continue: install KiCad 8 or newer, set `KICAD_CLI`, restart `ui/server.py` (it then logs per-turn deltas), and send "continue".
