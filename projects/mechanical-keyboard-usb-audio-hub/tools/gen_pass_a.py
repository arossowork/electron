"""Stage 4 pass A helper: writes the back-side key footprints (kb_keys) and prints the `nets:` and `components:`
YAML of the keyboard matrix, keyboard MCU, encoder and USB hub / USB data blocks.

design.yaml is the source of truth: the printed text was pasted into design.yaml once. Edit design.yaml after that.
Run: uv run python projects/mechanical-keyboard-usb-audio-hub/tools/gen_pass_a.py [--footprints] > out.yaml
"""
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
KB = PROJ / "lib" / "footprints" / "kb_keys.pretty"
U = 19.05
X0 = 5.7          # left edge of Esc (key field 22.5 u = 428.6 mm, centred on the floorplan key rows at x 220)
ROW_Y = [15.725, 44.3, 63.35, 82.4, 101.45, 120.5]   # F row (6.2 back margin + 0.5 u), then 1.5 u gap, 19.05 pitch
DIODE_DY = 5.6    # diode centre below the key centre, back face, clear of the 4.0 centre post and 1.75 plate pins

# ---------------------------------------------------------------- footprints (front-view coordinates, B.* copper)
SOCKET_PADS = """  (pad "1" smd roundrect (at -7.085 -2.54) (size 2.55 2.5) (layers "B.Cu" "B.Paste" "B.Mask") (roundrect_rratio 0.1))
  (pad "2" smd roundrect (at 5.842 -5.08) (size 2.55 2.5) (layers "B.Cu" "B.Paste" "B.Mask") (roundrect_rratio 0.1))
  (pad "" np_thru_hole circle (at 0 0) (size 4 4) (drill 4) (layers "*.Cu" "*.Mask"))
  (pad "" np_thru_hole circle (at -5.08 0) (size 1.75 1.75) (drill 1.75) (layers "*.Cu" "*.Mask"))
  (pad "" np_thru_hole circle (at 5.08 0) (size 1.75 1.75) (drill 1.75) (layers "*.Cu" "*.Mask"))
  (pad "" np_thru_hole circle (at -3.81 -2.54) (size 3.05 3.05) (drill 3.05) (layers "*.Cu" "*.Mask"))
  (pad "" np_thru_hole circle (at 2.54 -5.08) (size 3.05 3.05) (drill 3.05) (layers "*.Cu" "*.Mask"))
"""
SOCKET_BACK = """  (fp_rect (start -8.6 -6.85) (end 7.4 -0.75) (stroke (width 0.05) (type solid)) (fill none) (layer "B.CrtYd"))
  (fp_rect (start -8.4 -6.6) (end 7.2 -1.0) (stroke (width 0.1) (type solid)) (fill none) (layer "B.Fab"))
"""


def hole(x, y, d):
    return f'  (pad "" np_thru_hole circle (at {x} {y}) (size {d} {d}) (drill {d}) (layers "*.Cu" "*.Mask"))\n'


def socket_fp(name, descr, stab_holes, fcrt):
    x0, y0, x1, y1 = fcrt
    s = (f'(footprint "{name}" (version 20240108) (generator "hwflow") (generator_version "1.0")\n'
         f'  (layer "F.Cu")\n  (descr "{descr}")\n  (tags "MX hotswap Kailh CPG151101S11 back")\n  (attr smd)\n'
         f'  (property "Reference" "REF**" (at 0 -10.5 0) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))\n'
         f'  (property "Value" "{name}" (at 0 10.5 0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))\n'
         f'  (fp_rect (start -7 -7) (end 7 7) (stroke (width 0.1) (type solid)) (fill none) (layer "F.Fab"))\n'
         f'  (fp_rect (start {x0} {y0}) (end {x1} {y1}) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd"))\n'
         + SOCKET_BACK + SOCKET_PADS + "".join(hole(*h) for h in stab_holes) + ")\n")
    return s


BASE = ("Cherry MX cut-outs (4.0 centre post, 1.75 plate pins, 3.05 switch-pin holes) and Kailh CPG151101S11 hot-swap pads "
        "on B.Cu, drawn in front-view coordinates (switch pins at (-3.81,-2.54) and (2.54,-5.08) as Cherry MX seen from the "
        "top), so the part is placed unflipped and its copper is on the back face. Geometry of kiswitch "
        "SW_Hotswap_Kailh_MX_1.00u (community, Kailh drawing not read). F.CrtYd = switch housing 14 x 14 (+ stabilizer "
        "housings), B.CrtYd = socket body.")
STAB2 = [(-11.9, -7, 3.05), (-11.9, 8.24, 4), (11.9, -7, 3.05), (11.9, 8.24, 4)]        # KiCad SW_Cherry_MX_2.00u_PCB
STAB2V = [(-7, -11.9, 3.05), (8.24, -11.9, 4), (-7, 11.9, 3.05), (8.24, 11.9, 4)]       # KiCad SW_Cherry_MX_2.00u_Vertical_PCB
STAB625 = [(-50, -7, 3.05), (-50, 8.24, 4), (50, -7, 3.05), (50, 8.24, 4)]             # KiCad SW_Cherry_MX_6.25u_PCB

