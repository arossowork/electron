# Reference reuse: four ideas from HeyPCB for the 6-hour thermostat

HeyPCB designs a board by starting from a library of about 11,000 forked open-hardware boards and gives its tools to
agents over MCP. We take four of its ideas, map each one onto the /hw-design stages, and keep hwflow deterministic and
generic (no LLM call, no network, no design named in the code). The target is a thermostat from requirements to a
verified board and firmware in under 6 h. Run data: in run 1, review sent the circuit back 3 times (about 1 h of rework).
Layout convergence is the largest cost, and agents spend turns parsing long logs.

| # | Idea | Stage | Built | Expected saving per thermostat run |
|---|---|---|---|---|
| 1 | Start each major IC from a reference design that is known to work | 3 parts, 4 circuit | `hwflow ref search` | 15-30 min (one review round-trip less on application circuits) |
| 2 | Tool output for agents: small, structured, stable | every loop | `--json` on check, trace, nets, build, `hwflow summary` | 10-20 % of the build-loop tokens; fewer parse turns |
| 3 | Check datasheet limits and pin functions at parts time, not in review | 3 parts, 4 circuit | 13 new `hwflow check` codes, a parts checklist | 1 of the 3 review round-trips (about 20 min per late must-finding) |
| 4 | Reuse a known-good layout as a placement preset | 7 layout | `hwflow block match` (rigid preset); soft seed is design only | fewer stuck placements on designs with blocks (see 4) |

## 1. Reference-design retrieval (`hwflow ref search <mpn|words>`)

