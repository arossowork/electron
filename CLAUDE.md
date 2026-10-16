# electron: autonomous electronics design with Claude Code

This repository turns a product conversation into a verified, routed PCB. Claude Code is the agent runtime, so the work runs on the user's Claude subscription. The Python tool `hwflow` does every deterministic step and never calls an LLM.

## Layout

- `src/hwflow/`: the EDA engine (design model, SPICE, KiCad schematic and PCB writers, placer, Freerouting bridge, post-layout extraction, optimizer).
- `docs/design-yaml.md`: the design file reference. Read it before you write a `design.yaml`.
- `.claude/skills/hw-design/`: the orchestrator skill (`/hw-design`).
- `.claude/agents/`: specialist subagents (requirements, architect, parts, circuit, layout, reviewer).
- `projects/<name>/`: one folder per product. `examples/` holds reference designs.
- `evals/`: the evaluation harness (fixtures, product-person simulator, scorer, baseline).
- `ui/`: the local web UI that drives `claude -p` sessions.
- `tools/`: Freerouting jar. KiCad is in `~/Applications/KiCad`.

## Rules

- You edit `design.yaml`, `requirements.yaml`, and the markdown notes. You never edit files in `build/`, because `hwflow` regenerates them.
- You take pin numbers from `uv run hwflow lib show <lib_id>` and pad numbers from `uv run hwflow lib pads <fp_id>`. You never guess them.
- A number in a design decision has a source: a datasheet, a calculation in `notes/`, or a simulation.
- A requirement is done only when `hwflow trace` shows it verified and `hwflow build` gives a PASS verdict.
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
uv run hwflow optimize <proj> [--apply]
uv run hwflow trace <proj>             # requirement verification matrix
uv run hwflow report <proj>            # rewrite build/REPORT.md
uv run pytest -q                       # deterministic tests
```

`build/REPORT.md` is the short feedback after each run. `build/status.json` holds the full data and the `score` card that the evaluations use.
