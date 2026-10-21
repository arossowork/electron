# design.yaml reference

The file `projects/<name>/design.yaml` is the single source of truth for a design. The `hwflow` tool generates the schematic, the SPICE netlists, the PCB, and the fabrication files from it. You edit `design.yaml` and never edit files in `build/`.

## Top-level keys

```yaml
project: my_board              # file name stem for KiCad outputs
revision: A
description: one line
board: {...}                   # PCB constraints
nets: {...}                    # per-net electrical intent
components: {...}              # parts, keyed by reference designator
sim: {...}                     # SPICE testbench and measures
optimize: {...}                # optional optimization problem
libraries: [lib]               # optional project libraries (symbols/ and footprints/ inside)
placement: {weights: {...}}    # optional placer tuning
blocks: {...}                  # optional layout blocks bound to components (see below)
approval: {cost_pct: 5, size_mm: 0.5}   # optional tolerances of the approval check (see below)
floorplan: {...}               # optional construction, depth stack, regions and items (stage 2, see below)
```

## libraries

A part that KiCad's libraries do not have goes into a project library. Put `<lib>.kicad_sym` files in `<proj>/lib/symbols/` and `<lib>.pretty/` folders in `<proj>/lib/footprints/`. Folders named `lib`, `libs` or `kicad_share` load automatically, and `libraries:` adds others. Project libraries come before KiCad's own. Never point `KICAD_SHARE` at a project copy: the scorer and the fab outputs run without your shell environment.

## board

| key | default | meaning |
|---|---|---|
| width_mm, height_mm | 50, 40 | rectangular outline |
| layers | 2 | 2 or 4 copper layers |
| thickness_mm | 1.6 | board thickness |
| copper_oz | 1.0 | outer copper weight |
| clearance_mm, track_mm | 0.2, 0.25 | default rules |
| power_track_mm | 0.4 | minimum width for nets with `class: power` |
| via_drill_mm, via_dia_mm | 0.3, 0.6 | via size |
| gnd_pour | true | ground zones on all copper layers |
| er | 4.3 | dielectric constant (FR-4) |
| ref_plane_h_mm | thickness on 2 layers, 0.2 on 4 layers | trace to reference plane distance |
| decap_max_mm | 4.0 | maximum copper path from a decoupling capacitor to its IC pin |
| margin_mm | 1.0 | keep parts this far from the board edge |
| router | krt | `krt` or `freerouting` (KiCadRoutingTools: one pass per track-width class, no vias in pads) |
| post_tran_l | false | include extracted trace inductance in post-layout transients (always in AC and noise) |
| post_tran_lump | true | in post-layout transients, reduce each net's trace network to its pads (exact DC resistance, no trace C); AC and noise keep the full network. Scope: lumped transients check resistive drops and slow dynamics, not reflections, ringing, crosstalk or RC delay along a trace. Nets with `class: fast`, `edge_ns` or `post_full: true` keep their full R and C network |
| post_lump_check | none | name of one transient analysis that post-layout also runs with the full R and C network; the report gives the largest difference and both run times |
| router_retries | 3 | KiCadRoutingTools: when a width class leaves nets unrouted, route it again in another net order this many times and keep the best |
| repair_rounds | 3 | layout stage: after DRC, at most this many repair rounds (0 = off). A round rips up and re-routes only the nets the DRC errors name (wider radius after a round without gain, other net order each round) and adds stitching vias between GND pour fragments; it keeps a round only if it has fewer errors and no more unrouted nets, and stops after two rounds in a row without gain. The result shows `repair: 3 -> 0 errors in 2 rounds, 7.1 s`. Needs the KiCadRoutingTools router |
| drc_rules | [] | KiCad custom rules, one `(rule ...)` string each, written to `<project>.kicad_dru` and applied by DRC. Use for class-pair clearance and creepage, e.g. mains to SELV |
| isolation | [] | net groups that keep a distance to every other net, as data (backlog 38): `{name, nets: <glob or list>, class: <net class>, clearance_mm, creepage_mm, req}`. hwflow writes one KiCad clearance and one creepage rule per group into the `.kicad_dru` (next to `drc_rules`), runs them in the layout-stage DRC, reports the measured copper distance in plan with the limit and the DRC violations, and a group with `req: R8` verifies that requirement in `trace`. Example: `isolation: [{name: mains-SELV, class: mains, clearance_mm: 4.0, creepage_mm: 6.0, req: R8}]`. The plan distance does not see a stacked second board (see docs/design-note-stack-keepouts-boards.md). Routing: the member nets route first, and every router pass keeps its nets the group distance from the other side's pads, tracks and vias (`src/hwflow/routing.py`). Placement: when every part with a member pad is fixed, the other parts stay out of the area the member pads enclose |
| si_pdn_z_ohm | 1.0 | signal-integrity screen: the largest impedance of a decap loop at the knee frequency 0.35/t_r of its IC (see `edge_ns` under nets) |
| thermal_clearance_mm | 5 | placer: parts that dissipate more than 50 mW (from `build/status.json` derating, when a build has run) keep this distance from temperature-sensing parts |
| antenna_clearance_mm | 10 | placer: antenna parts keep this distance from relays (`K*` refs, `Relay*` symbols) and from the pads of `isolation` / mains groups |
| optimize_area | false | shrink the board below width x height to the smallest size that places and routes |

