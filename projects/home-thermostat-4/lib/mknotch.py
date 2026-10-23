"""Generate the layout-only keep-out footprints of home-thermostat-3 (no copper, not in the BOM).

NotchKeepout_8mm_L / _R : the two 8 mm screw-post notches (centre y = 23, x = 2.25 and 85.75). Rule area on all copper
    layers (no tracks, vias, pads, pour, footprints) out to r = 4.3 mm (notch r = 4 + 0.3 mm copper-to-edge), courtyards on
    both sides, a Dwgs.User circle with the true notch r = 4 mm. The polygon is a 24-gon around r = 4.3, clamped to the board.
MainsRuleAreas : two rule areas (no keep-out flags) that the `board.drc_rules` mains rules refer to by name:
    SELV_region (x >= 21.5, all copper layers) and NO_MAINS_INNER (whole board, In1.Cu and In2.Cu).
All three are placed at (0, 0), so the board-coordinate zone polygons equal the footprint-local ones (KiCad keeps a
footprint's zones in board coordinates; there is then no lib/board mismatch).
Run: uv run python projects/home-thermostat-3/lib/mknotch.py"""
import math
import uuid
from pathlib import Path

out = Path(__file__).resolve().parent / "footprints/thermo.pretty"
W, H, CY = 88.0, 46.0, 23.0
R_KO, R_NOTCH = 4.3, 4.0
ALL = '"F.Cu" "In1.Cu" "In2.Cu" "B.Cu"'
NO = "(keepout (tracks not_allowed) (vias not_allowed) (pads not_allowed) (copperpour not_allowed) (footprints not_allowed))"
RULE = "(keepout (tracks allowed) (vias allowed) (pads allowed) (copperpour allowed) (footprints allowed))"


def uid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "hwflow-thermo-" + name))


def zone(name, layers, flags, pts):
    xy = " ".join("(xy %g %g)" % p for p in pts)
    return (f'  (zone (net 0) (net_name "") (layers {layers}) (uuid "{uid(name)}") (name "{name}") (hatch edge 0.5)\n'
            f'    (connect_pads (clearance 0)) (min_thickness 0.25) (filled_areas_thickness no)\n'
            f'    {flags}\n    (fill (thermal_gap 0.5) (thermal_bridge_width 0.5))\n    (polygon (pts {xy})))\n')


def head(name, descr):
    return (f'(footprint "{name}" (version 20221018) (generator hwflow_projlib)\n  (layer "F.Cu")\n  (descr "{descr}")\n'
            f'  (tags "keepout")\n  (attr exclude_from_pos_files exclude_from_bom)\n'
            f'  (fp_text reference "REF**" (at 0 0) (layer "F.Fab") (effects (font (size 0.6 0.6) (thickness 0.1))))\n'
            f'  (fp_text value "{name}" (at 0 0) (layer "F.Fab") (effects (font (size 0.6 0.6) (thickness 0.1))))\n')


def notch(side, cx, lo, hi):
    n = 24
    rc = R_KO / math.cos(math.pi / n)
    pts = [(min(max(cx + rc * math.cos(2 * math.pi * (i + 0.5) / n), lo), hi), round(CY + rc * math.sin(2 * math.pi * (i + 0.5) / n), 4))
           for i in range(n)]
    pts = [(round(x, 4), y) for x, y in pts]
    name = "NotchKeepout_8mm_" + side
    s = head(name, f"8 mm screw-post notch at x={cx}, y={CY} (layout keep-out; the notched Edge.Cuts outline is added "
                   "before fabrication). Coordinates are board coordinates: place at (0, 0).")
    for lay in ("F.CrtYd", "B.CrtYd"):
        s += f'  (fp_circle (center {cx} {CY}) (end {cx + R_KO} {CY}) (stroke (width 0.05) (type solid)) (fill none) (layer "{lay}"))\n'
    s += f'  (fp_circle (center {cx} {CY}) (end {cx + R_NOTCH} {CY}) (stroke (width 0.1) (type solid)) (fill none) (layer "Dwgs.User"))\n'
    s += f'  (pad "1" smd rect (at {cx} {CY}) (size 0.2 0.2) (layers "Dwgs.User"))\n'
    s += zone("NOTCH_" + side, ALL, NO, pts) + ")\n"
    (out / (name + ".kicad_mod")).write_text(s)


notch("L", 2.25, 0.0, W)
notch("R", 85.75, 0.0, W)

name = "MainsRuleAreas"
s = head(name, "Rule areas for the mains rules in board.drc_rules: SELV_region (x >= 21.5, all copper layers) and "
               "NO_MAINS_INNER (whole board, inner layers). Board coordinates: place at (0, 0).")
s += f'  (pad "1" smd rect (at 44 23) (size 0.2 0.2) (layers "Dwgs.User"))\n'
s += zone("SELV_region", ALL, RULE, [(21.5, 0), (W, 0), (W, H), (21.5, H)])
s += zone("NO_MAINS_INNER", '"In1.Cu" "In2.Cu"', RULE, [(0, 0), (W, 0), (W, H), (0, H)])
# MAINS_BUBBLE: 6.2 mm (creepage 6.0 + half a track) around the J1 / F1 / K1 / K2 mains pads (J1 at 18.4, 28.6). Rule
# "selv_bubble" in board.drc_rules disallows tracks and vias of every net class but the mains one inside it.
bub = [(0, 0), (10.3, 0), (10.3, 7.2), (12.9, 7.2), (17.3, 9.0), (18.4, 10.74), (23.85, 13.0), (26.1, 18.44), (26.1, 30.1),
       (24.3, 34.5), (19.9, 36.3), (10.3, 36.3), (10.3, H), (0, H)]
s += zone("MAINS_BUBBLE", ALL, RULE, bub)
s += ")\n"
(out / (name + ".kicad_mod")).write_text(s)
print("written", sorted(p.name for p in out.glob("*.kicad_mod")))
