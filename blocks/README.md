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

## Blocks from home-thermostat-3 (round 8, 2026-10-23)

| block | parts | source | check |
|---|---|---|---|
| max17227a_boost | MAX17227A TDFN-8, 1 uH DFE201610P, CIN 0805, 2 x COUT 1206, RSEL 0402 (U2, L1, C3, C4, C5, R40) | extracted from the round-8 board, plus hand GND returns on top, a CIN ground via and an In1/In2 keep-out under RSEL (MAX17227A PCB Layout Guidelines); 4 layers (round-8 VIN/+3V3 use In2) | DRC 0 errors |
| pfet_switch_rc | SOT-23 P-FET series switch + gate RC 100 k / 10 nF / 1 M (M4, R41, C23, R42) | hand layout (round-8 R42 8 mm from M4); the guard FETs M1/M2 are rev_guard_2pfet | DRC 0 errors |
| ads1115_3ch_rc | ADS1115 VSSOP-10, VDD 100 nF, 3 x 1 k / 100 nF input RC (U3, C11, R11-R13, C12-C14) | hand layout (round-8 AIN nets ran on In1/In2 under the part) | DRC 0 errors |
| drv8837_hbridge | DRV8837 WSON-8, 100 nF on VM and on VCC (U4, C16, C17; bind once per bridge) | TI SLVSBA4 layout guidance, hand layout (round-8 C18/C19 9.2/6.5 mm from U5); caps 1.4 mm of track from their pins | DRC 0 errors |
| xtal32k_3215 | 32.768 kHz 3215 crystal, 2 x 5.6 pF 0402 (Y1, C9, C10) | hand layout (round-8 C9 8 mm from Y1) | DRC 0 errors |
| pfet_vbat_divider | SOT-23 P-FET, 20 k / 10 k 0603 divider, 1 nF tap cap (M3, R15, R16, C15) | hand layout (round-8 C15 9 mm from the tap) | DRC 0 errors |

Not saved from home-thermostat-3: the M1/M2 guard (round 8 binds rev_guard_2pfet, same copper; only the FET value differs)
and the ESP32-C3 module (round 8 binds esp32c3_mini_decoupling, same copper). A guard + M4 block was split: the
two-FET guard is rev_guard_2pfet and the series stage is pfet_switch_rc, so neither fixes the other's place.

The reference board of home-thermostat-3 (ht3-routed-ref) has no copper worth extracting for these three: L1 sits 9 mm from U2, C6 10 mm from the module supply pin, and the guard uses 0.6 mm tracks.

No 230 V block (relay, fuse, terminal). The isolation of home-thermostat-3 (6 mm creepage from AC_L_IN, AC_L, AC_ROOM1, AC_ROOM2 to every other net) is a distance to the parts around the section, which a block cannot hold alone; its parts are fixed to the board edge (wire entry), to the screw-post notch keep-outs and to the mains rule areas of `thermo:MainsRuleAreas`, which sit in board coordinates; and the section spans two relays 35 mm apart, so a rigid block would be the whole 20 x 46 mm zone with nothing left for the placer to arrange.

Re-checked on the round-8 layout (2026-10-23), also for a one-relay sub-block: the ADW1 footprint
(hw:Relay_Panasonic_ADW1_1coil, drawing only) has contact pads 13 (-10.5, -3.75) and 14 (-7.0, 3.75), 2.5 mm, and coil
pads at x = 10.5. A 6 mm ring around the contact pads spans x -17.75..0.25 and y -11.0..11.0 (pad radius 1.25 + 6), an 18 x 22 mm area
larger than the 24.5 x 10.5 mm relay itself, and a block keep-out forbids every net, including the mains tracks that
must reach pads 13/14 (hwflow has no net-class keep-out in a block). The isolation already follows the parts through
`board.isolation` and DRC, so a relay block would add only the footprint. Revisit a relay + DRV8837 block (driver
next to the coil pins, >= 6 mm from the contacts) once the ADW1 footprint is checked against a real relay.
