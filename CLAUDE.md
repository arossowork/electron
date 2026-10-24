# electron: autonomous electronics design with Claude Code

This repository turns a product conversation into a verified, routed PCB. Claude Code is the agent runtime, so the work runs on the user's Claude subscription. The Python tool `hwflow` does every deterministic step and never calls an LLM.

## Layout

- `src/hwflow/`: the EDA engine, no LLM and no cloud imports. Packages: `core` (design model, libraries, settings), `pipeline` (stage specs, runner, report, trace), `layout` (placer, router bridge, checks, diagnosis), `fw` (firmware headers and host tests), plus `buildplan`, `artifacts`, `runs` (the contracts shared with the platform).
- `harness/`: the local agent harness (`claude -p` sessions, limits, product-person simulator support).
- `services/platform/`: `hwplatform`, the hosted platform (API, Temporal workflows and workers, agent worker, outbox relay, Postgres migrations, S3). `web/`: its React SPA. `deploy/`: compose, Helm chart, worker image. See `docs/platform-dev.md`, `docs/platform-k8s.md`, `docs/runbook.md`, `docs/adr/0001-platform-contracts.md`.
- `docs/design-yaml.md`: the design file reference. Read it before you write a `design.yaml`.
- `.claude/skills/hw-design/`: the orchestrator skill (`/hw-design`).
- `.claude/agents/`: specialist subagents (requirements, architect, parts, circuit, layout, reviewer).
- `projects/<name>/`: one folder per product. `examples/` holds reference designs.
- `evals/`: the evaluation harness (fixtures, product-person simulator, scorer, baseline).
- `ui/`: the old local web UI (deprecated, replaced by `web/`; kept until its last features are ported).
- `tools/`: Freerouting jar and KiCadRoutingTools (gitignored). KiCad is in `~/Applications/KiCad`.

## Rules

- You edit `design.yaml`, `requirements.yaml`, and the markdown notes. You never edit files in `build/`, because `hwflow` regenerates them.
- You take pin numbers from `uv run hwflow lib show <lib_id>` and pad numbers from `uv run hwflow lib pads <fp_id>`. You never guess them.
- A number in a design decision has a source: a datasheet, a calculation in `notes/`, or a simulation.
- A requirement is done only when `hwflow trace` shows it verified and `hwflow build` gives a PASS verdict.
- A layout verdict `layout blocked, go back to <stage>` sends the loop back to that stage (parts, circuit or architecture). More layout cycles cannot fix it.
- You never read credential files or variables (`~/.claude/.credentials.json`, `.env`, API keys, OAuth tokens, `HWFLOW_*` remote tokens). Commands and the platform get them from the environment; if one is missing, say so and stop.
- If a fix needs a product decision (cost against performance, a changed spec), you ask the product person. You do not decide alone.

## Commands

```
uv run hwflow lib sym <words>          # search KiCad symbols
uv run hwflow lib show <lib:sym>       # pins: number, name, electrical type
uv run hwflow lib fp <words>           # search footprints
uv run hwflow lib pads <lib:fp>        # pads of a footprint
uv run hwflow parts <category> k=v --text "<words>"   # JLCPCB in-stock search
uv run hwflow check <proj>             # static and library checks
uv run hwflow build <proj> [--until sim] [--mc 50] [--reuse]
uv run hwflow build <proj> --remote    # on the hosted platform instead of inline (`run status|wait --remote` too)
uv run hwflow build <proj> ... --detach   # in the background; then:
uv run hwflow run wait <proj> [run_id]    # 0 PASS, 1 FAIL/failed, 2 still running (call again); `run status` for a snapshot
uv run hwflow build <proj> --retry-layout   # only when the product person overrides a blocked layout (docs/layout-diagnosis.md)
uv run hwflow optimize <proj> [--apply]
uv run hwflow trace <proj>             # requirement verification matrix
uv run hwflow fw test <proj>           # firmware host tests (gcc): headers, framework + fw/, run
uv run hwflow nets <proj> [net...]     # routed copper per net: widths, length, necks, layers, vias
uv run hwflow fw <proj> [--scaffold]   # firmware headers from design.yaml and interface.yaml; 0 errors required
uv run hwflow report <proj>            # rewrite build/REPORT.md
uv run pytest -q -m "not slow"         # fast tier (minutes); the full suite (about 20 min) before a merge
uv run pytest -q                       # deterministic tests, full suite
```

`HWFLOW_FLOW=legacy` runs the old annealing-only placement. It is a temporary escape hatch, not a tuning knob.

`build/REPORT.md` is the short feedback after each run. `build/status.json` holds the full data and the `score` card that the evaluations use.
