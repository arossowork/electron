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
placement: {weights: {...}}    # optional placer tuning (e.g. corridor: 30, see docs/layout-diagnosis.md)
blocks: {...}                  # optional layout blocks bound to components (see below)
budgets: [...]                 # optional requirement margins that layout may spend (see below)
approval: {cost_pct: 5, size_mm: 0.5}   # optional tolerances of the approval check (see below)
cost: {extra: [{name: PCB 10 pcs / 10, usd: 0.5, source: "JLC quote"}]}   # optional non-BOM unit-cost lines (see cost)
floorplan: {...}               # optional construction, depth stack, regions and items (stage 2, see below)
```

## libraries

A part that KiCad's libraries do not have goes into a project library. Put `<lib>.kicad_sym` files in `<proj>/lib/symbols/` and `<lib>.pretty/` folders in `<proj>/lib/footprints/`. Folders named `lib`, `libs` or `kicad_share` load automatically, and `libraries:` adds others. Project libraries come before KiCad's own. The repository's shared library `library/` (nickname `hw`: `library/symbols/hw.kicad_sym`, `library/footprints/hw.pretty`, notes in `library/datasheets/`, list in `library/README.md`) comes after the project libraries and before KiCad's; `hwflow lib sym|fp|show|pads` search it too. Never point `KICAD_SHARE` at a project copy: the scorer and the fab outputs run without your shell environment. A back-face part drawn as a mirrored `_back` footprint (the front footprint mirrored in x, body on B.CrtYd/B.Fab, placed on F.Cu) is written as bottom side in `build/fab/positions.csv` and `cpl.csv` (JLCPCB columns), with the rotation KiCad gives the same part flipped to B.Cu (orientation + 180) and the board X/Y; the build report lists the bottom-side and the through-hole parts (THT or hand soldering, not SMT placement).

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
| decap_max_mm | 4.0 | maximum copper path from a decoupling capacitor to its IC pin (post-layout warning; with `HWFLOW_PLACE_DECAP_HARD=1` also a hard placement limit on the straight pad distance over the routing allowance) |
| margin_mm | 1.0 | keep parts this far from the board edge |
| router | krt | `krt` or `freerouting` (KiCadRoutingTools: one pass per track-width class, no vias in pads) |
| post_tran_l | false | include extracted trace inductance in post-layout transients (always in AC and noise) |
| post_tran_lump | true | in post-layout transients, reduce each net's trace network to its pads (exact DC resistance, no trace C); AC and noise keep the full network. Scope: lumped transients check resistive drops and slow dynamics, not reflections, ringing, crosstalk or RC delay along a trace. Nets with `class: fast`, `edge_ns` or `post_full: true` keep their full R and C network |
| post_lump_check | none | name of one transient analysis that post-layout also runs with the full R and C network; the report gives the largest difference and both run times |
| router_retries | 3 | KiCadRoutingTools: when a width class leaves nets unrouted, route it again in another net order this many times and keep the best |
| repair_rounds | 3 | layout stage: after DRC, at most this many repair rounds (0 = off). A round rips up and re-routes only the nets the DRC errors name (wider radius after a round without gain, other net order each round) and adds stitching vias between GND pour fragments; it keeps a round only if it has fewer errors and no more unrouted nets, and stops after two rounds in a row without gain. The result shows `repair: 3 -> 0 errors in 2 rounds, 7.1 s`. Needs the KiCadRoutingTools router |
| drc_rules | [] | KiCad custom rules, one `(rule ...)` string each, written to `<project>.kicad_dru` and applied by DRC. Use for class-pair clearance and creepage, e.g. mains to SELV |
| isolation | [] | net groups that keep a distance to every other net, as data (backlog 38): `{name, nets: <glob or list>, class: <net class>, clearance_mm, creepage_mm, req, role, inputs}`. `role: mains` (or "mains" in the name or class) marks mains nets for the `mains-fuse` check, with the nets of a `kind: mains` floorplan region: they need an overcurrent device (fuse, PTC, breaker), and the line input (`inputs: [AC_LIN]`, or `nets.<n>.role: mains_input`; else the mains nets on a connector pin) must reach the rest of the circuit only through it. `nets.<n>.overcurrent: "<upstream device and source>"` records protection outside the board. hwflow writes one KiCad clearance and one creepage rule per group into the `.kicad_dru` (next to `drc_rules`), plus, when `creepage_mm` is larger than `clearance_mm`, a clearance rule at `creepage_mm` between the members and the copper pours (zones) of other nets: KiCad's zone filler honours clearance rules only, and on a board without a slot the creepage is the in-plane distance, so a pour at the clearance fails the creepage rule. It runs in the layout-stage DRC, reports the measured copper distance in plan with the limit and the DRC violations, and a group with `req: R8` verifies that requirement in `trace`. Example: `isolation: [{name: mains-SELV, class: mains, clearance_mm: 4.0, creepage_mm: 6.0, req: R8}]`. `between_mm` (optional) is the clearance and creepage between two different member nets of the group (functional insulation, e.g. 1.5 mm between AC_L_IN, AC_L, AC_ROOM1 and AC_ROOM2); without it they keep the board clearance. It becomes KiCad DRC clearance and creepage rules per member against the other members; for the router, one class `isb_<name>_between_<net>` per member holding only that net, with a track rule at between_mm (KiCadRoutingTools merges a net's classes and applies the largest rule that binds a pair, so the group rule still holds against the other nets), plus a keep-out of the other members' pads and earlier copper grown by between_mm in each pass; the placer keeps member pads of different nets between_mm + 0.1 mm apart, `hwflow check` warns about fixed pads closer than that, and the layout and post-layout isolation report a `<name> between members` row with the closest member pair (a layout failure below the limit; no `req`). The plan distance does not see a stacked second board (see docs/design-note-stack-keepouts-boards.md). Routing: the member nets route first, and every router pass keeps its nets the group distance from the other side's pads, tracks and vias (`src/hwflow/layout/board/routing.py`). Placement: when every part with a member pad is fixed, the other parts stay out of the area the member pads enclose |
| si_pdn_z_ohm | 1.0 | signal-integrity screen: the largest impedance of a decap loop at the knee frequency 0.35/t_r of its IC (see `edge_ns` under nets) |
| thermal_clearance_mm | 5 | placer: parts that dissipate more than 50 mW (from `build/status.json` derating, when a build has run) keep this distance from temperature-sensing parts; written here, it is a hard placement rule |
| antenna_clearance_mm | 10 | placer: antenna parts keep this distance from relays (`K*` refs, `Relay*` symbols) and from the pads of `isolation` / mains groups; written here, it is a hard placement rule |
| optimize_area | false | shrink the board below width x height to the smallest size that places and routes |
| budget_max_track_mm | 3.0 | widest track a `budgets:` entry may ask for; past it, the layout stage says the budget needs the parts closer |

Route cache: the layout stage stores the routed and repaired copper in `build/routecache/<key>.json`; the key hashes the unrouted board (placement, footprints, nets, outline), the rules files, the `board:` settings, the router version and the repair code. The same key skips the router and the repair (the full DRC still runs), so `layout --reuse` and a repeated build give the same copper. Delete `build/routecache/` to force a new route. `layout --auto` runs its attempts in parallel (`--workers N`, default cpu count - 1) and stops when the remaining errors are on parts with `fixed: true`. It prints one progress line per attempt. `HWFLOW_LAYOUT_BUDGET_S` (default 3000 s) is a routing/search deadline, including the initial route (placement takes at most half of it: no new placer seed starts and `unstick` stops past that point), region groups, net-order retries, area search and repair rounds. After it, no new attempt or router call starts; running router calls time out and the best completed candidate is retained. A completed variant from an interrupted pass is kept as a partial result with `budget_exhausted: true`; unfinished subprocess output is never imported, and partial boards or groups are not cached. Area search prefers an earlier complete size, otherwise retaining the interrupted size and its accepted copper. Final DRC, cleanup, diagnosis and writing `status.json` / `REPORT.md` may continue after the search deadline under their own command limits. This is not a strict whole-layout wall-time limit; use an outer process timeout when a hard cap is required. The repair is skipped when every DRC error is on fixed parts (courtyards, keep-outs: routing cannot change them) and stops after one round without improvement when some are (`HWFLOW_REPAIR_MAX_STALL`, default 2 rounds otherwise). The route cache key also hashes the `floorplan:` field. Route groups: when the floorplan regions that hold nets (`nets:`) and the `board.isolation` groups split the nets into more than one group, the router routes each group as a job of its own (a region group: nets with every pad in its region; `rest`; then the `interface` nets with pads in several regions), smallest region group first, each with the copper of the groups before it as obstacles. Each group's copper is cached in `build/routecache/groups/` under a key of its own pads, its nets' classes and layers, the rules and the router code, so a change in one group re-routes that group only; the joined board's DRC judges reused copper, and a DRC error on it drops that group's cache and routes it again. `REPORT.md` lists the groups (nets, seconds, unrouted, cache hit or miss).

## nets

Only nets that need intent are listed. Every net in `components.*.pins` exists automatically.

```yaml
nets:
  VBUS: {class: power, current_a: 0.5, external_supply: true}
  GND: {class: ground}
  ADC_IN: {class: signal, sensitive: true}
  CLK: {class: clock}                    # clock, switching, high_speed count as aggressors
  RF: {impedance_ohm: 50, width_mm: 1.1}
  AC_LIN: {role: mains_input}            # the line conductor where mains enters: a fuse must come first (mains-fuse)
  AC_L: {class: power, current_a: 2, layers: [B.Cu]}   # the router may use only these layers for the net
  AC_N: {class: mains, width_mm: 1.0, min_width_mm: 1.0}   # no neck under 1.0 mm (fuse coordination)
