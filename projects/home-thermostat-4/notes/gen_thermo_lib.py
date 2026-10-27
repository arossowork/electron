"""Generate the home-thermostat-3 project library.

Run from the repo root: uv run python projects/home-thermostat-3/notes/gen_thermo_lib.py projects/home-thermostat-3
Output: lib/symbols/thermo.kicad_sym and lib/footprints/thermo.pretty/*.kicad_mod
Sources and unverified geometry are listed in notes/parts.md.
"""
import sys
from pathlib import Path

proj = Path(sys.argv[1])
symdir = proj / "lib" / "symbols"
fpdir = proj / "lib" / "footprints" / "thermo.pretty"
symdir.mkdir(parents=True, exist_ok=True)
fpdir.mkdir(parents=True, exist_ok=True)

FX = "(effects (font (size 1.27 1.27)))"
FXH = "(effects (font (size 1.27 1.27)) hide)"


def prop(name, val, x, y, hide=False):
    return f'    (property "{name}" "{val}" (at {x} {y} 0) {FXH if hide else FX})\n'


def pin(typ, x, y, ang, name, num, length=2.54):
    return (f'      (pin {typ} line (at {x} {y} {ang}) (length {length})\n'
            f'        (name "{name}" {FX})\n        (number "{num}" {FX})\n      )\n')


# ------------------------------------------------------------------ symbols
def esp32_symbol():
    left = [("EN", 8, "input"), ("IO0", 12, "bidirectional"), ("IO1", 13, "bidirectional"),
            ("IO2", 5, "bidirectional"), ("IO3", 6, "bidirectional"), ("IO4", 18, "bidirectional"),
            ("IO5", 19, "bidirectional"), ("IO6", 20, "bidirectional"), ("IO7", 21, "bidirectional"),
            ("IO10", 16, "bidirectional")]
    right = [("IO8", 22, "bidirectional"), ("IO9", 23, "bidirectional"), ("IO18", 26, "bidirectional"),
             ("IO19", 27, "bidirectional"), ("IO20/RXD0", 30, "bidirectional"), ("IO21/TXD0", 31, "bidirectional")]
    gnd = [1, 2, 11, 14] + list(range(36, 54))
    nc = [4, 7, 9, 10, 15, 17, 24, 25, 28, 29, 32, 33, 34, 35]
    s = '  (symbol "ESP32-C3-MINI-1" (in_bom yes) (on_board yes)\n'
    s += prop("Reference", "U", 0, 19.05) + prop("Value", "ESP32-C3-MINI-1-H4X", 0, -19.05)
    s += prop("Footprint", "thermo:ESP32-C3-MINI-1", 0, 0, True)
    s += prop("Datasheet", "https://documentation.espressif.com/esp32-c3-mini-1_datasheet_en.html", 0, 0, True)
    s += prop("ki_description", "Espressif ESP32-C3-MINI-1 Wi-Fi/BLE module, 53 pads, pins from datasheet Table 3-1", 0, 0, True)
    s += '    (symbol "ESP32-C3-MINI-1_0_1"\n'
    s += '      (rectangle (start -10.16 15.24) (end 10.16 -15.24) (stroke (width 0.254) (type default)) (fill (type background)))\n'
    s += '    )\n    (symbol "ESP32-C3-MINI-1_1_1"\n'
    for i, (n, num, t) in enumerate(left):
        s += pin(t, -12.7, round(12.7 - 2.54 * i, 2), 0, n, num)
    for i, (n, num, t) in enumerate(right):
        s += pin(t, 12.7, round(12.7 - 2.54 * i, 2), 180, n, num)
    s += pin("power_in", 0, 17.78, 270, "3V3", 3)
    for num in gnd:  # stacked at one point: all GND pads are one net
        s += pin("power_in", 0, -17.78, 90, "GND", num)
    for num in nc:
        s += pin("no_connect", 12.7, -12.7, 180, "NC", num)
    s += '    )\n  )\n'
    return s