Route cache: the layout stage stores the routed and repaired copper in `build/routecache/<key>.json`; the key hashes the unrouted board (placement, footprints, nets, outline), the rules files, the `board:` settings, the router version and the repair code. The same key skips the router and the repair (the full DRC still runs), so `layout --reuse` and a repeated build give the same copper. Delete `build/routecache/` to force a new route. `layout --auto` runs its attempts in parallel (`--workers N`, default cpu count - 1) and stops when the remaining errors are on parts with `fixed: true`.

## nets

Only nets that need intent are listed. Every net in `components.*.pins` exists automatically.

```yaml
nets:
  VBUS: {class: power, current_a: 0.5, external_supply: true}
  GND: {class: ground}
  ADC_IN: {class: signal, sensitive: true}
  CLK: {class: clock}                    # clock, switching, high_speed count as aggressors
  RF: {impedance_ohm: 50, width_mm: 1.1}
```

`class: fast` or `edge_ns: <rise time>` keeps the full trace R and C network of that net in post-layout transients (see `post_tran_lump`). `edge_ns` also turns on the signal-integrity screen in post-layout (`si` findings, no SPICE): the delay of the longest pad-to-pad copper path against t_r/6 (warn: electrically long, terminate it or rely on the full transient), the vias and branch points of the net, the trace length with no pour on the adjacent copper layer (own via and pad antipads excluded), and for every IC on such a net (or with `edge_ns:` on the component) the extracted inductance of each decap path as impedance at 0.35/t_r against `board.si_pdn_z_ohm` (default 1 ohm). `current_a` sizes the track with IPC-2221 (10 C rise). A net wider than `board.track_mm` that cannot enter a fine-pitch pad at its width necks down: at most 3 mm of its copper at `board.track_mm`, or else at 0.15 mm (the build report names the net and the narrow length); a longer narrow stretch leaves the net open instead. `external_supply` marks a rail that enters through a connector. `sensitive` turns on crosstalk and return-path checks for that net.

## components

```yaml
components:
  U1:
    value: AP2112K-3.3                      # printed value
    mpn: AP2112K-3.3TRG1                    # manufacturer part number
    lcsc: C51118                            # JLCPCB/LCSC number, if known
    price_usd: 0.07                         # unit price at prototype quantity
    datasheet: https://...
    symbol: Regulator_Linear:AP2112K-3.3    # KiCad lib_id, must exist (hwflow lib sym)
    footprint: Package_TO_SOT_SMD:SOT-23-5  # KiCad footprint, must exist (hwflow lib fp)
    package: SOT-23-5                       # optional: datasheet package name, compared with the footprint
    package_pads: 5                         # optional: datasheet pad count (check error if the footprint differs)
    package_pitch_mm: 0.95                  # optional: datasheet pitch (check error if off by > 10 % or 0.05 mm)
    pins: {"1": VBUS, "2": GND, "3": VBUS, "5": +3V3}   # symbol pin NUMBER -> net
    nc: ["4"]                               # pins left open on purpose
    group: power                            # schematic column and placement cluster
    decouples: U2                           # for capacitors: the IC this cap serves
    placement: {edge: left}                 # or {x: 5, y: 10, rot: 90, fixed: true}
    tolerance: 0.01                         # Monte Carlo tolerance (R 1%, C 10%, L 20% default)
    voltage_rating_v: 16                    # capacitors: derating check (50% by default)
    power_rating_w: 0.1                     # resistors: default from package size
    thermal_vias: {pad: "2", count: 6}     # via grid inside a thermal pad, to the opposite-layer pour
    role: temp_sensor                       # optional: temp_sensor | antenna | none. Placement roles; inferred from the symbol (Sensor_Temperature:*, Device:Thermistor*; RF_Module:*, footprint with Antenna) when absent, none turns the inference off
    theta_ja: 250                           # junction temperature estimate
    tj_max_c: 125
    spice: {...}                            # how to simulate it, see below
```

