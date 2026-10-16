# electron

An autonomous electronics design flow that runs inside Claude Code. You describe a product, and the agent does the rest. It interviews you, finds conflicting requirements, selects parts, captures the schematic, and simulates it. It then optimizes, places and routes the PCB, and checks the routed board again with post-layout extraction. The agent runs on your Claude subscription through the `claude` CLI, and the EDA engine (`hwflow`) is plain Python with no LLM calls.

## Setup

```
scripts/setup.sh
```

The script installs ngspice, OpenJDK, and uv with Homebrew, copies KiCad 10 into `~/Applications` (no `sudo` needed), downloads the Freerouting jar, and runs the tests. Make sure that `ANTHROPIC_API_KEY` is not set, so that `claude` uses your subscription login.

## Three ways to run it

1. In Claude Code, interactive: open the repository and type `/hw-design new my-product`. The agent asks you questions with selectable options.
2. In the browser: run `uv run python ui/server.py` and open http://127.0.0.1:8765. You can install it as an app (PWA). Each message starts a `claude -p` session in the background. Questions appear as forms, and the report, the requirements, the conflicts, the PCB, and the 3D view refresh after each step.
3. Unattended: `scripts/hw-run.sh my-product opus`. It loops until the design is done or until the agent needs a product decision.

## How the flow works

| stage | who | output |
|---|---|---|
| 1 requirements and conflicts | `hw-requirements` subagent and the product person | `requirements.yaml`, `conflicts.md` |
| 2 architecture and budgets | `hw-architect` | `architecture.md` |
| 3 part selection | `hw-parts` (JLCPCB index, datasheets) | `notes/parts.md`, part data in `design.yaml` |
| 4 schematic and SPICE | `hw-circuit` with `hwflow build --until sim` | `design.yaml`, KiCad schematic, ERC, measures, Monte Carlo, derating |
| 5 design review | `hw-reviewer` | findings, fixed in `design.yaml` |
| 6 optimization | `hwflow optimize` | values and part choices against weighted criteria |
| 7 layout | `hw-layout` with placer, Freerouting, KiCad DRC | `.kicad_pcb`, DRC report |
| 8 post-layout and sign-off | `hwflow post`, `trace` | IR drop, decoupling, crosstalk, return path, re-simulation with copper parasitics, `SUMMARY.md`, Gerbers |

After every build, `build/REPORT.md` gives a short verdict, and the agent reads only that file to decide its next fix.

## Testing

There are three layers.

1. Deterministic unit and pipeline tests: `uv run pytest -q`. They include a full build of `examples/sensor_preamp`, which must give zero ERC and DRC errors and full routing.
2. Agent evaluations: `uv run python evals/harness.py run --models sonnet,opus --trials 3`. Each fixture in `evals/fixtures/` has a brief, a hidden specification that a simulated product person (a separate `claude -p` session) answers from, planted conflicts, and expected requirements. The scorer is deterministic. It rebuilds the agent's design with `hwflow` and scores ERC, DRC, routing, simulation and post-layout pass rates, requirement coverage, planted conflicts that the agent found, area, and cost (100 points).
3. Baseline tracking: `evals/harness.py baseline <run>` saves a baseline, and `evals/harness.py compare <run>` reports IMPROVED, same, or REGRESSION per fixture and model. The tolerance is the larger of 5 points and twice the trial spread. Use `evals/harness.py score <run>` to re-score saved runs after a scorer change, without new agent calls.

## Limits

The SPICE models are behavioral models with datasheet parameters, unless you add vendor models. Post-layout extraction treats the ground pour as ideal and uses closed-form microstrip formulas, not a field solver. The placer and router make a routable first layout, but they do not replace a review of switching power stages or RF layouts.