FOOTPRINTS = {
    "SW_MX_Hotswap_Kailh_CPG151101S11_1u_Back": (BASE + " 1u key.", [], (-7.25, -7.25, 7.25, 7.25)),
    "SW_MX_Hotswap_Kailh_CPG151101S11_2u_Back": (
        BASE + " 2u/2.25u/2.75u key with plate-mount stabilizer holes +-11.9 mm (KiCad SW_Cherry_MX_2.00u_PCB).",
        STAB2, (-14.0, -8.6, 14.0, 9.5)),
    "SW_MX_Hotswap_Kailh_CPG151101S11_2u_Vertical_Back": (
        BASE + " vertical 2u numpad key, stabilizer holes +-11.9 mm in y (KiCad SW_Cherry_MX_2.00u_Vertical_PCB).",
        STAB2V, (-8.6, -14.0, 10.3, 14.0)),
    "SW_MX_Hotswap_Kailh_CPG151101S11_6.25u_Back": (
        BASE + " 6.25u space bar, stabilizer holes +-50 mm (KiCad SW_Cherry_MX_6.25u_PCB).",
        STAB625, (-52.1, -8.6, 52.1, 9.5)),
}

DIODE_FP = """(footprint "D_SOD-123_Back" (version 20240108) (generator "hwflow") (generator_version "1.0")
  (layer "F.Cu")
  (descr "SOD-123 on the back face, drawn in front-view coordinates (KiCad Diode_SMD:D_SOD-123 mirrored in x, copper on B.Cu), so the part is placed unflipped. Pad 1 = cathode.")
  (tags "SOD-123 back")
  (attr smd)
  (property "Reference" "REF**" (at 0 -2 0) (layer "B.SilkS") (effects (font (size 1 1) (thickness 0.15)) (justify mirror)))
  (property "Value" "D_SOD-123_Back" (at 0 2.1 0) (layer "B.Fab") (effects (font (size 1 1) (thickness 0.15)) (justify mirror)))
  (fp_rect (start -1.4 -0.9) (end 1.4 0.9) (stroke (width 0.1) (type solid)) (fill none) (layer "B.Fab"))
  (fp_line (start 2.36 -1.0) (end 2.36 1.0) (stroke (width 0.12) (type solid)) (layer "B.SilkS"))
  (fp_rect (start -2.35 -1.15) (end 2.35 1.15) (stroke (width 0.05) (type solid)) (fill none) (layer "B.CrtYd"))
  (pad "1" smd roundrect (at 1.65 0) (size 0.9 1.2) (layers "B.Cu" "B.Paste" "B.Mask") (roundrect_rratio 0.25))
  (pad "2" smd roundrect (at -1.65 0) (size 0.9 1.2) (layers "B.Cu" "B.Paste" "B.Mask") (roundrect_rratio 0.25))
)
"""


def write_footprints():
    for name, (descr, holes, fcrt) in FOOTPRINTS.items():
        (KB / f"{name}.kicad_mod").write_text(socket_fp(name, descr, holes, fcrt))
    (KB / "D_SOD-123_Back.kicad_mod").write_text(DIODE_FP)


# ---------------------------------------------------------------- ANSI 104 layout: (label, left edge u, width u, row, kind)
def ansi104():
    k = []
    k += [("Esc", 0, 1, 0)] + [(f"F{i+1}", x, 1, 0) for i, x in enumerate([2, 3, 4, 5, 6.5, 7.5, 8.5, 9.5, 11, 12, 13, 14])]
    k += [("PrtSc", 15.25, 1, 0), ("ScrLk", 16.25, 1, 0), ("Pause", 17.25, 1, 0)]
    k += [(c, i, 1, 1) for i, c in enumerate("`1234567890-=")] + [("Bksp", 13, 2, 1)]
    k += [("Ins", 15.25, 1, 1), ("Home", 16.25, 1, 1), ("PgUp", 17.25, 1, 1)]
    k += [("NumLk", 18.5, 1, 1), ("KP/", 19.5, 1, 1), ("KP*", 20.5, 1, 1), ("KP-", 21.5, 1, 1)]
    k += [("Tab", 0, 1.5, 2)] + [(c, 1.5 + i, 1, 2) for i, c in enumerate("QWERTYUIOP[]")] + [("\\", 13.5, 1.5, 2)]
    k += [("Del", 15.25, 1, 2), ("End", 16.25, 1, 2), ("PgDn", 17.25, 1, 2)]
    k += [("KP7", 18.5, 1, 2), ("KP8", 19.5, 1, 2), ("KP9", 20.5, 1, 2), ("KP+", 21.5, 1, 2, "v")]
    k += [("Caps", 0, 1.75, 3)] + [(c, 1.75 + i, 1, 3) for i, c in enumerate("ASDFGHJKL;'")] + [("Enter", 12.75, 2.25, 3)]
    k += [("KP4", 18.5, 1, 3), ("KP5", 19.5, 1, 3), ("KP6", 20.5, 1, 3)]
    k += [("LShift", 0, 2.25, 4)] + [(c, 2.25 + i, 1, 4) for i, c in enumerate("ZXCVBNM,./")] + [("RShift", 12.25, 2.75, 4)]
    k += [("Up", 16.25, 1, 4), ("KP1", 18.5, 1, 4), ("KP2", 19.5, 1, 4), ("KP3", 20.5, 1, 4), ("KPEnter", 21.5, 1, 4, "v")]
    k += [("LCtrl", 0, 1.25, 5), ("LWin", 1.25, 1.25, 5), ("LAlt", 2.5, 1.25, 5), ("Space", 3.75, 6.25, 5),
          ("RAlt", 10, 1.25, 5), ("RWin", 11.25, 1.25, 5), ("Menu", 12.5, 1.25, 5), ("RCtrl", 13.75, 1.25, 5)]
    k += [("Left", 15.25, 1, 5), ("Down", 16.25, 1, 5), ("Right", 17.25, 1, 5), ("KP0", 18.5, 2, 5), ("KP.", 20.5, 1, 5)]
    return k


