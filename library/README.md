# Shared parts library (`hw`)

Parts that KiCad's libraries do not have, drawn for one product and kept for the next. hwflow loads this folder for
every project, after the project's own `lib/` and before KiCad's libraries; `uv run hwflow lib sym|fp|show|pads`
search it too. Datasheet facts are in `datasheets/<mpn>.md`.

Rules: a new part gets a row here with its source and what it was verified against. A part marked "drawing only" has
not been checked against a real part: check it before the first order that uses it.

## Symbols (`symbols/hw.kicad_sym`)

| symbol | part | source | verified |
|---|---|---|---|
| hw:ESP32-C3-MINI-1 | Espressif ESP32-C3-MINI-1 module | Espressif datasheet pinout | home-thermostat-3 (ERC, sim) |
| hw:MAX17227A | ADI/Maxim nanoPower boost, TDFN-8 2x2 | MAX17227A datasheet pin table | home-thermostat-3 (ERC, sim) |
| hw:TPS61299 | TI low-IQ boost, SOT-563 | TI SLVSGS9G | home-thermostat-3 (early rounds) |

## Footprints (`footprints/hw.pretty`)

`_back` footprints are the front footprint mirrored for a part on the bottom face (docs/design-yaml.md, libraries).

| footprint | part | source | verified |
|---|---|---|---|
| Relay_Panasonic_ADW1_1coil(_back) | Panasonic ADW1103HLW latching relay | Panasonic DW drawing | drawing only: check against a real relay |
| TerminalBlock_KF128-5.08-3P_back | Kefa KF128-5.08-3P screw terminal (LCSC C474953) | Kefa drawing | drawing only |
| Battery_AAA_Myoung_MY-AAA-20-J_19-J | Myoung MY-AAA-20-J + 19-J clip pair per cell | Myoung drawings | drawing only |
| BH-AAA-B5AA001, BH-AAA-B5AA001_2xAAA | 1 x / 2 x AAA holder | maker drawing | drawing only |
| Fuse_Littelfuse_395Series_back | TE5 fuse (Walter 2010T, Littelfuse 395) | KiCad Fuse_Littelfuse_395Series, mirrored | KiCad original |
| CP_Radial_D10.0mm_P5.00mm_back | 10 mm radial electrolytic | KiCad CP_Radial_D10.0mm_P5.00mm, mirrored | KiCad original |
| TerminalBlock_bornier-2/3_P5.08mm_back | 5.08 mm screw terminals | KiCad TerminalBlock_bornier, mirrored | KiCad original |
| ESP32-C3-MINI-1 | Espressif module land pattern and antenna keep-out | Espressif datasheet | home-thermostat-3 (DRC) |

Source project: projects/home-thermostat-3 (2026-10-23, layout round 8).