**What.** An index, read at call time, over three sources: every `design.yaml` under `examples/` and `projects/`
(`settings.paths.ref_dirs`), the layout block library (`blocks/`), and an external open-hardware index
(`settings.paths.oshw_index`, default `oshw/index.json`; it is optional). A query is MPNs (a family prefix such as
`MAX17227` matches `MAX17227AATA+T`) and/or function words (`boost`, `ldo`, `relay`, `ntc`, `usbc`, `hbridge`). A hit
is one part of one design with its sub-circuit: the discrete parts on its nets with values, footprints and nets, the
decoupling caps it owns (`decouples:`), the ICs and connectors next to it, the source path or URL, the licence, the
build verdict (from the design's `build/REPORT.md`), the layout block that covers it, and the datasheet notes that
already exist (`notes/datasheets/<mpn>.md`, `library/datasheets/`). The same sub-circuit in several project revisions
is printed once (`also in: ...`). `--json` gives schema `hwflow.ref/1`.

**External index schema** (the reader accepts it with or without netlists): a list, or an object with `designs`,
`boards`, `entries` or `items`. Each entry: `name` (required), `url`, `license`, `mcu`, `blocks` or `functions`, `mpns`,
`layers`, `size` (`[w, h]`, `{width_mm, height_mm}` or a string), `description`, `status`, and optionally `parts`
(`{ref: {value, mpn, footprint, pins: {pin: net}}}`). With `parts`, a hit shows the sub-circuit; without it, the entry
matches on its MPN list and functions and shows the metadata. A missing or unreadable file is named in the output and
skipped.

**Stages.** `hw-parts` runs it as the first step for each major IC (an IC with an application circuit), takes the parts,
values and footprints of a verified hit (a `build PASS` project or a layout block) and records the source and licence in
`notes/parts.md`. `hw-circuit` starts the capture of that IC from the hit and binds the block if one is listed.

**Risks.** A reference is only as good as its verification: only `build PASS` designs and blocks count as known-good;
an external entry is `external, unverified` and its licence must allow reuse (CERN-OHL-S and GPL carry obligations).
Copying a sub-circuit can carry its context (another supply, another load): the datasheet still decides the values.
Function words are a coarse match (synonym table in `core/refs.py`).

**Measured.** On this repository: 7 local designs and 9 blocks. `ref search MAX17227` returns the
`max17227a_boost` block (6 parts, layout and copper) and the home-thermostat-4 sub-circuit (PASS build, the same in
-3 and -4-blocks) in one call, with both datasheet notes. To measure the saving: count the review findings on
application circuits (missing or wrong external parts) per run, and the parts-stage turns, before and after.

## 2. Structured tool output (`--json`)

**What.** `hwflow check|sim|layout|post|fab|build <proj> --json` runs as before, sends the progress lines to stderr, and
prints one compact JSON object built from `build/status.json`: `hwflow.check/1` for check, `hwflow.summary/1` for the
others. `hwflow summary <proj> --json` gives the same summary without running anything (after `hwflow run wait`).
`trace --json` (`hwflow.trace/1`) gives counts, the verified ids and one row per open requirement (no requirement text:
it is in requirements.yaml). `nets --json` (`hwflow.nets/1`) gives one row per net under a column list. `ref`,
`block match` take `--json` too. Each view has a versioned `schema`; long lists are cut to 8 items next to a count.
The summary carries the verdict (from REPORT.md), the stale stages, the `score` card, check errors and warnings, ERC
errors, SPICE fails with value and limits, layout DRC counts by type with the first 8 errors and their location,
unrouted, the layout diagnosis and failing budgets, isolation and corridors, post-layout findings by code, and the trace
counts. Code: `src/hwflow/pipeline/summary.py`.

**Measured** on projects/home-thermostat-4 (the routed round-8 build: its `build/status.json`, 74 parts, 39 routed
nets; text and JSON from the same state):

| output | text (today) | `--json` | ratio |
|---|---|---|---|
| `hwflow check` (prints the whole report today) | 8090 B, 1234 words | 624 B | 13x smaller |
| `hwflow trace` (23 open requirements) | 7268 B | 3094 B | 2.3x |
| `hwflow nets` (39 nets) | 2901 B | 2147 B | 1.35x |
| build summary: `REPORT.md` / `summary --json` | 4385 B | 1484 B | 3x |
| `build/status.json` (what agents read for details) | 171546 B | 1484 B | 115x |

JSON tokenizes at about 3 bytes per token and prose at about 4, so the token ratio is about three quarters of the byte
ratio. The larger gain is turns: an agent reads `drc_by_type`, `fails` or `open` directly instead of reading
REPORT.md again or scripting over status.json. To measure the saving: tokens and turns per build loop of `hw-circuit`
and `hw-layout` in `.usage_agents.jsonl`, before and after.

**Risks.** Two formats to keep in step: the JSON reads the same status fields as the report, and
`tests/test_reference_reuse.py` pins the schema. An agent that needs a cut item reads status.json.

## 3. Datasheet and pin-limit checks at parts time

**What.** The late review findings of home-thermostat-3 (`conflicts.md` review 2, 2b, 2c, delta) and thermo-e2e-1
(`notes/review-1.md`, `review-2.md`), sorted by what could have caught them at parts time:

| finding (review) | caught by |
|---|---|
| +3V3 may reach 3.42 V against a 3.3 V relay coil maximum (e2e r1 #11) | **new `abs-max`**: `limits` on the part, `v_max` on the rail; driver outputs carry their supply's bound to the coil nets |
| fuse 3.15 A runs at 95 % of In (e2e r2) | **new `fuse-derating`**: `current_rating_a` against the nets' `current_a` (75 %, Littelfuse Fuseology) |
| KF350 drill 1.2 mm against 1.0 + 0.1 mm (e2e r1 #15) | **new `hole-size`**: footprint drill against the datasheet hole `limits.hole_mm` |
| D4 has no LCSC number (e2e r2) | **new `sourcing`**: active and electromechanical BOM parts without `lcsc` or a `sourcing:` note |
| TVS distance not constrained (e2e r1 #14) | **new `tvs-budget`**: a TVS/ESD diode without a `copper_len` budget |
| GPIO must be driven before RLY_EN; enable and strap pins in a defined state (e2e r1 #13, reviewer list) | **new `reset-state`**: a control input (EN, nSLEEP, RESET, ...) driven only by GPIO-type pins and no pull resistor |
| open-drain outputs need pull-ups (reviewer list) | **new `open-drain-pullup`, `i2c-pullup`** |
| ground or supply pin on the wrong net, undriven inputs | **new `pin-role`, `floating-input`**; **`cap-voltage`**: a capacitor's rating against its nets' bounds |
| setup button on a pin that cannot wake the MCU (ht3 D20) | existing `hwflow fw` `wake-pin` (run it at the end of parts, not only stage 4) |
| AC nets 0.23 mm apart, neck melts before the fuse (ht3 2b B1, 2c) | existing `between_mm`, `min_width_mm` (circuit checklist) |
| post 0.06-0.08 mm from K2, wire bend radius, cells near the antenna (ht3 2b M2, e2e r1 #3, #8) | existing `hwflow floorplan` (`envelope_mm`, `wall_mm`, regions) |
| guard FET without a surge rating; VGS(th) maximum corner; DC-bias capacitance; fuse breaking capacity; single source; coil must-operate hot; contact resistance; leakage at max | **parts checklist** (`hw-parts`): the datasheet number recorded with its page, and the corner simulated |
| missing fuse on mains, mechanical fixing, cover, insulation, changed acceptance without the PM | architecture, requirements or process: not a parts check |

Of about 30 late findings, 6 are caught by the new checks (rows 1-6), 6 by an existing command that had not run yet at
parts time, about 9 by the new checklist items in `hw-parts`, and the rest are architecture or process items. Rows 7-8
add the generic reviewer items (pull-ups, pin roles, undriven inputs, capacitor ratings).
Replay of row 1 on home-thermostat-4 (`+3V3: {v_max: 3.42}`, coil pins `limits: {pins: {A1: 3.3, A2: 3.3}}`): four
`abs-max` errors, K1/K2 A1/A2 on K1_COIL_A ... K2_COIL_B "reaches 3.42 V (bound from its drivers)", the DRV8837
outputs passing their VM supply's bound to the coil nets.

The checks are in `src/hwflow/core/pinchecks.py` and run in `hwflow check`. They read
the KiCad pin types and two new design fields: `limits:` on a component (`abs_max_v`, `rec_max_v`, `min_v`,
`pins: {pin or name glob: V}`, `hole_mm`, `current_a`, `source`) and `v_max`/`v_min`/`pullup` on a net
(docs/design-yaml.md). A net without `v_max` takes its voltage from a rail name (+3V3, 5V_SYS, P10V5) or an upper bound
from what drives it; an unknown net is not checked, never guessed.

**On the existing designs** (6 designs; warnings only, no new error, so no verdict changes): each gets one `sourcing`
line (home-thermostat-4: K1, K2; desktop-dac-hd6xx: 56 parts) and one `tvs-budget` line (home-thermostat-4: D2, D3);
the keyboard also gets 3 `open-drain-pullup` candidates (B5_PG, C1_EN, C2_EN, to confirm against the datasheets) and
8 `pin-role` warnings on USB-C GND pins tied to sensed returns (intended there). `abs-max` fires only once
`limits:` and `v_max` exist, so its value depends on `hw-parts` writing them.

**Risks.** Bounds are conservative, not exact: a voltage above a rail (a boost switch node, a charge pump) is unknown
unless written as `v_max`. Name-based rails assume the usual naming. The warnings can be noisy on large boards; each one
names the field that silences it.

**Measure.** Review findings per run at `must` level that a parts-stage check or checklist item covers; target: no
review round-trip for a finding in the table.

## 4. Reference layouts as placement presets

**Today.** A layout block is a rigid preset: placement and copper, moved as one unit by the macro stage
(docs/layout-flow.md). On ht4-blocks the old annealer got stuck on a rigid block (6 violations in one sample, 559 s in
`unstick`) and macro-first placed every sample clean (docs/asic-ideas.md, idea 3). Binding was done by hand.

**Built (prototype, rigid).** `hwflow block match <proj> [--json]` finds library blocks whose roles fit unbound parts:
same footprint per role, a consistent block-net to design-net map, two block nets on one design net only when both
are ports, and no internal block net reaching a part outside the binding (the rules of `hwflow check`). It prints the
`blocks:` entries, the value differences, and the parts whose own `placement:` must go. On home-thermostat-4 it finds the
four bindings that home-thermostat-4-blocks has by hand (adc, boost switch, xtal, VBAT divider) plus the boost and both
H-bridges; with them applied, `check` reports no block error. A block from an external board comes through
`hwflow block extract <board.kicad_pcb>` (the board found by `ref search`), then `block match`. Before any part is
captured (the parts stage), it also lists the blocks that the project texts (architecture.md, partition.md,
notes/parts*.md, notes/datasheets/) name by active part or by the words of the block name (`by_function` in the JSON):
the parts stage takes such a block's parts and values, or records in notes/parts.md why not.

**Design (soft seed, not built).** A rigid preset fails when the design differs from the reference (other values, a
fixed part in the way: ht4-blocks left max17227a_boost unbound because its courtyard overlapped the fixed MCU block).
The soft seed keeps the parts movable:
1. Source: a block (its `placement:`), an extracted external board, or the last routed placement of the same design
   (`build/placement.json`) for the parts whose footprint and nets did not change (an ECO seed after a circuit fix).
2. Match: `blocks.match` with partial matches allowed (at least the anchor IC and 70 % of the roles).
3. Placer: the matched parts start global placement at the reference offsets around the anchor (rotations included),
   and a cohesion term (springs to the reference offsets, weight decaying over the rounds) joins the cost; no copper is
   pre-placed, so the rules of the board apply.
4. Switch: `placement.seeds: {<name>: {from: <block|board|last>, parts: {...}}}` in design.yaml, off by default.
Measure on three designs (home-thermostat-4, desktop-dac-hd6xx, sensor_preamp): placement violations, HPWL, unrouted
after the first route, and wall time to the first clean board, against no seed, over the same placer seeds. Keep it
only if it wins on more than one design. Risks: a seed from a board with other rules or density can be worse than no
seed; the ECO seed can lock in a bad placement (hence the decay); front side only, like blocks.

## What is left

- Soft seed (section 4): placer work and the A/B measurement.
- `ref search`: the external index (being built in `oshw/index.json`); pin names in the sub-circuit (needs the symbols);
  an MCP wrapper is not needed while agents call the CLI.
- `limits` for the parts of the existing projects (only new parts get them from `hw-parts`).
- Measuring the time saved needs a full run with the new agent instructions (the `hwflow time` stage marks give the
  rework hours per stage).