def matrix():
    keys = ansi104()
    assert len(keys) == 104, len(keys)
    out, used = [], set()
    count = {}
    for i, key in enumerate(keys, 1):
        label, left, w, row = key[:4]
        vert = len(key) > 4
        half = 0 if left < 11 else 1                      # fold at 11 u (notes/parts-keyboard.md)
        lrow = row + 6 * half
        col = count.get(lrow, 0)
        count[lrow] = col + 1
        assert col <= 10, (label, lrow)
        assert (lrow, col) not in used
        used.add((lrow, col))
        cx = X0 + (left + w / 2) * U
        cy = ROW_Y[row] + (U / 2 if vert else 0)
        fp = ("SW_MX_Hotswap_Kailh_CPG151101S11_2u_Vertical_Back" if vert else
              "SW_MX_Hotswap_Kailh_CPG151101S11_6.25u_Back" if w >= 6 else
              "SW_MX_Hotswap_Kailh_CPG151101S11_2u_Back" if w >= 2 else "SW_MX_Hotswap_Kailh_CPG151101S11_1u_Back")
        out.append(dict(n=i, label=label, row=lrow, col=col, x=round(cx, 3), y=round(cy, 3), fp=fp, w=w, vert=vert))
    return out, count


# ---------------------------------------------------------------- YAML emitters
L = []


def q(s):
    s = str(s)
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def comp(ref, **f):
    L.append(f"  {ref}:")
    for k in ("value", "mpn", "lcsc", "price_usd", "datasheet", "symbol", "footprint", "group", "decouples",
              "voltage_rating_v", "tolerance", "height_mm", "spice", "placement", "pins", "nc", "notes"):
        if k not in f or f[k] is None:
            continue
        v = f[k]
        if k == "pins":
            v = "{" + ", ".join(f"{q(a)}: {b}" for a, b in v.items()) + "}"
        elif k == "nc":
            v = "[" + ", ".join(q(a) for a in v) + "]"
        elif k == "placement":
            v = "{" + ", ".join(f"{a}: {str(b).lower() if isinstance(b, bool) else b}" for a, b in v.items()) + "}"
        elif k == "spice":
            v = "false" if v is False else v
        elif isinstance(v, str) and k not in ("price_usd",):
            v = q(v)
        L.append(f"    {k}: {v}")


CAPS = {"100nF": ("C1525", 16, "Capacitor_SMD:C_0402_1005Metric", "CL05B104KO5NNNC"),
        "10uF": ("C19702", 10, "Capacitor_SMD:C_0603_1608Metric", "CL10A106KP8NNNC"),
        "1uF": ("C52923", 10, "Capacitor_SMD:C_0402_1005Metric", "CL05A105KA5NQNC"),
        "18pF": (None, 50, "Capacitor_SMD:C_0402_1005Metric", "0402 C0G 18 pF"),
        "10nF": ("C15195", 50, "Capacitor_SMD:C_0402_1005Metric", "CL05B103KB5NNNC")}
RES = {"10k": "C25744", "1k": "C11702", "100k": "C25741", "20k": "C25765"}


def cap(ref, val, a, b, group, dec=None):
    lc, vr, fp, mpn = CAPS[val]
    comp(ref, value=val, mpn=mpn, lcsc=lc, price_usd=0.003 if val != "10uF" else 0.006, symbol="Device:C",
         footprint=fp, group=group, decouples=dec, voltage_rating_v=vr, pins={"1": a, "2": b})


def res(ref, val, a, b, group, tol=None):
    comp(ref, value=val, mpn=f"0402 1% {val}", lcsc=RES.get(val), price_usd=0.002, symbol="Device:R",
         footprint="Resistor_SMD:R_0402_1005Metric", group=group, tolerance=tol, pins={"1": a, "2": b})


GL_PORT = {0: (69, 68, 72, 71, 66, 67), 1: (59, 58, 62, 61, 63, 64), 2: (21, 20, 24, 23, 25, 26),
           3: (16, 15, 19, 18, 13, 14), 4: (6, 5, 9, 8, 10, 11)}
GL_PWREN = {1: 37, 2: 36, 3: 33, 4: 32}
GL_OVCUR = {1: 38, 2: 35, 3: 34, 4: 31}