```

`v_max` / `v_min` are the highest and lowest voltage the net reaches, `pullup: internal` says an IC pull-up holds an open-drain or I2C net (see `limits` under components).

`min_width_mm` is the narrowest copper the net may have: the router does not neck the net under it (no pad-entry or neck-down pass at a smaller width, no corridor-proof narrow-first pass or seed route when it is above the 0.15 mm neck floor), so a net that does not fit at that width stays open. A track of the net narrower than it is a layout failure (`too_narrow` in the layout status). Use it where a neck is a safety problem, for example a mains track that must carry the fuse's let-through I²t.

`req` on a net (`req: R13`, several as `R13,R14`) ties that net's routed copper to requirements: with `min_width_mm` its narrowest track must reach it, with `layers` its tracks must stay on them; `hwflow trace` lists each as a measure (`net AC min width 1.0/1 mm`). A `verification: layout` requirement that no current board check names (no `req:` on a net, a `board.isolation` group, a budget or a `layout_checks` entry) stays `UNVERIFIED` in `hwflow trace`; evidence prose does not verify routed copper. `uv run hwflow nets <proj> [net...]` prints the routed copper of each net from the last layout (class width, narrowest and widest track, length, length under the class width, vias, layers); `status.json` holds it under `layout.nets`.

`layers` lists the copper layers the router may use for the net (KiCad names; globs such as `In*.Cu` match the board's layers). KiCadRoutingTools routes those nets in passes of their own, with every other layer forbidden (`--layers <all> --layer-costs`, -1 on the others: an obstacle that through vias may cross, no track on it); repair reroutes keep the same passes. The Freerouting path writes the net class with `(circuit (use_layer ...))`. `hwflow check` fails on a name that matches no copper layer of the board, and on an SMD pad of the net that sits only on layers outside the list (through-hole pads reach every layer). A track of the net on another layer is a layout failure (`off_layer` in the layout status) and a post-layout error (`net-layer`), so the build cannot pass with it. The net's layers win over a `board.drc_rules` `disallow` rule on a named rule area (`A.intersectsArea('Z')`, for example a mains-off-the-inner-layers rule): when the list shares a copper layer with area Z and the rule names the net (NetName, its class or its isolation class) or no net at all, hwflow writes the rule with `&& A.NetName != '<net>'`, for KiCad DRC and the router alike, and `hwflow check` warns about the overlap.

`class: fast` or `edge_ns: <rise time>` keeps the full trace R and C network of that net in post-layout transients (see `post_tran_lump`). `edge_ns` also turns on the signal-integrity screen in post-layout (`si` findings, no SPICE): the delay of the longest pad-to-pad copper path against t_r/6 (warn: electrically long, terminate it or rely on the full transient), the vias and branch points of the net, the trace length with no pour on the adjacent copper layer (own via and pad antipads excluded), and for every IC on such a net (or with `edge_ns:` on the component) the extracted inductance of each decap path as impedance at 0.35/t_r against `board.si_pdn_z_ohm` (default 1 ohm). `current_a` sizes the track with IPC-2221 (10 C rise, or the net's `max_rise_c`) and sets the net's floor: the copper is under that width (rounded up to 0.01 mm) only in pad-entry necks of at most 3 mm (post-layout `track-current` warning); a longer stretch under it fails the layout (`too_narrow`) as it fails post-layout (`track-current` error). The router's neck-down never makes a longer one, and layout-block copper with one is widened to the IPC width (a `hwflow check` warning names it). You do not add `min_width_mm` for the current; add it only where no neck at all is allowed. Post-layout reports the IR drop of a net with `current_a` as that current through the routed copper (vias included) between its pad pair of largest resistance, the worst case of where source and load sit; a `class: power` net without it gets the pad voltage spread at the circuit's operating point. A net wider than `board.track_mm` that cannot enter a fine-pitch pad at its width necks down: each narrow stretch (a neck at a pad) at most 3 mm long, at `board.track_mm` or else at 0.15 mm, never under its `min_width_mm` (a net whose `min_width_mm` lies between them necks at it) (the build report names the net and the narrow length); a longer narrow stretch leaves the net open instead. `external_supply` marks a rail that enters through a connector. `sensitive` turns on crosstalk and return-path checks for that net.

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
    placement: {edge: left}                 # courtyard within 1 mm of that edge (hard), or {x: 5, y: 10, rot: 90, fixed: true}
    tolerance: 0.01                         # Monte Carlo tolerance (R 1%, C 10%, L 20% default)
    voltage_rating_v: 16                    # capacitors: derating check (50% by default)
    power_rating_w: 0.1                     # resistors: default from package size
    limits: {abs_max_v: 6.0, rec_max_v: 5.5, pins: {"7": 5.5, "EN": 6.0}, source: "ds p.2"}   # optional, see below
    current_rating_a: 2.0                   # fuses: rated current (fuse-derating check)
    sourcing: "Mouser 123-ABC, hand solder" # optional: where a part without `lcsc` comes from
    thermal_vias: {pad: "2", count: 6}     # via grid inside a thermal pad, to the opposite-layer pour
    vias_under: true                        # optional: pour-tap vias may lie in this module's courtyard (default false for SMD footprints of 25 mm2 or more)
    bom: false                              # optional: copper, not a purchased part (default false for a KiCad NetTie symbol or footprint, true otherwise): left out of the BOM, the cost and bom.csv
    role: temp_sensor                       # optional: temp_sensor | antenna | none. Placement roles; inferred from the symbol (Sensor_Temperature:*, Device:Thermistor*; RF_Module:*, footprint with Antenna) when absent, none turns the inference off
    theta_ja: 250                           # junction temperature estimate
    tj_max_c: 125
    spice: {...}                            # how to simulate it, see below
```