def tps_symbol():
    s = '  (symbol "TPS61299" (in_bom yes) (on_board yes)\n'
    s += prop("Reference", "U", 0, 7.62) + prop("Value", "TPS61299", 0, -7.62)
    s += prop("Footprint", "Package_TO_SOT_SMD:SOT-563", 0, 0, True)
    s += prop("Datasheet", "https://www.ti.com/lit/ds/symlink/tps61299.pdf", 0, 0, True)
    s += prop("ki_description", "TI TPS61299DRL boost, 1.2 A input limit, VSEL resistor sets VOUT (GND = 3.3 V, confirm), SOT-563 pins 1 VIN 2 SW 3 EN 4 VSEL 5 VOUT 6 GND", 0, 0, True)
    s += '    (symbol "TPS61299_0_1"\n'
    s += '      (rectangle (start -5.08 5.08) (end 5.08 -5.08) (stroke (width 0.254) (type default)) (fill (type background)))\n'
    s += '    )\n    (symbol "TPS61299_1_1"\n'
    s += pin("power_in", -7.62, 2.54, 0, "VIN", 1) + pin("input", -7.62, 0, 0, "EN", 3) + pin("input", -7.62, -2.54, 0, "VSEL", 4)
    s += pin("passive", 7.62, 2.54, 180, "SW", 2) + pin("power_out", 7.62, 0, 180, "VOUT", 5)
    s += pin("power_in", 0, -7.62, 90, "GND", 6)
    s += '    )\n  )\n'
    return s


def max17227a_symbol():
    # MAX17227AATA+ (TDFN-8 2x2, Maxim outline 21-0168 = KiCad TDFN-8-1EP_2x2mm_P0.5mm_EP0.8x1.2mm, EP = pad 9).
    # Pin table: MAX17227A datasheet "Pin Description" (TDFN column): 1, 2 OUT; 3 LX; 4 GND; 5 AGND; 6 RSEL;
    # 7 IN; 8 EN; EP to GND. OUT pins stacked; GND (4) and EP (9) stacked, AGND (5) beside them (all on GND).
    s = '  (symbol "MAX17227A" (in_bom yes) (on_board yes)\n'
    s += prop("Reference", "U", 0, 10.16) + prop("Value", "MAX17227A", 0, -12.7)
    s += prop("Footprint", "Package_DFN_QFN:TDFN-8-1EP_2x2mm_P0.5mm_EP0.8x1.2mm", 0, 0, True)
    s += prop("Datasheet", "https://www.analog.com/media/en/technical-documentation/data-sheets/max17227a.pdf", 0, 0, True)
    s += prop("ki_description", "ADI/Maxim MAX17227A nanoPower boost, 2 A peak inductor current limit, 350 nA IQ, RSEL to GND sets VOUT (191k 1 % = 3.3 V), TDFN-8 2x2: 1/2 OUT 3 LX 4 GND 5 AGND 6 RSEL 7 IN 8 EN 9 EP", 0, 0, True)
    s += '    (symbol "MAX17227A_0_1"\n'
    s += '      (rectangle (start -5.08 7.62) (end 5.08 -7.62) (stroke (width 0.254) (type default)) (fill (type background)))\n'
    s += '    )\n    (symbol "MAX17227A_1_1"\n'
    s += pin("power_in", -7.62, 5.08, 0, "IN", 7) + pin("input", -7.62, 2.54, 0, "EN", 8)
    s += pin("passive", -7.62, -2.54, 0, "RSEL", 6)
    s += pin("passive", 7.62, 5.08, 180, "LX", 3)
    s += pin("power_out", 7.62, 0, 180, "OUT", 1) + pin("passive", 7.62, 0, 180, "OUT", 2)
    s += pin("power_in", 0, -10.16, 90, "GND", 4) + pin("passive", 2.54, -10.16, 90, "AGND", 5)
    s += pin("passive", 0, -10.16, 90, "GND", 9)
    s += '    )\n  )\n'
    return s


hdr = '(kicad_symbol_lib (version 20220914) (generator hwflow_projlib)\n'
(symdir / "thermo.kicad_sym").write_text(hdr + esp32_symbol() + tps_symbol() + max17227a_symbol() + ')\n')