def hub(n, base, vbus_src, ports, pwren, ovcur_ext):
    """ports: {p: (txp_net, txn_net, rxp_net, rxn_net, dp_net, dm_net) or (None.. for SS nc)}."""
    H, g = f"HUB{n}", f"hub{n}"
    V3, V12 = f"{H}_3V3", f"{H}_1V2"
    pins, nc = {}, []
    for p in (43, 42):
        pins[str(p)] = "5V_SYS"
    pins["40"] = "GND"
    pins["77"] = "GND"
    for p in (44, 55, 73, 65, 12, 27):
        pins[str(p)] = V3
    pins["41"] = f"{H}_SW"
    for p in (39, 28, 47, 4, 70, 60, 22, 17, 7):
        pins[str(p)] = V12
    pins.update({"57": f"{H}_XI", "56": f"{H}_XO", "46": f"{H}_RTERM", "45": f"{H}_CHIPEN", "48": f"{H}_RESETJ",
                 "54": f"{H}_VBUSDET", "53": f"{H}_PGANG", "30": f"{H}_PSWPOL"})
    nc += ["74", "29", "2", "76", "75", "3", "1", "49", "50", "51", "52"]
    for p, nets in ports.items():
        for pin, net in zip(GL_PORT[p], nets):
            if net is None:
                nc.append(str(pin))
            else:
                pins[str(pin)] = net
    for i in (1, 2, 3, 4):
        if i in pwren:
            pins[str(GL_PWREN[i])] = f"{H}_PWREN{i}"
        else:
            nc.append(str(GL_PWREN[i]))
        pins[str(GL_OVCUR[i])] = f"{H}_OVCUR{i}" if i in ovcur_ext else V3
    comp(f"U{base+1}", value="GL3523", mpn="GL3523-OTY30", lcsc="C390630", price_usd=1.54,
         datasheet="notes/datasheets/gl3523.md", symbol="kb_hub:GL3523",
         footprint="Package_DFN_QFN:QFN-76-1EP_9x9mm_P0.4mm_EP5.81x6.31mm", group=g, spice=False, height_mm=0.9,
         pins=pins, nc=sorted(nc, key=int))
    U_ = f"U{base+1}"
    c = base
    cap(f"C{c+1}", "10uF", "5V_SYS", "GND", g, U_)
    cap(f"C{c+2}", "100nF", "5V_SYS", "GND", g, U_)      # V5 pin 43
    cap(f"C{c+3}", "100nF", "5V_SYS", "GND", g, U_)      # VDDP pin 42
    cap(f"C{c+4}", "10uF", V3, "GND", g, U_)             # V33 LDO output
    for k in range(5, 11):                               # V33 44, DVDD33 55, AVDD33 73/65/12/27
        cap(f"C{c+k}", "100nF", V3, "GND", g, U_)
    for k in range(11, 20):                              # FB 39 / DVDD12 28/47, AVDD12 4, VP12 x5
        cap(f"C{c+k}", "100nF", V12, "GND", g, U_)
    cap(f"C{c+20}", "10uF", V12, "GND", g, U_)
    cap(f"C{c+21}", "10uF", V12, "GND", g, U_)
    cap(f"C{c+22}", "18pF", f"{H}_XI", "GND", g)
    cap(f"C{c+23}", "18pF", f"{H}_XO", "GND", g)
    cap(f"C{c+24}", "1uF", f"{H}_RESETJ", "GND", g)
    comp(f"L{base+1}", value="4.7uH", mpn="NR3015T4R7M", price_usd=0.05, symbol="Device:L",
         footprint="Inductor_SMD:L_Taiyo-Yuden_NR-30xx", group=g, pins={"1": f"{H}_SW", "2": V12})
    comp(f"Y{base+1}", value="25MHz", mpn="X322525MOB4SI", lcsc="C9006", price_usd=0.067, symbol="Device:Crystal_GND24",
         footprint="Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", group=g, spice=False, height_mm=0.8,
         pins={"1": f"{H}_XI", "2": "GND", "3": f"{H}_XO", "4": "GND"})
    r = base
    res(f"R{r+1}", "20k", f"{H}_RTERM", "GND", g)
    res(f"R{r+2}", "10k", V3, f"{H}_CHIPEN", g)
    res(f"R{r+3}", "10k", V3, f"{H}_RESETJ", g)
    res(f"R{r+4}", "10k", f"{H}_PGANG", "GND", g)
    res(f"R{r+5}", "10k", f"{H}_PSWPOL", "GND", g)
    res(f"R{r+6}", "47k", vbus_src, f"{H}_VBUSDET", g)
    res(f"R{r+7}", "100k", f"{H}_VBUSDET", "GND", g)
    for k, i in enumerate(sorted(ovcur_ext)):
        res(f"R{r+8+k}", "10k", V3, f"{H}_OVCUR{i}", g)


def ac_caps(base, g, pairs):
    """pairs: list of (hub_p, hub_n, far_p, far_n): 100 nF at the transmitter."""
    k = base
    for hp, hn, fp_, fn in pairs:
        for a, b in ((hp, fp_), (hn, fn)):
            k += 1
            comp(f"C{k}", value="100nF", mpn="CL05B104KO5NNNC", lcsc="C1525", price_usd=0.003, symbol="Device:C",
                 footprint="Capacitor_SMD:C_0402_1005Metric", group=g, voltage_rating_v=16, pins={"1": a, "2": b})


def ss_esd(ref, g, n1, n2, n3, n4):
    comp(ref, value="TPD4E02B04", mpn="TPD4E02B04DQAR", lcsc="C106794", price_usd=0.22,
         symbol="Power_Protection:TPD4E02B04DQA", footprint="Package_SON:USON-10_2.5x1.0mm_P0.5mm", group=g,
         spice=False, pins={"1": n1, "2": n2, "4": n3, "5": n4, "3": "GND", "8": "GND"}, nc=["6", "7", "9", "10"])


def d_esd(ref, g, dp, dm):
    comp(ref, value="PUSB3FR4Z", mpn="PUSB3FR4Z-ES", lcsc="C41429303", price_usd=0.0437,
         symbol="Power_Protection:TPD4E05U06DQA", footprint="Package_SON:USON-10_2.5x1.0mm_P0.5mm", group=g,
         spice=False, pins={"1": dp, "2": dm, "3": "GND", "8": "GND"}, nc=["4", "5", "6", "7", "9", "10"])


def usbc(ref, g, pre, vbus, cc1, cc2):
    pins = {p: "GND" for p in ("A1", "A12", "B1", "B12", "S1")}
    pins.update({p: vbus for p in ("A4", "A9", "B4", "B9")})
    pins.update({"A5": cc1, "B5": cc2, "A6": f"{pre}_DP", "B6": f"{pre}_DP", "A7": f"{pre}_DM", "B7": f"{pre}_DM",
                 "A2": f"{pre}_TX1_P", "A3": f"{pre}_TX1_N", "B11": f"{pre}_RX1_P", "B10": f"{pre}_RX1_N",
                 "B2": f"{pre}_TX2_P", "B3": f"{pre}_TX2_N", "A11": f"{pre}_RX2_P", "A10": f"{pre}_RX2_N"})
    comp(ref, value="USB-C 24P MM", mpn="HC-TYPE-C-24P-MM-1.4-5A-12", lcsc="C5307768", price_usd=0.6624,
         symbol="Connector:USB_C_Receptacle", footprint="kb_hub:USB_C_Receptacle_HCTL_HC-TYPE-C-24P-MM-1.4",
         group=g, spice=False, height_mm=3.3, pins=pins, nc=["A8", "B8"])