Every symbol pin must be in `pins` or in `nc`, otherwise `hwflow check` reports an error. Read the pin numbers with `hwflow lib show <lib_id>`. Do not guess them.

### limits (parts-time pin checks)

`limits` holds the datasheet numbers that `hwflow check` compares with the nets (docs/reference-reuse.md, section 3; code `src/hwflow/core/pinchecks.py`). Write it in stage 3 for every IC, connector, relay and fuse, with the page in `source`:

| key | meaning | check |
|---|---|---|
| abs_max_v | absolute maximum of every pin to ground | `abs-max` error when a pin's net can go higher |
| rec_max_v | recommended operating maximum | `rec-max` warning |
| min_v | absolute minimum | `abs-min` error against `nets.<n>.v_min` |
| pins | `{pin number or pin-name glob: V}`, overrides abs_max_v per pin (a relay coil, a 5 V-tolerant input) | as abs_max_v |
| rec_pins | the same for rec_max_v | as rec_max_v |
| hole_mm | the datasheet's PCB hole for a through-hole lead | `hole-size` warning when a footprint drill differs by more than 0.05 mm |
| current_a | a fuse's rated current (or `current_rating_a`) | `fuse-derating` warning above 75 % of it from the nets' `current_a` |

The voltage of a net: `nets.<n>.v_max` (the highest it reaches: a regulator's maximum output with accuracy and ripple, not its nominal; `v_min` for the lowest), else the rail in its name (`+3V3`, `5V_SYS`, `1V8`, `P10V5`, `N10V`), 0 V on a ground net, else an upper bound from what drives it (a resistor, inductor, fuse, diode or transistor passes its other nets' bound; an IC output passes its IC's highest supply). An unknown net is not checked. The same pass also reports, as warnings: `cap-voltage` (a capacitor's `voltage_rating_v` below the bound across it, an error), `open-drain-pullup` and `i2c-pullup` (no pull-up resistor; `nets.<n>.pullup: internal` when the IC has one, with the datasheet line), `reset-state` (an EN, nSLEEP or RESET input driven only by GPIO-type pins, no pull resistor), `pin-role` (a GND pin off ground, a supply pin or power output on ground), `floating-input` (a net of input pins only), `tvs-budget` (a TVS or ESD diode without a `copper_len` budget) and `sourcing` (an active or electromechanical part without `lcsc` or `sourcing`).

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

