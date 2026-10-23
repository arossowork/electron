"""Generate mirrored back-body variants of THT footprints (face B body, pads inserted from the back).
Run: uv run python projects/home-thermostat-3/lib/mkback.py"""
import re
from pathlib import Path
from hwflow.libs import Libs

proj = Path(__file__).resolve().parent
L = Libs(extra_dirs=[str(proj / "footprints")])
out = proj / "footprints/thermo.pretty"
pairs = {
    "TerminalBlock:TerminalBlock_bornier-3_P5.08mm": "TerminalBlock_bornier-3_P5.08mm_back",
    "TerminalBlock:TerminalBlock_bornier-2_P5.08mm": "TerminalBlock_bornier-2_P5.08mm_back",
    "Capacitor_THT:CP_Radial_D10.0mm_P5.00mm": "CP_Radial_D10.0mm_P5.00mm_back",
    "thermo:Relay_Panasonic_ADW1_1coil": "Relay_Panasonic_ADW1_1coil_back",
    "Fuse:Fuse_Littelfuse_395Series": "Fuse_Littelfuse_395Series_back",  # F1 Walter 2010T (TE5 body 8.2 x 4.0, P 5.08)
}
pat = re.compile(r"\((at|start|end|mid|center|xy)\s+(-?[\d.]+)\s+(-?[\d.]+)")


def flip(m):
    return "(%s %g %s" % (m.group(1), -float(m.group(2)), m.group(3))


def mirror_text(txt):
    """Text on B.* layers must be mirrored (KiCad nonmirrored_text_on_back_layer)."""
    return re.sub(r'(\(layer "B\.[A-Za-z]+"\)\s*\(effects \(font \(size [^)]*\) \(thickness [^)]*\)\))\)',
                  r"\1 (justify mirror))", txt)


def trim_relay_silk(txt):
    """Silk rectangle of the relay: the round contact pads 13/14 (d 3.0 at x = 9.75, y = +-3.75) reach y = +-5.25, the
    silk is at +-5.1: cut the long sides at x 8.4..11.1 so the silk keeps >= 0.3 mm from the copper."""
    rect = re.search(r'  \(fp_rect \(start 12\.1 -5\.1\) \(end -12\.1 5\.1\)[^\n]*"B\.SilkS"\)\)\n', txt)
    if not rect:
        return txt
    seg = '  (fp_line (start %g %g) (end %g %g) (stroke (width 0.12) (type solid)) (layer "B.SilkS"))\n'
    new = "".join(seg % s for s in [(-12.1, -5.1, 8.4, -5.1), (11.1, -5.1, 12.1, -5.1), (-12.1, 5.1, 8.4, 5.1),
                                    (11.1, 5.1, 12.1, 5.1), (12.1, -5.1, 12.1, 5.1), (-12.1, -5.1, -12.1, 5.1)])
    return txt.replace(rect.group(0), new)


for src, name in pairs.items():
    srcp = Path(L.footprint_path(src))
    if "_back" in srcp.name:
        continue
    txt = srcp.read_text()
    txt = re.sub(r'\((footprint|module)\s+"?[^"\s)]+"?', '(footprint "%s"' % name, txt, count=1)
    txt = pat.sub(flip, txt)
    for k in ("CrtYd", "SilkS", "Fab"):
        txt = txt.replace("F." + k, "B." + k)
    if "Relay" in name:
        txt = trim_relay_silk(txt)
    txt = mirror_text(txt)
    (out / (name + ".kicad_mod")).write_text(txt)
    a = L.footprint(src)
    b = Libs(extra_dirs=[str(proj / "footprints")]).footprint("thermo:" + name)
    pa = {p["number"]: (p["x"], p["y"]) for p in a["pads"]}
    pb = {p["number"]: (p["x"], p["y"]) for p in b["pads"]}
    ok = set(pa) == set(pb) and all(abs(pa[n][0] + pb[n][0]) < 1e-6 and abs(pa[n][1] - pb[n][1]) < 1e-6 for n in pa)
    print(name, "pads mirrored ok" if ok else "MISMATCH", b["sides"])