def usba(ref, g, pre, vbus):
    comp(ref, value="USB3-A", mpn="HC-ST-003-01-Z", lcsc="C2845331", price_usd=0.1078, symbol="Connector:USB3_A",
         footprint="kb_hub:USB3_A_HCTL_HC-ST-003-01-Z_Horizontal", group=g, spice=False, height_mm=7.0,
         pins={"1": vbus, "2": f"{pre}_DM", "3": f"{pre}_DP", "4": "GND", "5": f"{pre}_SSRX_N", "6": f"{pre}_SSRX_P",
               "7": "GND", "8": f"{pre}_SSTX_N", "9": f"{pre}_SSTX_P", "10": "GND"})


def mux3212(ref, g, pre, orient):
    comp(ref, value="HD3SS3212", mpn="HD3SS3212RKSR", lcsc="C129320", price_usd=1.03,
         datasheet="notes/datasheets/hd3ss3212.md", symbol="kb_hub:HD3SS3212",
         footprint="kb_hub:VQFN-20-1EP_2.5x4.5mm_P0.5mm_EP1.0x3.0mm", group=g, spice=False,
         pins={"2": "GND", "3": f"{pre}_TX_P", "4": f"{pre}_TX_N", "7": f"{pre}_RX_P", "8": f"{pre}_RX_N", "9": orient,
               "19": f"{pre}_TX1_P", "18": f"{pre}_TX1_N", "17": f"{pre}_RX1_P", "16": f"{pre}_RX1_N",
               "15": f"{pre}_TX2_P", "14": f"{pre}_TX2_N", "13": f"{pre}_RX2_P", "12": f"{pre}_RX2_N",
               "6": "3V3_D", "5": "GND", "11": "GND", "20": "GND", "21": "GND"}, nc=["1", "10"])


