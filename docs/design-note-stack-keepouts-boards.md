# Design note: `boards:`, `stack:` and `keepouts:` in design.yaml (backlog 52, 53, 59)

Status: proposal. Nothing here is implemented. The field names need the user's confirmation first.

## Why

The home-thermostat baseline shows three gaps (docs/token-improvements.md, items 52, 53, 59):

- Keep-outs are fake components. KO_ANT, KO_MAINS and KO_STACK are footprints with rule areas, placed by hand. Architecture 8.2 asked for a 4 mm front-board keep-out over the J1 screw heads. Nobody drew it until the final review.
- The two boards are drawn side by side on one panel, so every distance is a plan distance. The 18.3 mm "across the slot" is an artifact of the panel. The real question is 3D: how far rear mains copper is from front SELV copper with an 11 mm gap between the boards. hwflow cannot answer it.
- Fabrication uses the panel. The rear board is mirrored by hand, bridge tracks over a tab join J4A and J4B, and the GND pour crosses the cut. `build/fab` exports all of it as one board.

## Proposed fields

```yaml
boards:                       # optional; without it the design is one board, as today
  rear:
    region: [0, 0, 62, 40]    # x0, y0, x1, y1 in panel mm; the tool's drawing area for this board
    side: bottom_up           # top_up (default) | bottom_up: drawn mirrored, assembled flipped
  front:
    region: [68, 0, 130, 40]
  mating:                     # connector pairs that join the boards; their nets are one net
    - {a: J4A, b: J4B}

stack:                        # optional; z of each board in the assembly, needs boards:
  front: {z_mm: 12.6}         # bottom copper of the front board
  rear: {z_mm: 0}
  gaps:                       # declared air or solid layers between boards
    - {between: [rear, front], mm: 11.0, material: air}
    - {between: [rear, front], mm: 0.4, material: polycarbonate, region: [10, 0, 52, 40]}

keepouts:
  - name: KO_STACK
    board: front
    rect: [77, 25.8, 120, 40]          # or polygon: [[x, y], ...], or around: {ref: J1, margin_mm: 4.0, from: rear}
    layers: [F.Cu, In1.Cu, In2.Cu, B.Cu]   # or all
    forbid: [tracks, vias, pads, pour, footprints]
    max_height_mm: null                # parts under this area may not be taller (checked against stack gaps)
    req: R8
  - name: KO_ANT
    board: front
    around: {ref: U1, pad_side: antenna, margin_mm: 15}
    layers: all
    forbid: [tracks, vias, pour]
```

Semantics:

- `boards.<name>.region` cuts the panel into boards. A part belongs to the board whose region holds its placement. `side: bottom_up` means that the board's placement is mirrored in the drawing, so a part's assembled XY is `x1 - (x - x0)`.
- `boards.mating` replaces the bridge tracks. hwflow treats the pins of `a` and `b` with the same number as one node for routing, extraction and SPICE, and checks that the two footprints line up in the stacked frame (same assembled XY within 0.2 mm).
- `stack.<board>.z_mm` places each board in z. Part heights come from a `height_mm` field on the component or the 3D model bounding box.
- `keepouts` become KiCad rule areas, written by hwflow on the right board and layers. `around:` follows the part when the placer moves it. `max_height_mm` limits part height under the area.

## What hwflow would do with them

| step | boards | stack | keepouts |
|---|---|---|---|
| draw (layout) | slot and tabs on Edge.Cuts between regions; no pour across a region border | none | rule areas from the data; no keep-out footprints |
| place | parts stay in their region; mating pairs stay aligned | none | rule area in the placer cost |
| check (layout, post) | mating pins aligned; no copper crosses a region border except the mating connectors | 3D clearance between the copper of different boards: plan distance combined with the z gap, `sqrt(d_plan^2 + dz^2)`; part height against the gap above it | DRC (rule areas); part heights under `max_height_mm` |
| report | one line per board (area, layers, parts) | per isolation group (backlog 38): plan distance on each board, 3D distance across boards, with limit; plan-only numbers are marked "plan" | violations by keep-out name, with `req` traced |
| fab | one set per board: gerbers, drill, BOM and positions from a copy of the panel cut to the region, mirrored back for `bottom_up`, bridge copper and tabs left out; the panel stays in `build/` only for the tool | none | none |

The 3D check feeds `board.isolation` (backlog 38). A group with `req:` gets a measure for each distance, so R8 in the baseline would show both numbers: 6.00 mm in plan on the rear board, and the front copper above J1 at 4.12 mm in 3D.

## Order of implementation (after confirmation)

1. `keepouts:` with `rect` and `polygon` (pcb.py rule areas, DRC). This removes the fake components. Small.
2. `boards:` with region, side and mating, and the fab export per board (kicad.py `export_fab`). This removes the bridge tracks and the hand steps.
3. `stack:` with the 3D distance and height checks. This needs part heights, which the parts agent must record.

The architect agent (`.claude/agents/hw-architect.md`) would write `stack:` and `keepouts:` as data at stage 2, not only as prose. That is a prompt change for group 4.

Needs user confirmation of field names