Every symbol pin must be in `pins` or in `nc`, otherwise `hwflow check` reports an error. Read the pin numbers with `hwflow lib show <lib_id>`. Do not guess them.

### spice

| part | spice entry |
|---|---|
| R, C, L | none. The value is used. For capacitors you can add `esr` and `esl`. |
| diode or LED | `{model: LED_GREEN}`. Ports default to anode=pin 2, cathode=pin 1, as in `Device:D` and `Device:LED`. |
| MOSFET | `{model: NMOS_LOGIC, ports: [D, G, S]}` with pin numbers. **The reference must start with `M`** (`M1`, `M2`): the first letter of the reference picks the SPICE element, so a MOSFET named `Q2` is netlisted as a bipolar transistor and ngspice aborts with an error on that line. `hwflow check` reports this as a `spice-element` error (the model type is read from the `.model` line). Bipolar transistors use `Q`. |
| BJT | `{model: NPN_GENERIC, ports: [C, B, E]}` |
| IC | `{subckt: OPAMP_GENERIC, ports: [...pin numbers in model port order...], params: {...}}` |
| vendor model | `{lib: models/opa344.lib, subckt: OPA344, ports: [...]}` (path is relative to the project) |
| not simulated | `spice: false` (connectors, mounting holes, test points) |