def emit_components():
    L.append("components:")
    # ------------------------------------------------ hub 1: US = host (via HD3SS3220), DS1 C1, DS2 C2, DS3 A1, DS4 hub2
    L.append("  # ---- hub 1 (GL3523, upstream from the host Type-C mux U301)")
    hub(1, 100, "VBUS_HOST",
        {0: ("HUB1_US_TX_P", "HUB1_US_TX_N", "HOST_RX_P", "HOST_RX_N", "HOST_DP", "HOST_DM"),
         1: ("HUB1_DS1_TX_P", "HUB1_DS1_TX_N", "C1_RX_P", "C1_RX_N", "C1_DP", "C1_DM"),
         2: ("HUB1_DS2_TX_P", "HUB1_DS2_TX_N", "C2_RX_P", "C2_RX_N", "C2_DP", "C2_DM"),
         3: ("HUB1_DS3_TX_P", "HUB1_DS3_TX_N", "A1_SSRX_P", "A1_SSRX_N", "A1_DP", "A1_DM"),
         4: ("HUB1_DS4_TX_P", "HUB1_DS4_TX_N", "HUB1_DS4_RX_P", "HUB1_DS4_RX_N", "HUB2_DP", "HUB2_DM")},
        pwren={1, 2, 3}, ovcur_ext={1, 2, 3})
    ac_caps(130, "hub1", [("HUB1_US_TX_P", "HUB1_US_TX_N", "HOST_TX_P", "HOST_TX_N"),
                          ("HUB1_DS1_TX_P", "HUB1_DS1_TX_N", "C1_TX_P", "C1_TX_N"),
                          ("HUB1_DS2_TX_P", "HUB1_DS2_TX_N", "C2_TX_P", "C2_TX_N"),
                          ("HUB1_DS3_TX_P", "HUB1_DS3_TX_N", "A1_SSTX_P", "A1_SSTX_N"),
                          ("HUB1_DS4_TX_P", "HUB1_DS4_TX_N", "HUB2_US_RX_P", "HUB2_US_RX_N")])
    # ------------------------------------------------ hub 2: US = hub1 DS4, DS1 A2, DS2 audio bridge (HS), DS3 RP2040 (FS)
    L.append("  # ---- hub 2 (GL3523, cascaded on hub 1 DS4)")
    hub(2, 150, "5V_SYS",
        {0: ("HUB2_US_TX_P", "HUB2_US_TX_N", "HUB2_US_RX_P", "HUB2_US_RX_N", "HUB2_DP", "HUB2_DM"),
         1: ("HUB2_DS1_TX_P", "HUB2_DS1_TX_N", "A2_SSRX_P", "A2_SSRX_N", "A2_DP", "A2_DM"),
         2: (None, None, None, None, "AUD_DP", "AUD_DM"),
         3: (None, None, None, None, "MCU_DP", "MCU_DM"),
         4: (None,) * 6},
        pwren={1}, ovcur_ext={1})
    ac_caps(180, "hub2", [("HUB2_US_TX_P", "HUB2_US_TX_N", "HUB1_DS4_RX_P", "HUB1_DS4_RX_N"),
                          ("HUB2_DS1_TX_P", "HUB2_DS1_TX_N", "A2_SSTX_P", "A2_SSTX_N")])
    # ------------------------------------------------ host port (back): HD3SS3220 UFP, GPIO mode
    L.append("  # ---- host USB-C (back edge): HD3SS3220 UFP + SS orientation mux, GPIO mode (ADDR open, PORT low)")
    g = "usb_host"
    comp("U301", value="HD3SS3220", mpn="HD3SS3220IRNHR", lcsc="C701817", price_usd=1.09,
         datasheet="notes/datasheets/hd3ss3220.md", symbol="kb_hub:HD3SS3220",
         footprint="kb_hub:WQFN-30-1EP_2.5x4.5mm_P0.4mm_EP1.2x3.2mm", group=g, spice=False,
         pins={"2": "HOST_CC1", "1": "HOST_CC2", "5": "HOST_VBUSDET", "29": "GND", "12": "GND", "4": "GND", "3": "GND",
               "25": "HOST_RP_OUT1", "26": "HOST_RP_OUT2",
               "6": "HOST_TX_P", "7": "HOST_TX_N", "9": "HOST_RX_P", "10": "HOST_RX_N",
               "17": "HOST_TX1_P", "16": "HOST_TX1_N", "15": "HOST_RX1_P", "14": "HOST_RX1_N",
               "21": "HOST_TX2_P", "20": "HOST_TX2_N", "19": "HOST_RX2_P", "18": "HOST_RX2_N",
               "30": "5V_SYS", "8": "3V3_D", "13": "GND", "28": "GND", "31": "GND"},
         nc=["11", "22", "23", "24", "27"])
    cap("C301", "100nF", "5V_SYS", "GND", g, "U301")
    cap("C302", "100nF", "3V3_D", "GND", g, "U301")
    comp("R301", value="910k", mpn="0402 1% 910k", price_usd=0.002, symbol="Device:R",
         footprint="Resistor_SMD:R_0402_1005Metric", group=g, pins={"1": "VBUS_HOST", "2": "HOST_VBUSDET"})
    res("R302", "200k", "3V3_D", "HOST_RP_OUT1", g)
    res("R303", "200k", "3V3_D", "HOST_RP_OUT2", g)
    usbc("J301", g, "HOST", "VBUS_HOST", "HOST_CC1", "HOST_CC2")
    ss_esd("D301", g, "HOST_TX1_P", "HOST_TX1_N", "HOST_RX1_P", "HOST_RX1_N")
    ss_esd("D302", g, "HOST_TX2_P", "HOST_TX2_N", "HOST_RX2_P", "HOST_RX2_N")
    d_esd("D303", g, "HOST_DP", "HOST_DM")
    # ------------------------------------------------ front USB-C1/C2 with HD3SS3212 orientation muxes
    L.append("  # ---- front USB-C1 / C2: HD3SS3212 orientation mux (SEL from pass B: C1_ORIENT / C2_ORIENT, L = CC1 = TX1/RX1)")
    g = "usb_c"
    for i, (mux, j, e) in enumerate((("U311", "J311", 311), ("U312", "J312", 314)), 1):
        pre = f"C{i}"
        mux3212(mux, g, pre, f"{pre}_ORIENT")
        cap(f"C{310+i}", "100nF", "3V3_D", "GND", g, mux)
        usbc(j, g, pre, f"VBUS_{pre}", f"CC1_{pre}", f"CC2_{pre}")
        ss_esd(f"D{e}", g, f"{pre}_TX1_P", f"{pre}_TX1_N", f"{pre}_RX1_P", f"{pre}_RX1_N")
        ss_esd(f"D{e+1}", g, f"{pre}_TX2_P", f"{pre}_TX2_N", f"{pre}_RX2_P", f"{pre}_RX2_N")
        d_esd(f"D{e+2}", g, f"{pre}_DP", f"{pre}_DM")
    # ------------------------------------------------ front USB-A1/A2
    L.append("  # ---- front USB-A1 (hub 1 DS3) / USB-A2 (hub 2 DS1)")
    g = "usb_a"
    for i, (j, e, d) in enumerate((("J321", "D321", "D323"), ("J322", "D322", "D324")), 1):
        pre = f"A{i}"
        usba(j, g, pre, f"VBUS_{pre}")
        ss_esd(e, g, f"{pre}_SSTX_P", f"{pre}_SSTX_N", f"{pre}_SSRX_P", f"{pre}_SSRX_N")
        d_esd(d, g, f"{pre}_DP", f"{pre}_DM")
    # ------------------------------------------------ keyboard MCU
    L.append("  # ---- keyboard MCU (RP2040 minimal design, hardware design guide; GPIO map notes/parts-keyboard.md)")
    g = "mcu"
    gp = {0: 2, 1: 3, 2: 4, 3: 5, 4: 6, 5: 7, 6: 8, 7: 9, 8: 11, 9: 12, 10: 13, 11: 14, 12: 15, 13: 16, 14: 17, 15: 18,
          16: 27, 17: 28, 18: 29, 19: 30, 20: 31, 21: 32, 22: 34, 23: 35, 24: 36, 25: 37, 26: 38, 27: 39, 28: 40, 29: 41}
    gpnet = {i: f"ROW{i}" for i in range(12)}
    gpnet.update({12 + i: f"COL{i}" for i in range(11)})
    gpnet.update({23: "ENC_A", 24: "ENC_B", 25: "ENC_SW", 26: "JACK_DET", 27: "HOST_RP_OUT1", 28: "HOST_RP_OUT2",
                  29: "PD_STAT"})
    pins = {str(gp[i]): gpnet[i] for i in range(30)}
    pins.update({p: "3V3_D" for p in ("1", "10", "22", "33", "42", "49", "44", "48", "43")})
    pins.update({"23": "MCU_1V1", "50": "MCU_1V1", "45": "MCU_1V1", "57": "GND", "19": "GND",
                 "20": "MCU_XIN", "21": "MCU_XOUT", "24": "SWCLK", "25": "SWDIO", "26": "MCU_RUN",
                 "46": "RP_DM", "47": "RP_DP",
                 "51": "QSPI_SD3", "52": "QSPI_SCLK", "53": "QSPI_SD0", "54": "QSPI_SD2", "55": "QSPI_SD1",
                 "56": "QSPI_SS"})
    assert len(pins) == 57
    comp("U401", value="RP2040", mpn="RP2040", lcsc="C2040", price_usd=0.99, datasheet="notes/datasheets/rp2040.md",
         symbol="MCU_RaspberryPi:RP2040", footprint="Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm",
         group=g, spice=False, pins=pins)
    comp("U402", value="W25Q16JV", mpn="W25Q16JVSNIQ", lcsc="C2456211", price_usd=0.80,
         symbol="Memory_Flash:W25Q128JVS", footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", group=g, spice=False,
         pins={"1": "QSPI_SS", "2": "QSPI_SD1", "3": "QSPI_SD2", "4": "GND", "5": "QSPI_SD0", "6": "QSPI_SCLK",
               "7": "QSPI_SD3", "8": "3V3_D"})
    comp("Y401", value="12MHz", mpn="CJ13-120001210B20", lcsc="C2875246", price_usd=0.08,
         symbol="Device:Crystal_GND24", footprint="Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", group=g, spice=False,
         height_mm=0.8, pins={"1": "MCU_XIN", "2": "GND", "3": "MCU_XOUT_X", "4": "GND"})
    cap("C401", "18pF", "MCU_XIN", "GND", g)
    cap("C402", "18pF", "MCU_XOUT_X", "GND", g)
    res("R401", "1k", "MCU_XOUT", "MCU_XOUT_X", g)
    for k in range(6):
        cap(f"C{403+k}", "100nF", "3V3_D", "GND", g, "U401")      # IOVDD 1, 10, 22, 33, 42, 49
    cap("C409", "100nF", "3V3_D", "GND", g, "U401")              # USB_VDD 48
    cap("C410", "100nF", "3V3_D", "GND", g, "U401")              # ADC_AVDD 43
    cap("C411", "1uF", "3V3_D", "GND", g, "U401")                # VREG_IN 44
    cap("C412", "1uF", "MCU_1V1", "GND", g, "U401")              # VREG_VOUT 45
    cap("C413", "100nF", "MCU_1V1", "GND", g, "U401")            # DVDD 23
    cap("C414", "100nF", "MCU_1V1", "GND", g, "U401")            # DVDD 50
    cap("C415", "100nF", "3V3_D", "GND", g, "U402")
    cap("C416", "10uF", "3V3_D", "GND", g, "U401")
    comp("R402", value="27", mpn="0402 1% 27R", price_usd=0.002, symbol="Device:R",
         footprint="Resistor_SMD:R_0402_1005Metric", group=g, pins={"1": "RP_DP", "2": "MCU_DP"})
    comp("R403", value="27", mpn="0402 1% 27R", price_usd=0.002, symbol="Device:R",
         footprint="Resistor_SMD:R_0402_1005Metric", group=g, pins={"1": "RP_DM", "2": "MCU_DM"})
    res("R404", "1k", "QSPI_SS", "BOOT_N", g)
    res("R405", "10k", "3V3_D", "MCU_RUN", g)
    comp("SW401", value="BOOTSEL", mpn="TS-1187A-B-A-B", price_usd=0.03, symbol="Switch:SW_Push",
         footprint="Button_Switch_SMD:SW_SPST_TL3342", group=g, spice=False, pins={"1": "BOOT_N", "2": "GND"})
    comp("SW402", value="RESET", mpn="TS-1187A-B-A-B", price_usd=0.03, symbol="Switch:SW_Push",
         footprint="Button_Switch_SMD:SW_SPST_TL3342", group=g, spice=False, pins={"1": "MCU_RUN", "2": "GND"})
    for k, n in enumerate(("SWCLK", "SWDIO", "GND"), 1):
        comp(f"TP40{k}", value=n, symbol="Connector:TestPoint", footprint="TestPoint:TestPoint_Pad_D1.0mm", group=g,
             spice=False, pins={"1": n})
    # ------------------------------------------------ encoder
    L.append("  # ---- volume encoder with push (EC11E, vertical); 10 k pull-ups, 10 k x 10 nF = 100 us RC on A/B")
    g = "ui"
    comp("SW501", value="EC11E153440D", mpn="EC11E153440D", lcsc="C370992", price_usd=1.22,
         symbol="Device:RotaryEncoder_Switch", footprint="Rotary_Encoder:RotaryEncoder_Alps_EC11E-Switch_Vertical_H20mm",
         group=g, spice=False, height_mm=7.0,
         pins={"A": "ENC_A", "B": "ENC_B", "C": "GND", "S1": "ENC_SW", "S2": "GND"})
    res("R501", "10k", "3V3_D", "ENC_A", g)
    res("R502", "10k", "3V3_D", "ENC_B", g)
    res("R503", "10k", "3V3_D", "ENC_SW", g)
    cap("C501", "10nF", "ENC_A", "GND", g)
    cap("C502", "10nF", "ENC_B", "GND", g)
    # ------------------------------------------------ matrix
    keys, _ = matrix()
    L.append("  # ---- key matrix: 104 Kailh hot-swap sockets + 1N4148W, back face, ANSI 104 at 19.05 mm (R1, R2, R27).")
    L.append("  #      12 x 11 folded at 11 u (notes/parts-keyboard.md); COL -> socket -> diode anode, cathode -> ROW (COL2ROW)")
    for k in keys:
        n = k["n"]
        L.append(f"  # {k['label']}: row {k['row']} col {k['col']}")
        comp(f"SW{n}", value=f"MX {k['label']}", mpn="CPG151101S11", lcsc="C2803348", price_usd=0.075,
             symbol="Switch:SW_Push", footprint=f"kb_keys:{k['fp']}", group="matrix", spice=False, height_mm=1.85,
             placement={"x": k["x"], "y": k["y"], "rot": 0, "fixed": True},
             pins={"1": f"COL{k['col']}", "2": f"K{n}"})
        comp(f"D{n}", value="1N4148W", mpn="1N4148W", lcsc="C81598", price_usd=0.012, symbol="Diode:1N4148W",
             footprint="kb_keys:D_SOD-123_Back", group="matrix", spice=False, height_mm=1.2,
             placement={"x": k["x"], "y": round(k["y"] + DIODE_DY, 3), "rot": 0, "fixed": True},
             pins={"1": f"ROW{k['row']}", "2": f"K{n}"})