`spice.mpn` names the part the model or the fitted numbers come from (the vendor model's part, or the datasheet the fit read). `hwflow check` fails (`model-mpn`) when it is not the BOM `mpn` (a family prefix such as `TPS61299` for `TPS61299DRLR` matches): a leakage or threshold limit of another maker's or variant's datasheet does not hold for the bought part. Without `spice.mpn`, a near-identical part number in the model's comments (`1N5819HW` for a `1N5819WS` on the BOM) is a warning.

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
  ngbehavior: ps                         # optional PSpice parser; default native
  derating_timeout_s: 60                  # separate operating-point audit (default 600)
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

`ngbehavior: ps` runs ngspice with `-n -D ngbehavior=ps`, before it parses the circuit. Use original PSpice model files with this explicit mode instead of translating their switches or mathematical functions by hand. The generated netlist records the mode and includes it in the simulation cache key; the setting does not modify a global `.spiceinit` or affect other designs. Only `native` and `ps` are supported.

Each analysis that stresses the design (a hot-plug, reverse polarity, a surge, a brown-out) carries a `realism:` note, free text that `hwflow` ignores: `realism: "who: user, one cell reversed; how often: once per battery change; timing: >= 5 s between removal and insertion; limit: Q1 Idmax 2 A DC, 8 A for 10 us (datasheet p.3)"`. If no user or fault can produce the scenario, write `realism: "stress case: <why unreachable>"`. A stress case does not block the verdict and does not justify a hardware change without a product decision.

Terminals are net names or `REF.PIN`. Use `REF.PIN` for sources that feed a connector, because the post-layout run then injects at that pad. `v(NET)` and `$(NET)` inside commands are replaced with SPICE node names. `meas` is the ngspice `meas` syntax without the analysis type and the name. `expr` is an ngspice vector expression. `scale` multiplies the result before the limits apply. `req` names the requirement IDs that the measure verifies, as a comma-separated list.

`timeout_s` on an analysis (default 600) bounds its ngspice run. An analysis that times out, crashes, aborts or reports an error is named in the report with its status and run time, and every measure without a value says why (`n/a: timeout after 600 s`, `n/a: aborted: ...`). A run that crashes on a signal (`free(): invalid ...`) is retried once and reported as flaky.

A measure whose name starts with `dbg_`, or that sets `debug: true`, is a debug probe: the report lists its value apart, and it never counts in the pass ratio, the verdict, the requirement trace, the post-layout comparison or the Monte Carlo yield.

### Convergence fallback

A run whose DC operating point (OP) did not converge cleanly (ngspice reports `gmin stepping failed`, `source stepping failed`, `DC solution failed`, `Transient op failed`; or the run failed with a `singular matrix`) is retried without changing the circuit, cheapest first:

1. `.nodeset` with every node voltage of a clean OP of the same netlist (the analysis' own options and `alter` lines included): at 25 C, else at the nearest temperature of the analysis that converges without help. A nodeset is an initial guess that ngspice releases, not a constraint.
2. gmin and source stepping: `option gmin=1e-12 gminsteps=100 srcsteps=100`, inserted after the analysis' own options.
3. Both.

Each step first solves the OP alone (cheap). A step counts only when its OP converges cleanly and is plausible: every net is compared with the reference OP, the report gives the largest move, and a move beyond `max(0.05 V, 5 %)` (`sim.fallback_tol: {v: 0.05, rel: 0.05}`) on a supply net (`class: power`), a scope boundary or a net the analysis measures rejects the step. Moves on other nets are listed, not rejected. The report names the step that produced the result (`convergence fallback <analysis> @ <T>: gmin+source stepping ...`). When no step gives a clean, plausible OP, a run that ngspice called finished is not evidence either: its measures fail with `n/a: DC operating point not trustworthy (...)`. A transient with `uic` computes no OP and gets no fallback, nor does a transient that fails after its OP (`timestep too small` alone). Monte Carlo runs get no fallback. `sim.fallback: false` turns it off.

### Floating nodes

A node with no DC path to ground (only capacitors, MOSFET gates, unsimulated parts or high-impedance model inputs) makes the OP singular. `hwflow` finds them in the netlist before ngspice runs: R, L, V, diodes, transistor junctions, switches, voltage-output controlled sources and the ports of a subckt whose model file it can read (it follows the subckt's own elements) count as DC paths; an element it cannot classify counts as a path, so a leak is never added on a guess. It adds one noiseless resistor (`noisy=0`, default 1 Gohm, `sim.leaks: {ohm: 1e9}`) from each floating island to ground. `hwflow check` warns about each (`floating-node`), so the designer can give the node a real DC path; the report lists each leak with its OP voltage, its current and its corner `1/(2 pi R C)`: the leak changes AC results by less than 0.5 % above 10 times that corner (the report flags a corner within 10x of the lowest analysis frequency). At DC the island has no other conducting element, so the leak only defines its level. `sim.leaks: false` turns it off; `rshunt` in `sim.options` already ties every node to ground, so no leak is added then.

### scope

A scoped analysis simulates one block instead of the whole board, so that a block-level measure (audio noise, a filter response) does not depend on the convergence of unrelated circuitry. It is opt-in per analysis:

```yaml
    - name: hp_noise
      cmd: noise v(HP_L) VIN_AUD dec 40 20 20k
      temps: [0, 25, 40]
      scope:
        nets: [HP_L]                  # measured nets (the nets the measures and the cmd name are added)
        boundary: [5V_SYS, 3V3_D]     # supply nets replaced by a Thevenin source
        tol: 0.02                     # relative tolerance of the validation gate (default 2 %)
        atol: 0                       # absolute floor in the measure's unit (default 0)
        band: [20, 20k]               # impedance-fit band (default: the ac/noise band of the cmd)
        timeout_s: 120                # bound of each scoped ngspice run (default 120 s, at most the analysis' timeout_s)
```

`hwflow` keeps every part reachable from the measured nets (and from the noise input source and `i(X)` probes) without crossing a boundary net or ground, so a regulator between a boundary and the block stays in with its noise: put the boundary upstream of every part whose effect the measure must include. Each boundary net reached becomes `V (DC) - R - [L] - node [|| C to ground]`: the DC value is the net's voltage in the full-board OP at that temperature (with the convergence fallback if needed) plus the block's own DC current times R, so the block sees the full-board voltage; the impedance is fitted (R, R+L, R||C or (R+L)||C, the simplest within 1.25x of the best maximum relative error) to the full board's small-signal impedance at the net (an AC run with 1 A injected, the response without injection subtracted, then de-embedded from the block's own load). The fitted resistor is noiseless: noise from beyond the boundary is not in the scoped result. Mutual coupling between two boundaries is not modelled. `alter` lines on devices outside the scope are dropped; scoped OPs start from the full-board OP (`.nodeset`).

The validation gate is mandatory: at every temperature where the full board converges (with a plausible fallback counted), the full and the scoped analysis both run, and every non-debug measure of the scoped run must be within `max(tol x |full|, atol)` of the full one. Passed: the scoped values are the result at every temperature, including those where the full board does not converge. Failed, or no temperature where the full board converges: the scope is rejected; where the full board converged its own result stands, elsewhere every measure fails with `n/a: scope not validated (...)`. The report lists the gate (`scope <analysis>: VALIDATED at ...` or `REJECTED (...)`, full vs scoped per measure and temperature, the boundary models and run times) and `status.json` holds it under `sim.scopes`. Every run goes through the simulation cache, so an unchanged netlist does not rerun the full-board validation. A scope applies to the schematic-level simulation only: post-layout and Monte Carlo runs simulate the full board.

When a full-board analysis does not converge, the report names the nets of ngspice's `check node` messages and suggests a scope.

The separate derating operating-point audit accepts `sim.derating_timeout_s` (default 600 seconds). A timeout, simulator error, or incomplete set of pin-current results fails the simulation stage; it never reports a clean derating audit. Timed-out analyses preserve partial simulator diagnostics in their `.log` files.

## blocks

A layout block is a sub-circuit laid out and checked once: a boost cell, an MCU module with its decoupling, a guard FET pair. The library is `blocks/<name>/block.yaml` in the repository (`uv run hwflow block list`, `uv run hwflow block show <name>`); a project may add its own in `<proj>/blocks/`. `uv run hwflow block match <proj>` lists the library blocks whose roles, footprints and pin nets fit unbound parts of a design, as `blocks:` entries to paste (with value differences and the parts whose own `placement:` must go). A design binds its components to a block's roles:

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

## budgets

A budget is the part of a requirement margin that layout may spend on copper. The architect writes it, the circuit stage sets the limit from the pre-layout simulation, and hwflow holds layout to it.

```yaml
budgets:
  - name: guard_copper
    req: R4                                  # optional: the budget verifies this requirement in `trace`
    kind: copper_r                           # copper_r (resistance) or copper_len (length)
    path: ["BT1.1 -> M1.3, M2.3", "M1.2, M2.2 -> C1.1"]
    current_a: 0.7                           # copper_r: the current the requirement states
    max_mohm: 15.7                           # copper_r: limit; copper_len: max_mm
    source: "R4 50 mV - 38.97 mV pre-layout worst case at 0.7 A = 15.7 mOhm"
  - {name: c6_loop, kind: copper_len, path: ["C6.1 -> U1.2"], max_mm: 3, source: "..."}
```

`path` is a list of legs, each on one net. A leg is `pads -> pads`; pads joined by a comma are one end (parallel parts, or several pins of one IC), and the current splits equally over them. A part between two nets ends one leg and starts the next; its own drop is not counted. A bare net name as a leg means the worst pad pair of that net. Measure the pads that the SPICE nodes of the requirement map to: post-layout, a net node is its first pad, connectors first, then by reference (`v(VBAT_P)` is `C1.1` when C1 is the first part on VBAT_P).

`source` is required and holds the arithmetic: the requirement limit minus the worst simulated value, at the stated current, with no extra margin.

Before routing, each `copper_r` budget becomes a track width for the nets of its path. The length estimate is the Manhattan distance between the closest pads of each leg times `detour` (default 1.0, fitted to routed home-thermostat-3 boards), and 1 via per leg comes off the budget first. Resistance uses 35 um x `copper_oz` at 20 C, as the post-layout extraction. One width serves the whole path, rounded up to 0.05 mm and capped at `board.budget_max_track_mm`. A net never gets narrower than its own width. The layout result lists each derived width with its numbers; a budget that the cap cannot meet says that the parts are too far apart. With `board.gnd_pour`, a leg on a ground net runs through the pour: it gets no track width and counts 0 mOhm, as the post-layout extraction treats the poured ground as ideal.

After routing, the layout stage and the post-layout checks measure every budget on the extracted copper (vias included). A budget over its limit, or with a leg that is not connected by tracks, is a layout error. The message gives the path, the value, the limit, the drop at `current_a`, and the worst stretch (net, layer, width, length, share of the resistance). Fix it with width or placement. Do not raise the budget.

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

The architect writes the floorplan in stage 2, before parts exist, and `uv run hwflow floorplan <proj>` checks it in seconds to about a minute: no simulation, no schematic, no routing. It is the early layout feasibility gate of `/hw-design`: stage 2 ends on it, stage 3 runs it again with the chosen footprints (`footprint:` on the items) before the parts are committed, and stage 4 runs it with the captured parts before the circuit capture ends; then it also runs the layout proofs of `hwflow check` (escape, barrier, cut, corridor, padiso; `--no-proofs` skips them). A stage-2 `design.yaml` may hold only `project`, `board` (size, layers, `isolation`) and `floorplan`. Stage 4 adds the components and names the real parts of each item (`refs`); the same command then uses their courtyards. In stage 7 the placer keeps the regions.

```yaml
floorplan:
  construction: double          # single (one face) | double (two faces) | stacked (two or more boards)
  fill_max: 0.85                # optional: face fill that fails (risk above 0.70)
  wall_mm: {left: 4, right: 4}  # optional: board edge to the enclosure wall (room for plugs and cables)
  depth:
    limit_mm: 30                # R2
    extra: [{name: cover sheet and air, mm: 1.0, source: R2}]
  regions:
    - {name: notch_l, kind: keepout, circle: [2.25, 23, 4], conductive: true}   # a screw is accessible metal
    - {name: post, cut: true, circle: [0, 46, 5]}  # the board is cut away here (a notch for a housing post)
    - {name: mains, kind: mains, rect: [0, 0, 20, 46], nets: ["AC_*"], isolation_mm: 6.0, source: R10}
    - {name: hv_parts, kind: barrier, rect: [0, 0, 26, 46], items: [relays, terminals]}
    - {name: battery, kind: battery, rect: [27, 0, 82, 28.5], items: [cells]}
    - {name: antenna, kind: antenna, rect: [60, 40, 73, 46], allow: [module], isolation_mm: 15, from: [mains, barrier, battery]}
  items:
    - {name: relays, size_mm: [24.5, 10.5], count: 2, side: back, height_mm: 15.8, protrusion_mm: 1.0, source: "ADW1 datasheet"}
    - {name: cells, refs: [BT1, BT2], height_mm: 12.0, source: "LR03 10.5 + clip 1.5"}
    - {name: module, refs: [U1], height_mm: 2.4}
    - {name: mains_conn, footprint: "TerminalBlock:TerminalBlock_bornier-3_P5.08mm", edge: left, envelope_mm: 12, source: "plug 6 + wire bend radius 6"}
  nets: [{between: [module, relays], count: 4}]   # stage 2 only: wires between placeholders
```

Coordinates are board millimetres, as in `placement: {x, y}` (origin top left, y down).

### Stage 2: the mechanical parts (`hwflow floorplan <proj> --stage arch`)

In stage 2 the architect already knows the big and mechanical parts: battery holder, relays, terminals, connectors, fuse, module, display, mounting holes and bosses. Draw each one as an item with a footprint chosen from the library (`uv run hwflow lib fp <words>`, pads from `uv run hwflow lib pads <fp>`) or, when no footprint exists yet, as a box with rows of pins, and give the pins that carry a net of an isolation group (mains, the battery path) their net:

```yaml
  items:
    - {name: relay1, refs: [K1], footprint: "hw:Relay_Panasonic_ADW1_1coil_back", side: back, at: [60.05, 38.6],
       rot: 270, pins: {"13": L, "14": OUT1}, height_mm: 15.8}          # at = footprint origin, as placement: {x, y}
    - {name: cells, refs: [BT1], footprint: "Battery:BatteryHolder_Keystone_2468_2xAAA", side: front}  # movable
    - {name: display, size_mm: [30, 12], side: front, at: [40, 6], keepout_mm: 1.0,           # a box, centre at `at`
       pin_rows: [{n: 8, pitch: 2.54, at: [-8.89, 4.5], dir: x, drill_mm: 1.0, pad_mm: 1.7}]}
    - {name: rest_front, side: front, area_mm2: 1040, source: "power 168 + ADC 150 + drivers 200 + caps 430 + ..."}
```

- `footprint` + `at` + `rot` is a fixed part at its real position (`at` is the footprint origin, as in `placement`); without `at` it is a movable part that must fit in its region. A footprint drawn for the other face than `side` is mirrored (the `_back` convention); without `side` the item takes the face its footprint is drawn for.
- `pin_rows` (with `size_mm`, no footprint): rows of `n` pins, `pitch`, first pin `at: [dx, dy]` from the box centre, `dir: x | y`, `drill_mm`, `pad_mm`, `nets` (one per pin), `smd: true` for surface pads. Pads are numbered 1.. across the rows; `at` of the item is the box centre.
- `pins: {pad: net}` gives pads their nets (the other pads have none and are obstacles to every net).
- `keepout_mm` grows the item's courtyard on every side (room to insert a cell, to swing a lever, for a tool).
- An item with only `area_mm2` and `side` is an area budget for parts not drawn yet (the blocks of the circuit).

`hwflow floorplan <proj> --stage arch` checks these parts in seconds (no quick placement), writes `build/FLOORPLAN-arch.md` and `build/floorplan-arch.svg`, and works the same on a captured design (it then takes the fixed, through-hole and large parts, at least 100 mm2, and counts every other part as area). It reports: courtyards of fixed parts that overlap (`overlap`); through-hole pins and pegs of one part under the courtyard of a fixed part on the other face (`tht-under`); a movable part (a holder, a module) that fits nowhere in its region or on its face clear of the fixed parts' courtyards, their through-hole pins and the exclusive regions, with its own pins off the other face's courtyards (`fit`); the region, keep-out and isolation rules of the fixed parts (`fixed-placement`); region distances and pad isolation (`isolation`); edges, envelopes and depth as below; the layout proofs on the pins' nets (`proof-corridor`: a mains net that cannot pass between the fixed pads, the isolation band of an accessible wall or screw and the board edge; `proof-padiso`, `-barrier`, `-escape`, `-cut`); and the area left for the rest of the circuit outside the exclusive regions and bands against the `area_mm2` items (`budget`; the face fill check holds the limit). Every failure names the stage it goes back to (architecture or parts).

- `construction: stacked` needs `boards: [{name, size_mm: [w, h], at: [x, y], stack_mm, connector, flipped}]`, listed from the top (face A) down. hwflow lays the boards side by side in one panel (`at`, default left to right with a 6 mm gap), so `board:` is the panel. `stack_mm` is the mated height of the board-to-board connector below that board. `flipped: true` marks a board drawn with mirrored footprints (its back face looks up). Every item and region of a stacked floorplan names its `board`.
- A region has a shape (`rect: [x0, y0, x1, y1]`, `poly: [[x, y], ...]` or `circle: [cx, cy, r]`), a `side` (front, back, both; default both) and a `kind`:
  - `mains`, `antenna`, `keepout`, `barrier` are exclusive: a part that is not a member stays out on the region's sides. A barrier region holds the parts that bridge an isolation band (relays, optocouplers); its members may enter the mains region, and the mains members may enter it.
  - `selv`, `battery`, `other` only hold their members.
  - `items` lists the members (item names or ref patterns); an item's `region` does the same. A member stays inside. `allow` lets other parts into an exclusive region. `nets` (globs) are the nets whose pads stay inside the region; `uv run hwflow floorplan <proj> --local-nets` lists, per region, the routed nets whose pads are all on its members. `board.route_groups: members` makes those nets route groups (one cached router job per region, see Route groups) without the constraints of `nets:` (no corridor proof, no region-nets warnings): use it on a large board whose single router job runs for hours. The router does not see the region, so their tracks and vias may leave it: post-layout warns (`region-nets`) with the copper of each such net outside the declared shape and its largest excursion in mm.
  - `isolation_mm` is the distance to the other regions on the same board (by default every other kind except barrier and keepout; `from` names kinds or regions). A `conductive` keep-out is cut out of every region with `isolation_mm`, grown by that distance. The router keeps the region's `nets` out of every `conductive` keep-out or cut grown by that distance (tracks and vias, `src/hwflow/layout/board/routing.py` conductive_keepouts), and the layout stage and post-layout checks (`conductive_isolation`, error `conductive-isolation`) fail on a track, via or pad of those nets closer than it.
  - `mechanical: fixing | screw | rivet | standoff | post | wall | frame | cover | film | enclosure | other` says which mechanical part the region stands for (a keep-out or cut, usually). `accessible: true` marks a touchable surface: the isolation checks treat it like `conductive: true`, because a touchable insulating surface counts as covered by metal foil (the test-finger foil rule of IEC 60730-1, 60335-1 and 62368-1). Fixings, posts, walls and frames are accessible unless `accessible: false` gives the reason (inside a sealed part, a glued joint with its type test); a `cover`, `film` or `enclosure` region is a barrier: model its wall foot (`wall`) and its fixings (`rivet`, `screw`) as their own regions. A rivet or screw region is its head, not its hole. A `board.isolation` group with `creepage_mm` keeps its nets that far from every conductive or accessible keep-out too, where no region asks for as much. In a design with mains nets, `hwflow check` warns (`mechanical`) when no region is a fixing, none is a cover, wall, enclosure or film, or a region named like a fixing or cover (cover, rivet, screw, standoff, boss, fixing, mount, wall, frame, clip) does not say whether it is touchable.
  - `cut: true` removes the region's shape from the board: a notch where it crosses the board edge, a hole where it lies inside. Edge.Cuts becomes the board rectangle minus the union of the cut regions (lines, arcs along a circle cut, a circle for a round hole), so the router (KiCadRoutingTools reads Edge.Cuts; the Freerouting DSN gets the outline as its boundary and inner holes as keep-outs), the GND pour (KiCad clips the fill at the copper-to-edge clearance, 0.3 mm), the area and fill arithmetic and the feasibility proofs all see the real edge. A cut region is a keep-out on both faces (no `kind` other than keepout, no `side` other than both, no `items`); the placer keeps every part `board.margin_mm` from it, as from the board edge. `conductive: true` on a cut measures isolation to its edge. `hwflow check` fails when a cut lies outside the board, removes all of it or splits it in pieces, and warns when a pad of a fixed part lies within the copper-to-edge clearance (`max(board.clearance_mm, 0.3)`) of a cut.
- An item is a placeholder (`size_mm: [w, h]`, `count`, `side`, `at: [x, y]` centre, `edge`; a `footprint` item is drawn with its real pads and `at` is its origin, see Stage 2 above) or real parts (`refs` and `groups`, patterns on refs and component groups; a ref named in `refs` wins over a group). `height_mm` (or `height_mm` on the component) enters the depth stack, `protrusion_mm` adds the through-hole pins on the other face. `area_mm2` replaces the courtyard area in the fill (a holder on wire leads leaves the board under it free). Later, a pre-laid-out block is an item with a size too. `footprint: Lib:Name` instead of `size_mm` gives a placeholder the courtyard of the footprint chosen in stage 3, before the circuit names the part. `envelope_mm` is the room the mating part, the cable and its bend radius need in front of the part's `edge` side (else the side nearest a board edge), across its width: it must stay within `wall_mm` of that edge and off the keep-outs, the cuts and the other parts' courtyards.

The check, written to `build/FLOORPLAN.md`, `build/floorplan.svg` and `status.json` (`floorplan`):

| check | fails when |
|---|---|
| area | a board face, or the pool of a face left outside the exclusive regions and the mains isolation band, fills more than `fill_max` (risk above 70 %); a region holds more courtyard area than it has |
| fit | a member cannot fit in its region in any position or rotation; or it fits nowhere once the courtyards of the fixed parts already there, the through-hole pins and pegs of fixed parts on the other face and the keep-outs are taken out (a through-hole member also keeps its pins off the other face's fixed courtyards) and its pads on the nets of a mains region with `isolation_mm` stay in that region (which excludes the isolation band around conductive keep-outs). A fixed or edge part may overhang the board edge. Back to architecture |
| depth | the stack is above `limit_mm`, or a connector `stack_mm` is lower than the parts between the boards. Single and double: front tallest + board + back tallest. Stacked: top front + boards + gaps (max of `stack_mm` and back of the upper board + front of the lower one) + bottom back. Plus `extra` |
| isolation | two regions are closer than `isolation_mm`; a pad of a fixed part on a mains region's nets is closer than `isolation_mm` to a conductive keep-out; the isolation bubble of a `board.isolation` group that no region confines (its parts at the fill limit with the band on their open sides, in a corner or along an edge) leaves the other parts no room. Risks: a group no region confines, a group of two or more nets without `between_mm`, member nets without `nets.<n>.layers` on a board with more than 2 layers (they may pass under the other zone on an inner layer) |
| edge | a movable part with an `edge` (its item's or `placement.edge`) has no stretch of that edge, inside its region and clear of the cuts (with the board margin) and of the exclusive regions it may not enter, as long as its shorter courtyard side; or the parts sent to one edge need more than the edge has. Back to architecture |
| envelope | an item's `envelope_mm` reaches past `wall_mm`, or crosses a keep-out or cut, or a fixed part's courtyard (a quick-placed part: a risk). Back to parts or architecture |
| fixed-placement | a fixed part breaks a region, keep-out or isolation rule. A shortfall within the placer's own margin, and a courtyard in a cut's board margin but off the cut, are risks |
| area (placer fill) | the courtyards with the placer margin fill more than 95 % of a face inside the board margin: the stage-7 legality gate would stop (layout.place.legality). The quick placement is then skipped, as after any area, fit, edge, isolation or depth failure |
| proof-escape, -barrier, -cut, -corridor, -padiso | the layout proofs of `hwflow check` fail on the captured parts; each names its stage |
| routability | a quick placement (the stage-7 placer with the regions, 20000 moves, 2 seeds) needs more than 20 % of the track capacity where its nets may run (risk above 10 % or a cell at twice its capacity) |

The thresholds come from the thermostats: the baseline (two stacked 62 x 40 boards, 4 layers, routed clean) fills its faces 20-42 % and uses 5 % of the track capacity; home-thermostat-3 at 88 x 46 on 4 layers (4 unrouted) fills its SELV face 74 % and uses 11-13 %; at 74 x 46 on 2 layers (95 unrouted) 95 % and 24-28 %. Overlaps left by the quick placement are risks, not failures. The verdict is PASS, PASS with risks, or FAIL; a FAIL that needs a size, layer, construction or cost change is a product decision.

In stage 7 a part in a region keeps its courtyard inside it and the other parts stay out of the exclusive regions (a placer cost like the keep-out term). Fixed parts are reported, not moved.

## cost

The unit cost that `hwflow check` compares with a cost requirement (`cost-limit`) is recomputed from design.yaml: the BOM (`price_usd` x quantity) plus `cost.extra`, the lines that are not parts on the BOM (PCB, assembly and extended-part fees divided by the build quantity, cables, probes, mechanics), each with its `source`:

```yaml
cost:
  extra:
    - {name: "PCB 92 x 55, 2 layers, 10 pcs / 10", usd: 0.50, source: "JLC quote 2026-10-27"}
    - {name: "JLC setup + stencil + 8 extended parts / 10", usd: 3.35, source: "JLC fee page"}
```

The limit comes from the requirement: `cost_max_usd: 25` on it, else the first currency limit in its acceptance, then its text (`< 25.00 USD`, `<= $12`, `25 USD or less`; a `<` is strict). A miss is an error on a `must` and a warning on a `should`; a limit in another currency is reported, not compared. When the project has `notes/cost.md` (or `cost.md`), its last stated total (the last line that says "total") must match the recomputed one within 2 % (`cost-doc`: an error when the document claims a must limit met that design.yaml does not reproduce, a warning otherwise), a total the document states above a limit is a `cost-limit` finding, and a table row with a unit-price column that prices a design part (by ref or MPN) differently from its `price_usd` is a warning. With hwharness, a cost error while the cost question is open (`change: cost` in conflicts.md, or an unanswered inbox question with `"change": "cost"`) is a warning; once answered, the error is back until the design meets the limit or the requirement changes.

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

**Requirements baseline.** `hwflow approve <proj> --requirements [--note "round 1"]` records requirements.yaml as accepted by the product person (end of stage 1, and after each answer that changes a requirement): per requirement its priority, acceptance, verification method and the numbers of its text. From then on `check` reports (`acceptance`) every requirement whose priority, acceptance, verification or text numbers changed, and every requirement added or removed: an **error** for a `must` (a warning for the others) unless a product decision records it, then a **warning** until the next `approve --requirements`:

- an inbox question with `"change": "R10"` (or `"reqs": ["R10", "R12"]`): answered or still open;
- a new `PM round N, qM` citation in the requirement's `source` whose answer is in `inbox/answers.md`;
- `change: R10` in conflicts.md (the question is on its way);
- a derived limit (the text or source says `derived`) re-allocated in a `notes/` file that the source names (the /hw-design rule: no question needed while the product number holds).

Identifiers (R7, C23, q2, round 2) are not numbers of the spec. Wording that changes no number, priority, acceptance or method is not flagged.


### layout_checks

`layout_checks:` (top level) lists geometric checks that `hwflow build` measures on the built `.kicad_pcb` in the post-layout stage (deterministic, no router; result in `status.json` under `post.geometry`, lines `layout check ...` in `REPORT.md`). `req: R24` (several as `R24,R25`) ties a check to requirements, so `hwflow trace` lists it as a measure and a `verification: layout` requirement becomes verified (all its checks pass) or failing (one fails). A failing check is also a post-layout error; `hwflow check` rejects a malformed entry.

```yaml
layout_checks:
  - {name: outline, kind: outline_max, width_mm: 90, height_mm: 48, req: R1}          # Edge.Cuts bounding box; rotate_ok: true also accepts 48 x 90
  - {name: ant-relays, kind: min_distance, from: {region: antenna}, to: {parts: [K1, K2]}, min_mm: 15, req: R24}
  - {name: ant-mains, kind: min_distance, from: {parts: [U1]}, to: {net_class: mains}, min_mm: 15, req: R24}
  - {name: ant-nocopper, kind: keepout_free, area: {region: antenna}, layers: [F.Cu, B.Cu], except_parts: [U1], req: R24}
  - {name: smd-top, kind: side_only, side: front, parts: [smd], req: R28}              # parts: refs, globs (J*), smd (no through-hole pad), tht
```

A selector (`from`, `to`, `area`) is a mapping with one or more of `parts` (refs or globs, courtyard and pads), `role` (component `role`, for example `antenna`), `nets` / `net_class` (all copper of the nets: tracks, vias, pads, zone fills; `layers:` restricts it), `rect: [x0, y0, x1, y1]`, `circle: [x, y, r]` and `region` (a `floorplan.regions` name with a rect or circle); `margin_mm` grows it. `min_distance` is the closest approach of the two selections in the plane (every layer) and must be at least `min_mm`. `keepout_free` needs no copper of the checked layers (default F.Cu and B.Cu) inside the area, except `except_nets`, `except_parts` and overlaps under `tolerance_mm2` (default 0.02, the slits of a fractured pour). `side_only` needs the listed parts on the `front` (F.Cu) or `back` face.

## Requirement verification evidence

`requirements.yaml` selects what proves each requirement with `verification`. A `simulation` requirement needs current tagged simulation measures; `layout` needs current tagged board checks. Failing linked checks still fail a requirement, but passing simulation alone cannot complete a `test` (bench), `inspection`, `datasheet`, or `analysis` requirement.

For those four manual methods, record a completed result explicitly:

```yaml
requirements:
  - id: R1
    text: Both channels meet the output target at the headphone jack.
    verification: test
    evidence:
      status: pass                  # pass | fail | not_checked
      artifact: notes/output-test.md
      design_hash: 0123456789abcdef  # copy the evidence design hash printed by hwflow trace
```

The artifact must be a nonempty file inside the project, and `design_hash` must match the current parsed design and all requirement fields except evidence itself, including written targets in `text`. Edits to component values, connectivity, requirement wording or provenance invalidate it; YAML comments, whitespace, key order and evidence edits do not. This is deliberately stricter than the build-cache hash, which ignores requirement prose. This hash is a design-version check, not an artifact-content signature or a complete hash of external models and tools. Revalidate manually when those dependencies change.

A record is an explicit attestation by its author: hwflow checks the state, design version and file existence, not whether a written measurement is truthful or sufficient. `fail` contributes to the failing count; `not_checked`, missing/invalid records, absent/empty files and stale hashes stay unverified with a reason. Legacy prose evidence remains visible in the source and its missing-file diagnostics, but no longer counts as verification. Migrate it only after reviewing the actual result; do not automatically label every old note `pass`. The verified count excludes failures, so verified + failing + unverified equals total.