Every active part with a model in a project `.lib` file sets `spice.source`: `vendor` (the manufacturer's model, unchanged or only translated), `fit` (parameters fitted to the datasheet), or `behavioral` (written for this project). Only `vendor` counts as an exact model. The report lists every other active part as approximate.

The built-in models are in `src/hwflow/models/generic.lib`. You set their parameters from the datasheet:

- `OPAMP_GENERIC` ports `inp inn out vcc vee`: GBW, AOL, VOS, IQ, EN (V/rtHz), ROUT, VDROP (swing to rail), SR.
- `COMP_GENERIC` ports `inp inn out vcc vee`: TPD, IQ.
- `LDO_GENERIC` ports `in out gnd`: VOUT, VDROP, IQ, ROUT, PSRR_DB, ILIM, TAU.
- `BUCK_AVG` ports `vin out gnd`: VOUT, EFF, ILIM, IQ (averaged, no ripple).
- `VREF_SHUNT` ports `k a`: VREF, RZ.
- Models: `D_GENERIC D_SCHOTTKY LED_RED LED_GREEN LED_BLUE LED_WHITE NMOS_LOGIC PMOS_LOGIC NPN_GENERIC PNP_GENERIC`.

## sim

```yaml
sim:
  ambient_c: 40
  includes: [models/vendor.lib]          # optional extra model files
  sources:                               # testbench only, not in the BOM
    - {name: VIN, p: J1.1, n: J1.2, value: "DC 5 AC 0 PULSE(4.5 5.25 1m 10u 10u 2m 4m)"}
    - {name: RL, p: +3V3, n: GND, value: "33"}            # name prefix sets the element type
    - {line: "Iload $(+3V3) 0 PULSE(0 0.1 1m 1u 1u 1m 2m)"}
  analyses:
    - name: dc_op
      cmd: op
      temps: [-20, 25, 85]               # optional: repeat at each temperature
      measures:
        - {name: v3v3, expr: "v(+3V3)", min: 3.25, max: 3.35, unit: V, req: R2}
    - name: ac
      cmd: ac dec 50 1 1meg
      measures:
        - {name: gain_db, meas: "find vdb(OUT) at=1k", min: 19.8, max: 20.2, unit: dB, req: R3}
    - name: noise
      cmd: noise v(OUT) VIN dec 20 1 100k
      monte_carlo: false
      measures:
        - {name: noise_uv, plot: noise2, expr: "onoise_total*1e6", max: 50, unit: uVrms, req: R6}
```

Each analysis that stresses the design (a hot-plug, reverse polarity, a surge, a brown-out) carries a `realism:` note, free text that `hwflow` ignores: `realism: "who: user, one cell reversed; how often: once per battery change; timing: >= 5 s between removal and insertion; limit: Q1 Idmax 2 A DC, 8 A for 10 us (datasheet p.3)"`. If no user or fault can produce the scenario, write `realism: "stress case: <why unreachable>"`. A stress case does not block the verdict and does not justify a hardware change without a product decision.

Terminals are net names or `REF.PIN`. Use `REF.PIN` for sources that feed a connector, because the post-layout run then injects at that pad. `v(NET)` and `$(NET)` inside commands are replaced with SPICE node names. `meas` is the ngspice `meas` syntax without the analysis type and the name. `expr` is an ngspice vector expression. `scale` multiplies the result before the limits apply. `req` names the requirement IDs that the measure verifies, as a comma-separated list.

`timeout_s` on an analysis (default 600) bounds its ngspice run. An analysis that times out, crashes, aborts or reports an error is named in the report with its status and run time, and every measure without a value says why (`n/a: timeout after 600 s`, `n/a: aborted: ...`). A run that crashes on a signal (`free(): invalid ...`) is retried once and reported as flaky.

A measure whose name starts with `dbg_`, or that sets `debug: true`, is a debug probe: the report lists its value apart, and it never counts in the pass ratio, the verdict, the requirement trace, the post-layout comparison or the Monte Carlo yield.

## blocks

A layout block is a sub-circuit laid out and checked once: a boost cell, an MCU module with its decoupling, a guard FET pair. The library is `blocks/<name>/block.yaml` in the repository (`uv run hwflow block list`, `uv run hwflow block show <name>`); a project may add its own in `<proj>/blocks/`. A design binds its components to a block's roles:

```yaml
blocks:
  boost: {use: tps61299_boost, parts: {U: U2, L: L1, CIN: C3, COUT: C4}}             # the placer chooses the place
  mcu: {use: esp32c3_mini_decoupling, parts: {U: U1, C100N: C7, C10U: C6},
        at: {x: 62, y: 37.5, rot: 180, fixed: true}}                                 # block origin on the board
```

The components stay in `components:`. `hwflow check` verifies the binding: every role is bound once, each part has the role's footprint, the part's pin nets map one to one onto the block's nets (a block net cannot be two design nets, an internal net cannot reach a part outside the block, two block nets on one design net must both be ports), the value matches `value`, `value_min` or `value_max` (a warning), the board has `layers_min` layers, and a part in a block has no `placement:` of its own. A board clearance or via size larger than the block's `rules` is a warning.

- `at` (optional): `{x, y, rot, fixed}` of the block origin; `fixed` defaults to true, `rot` is a multiple of 90. Without `at` the placer places the block. Only the front side is supported.
- Placement: the placer moves the block's parts as one rigid unit (translation, rotation by 90 degrees) and never changes their relative placement. The block courtyard counts in the overlap, keep-out and bounds costs like a part courtyard.
- Routing: the block copper is in the unrouted board and every router pass keeps it (`--keep-input-copper`). A net whose pads are all in the block and joined by its copper is not routed. The routed board holds the block copper once, also after repair and from the route cache.
- `drc_rules` of a block (a land pattern that needs a smaller clearance) are added to the `.kicad_dru`.
- Instantiate mode: `{use: <block>, nets: {VIN: VBAT, VOUT: +3V3, GND: GND}, refs: {U: U7}}` without `parts` adds the block's components (refs from `refs`, else the block's); internal nets become `<entry>_<net>`. `Design.save` does not write them back.

block.yaml:

```yaml
name: tps61299_boost
description: one line
source: datasheet section and URL, or the board and run it was extracted from
layers_min: 2
rules: {clearance_mm: 0.2, via_dia_mm: 0.6, via_drill_mm: 0.3}   # what the copper was checked at
drc_rules: ['(rule "{block} fine pitch" (condition "A.memberOfFootprint(''{U}'')") (constraint clearance (min 0.15mm)))']
ports: [VIN, VOUT, GND]                    # nets the block exposes; every other pin net is internal
roles:
  U: {ref: U2, symbol: thermo:TPS61299, footprint: Package_TO_SOT_SMD:SOT-563, value: TPS61299,
      pins: {'1': VIN, '2': SW, '3': VIN, '4': GND, '5': VOUT, '6': GND}}
  CIN: {ref: C3, symbol: Device:C, footprint: Capacitor_SMD:C_0805_2012Metric, value_min: 2.2u, pins: {'1': VIN, '2': GND}}
placement:                                 # footprint origin of each role from the block origin
  U: {x: 0, y: 0, rot: 0, side: front}
copper:
  tracks: [{net: SW, layer: F.Cu, w: 0.2, pts: [[-0.75, 0], [-1.6, 0]]}]   # polylines, block nets
  vias: [{net: GND, x: 1.75, y: -2.75}]
courtyard: [x0, y0, x1, y1]                # the whole block, parts and copper
keepouts: [{rect: [x0, y0, x1, y1], layers: [In1.Cu, In2.Cu], forbids: [tracks, vias, copperpour]}]   # optional
resistance_mohm: {IN-OUT copper at 20 C: 2.8}                         # optional, with the arithmetic in a comment
```

A block's own footprints go in `blocks/<name>/footprints/<lib>.pretty/`. `uv run hwflow block check <name>` places the block alone on a small board (ports as nets, ground pour on GND) and runs KiCad DRC on its copper. `uv run hwflow block extract <board.kicad_pcb> --refs U2,L1,C3,C4 --name <name> --source "<text>" [--roles U=U2,...] [--project <proj>]` writes a block from a routed board: the parts' placement relative to the first one, the copper of the nets whose pads are all in the set, and the port copper attached to the parts, clipped 1 mm around them.

## optimize

```yaml
optimize:
  budget: 60
  margin: 0.1
  variables:
    - {ref: R1, min: 10k, max: 1meg, series: E96}
    - {ref: U2, choices: [{}, {value: OPA344, mpn: OPA344NA, price_usd: 0.95, spice: {params: {EN: 9n}}}]}
  objectives:
    - {measure: noise_uv, weight: 1}
    - {measure: cost_usd, weight: 0.5}
    - {measure: bandwidth, weight: 1, goal: max}
```

Every measure with limits stays a hard constraint. `margin` (default 0.1) keeps each measure that fraction of its limit span away from the limits, so the optimum is not on the edge. Objectives are normalized to the starting design and summed with the weights. A choice is a patch that is merged into the component.

## floorplan

The architect writes the floorplan in stage 2, before parts exist, and `uv run hwflow floorplan <proj>` checks it in about 20 s: no simulation, no schematic, no routing. A stage-2 `design.yaml` may hold only `project`, `board` (size, layers) and `floorplan`. Stage 4 adds the components and names the real parts of each item (`refs`); the same command then uses their courtyards. In stage 7 the placer keeps the regions.

```yaml
floorplan:
  construction: double          # single (one face) | double (two faces) | stacked (two or more boards)
  fill_max: 0.85                # optional: face fill that fails (risk above 0.70)
  depth:
    limit_mm: 30                # R2
    extra: [{name: cover sheet and air, mm: 1.0, source: R2}]
  regions:
    - {name: notch_l, kind: keepout, circle: [2.25, 23, 4], conductive: true}   # a screw is accessible metal
    - {name: mains, kind: mains, rect: [0, 0, 20, 46], nets: ["AC_*"], isolation_mm: 6.0, source: R10}
    - {name: hv_parts, kind: barrier, rect: [0, 0, 26, 46], items: [relays, terminals]}
    - {name: battery, kind: battery, rect: [27, 0, 82, 28.5], items: [cells]}
    - {name: antenna, kind: antenna, rect: [60, 40, 73, 46], allow: [module], isolation_mm: 15, from: [mains, barrier, battery]}
  items:
    - {name: relays, size_mm: [24.5, 10.5], count: 2, side: back, height_mm: 15.8, protrusion_mm: 1.0, source: "ADW1 datasheet"}
    - {name: cells, refs: [BT1, BT2], height_mm: 12.0, source: "LR03 10.5 + clip 1.5"}
    - {name: module, refs: [U1], height_mm: 2.4}
  nets: [{between: [module, relays], count: 4}]   # stage 2 only: wires between placeholders
```

Coordinates are board millimetres, as in `placement: {x, y}` (origin top left, y down).

