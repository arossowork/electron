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
| hw:ACS108-8SN | ST ACS108-8SN AC switch, SOT-223 (pin 1 OUT, 2 COM+tab, 3 G) | ST DS12893 Rev 2 p.1 (names); pin 1/3 order from label order of the p.1 figure, not a pin table | not yet: confirm pin 1/3 before layout (timed-bathroom-vent) |
| hw:LM66100 | TI LM66100DCKR ideal diode, SC-70-6 (footprint Package_TO_SOT_SMD:SOT-363_SC-70-6) | TI SLVSEZ8A p.3 pin table | battery_logger parts stage (pin table read from the datasheet page image) |
| hw:TPS61299 | TI low-IQ boost, SOT-563 | TI SLVSGS9G | home-thermostat-3 (early rounds) |

## Footprints (`footprints/hw.pretty`)

`_back` footprints are the front footprint mirrored for a part on the bottom face (docs/design-yaml.md, libraries).

| footprint | part | source | verified |
|---|---|---|---|
| C_0805_2012Metric_back, R_0805_2012Metric_back, C_1206_3216Metric_back, R_1206_3216Metric_back, C_1812_4532Metric_back, D_SOD-123_back, SOT-23_back, TestPoint_Pad_1.5x1.5mm_back | SMD chip parts, SOD-123, SOT-23 and test pads on the bottom face | KiCad originals (Capacitor_SMD, Resistor_SMD, Diode_SMD, Package_TO_SOT_SMD, TestPoint), mirrored in x, pads on B.Cu/B.Mask/B.Paste, body on B.*, 3D model dropped | KiCad original geometry; mirror checked with `hwflow lib pads` |
| Relay_Panasonic_ADW1_1coil(_back) | Panasonic ADW1103HLW latching relay | Panasonic DW drawing | drawing only: check against a real relay |
| TerminalBlock_KF128-5.08-3P_back | Kefa KF128-5.08-3P screw terminal (LCSC C474953) | Kefa drawing | drawing only |
| Battery_AAA_Myoung_MY-AAA-20-J_19-J | Myoung MY-AAA-20-J + 19-J clip pair per cell | Myoung drawings | drawing only |
| BH-AAA-B5AA001, BH-AAA-B5AA001_2xAAA | 1 x / 2 x AAA holder | maker drawing | drawing only |
| Fuse_Littelfuse_395Series_back | TE5 fuse (Walter 2010T, Littelfuse 395) | KiCad Fuse_Littelfuse_395Series, mirrored | KiCad original |
| CP_Radial_D10.0mm_P5.00mm_back | 10 mm radial electrolytic | KiCad CP_Radial_D10.0mm_P5.00mm, mirrored | KiCad original |
| CP_Radial_D6.3mm_P2.50mm_LaidFlat_L12 | 6.3 x 12 mm radial electrolytic laid flat (leads bent 90 deg, body on the board) | KiCad CP_Radial_D6.3mm_P2.50mm pads, courtyard over the 12 mm body + 1.5 mm bend | not yet: pads and bend length from a real part (timed-bathroom-vent) |
| RV_Disc_D7mm_P5mm_LaidFlat | 7 mm varistor disc laid flat (TDK S07K) | KiCad RV_Disc_D7mm pads at 5 mm pitch, courtyard over a 9 mm body + lead bend | not yet: TDK drawing not read (timed-bathroom-vent) |
| CP_Radial_D6.3mm_P5.00mm_LaidFlat_L12 | 6.3 x 12 mm radial e-cap laid flat, leads formed to 5.0 mm, F.Cu rule area under the body | derived from CP_Radial_D6.3mm_P2.50mm_LaidFlat_L12 | not yet (timed-bathroom-vent) |
| R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal_RuleArea | DIN0207 axial resistor with an F.Cu rule area under the body | KiCad Resistor_THT original + zone | KiCad pads |
| SMDIP-6_W9.53mm_NoPin5 | SMD DIP-6 opto-triac, pad 5 without copper (NC pin) | onsemi FOD4218/D Rev 4 p.9 case 646CE land pattern (pads 1.5 x 1.3, row centres 9.2); pad 5 on F.Fab only | land pattern checked against the PDF (timed-bathroom-vent) |
| L_Bourns_SDR0403 | Bourns SDR0403 SMD inductor | Bourns SDR0403 REV. 02/18 p.1 recommended layout | drawing read (timed-bathroom-vent) |
| MBS_Diodes_MB10S_SymbolPinout | Diodes MBS bridge (MB10S), pads numbered as KiCad Diode_Bridge:MB4S (1,2 AC, 3 +, 4 -) | Diodes DS39045 Rev 3-2 p.4 suggested pad layout (C 2.50, Y 2.15, Y1 7.50), pad width X 1.00 narrowed to 0.70 = lead width b max (p.4: b 0.50-0.70, e 2.30-2.70) for a 1.80 mm gap in a pair (1.8 mm creepage; 1.85 would need X 0.65 < b max); maker pin 1 + = pad 3, 3 - = pad 4, 2/4 AC = pads 2/1 | drawing read (timed-bathroom-vent) |
| D_SMA_Handsoldering_back | SMA (DO-214AC) diode on the bottom face, 1.85 mm pad gap | KiCad D_SMA_Handsoldering mirrored, pads shortened on the inner side to 3.325 x 1.8 at +/-2.5875 (outer edge 4.25 kept). Lead span 0.945-2.64 mm from the centre from JEDEC DO-214AC as drawn in the Vishay SMA outline (overall 4.93-5.28, lead length 0.76-1.52, lead width 1.25-1.65; mounting pad gap 1.88 max); no ES1J/M7 PDF on disk | not yet: check against the ES1J/M7 maker drawing (timed-bathroom-vent) |
| D_SMB_back | SMB (DO-214AA) diode/TVS on the bottom face, 1.85 mm pad gap | KiCad D_SMB mirrored, pads shortened on the inner side to 2.475 x 2.3 at +/-2.1625 (outer edge 3.4 kept). Littelfuse SMBJ datasheet rev 06/03/20 p.5: G 5.21-5.59, E 0.76-1.52, A 1.93-2.20 (lead span 1.085-2.795 mm from the centre), solder pads K 2.74 max, J/L 2.16 min | drawing read (timed-bathroom-vent) |
| DPAK_ST_T8_Tab | ST DPAK triac (T8xx, BTx08): tab 6.7 x 6.7, lead pads 3.0 x 1.6 | ST DS2114 Rev 15 p.9 Fig. 14; pad numbers 1 A1, 2 A2 tab, 3 G by ST convention | drawing read; pin numbers to confirm on a part (timed-bathroom-vent) |
| R_Axial_FKN100_L12.5mm_D5.1mm_P15.24mm_Horizontal_RuleArea | Yageo FKN100 (1 W fusible) axial laid flat, rule area | Yageo FKN datasheet V.5 Dimensions table (L 11.5 +/-1, D 4.6 +/-0.5, d 0.8) | drawing read (timed-bathroom-vent) |
| TerminalBlock_bornier-2/3_P5.08mm_back | 5.08 mm screw terminals | KiCad TerminalBlock_bornier, mirrored | KiCad original |
| ESP32-C3-MINI-1 | Espressif module land pattern and antenna keep-out | Espressif datasheet | home-thermostat-3 (DRC) |

Source project: projects/home-thermostat-3 (2026-10-23, layout round 8).
