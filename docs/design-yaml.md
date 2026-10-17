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
| router | freerouting | `freerouting` or `krt` (KiCadRoutingTools: one pass per track-width class, no vias in pads) |
| optimize_area | false | shrink the board below width x height to the smallest size that places and routes |

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

`current_a` sizes the track with IPC-2221 (10 C rise). `external_supply` marks a rail that enters through a connector. `sensitive` turns on crosstalk and return-path checks for that net.

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
    pins: {"1": VBUS, "2": GND, "3": VBUS, "5": +3V3}   # symbol pin NUMBER -> net
    nc: ["4"]                               # pins left open on purpose
    group: power                            # schematic column and placement cluster
    decouples: U2                           # for capacitors: the IC this cap serves
    placement: {edge: left}                 # or {x: 5, y: 10, rot: 90, fixed: true}
    tolerance: 0.01                         # Monte Carlo tolerance (R 1%, C 10%, L 20% default)
    voltage_rating_v: 16                    # capacitors: derating check (50% by default)
    power_rating_w: 0.1                     # resistors: default from package size
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
| MOSFET | `{model: NMOS_LOGIC, ports: [D, G, S]}` with pin numbers |
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

Terminals are net names or `REF.PIN`. Use `REF.PIN` for sources that feed a connector, because the post-layout run then injects at that pad. `v(NET)` and `$(NET)` inside commands are replaced with SPICE node names. `meas` is the ngspice `meas` syntax without the analysis type and the name. `expr` is an ngspice vector expression. `scale` multiplies the result before the limits apply. `req` names the requirement IDs that the measure verifies, as a comma-separated list.

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