- `construction: stacked` needs `boards: [{name, size_mm: [w, h], at: [x, y], stack_mm, connector, flipped}]`, listed from the top (face A) down. hwflow lays the boards side by side in one panel (`at`, default left to right with a 6 mm gap), so `board:` is the panel. `stack_mm` is the mated height of the board-to-board connector below that board. `flipped: true` marks a board drawn with mirrored footprints (its back face looks up). Every item and region of a stacked floorplan names its `board`.
- A region has a shape (`rect: [x0, y0, x1, y1]`, `poly: [[x, y], ...]` or `circle: [cx, cy, r]`), a `side` (front, back, both; default both) and a `kind`:
  - `mains`, `antenna`, `keepout`, `barrier` are exclusive: a part that is not a member stays out on the region's sides. A barrier region holds the parts that bridge an isolation band (relays, optocouplers); its members may enter the mains region, and the mains members may enter it.
  - `selv`, `battery`, `other` only hold their members.
  - `items` lists the members (item names or ref patterns); an item's `region` does the same. A member stays inside. `allow` lets other parts into an exclusive region. `nets` (globs) are the nets whose pads stay inside the region.
  - `isolation_mm` is the distance to the other regions on the same board (by default every other kind except barrier and keepout; `from` names kinds or regions). A `conductive` keep-out is cut out of every region with `isolation_mm`, grown by that distance.
- An item is a placeholder (`size_mm: [w, h]`, `count`, `side`, `at: [x, y]` centre, `edge`) or real parts (`refs` and `groups`, patterns on refs and component groups; a ref named in `refs` wins over a group). `height_mm` (or `height_mm` on the component) enters the depth stack, `protrusion_mm` adds the through-hole pins on the other face. `area_mm2` replaces the courtyard area in the fill (a holder on wire leads leaves the board under it free). Later, a pre-laid-out block is an item with a size too.

The check, written to `build/FLOORPLAN.md`, `build/floorplan.svg` and `status.json` (`floorplan`):

| check | fails when |
|---|---|
| area | a board face, or the pool of a face left outside the exclusive regions and the mains isolation band, fills more than `fill_max` (risk above 70 %); a region holds more courtyard area than it has |
| fit | a member cannot fit in its region in any position or rotation |
| depth | the stack is above `limit_mm`, or a connector `stack_mm` is lower than the parts between the boards. Single and double: front tallest + board + back tallest. Stacked: top front + boards + gaps (max of `stack_mm` and back of the upper board + front of the lower one) + bottom back. Plus `extra` |
| isolation | two regions are closer than `isolation_mm` |
| routability | a quick placement (the stage-7 placer with the regions, 20000 moves, 2 seeds) needs more than 20 % of the track capacity where its nets may run (risk above 10 % or a cell at twice its capacity) |

The thresholds come from the thermostats: the baseline (two stacked 62 x 40 boards, 4 layers, routed clean) fills its faces 20-42 % and uses 5 % of the track capacity; home-thermostat-3 at 88 x 46 on 4 layers (4 unrouted) fills its SELV face 74 % and uses 11-13 %; at 74 x 46 on 2 layers (95 unrouted) 95 % and 24-28 %. Overlaps left by the quick placement are risks, not failures. The verdict is PASS, PASS with risks, or FAIL; a FAIL that needs a size, layer, construction or cost change is a product decision.

In stage 7 a part in a region keeps its courtyard inside it and the other parts stay out of the exclusive regions (a placer cost like the keep-out term). Fixed parts are reported, not moved.

## approval

Layers, board size and BOM cost are product decisions (backlog 44). `hwflow approve <proj> [--note "round 3 q1"]` records them as accepted by the product person in `build/approved.json` (layers, width and height from design.yaml, BOM cost, date, design hash, note). From then on `hwflow check` (and the check stage of `build`) compares the design with that baseline:

| change | flagged when |
|---|---|
| layers | any change of `board.layers` |
| size | `board.width_mm` or `board.height_mm` moves more than `approval.size_mm` (default 0.5 mm), either way |
| cost | the BOM cost rises more than `approval.cost_pct` (default 5 %) |

A flagged change is an **error** (the build stops at check) unless the product person knows about it, then it is a **warning**:

- a line `change: layers` (or `size`, `cost`) in `conflicts.md`, with the numbers, or
- a question in `inbox/questions*.json` with `"change": "layers"`; the warning says whether `inbox/answers.md` already holds the answer of that round.

After the answer, run `hwflow approve <proj>` so the new state becomes the baseline and the warning goes. Without `build/approved.json` check only prints an info line. `build/approved.json` is written by hwflow only; the design agents run `approve` only after an answered question.
