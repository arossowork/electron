# Layout blocks

Each folder is one pre-laid-out, checked sub-circuit: `block.yaml` (format in docs/design-yaml.md, section `blocks`) and, when the block needs a footprint that KiCad does not have, `footprints/<lib>.pretty/`. A design binds its parts to a block in `blocks:`; the placer moves the block as one unit and the router only joins blocks.

```
uv run hwflow block list
uv run hwflow block show <name>
uv run hwflow block check <name>        # the block alone on a small board, KiCad DRC of its copper
uv run hwflow block extract <board.kicad_pcb> --refs U2,L1,C3,C4 --name <name> --source "<run, board>" [--roles U=U2,...] [--project <proj>]
```

Rules for a new block:

- `source` says where the layout comes from: a datasheet section with its URL, or the routed board and run it was extracted from.
- Coordinates are computed (pad positions from `uv run hwflow lib pads`, courtyards, clearances), and the arithmetic is in a comment.
- `hwflow block check <name>` gives 0 DRC errors before the block is committed.

## Seed blocks

| block | parts | source | check |
|---|---|---|---|
| tps61299_boost | TPS61299 SOT-563, 1 uH DFE201610P, CIN and COUT 0805 | TI SLVSGS9G 8.6 layout guidelines, hand layout | DRC 0 errors |
| rev_guard_2pfet | two SOT-23 P-FETs in parallel (CJ2305) | home-thermostat-3 R4 budget, hand layout; copper 2.8 mOhm IN to OUT (3.1 at 45 C) | DRC 0 errors |
| esp32c3_mini_decoupling | ESP32-C3-MINI-1, 100 nF 0402, 10 uF 0805 | Espressif datasheet and hardware guidelines, hand layout; inner-layer antenna keep-out | DRC 0 errors |

The reference board of home-thermostat-3 (ht3-routed-ref) has no copper worth extracting for these three: L1 sits 9 mm from U2, C6 10 mm from the module supply pin, and the guard uses 0.6 mm tracks.

No 230 V block (relay, fuse, terminal). The isolation of home-thermostat-3 (6 mm creepage from AC_L_IN, AC_L, AC_ROOM1, AC_ROOM2 to every other net) is a distance to the parts around the section, which a block cannot hold alone; its parts are fixed to the board edge (wire entry), to the screw-post notch keep-outs and to the mains rule areas of `thermo:MainsRuleAreas`, which sit in board coordinates; and the section spans two relays 35 mm apart, so a rigid block would be the whole 20 x 46 mm zone with nothing left for the placer to arrange.