# ------------------------------------------------------------------ footprints
def txt(kind, text, x, y, layer, size=1.0):
    return (f'  (fp_text {kind} "{text}" (at {x} {y}) (layer "{layer}")\n'
            f'    (effects (font (size {size} {size}) (thickness 0.15)))\n  )\n')


def rect(x1, y1, x2, y2, layer, w):
    return (f'  (fp_rect (start {x1} {y1}) (end {x2} {y2}) (stroke (width {w}) (type solid)) '
            f'(fill none) (layer "{layer}"))\n')


def smd(num, x, y, w, h, shape="rect", layers='"F.Cu" "F.Paste" "F.Mask"'):
    return f'  (pad "{num}" smd {shape} (at {x} {y}) (size {w} {h}) (layers {layers}))\n'


def esp32_fp():
    s = '(footprint "ESP32-C3-MINI-1" (version 20221018) (generator hwflow_projlib)\n  (layer "F.Cu")\n'
    s += '  (descr "Espressif ESP32-C3-MINI-1 module 13.2 x 16.6 x 2.4 mm, 53 pads. Pad pattern from the Espressif KiCad library (web summary), checked against datasheet s10.1/s11.1: pad rows 11.8 mm across, 9.8 mm along")\n'
    s += '  (tags "ESP32-C3 module wifi ble")\n  (attr smd)\n'
    s += txt("reference", "REF**", 0, -9.6, "F.SilkS") + txt("value", "ESP32-C3-MINI-1", 0, 9.6, "F.Fab")
    s += txt("user", "${REFERENCE}", 0, 3.0, "F.Fab", 0.8)
    s += rect(-6.6, -8.3, 6.6, 8.3, "F.Fab", 0.1)
    s += rect(-6.7, -8.4, 6.7, 8.4, "F.SilkS", 0.12)
    s += '  (fp_line (start -6.6 -2.9) (end 6.6 -2.9) (stroke (width 0.1) (type solid)) (layer "F.Fab"))\n'
    s += rect(-6.8, -8.5, 6.8, 8.5, "F.CrtYd", 0.05)
    # antenna keep-out on a user layer plus a copper keep-out rule area (no tracks, vias, pours on any layer)
    s += rect(-6.6, -8.3, 6.6, -2.9, "Dwgs.User", 0.15)
    s += txt("user", "ANTENNA KEEP-OUT 5.4 mm: no copper; antenna at board edge, 15 mm clear", 0, -5.6, "Dwgs.User", 0.6)
    s += ('  (zone (net 0) (net_name "") (layers "F&B.Cu") (hatch edge 0.5) (connect_pads (clearance 0)) (min_thickness 0.25)\n'
          '    (keepout (tracks not_allowed) (vias not_allowed) (pads allowed) (copperpour not_allowed) (footprints allowed))\n'
          '    (fill (thermal_gap 0.5) (thermal_bridge_width 0.5))\n'
          '    (polygon (pts (xy -6.6 -8.3) (xy 6.6 -8.3) (xy 6.6 -2.9) (xy -6.6 -2.9)))\n  )\n')
    s += '  (fp_circle (center -7.3 -1.3) (end -7.1 -1.3) (stroke (width 0.2) (type solid)) (fill solid) (layer "F.SilkS"))\n'
    for i in range(11):                      # left side 1-11, top to bottom
        s += smd(i + 1, -5.9, round(-1.3 + 0.8 * i, 2), 0.8, 0.4)
    for i in range(13):                      # bottom 12-24, left to right
        s += smd(12 + i, round(-4.8 + 0.8 * i, 2), 7.6, 0.4, 0.8)
    for i in range(11):                      # right side 25-35, bottom to top
        s += smd(25 + i, 5.9, round(6.7 - 0.8 * i, 2), 0.8, 0.4)
    for i in range(13):                      # 36-48 row next to the antenna zone, right to left
        s += smd(36 + i, round(4.8 - 0.8 * i, 2), -2.2, 0.4, 0.8)
    for x in (-1.975, 0.0, 1.975):           # pad 49: 3 x 3 ground pads
        for y in (0.725, 2.7, 4.675):
            s += smd(49, x, y, 1.45, 1.45)
    for n, (x, y) in zip((50, 51, 52, 53), ((5.95, -2.25), (5.95, 7.65), (-5.95, 7.65), (-5.95, -2.25))):
        s += smd(n, x, y, 0.7, 0.7)
    return s + ')\n'


