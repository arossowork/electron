# electron: autonomous electronics design with Claude Code

This repository turns a product conversation into a verified, routed PCB. Claude Code is the agent runtime, so the work runs on the user's Claude subscription. The Python tool `hwflow` does every deterministic step and never calls an LLM.

## Layout

- `src/hwflow/`: the EDA engine (design model, SPICE, KiCad schematic and PCB writers, the layout flow, post-layout extraction, optimizer, firmware headers).
  Packages (layers, top to bottom): `pipeline`, `verify`, `layout` (`route`, `place`, `board`), `sim` | `schematic`, `core`; `fw` imports only `core`. Contracts and the module map: `docs/import-graph.md`.
- `docs/layout-flow.md`: the single layout flow, stage by stage: macro stage, analytic global placement with RUDY density feedback, legalization, local moves, short anneal, pin access, KRT routing with repair.
- `docs/design-yaml.md`: the design file reference. Read it before you write a `design.yaml`.
- `.claude/skills/hw-design/`: the orchestrator skill (`/hw-design`).
- `.claude/agents/`: specialist subagents (requirements, HW/SW partition, architect, parts, circuit, reviewer, layout, firmware).
- `projects/<name>/`: one folder per product. `examples/` holds reference designs.
- `harness/` (package `hwharness`, uv workspace member): `claude -p` sessions (`hwharness.agent`), the product-person protocol (`hwharness.protocol`: approval, conflicts, inbox) and the eval scorer. It registers hooks in the engine through `hwflow.pipeline.hooks`; the engine never imports it.
- `evals/`: the evaluation harness (fixtures, product-person simulator, scorer, baseline).
- `ui/`: the local web UI that drives `claude -p` sessions.
- `tools/`: KiCadRoutingTools (the router) and the Freerouting jar (fallback). KiCad is in `~/Applications/KiCad`.

## Rules

- You edit `design.yaml`, `requirements.yaml`, and the markdown notes. You never edit files in `build/`, because `hwflow` regenerates them.
- You take pin numbers from `uv run hwflow lib show <lib_id>` and pad numbers from `uv run hwflow lib pads <fp_id>`. You never guess them.
- A number in a design decision has a source: a datasheet, a calculation in `notes/`, or a simulation.
- A requirement is done only when `hwflow trace` shows it verified and `hwflow build` gives a PASS verdict.
- A layout verdict `layout blocked, go back to <stage>` sends the loop back to that stage (parts, circuit or architecture). More layout cycles cannot fix it.
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
uv run hwflow build <proj> --retry-layout   # only when the product person overrides a blocked layout (docs/layout-diagnosis.md)
uv run hwflow optimize <proj> [--apply]
uv run hwflow trace <proj>             # requirement verification matrix
uv run hwflow nets <proj> [net...]     # routed copper per net: widths, length, necks, layers, vias
uv run hwflow fw <proj> [--scaffold]   # firmware headers from design.yaml and interface.yaml; 0 errors required
uv run hwflow report <proj>            # rewrite build/REPORT.md
uv run pytest -q -m "not slow"         # fast tier (minutes); the full suite (about 20 min) before a merge
uv run pytest -q                       # deterministic tests, full suite
```

`HWFLOW_FLOW=legacy` runs the old annealing-only placement. It is a temporary escape hatch, not a tuning knob.

`build/REPORT.md` is the short feedback after each run. `build/status.json` holds the full data and the `score` card that the evaluations use.
