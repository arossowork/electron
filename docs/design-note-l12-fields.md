# Design note: placer terms from post-layout checks (L12), proposed fields

L12 adds placer cost terms for what `postlayout.py` reports after the fact. Three of them need nothing new in
`design.yaml`:

| term | data it uses (exists today) |
|---|---|
| decap loop length, through the ground return pad | `decouples: <IC>` on the capacitor, the IC's supply and ground pins from the netlist |
| aggressor to sensitive separation | net `class: clock / switching / high_speed` (aggressors), net `sensitive: true` |
| heat sources | the derating table (dissipation per part) |

Two need to know a part's role, which `design.yaml` cannot state today:

1. **Temperature-sensing parts** (an NTC, a temperature IC, the probe connector of a thermostat), kept away from
   parts that dissipate.
2. **The antenna or RF module**, kept away from relays and mains copper (the `board.isolation` groups).

## Without new fields (what L12 implements now)

- Temperature sensing: inferred from the symbol, `Sensor_Temperature:*` and `Device:Thermistor*`.
- Antenna or RF: inferred from `RF_Module:*` symbols, footprints whose name contains `Antenna`, and parts with a
  pad on a net with `impedance_ohm`.
- Distances: built-in defaults. Thermal separation is 5 mm from any part above 50 mW. Antenna to relay and
  mains copper is 10 mm.

## Fields (decided by the user on 2026-10-20: `role` plus the two board distances; `placement.weights.congestion` from L9 kept)

On a component:
- `role: temp_sensor` or `role: antenna`. Overrides the inference, for a part the symbol does not reveal (for
  example a probe connector).
- `role: none` switches the inference off for a part.

On `board`:
- `thermal_clearance_mm` (default 5)
- `antenna_clearance_mm` (default 10)

Alternatives, if `role` is too generic:
- `temp_sensor: true` and `antenna: true` as two boolean fields;
- or a `placement:` sub-key per part (`placement: {keep_from: [K1, K2], mm: 10}`), which is more general
  but needs more care from the agent.