def relay_fp():
    # PLACEHOLDER pin geometry: Panasonic PC-board pattern was not readable in stage 3 (see notes/parts.md)
    s = '(footprint "Relay_Panasonic_ADW1_1coil" (version 20221018) (generator hwflow_projlib)\n  (layer "F.Cu")\n'
    s += '  (descr "Panasonic ADW1 (DW) 1 Form A latching 1-coil relay, body 24 x 10 mm, height 15.8 mm (HL). PIN POSITIONS AND HOLE SIZES ARE PLACEHOLDERS: verify against the Panasonic PC-board pattern before layout")\n'
    s += '  (tags "relay panasonic ADW1 DW latching")\n  (attr through_hole)\n'
    s += txt("reference", "REF**", 0, -6.6, "F.SilkS") + txt("value", "ADW1103HLW", 0, 6.6, "F.Fab")
    s += txt("user", "${REFERENCE}", 0, 0, "F.Fab", 0.8)
    s += rect(-12.0, -5.0, 12.0, 5.0, "F.Fab", 0.1)
    s += rect(-12.1, -5.1, 12.1, 5.1, "F.SilkS", 0.12)
    s += rect(-12.25, -5.25, 12.25, 5.25, "F.CrtYd", 0.05)
    s += txt("user", "PLACEHOLDER PIN POSITIONS - VERIFY", 0, 3.6, "Dwgs.User", 0.6)

    def th(num, x, y, d, drill):
        return (f'  (pad "{num}" thru_hole circle (at {x} {y}) (size {d} {d}) (drill {drill}) '
                f'(layers "*.Cu" "*.Mask"))\n')
    s += th("13", -9.75, -3.75, 3.0, 1.7) + th("14", -9.75, 3.75, 3.0, 1.7)   # contact pins (HV end)
    s += th("A1", 9.75, -2.5, 2.4, 1.4) + th("A2", 9.75, 2.5, 2.4, 1.4)       # coil pins (SELV end)
    return s + ')\n'


def bat_fp():
    # PLACEHOLDER geometry from the AAA cell (10.5 x 44.5 mm); Myoung drawing not readable in stage 3
    s = '(footprint "BH-AAA-B5AA001" (version 20221018) (generator hwflow_projlib)\n  (layer "F.Cu")\n'
    s += '  (descr "Myoung BH-AAA-B5AA001 horizontal SMT holder for one AAA cell. PAD SIZE AND SPAN ARE PLACEHOLDERS derived from the cell (44.5 x 10.5 mm): verify against the Myoung drawing")\n'
    s += '  (tags "battery holder AAA SMT")\n  (attr smd)\n'
    s += txt("reference", "REF**", 0, -7.6, "F.SilkS") + txt("value", "BH-AAA-B5AA001", 0, 7.6, "F.Fab")
    s += rect(-24.5, -6.0, 24.5, 6.0, "F.Fab", 0.1)
    s += rect(-24.6, -6.1, 24.6, 6.1, "F.SilkS", 0.12)
    s += rect(-25.0, -6.5, 25.0, 6.5, "F.CrtYd", 0.05)
    s += txt("user", "+", -26.5, 0, "F.SilkS", 1.2)
    s += smd("1", -23.75, 0, 3.0, 5.0)    # + contact (assumed side)
    s += smd("2", 23.75, 0, 3.0, 5.0)     # - contact
    return s + ')\n'


(fpdir / "ESP32-C3-MINI-1.kicad_mod").write_text(esp32_fp())
(fpdir / "Relay_Panasonic_ADW1_1coil.kicad_mod").write_text(relay_fp())
(fpdir / "BH-AAA-B5AA001.kicad_mod").write_text(bat_fp())
print("ok")