def emit_nets():
    L.append("nets:")
    def n(name, **kw):
        L.append(f"  {name}: {{" + ", ".join(f"{k}: {str(v).lower() if isinstance(v, bool) else v}" for k, v in kw.items()) + "}")
    L.append("  # rails (5V_SYS and 3V3_D are made by pass B; VBUS_HOST enters from the host cable)")
    n("GND", **{"class": "ground"})
    n("5V_SYS", **{"class": "power", "current_a": 1.0})
    n("3V3_D", **{"class": "power", "current_a": 0.3})
    n("VBUS_HOST", **{"class": "power", "current_a": 3.0, "external_supply": True})
    for p, a in (("C1", 3.0), ("C2", 3.0), ("A1", 1.5), ("A2", 1.5)):
        n(f"VBUS_{p}", **{"class": "power", "current_a": a, "allow_single": True})
    for h in (1, 2):
        n(f"HUB{h}_1V2", **{"class": "power", "current_a": 0.6})
        n(f"HUB{h}_3V3", **{"class": "power", "current_a": 0.25})
        n(f"HUB{h}_SW", **{"class": "switching", "current_a": 0.8})
        n(f"HUB{h}_XI", **{"class": "clock"})
        n(f"HUB{h}_XO", **{"class": "clock"})
    n("MCU_1V1", **{"class": "power", "current_a": 0.05})
    for x in ("MCU_XIN", "MCU_XOUT", "MCU_XOUT_X"):
        n(x, **{"class": "clock"})
    L.append("  # interface nets completed by pass B (power/PD) and pass C (audio)")
    for x in ("CC1_C1", "CC2_C1", "CC1_C2", "CC2_C2", "C1_ORIENT", "C2_ORIENT", "HUB1_PWREN1", "HUB1_PWREN2",
              "HUB1_PWREN3", "HUB2_PWREN1", "JACK_DET", "PD_STAT"):
        n(x, **{"class": "signal", "allow_single": True})
    L.append("  # SuperSpeed pairs: 90 ohm differential (R5); impedance_ohm is single-ended in hwflow, 45 = odd-mode half of 90")
    ss = []
    for pre in ("HOST", "C1", "C2"):
        ss += [f"{pre}_{s}_{pn}" for s in ("TX", "RX", "TX1", "RX1", "TX2", "RX2") for pn in "PN"]
    ss += [f"{a}_{s}_{pn}" for a in ("A1", "A2") for s in ("SSTX", "SSRX") for pn in "PN"]
    ss += [f"HUB1_{p}_TX_{pn}" for p in ("US", "DS1", "DS2", "DS3", "DS4") for pn in "PN"]
    ss += [f"HUB1_DS4_RX_{pn}" for pn in "PN"] + [f"HUB2_{p}_{pn}" for p in ("US_TX", "US_RX", "DS1_TX") for pn in "PN"]
    for x in ss:
        n(x, **{"class": "high_speed", "edge_ns": 0.05, "impedance_ohm": 45})
    L.append("  # USB 2.0 pairs: 90 ohm differential")
    for pre in ("HOST", "C1", "C2", "A1", "A2", "HUB2", "MCU", "RP"):
        for s in ("DP", "DM"):
            n(f"{pre}_{s}", **{"class": "high_speed", "edge_ns": 0.5, "impedance_ohm": 45})
    for s in ("DP", "DM"):
        n(f"AUD_{s}", **{"class": "high_speed", "edge_ns": 0.5, "impedance_ohm": 45, "allow_single": True})
    L.append("  # key matrix: 2 mA slow-slew GPIO (notes/parts-keyboard.md)")
    for i in range(12):
        n(f"ROW{i}", **{"class": "signal", "edge_ns": 20})
    for i in range(11):
        n(f"COL{i}", **{"class": "signal", "edge_ns": 20})


if __name__ == "__main__":
    if "--footprints" in sys.argv:
        write_footprints()
    if "--map" in sys.argv:
        keys, count = matrix()
        for k in keys:
            print(k["n"], k["label"], k["row"], k["col"], k["x"], k["y"], k["fp"])
        print({r: count[r] for r in sorted(count)})
        sys.exit()
    emit_nets()
    emit_components()
    text = "\n".join(L) + "\n"
    if "--apply" in sys.argv:   # one-time paste: replace everything from `nets:` on in design.yaml
        d = PROJ / "design.yaml"
        src = d.read_text()
        i = src.find("\nnets:\n")
        head = src if i < 0 else src[:i + 1]
        d.write_text(head.rstrip("\n") + "\n\n" + text)
    else:
        print(text)
