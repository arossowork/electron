"""Stage 4 pass B helper: POWER / USB PD blocks (refs 600-899), their nets, the sim testbench and the budgets.

Writes three marked blocks into design.yaml (idempotent; re-run after editing this file):
  nets       between '  # ==== PASS B NETS BEGIN' / END  (inserted before 'components:')
  components between '  # ==== PASS B COMPONENTS BEGIN' / END (end of components)
  sim + budgets: everything from '# ==== PASS B SIM BEGIN' to '# ==== PASS B SIM END' (top level, end of file)
Design calculations: notes/circuit.md, pass B section.
Run: uv run python projects/mechanical-keyboard-usb-audio-hub/tools/gen_pass_b.py [--apply]
"""
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
LIB = "lib/spice/power_fit.lib"
OUT = []          # (comment or (ref, dict))


def q(v):
    return json.dumps(v)


def emit(ref, d):
    OUT.append((ref, d))


def note(text):
    OUT.append(("#", text))


R0402 = "Resistor_SMD:R_0402_1005Metric"
C0402 = "Capacitor_SMD:C_0402_1005Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"
C1206 = "Capacitor_SMD:C_1206_3216Metric"


def R(ref, val, a, b, grp, tol=None, mpn=None, fp=R0402, price=0.002, lcsc=None, spice=None, **kw):
    d = {"value": val, "mpn": mpn or f"0402 {'0.5%' if tol == 0.005 else '1%'} {val}", "price_usd": price}
    if lcsc:
        d["lcsc"] = lcsc
    d.update({"symbol": "Device:R", "footprint": fp, "group": grp})
    if tol:
        d["tolerance"] = tol
    if spice is not None:
        d["spice"] = spice
    d.update(kw)
    d["pins"] = {"1": a, "2": b}
    emit(ref, d)


CAPS = {  # value, voltage -> (mpn, lcsc, price, footprint)
    ("100nF", 16): ("CL05B104KO5NNNC", "C1525", 0.003, C0402),
    ("100nF", 50): ("CL05B104KB54PNC", None, 0.004, C0402),
    ("10nF", 50): ("CL05B103KB5NNNC", "C15195", 0.002, C0402),
    ("3.3nF", 50): ("CL05B332KB5NNNC", None, 0.002, C0402),
    ("330pF", 50): ("CL05C331JB5NNNC", None, 0.002, C0402),
    ("22pF", 50): ("CL05C220JB5NNNC", "C1555", 0.002, C0402),
    ("220nF", 16): ("CL05B224KO5NNNC", None, 0.003, C0402),
    ("1uF", 16): ("CL05A105KO5NNNC", "C52923", 0.004, C0402),
    ("1uF", 25): ("CL10A105KA8NNNC", None, 0.006, C0603),
    ("1uF", 50): ("CL10A105KB8NNNC", "C15849", 0.008, C0603),
    ("2.2uF", 16): ("CL05A225MO5NQNC", None, 0.006, C0402),
    ("4.7uF", 50): ("CL31B475KBHNNNE", None, 0.03, C1206),
    ("10uF", 25): ("CL21A106KAYNNNE", "C15850", 0.012, C0805),
    ("22uF", 25): ("CL31A226KAHNNNE", "C12891", 0.0932, C1206),
}


def C(ref, val, a, b, grp, v=16, dec=None, **kw):
    mpn, lcsc, price, fp = CAPS[(val, v)]
    d = {"value": val, "mpn": mpn, "price_usd": price}
    if lcsc:
        d["lcsc"] = lcsc
    d.update({"symbol": "Device:C", "footprint": fp, "group": grp})
    if dec:
        d["decouples"] = dec
    d["voltage_rating_v"] = v
    d.update(kw)
    d["pins"] = {"1": a, "2": b}
    emit(ref, d)


def NFET(ref, g, d, s, grp):
    emit(ref, {"value": "2N7002", "mpn": "2N7002", "lcsc": "C8545", "price_usd": 0.0155,
               "symbol": "Transistor_FET:2N7002", "footprint": "Package_TO_SOT_SMD:SOT-23", "group": grp,
               "spice": {"model": "NMOS_LOGIC", "ports": ["3", "1", "2"]},
               "pins": {"1": g, "2": s, "3": d}})


def PFET_S(ref, g, d, s, grp):
    emit(ref, {"value": "AO3401A", "mpn": "AO3401A", "lcsc": "C15127", "price_usd": 0.02,
               "symbol": "Transistor_FET:AO3401A", "footprint": "Package_TO_SOT_SMD:SOT-23", "group": grp,
               "spice": {"lib": LIB, "subckt": "AO3401A_FIT", "ports": ["3", "1", "2"], "source": "fit"},
               "pins": {"1": g, "2": s, "3": d}})


def SO8FET(ref, kind, g, d, s, grp):
    if kind == "P":
        e = {"value": "AO4407A", "mpn": "AO4407A", "lcsc": "C16072", "price_usd": 0.2433,
             "symbol": "Transistor_FET:IRF7404", "sub": "AO4407A_FIT"}
    else:
        e = {"value": "AO4406A", "mpn": "AO4406A", "price_usd": 0.15, "symbol": "Transistor_FET:IRF7403",
             "sub": "AO4406A_FIT"}
    sub = e.pop("sub")
    e.update({"footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "group": grp, "theta_ja": 75, "tj_max_c": 150,
              "spice": {"lib": LIB, "subckt": sub, "ports": ["5", "4", "1"], "source": "fit"},
              "pins": {"1": s, "2": s, "3": s, "4": g, "5": d, "6": d, "7": d, "8": d}})
    emit(ref, e)


def ZENER(ref, val, k, a, grp, model=None):
    d = {"value": val, "mpn": val, "price_usd": 0.01, "symbol": "Device:D_Zener",
         "footprint": "Diode_SMD:D_SOD-123", "group": grp}
    d["spice"] = {"model": model, "lib": LIB, "source": "fit"} if model else False
    d["pins"] = {"1": k, "2": a}
    emit(ref, d)


def TVS(ref, mpn, a, grp, fp, price, lcsc=None, b="GND"):
    d = {"value": mpn, "mpn": mpn, "price_usd": price}
    if lcsc:
        d["lcsc"] = lcsc
    d.update({"symbol": "Device:D_TVS", "footprint": fp, "group": grp, "spice": False, "pins": {"1": a, "2": b}})
    emit(ref, d)


def BUCK_LM(ref, grp, vin, en, rt, fb, bias, sw, cb, vcc, eff5, eff9, theta=35):
    emit(ref, {"value": "LM61460AFS", "mpn": "LM61460AFSQRJRRQ1", "lcsc": "C2876600", "price_usd": 1.51,
               "datasheet": "notes/datasheets/lm61460.md", "symbol": "kb_power:LM61460AFS",
               "footprint": "kb_power:VQFN-HR-14_RJR0014A_4.0x3.5mm", "group": grp, "height_mm": 1.0,
               "theta_ja": theta, "tj_max_c": 150,
               "spice": {"lib": LIB, "subckt": "BUCK_FB", "ports": ["8", "10", "4", "7", "3"], "source": "fit",
                         "params": {"VREF": 1.0, "EFF5": eff5, "EFF9": eff9, "ILIM": 8, "IQ": 0.6e-6,
                                    "VEN": 0.91, "THETA": theta, "VUV": 3.5}},
               "pins": {"8": vin, "12": vin, "7": en, "6": rt, "4": fb, "1": bias, "10": sw, "13": cb, "14": cb,
                        "2": vcc, "3": "GND", "9": "GND", "11": "GND"},
               "nc": ["5"]})


# =====================================================================================================
note("==== pass B: POWER / USB PD (notes/circuit.md pass B; refs 600-899, pass C uses >= 900)")
# ---------------------------------------------------------------- PD input (back): USB-C power only
G = "pd_sink"
note("---- PD input: USB-C (power only) -> CH224D 20 V sink -> AO4407A back-to-back -> VIN20 (R9)")
emit("J600", {"value": "USB-C 24P MM (PD in)", "mpn": "HC-TYPE-C-24P-MM-1.4-5A-12", "lcsc": "C5307768",
              "price_usd": 0.6624, "symbol": "Connector:USB_C_Receptacle",
              "footprint": "kb_hub:USB_C_Receptacle_HCTL_HC-TYPE-C-24P-MM-1.4", "group": G, "height_mm": 3.3,
              "spice": False,
              "pins": {"A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND", "S1": "GND",
                       "A4": "PDIN_VBUS", "A9": "PDIN_VBUS", "B4": "PDIN_VBUS", "B9": "PDIN_VBUS",
                       "A5": "PDIN_CC1", "B5": "PDIN_CC2", "A6": "PDIN_DP", "B6": "PDIN_DP", "A7": "PDIN_DM",
                       "B7": "PDIN_DM"},
              "nc": ["A2", "A3", "B2", "B3", "A10", "A11", "B10", "B11", "A8", "B8"]})
emit("U600", {"value": "CH224D", "mpn": "CH224D", "lcsc": "C3975094", "price_usd": 0.2403,
              "datasheet": "notes/datasheets/ch224d.md", "symbol": "kb_power:CH224D",
              "footprint": "Package_DFN_QFN:QFN-20-1EP_3x3mm_P0.4mm_EP1.65x1.65mm", "group": G, "spice": False,
              "pins": {"2": "CH224_VBUS", "7": "CH224_VDD", "11": "PDIN_CC1", "10": "PDIN_CC2", "8": "PDIN_DP",
                       "9": "PDIN_DM", "6": "GND", "14": "GND", "15": "GND", "21": "GND"},
              "nc": ["1", "19", "13", "12", "5", "3", "4", "16", "17", "18", "20"]})
R("R600", "100", "PDIN_VBUS", "CH224_VBUS", G, mpn="0603 1% 100R 0.25W", fp="Resistor_SMD:R_0603_1608Metric")
ZENER("D600", "BZT52C22", "CH224_VBUS", "GND", G)
C("C600", "1uF", "CH224_VBUS", "GND", G, v=50, dec="U600")
C("C601", "1uF", "CH224_VDD", "GND", G, v=16, dec="U600")
R("R601", "5.1k", "PDIN_CC1", "GND", G)
R("R602", "5.1k", "PDIN_CC2", "GND", G)
TVS("D601", "PESD12VS1UB", "PDIN_CC1", G, "Diode_SMD:D_SOD-523", 0.03)
TVS("D602", "PESD12VS1UB", "PDIN_CC2", G, "Diode_SMD:D_SOD-523", 0.03)
TVS("D603", "SMBJ22A", "PDIN_VBUS", G, "Diode_SMD:D_SMB", 0.0314, "C284010")
C("C602", "100nF", "PDIN_VBUS", "GND", G, v=50)
SO8FET("M600", "P", "SINK_G", "PDIN_VBUS", "SINK_S", G)
SO8FET("M601", "P", "SINK_G", "VIN20", "SINK_S", G)
R("R603", "10k", "SINK_G", "GND", G)
R("R604", "100k", "SINK_S", "SINK_G", G)
ZENER("D604", "BZT52C12", "SINK_S", "SINK_G", G, "DZ12")

# ---------------------------------------------------------------- mode logic (PD mode / host Rp level)
G = "pwr_ctl"
note("---- mode logic: PDS_G = VIN20 > ~14 V, 30 ms on-delay, fast off; PDMODE_N / PD_STAT 3.3 V logic; HOST15/HOST30")
ZENER("D630", "BZT52C12", "VIN20", "PDS_A", G, "DZ12")
R("R630", "10k", "PDS_A", "GND", G)
R("R631", "100k", "PDS_A", "PDS_G", G)
C("C630", "1uF", "PDS_G", "GND", G, v=16)
emit("D631", {"value": "1N4148W", "mpn": "1N4148W", "lcsc": "C81598", "price_usd": 0.012,
              "symbol": "Diode:1N4148W", "footprint": "Diode_SMD:D_SOD-123", "group": G,
              "pins": {"1": "PDS_A", "2": "PDS_G"}})
NFET("M630", "PDS_G", "PDMODE_N", "GND", G)
R("R632", "100k", "3V3_D", "PDMODE_N", G)
NFET("M631", "PDMODE_N", "PD_STAT", "GND", G)
R("R633", "100k", "3V3_D", "PD_STAT", G)
R("R634", "100k", "VIN20", "BUCK_EN", G)
R("R635", "9.09k", "BUCK_EN", "GND", G)
NFET("M632", "HOST_RP_OUT1", "HOST15", "GND", G)
R("R636", "100k", "3V3_D", "HOST15", G)
NFET("M633", "HOST_RP_OUT2", "HOST30", "GND", G)
R("R637", "100k", "3V3_D", "HOST30", G)
R("R638", "100k", "5V_SYS", "RP_G", G)
NFET("M634", "PDMODE_N", "RP_G", "GND", G)

# ---------------------------------------------------------------- host VBUS path
G = "vbus_path"
note("---- host VBUS: SMBJ5.0A, CC TVS, TPS25940A eFuse (ILIM 88.7 k / 53.2 k / 26.4 k), off in PD mode -> 5V_SYS")
TVS("D650", "SMBJ5.0A-TR", "VBUS_HOST", G, "Diode_SMD:D_SMB", 0.197, "C130397")
TVS("D651", "PESD5V0S1UB", "HOST_CC1", G, "Diode_SMD:D_SOD-523", 0.03)
TVS("D652", "PESD5V0S1UB", "HOST_CC2", G, "Diode_SMD:D_SOD-523", 0.03)
emit("U650", {"value": "TPS25940A", "mpn": "TPS25940ARVCR", "lcsc": "C2653873", "price_usd": 1.5094,
              "datasheet": "notes/datasheets/tps25940a.md", "symbol": "kb_power:TPS25940A",
              "footprint": "kb_power:WQFN-20_RVC0020A_4.0x3.0mm_P0.5mm_EP2.6x1.6mm", "group": G,
              "theta_ja": 50, "tj_max_c": 125,
              "spice": {"lib": LIB, "subckt": "EFUSE_FIT", "ports": ["9", "4", "14", "15", "17", "16"],
                        "source": "fit", "params": {"RON": 0.042, "K": 87.5, "IQ": 210e-6, "THETA": 50}},
              "pins": {"9": "VBUS_HOST", "10": "VBUS_HOST", "11": "VBUS_HOST", "12": "VBUS_HOST",
                       "13": "VBUS_HOST", "4": "5V_SYS", "5": "5V_SYS", "6": "5V_SYS", "7": "5V_SYS",
                       "8": "5V_SYS", "14": "EFUSE_EN", "15": "EFUSE_OVP", "1": "GND", "3": "GND",
                       "17": "EFUSE_ILIM", "18": "EFUSE_DVDT", "19": "HOST_IMON", "16": "GND", "21": "GND"},
              "nc": ["2", "20"]})
C("C650", "1uF", "VBUS_HOST", "GND", G, v=25, dec="U650")
C("C651", "100nF", "VBUS_HOST", "GND", G, v=16, dec="U650")
R("R650", "100k", "VBUS_HOST", "EFUSE_EN", G)
R("R651", "30.1k", "EFUSE_EN", "GND", G)
NFET("M650", "PD_STAT", "EFUSE_EN", "GND", G)
R("R652", "100k", "VBUS_HOST", "EFUSE_OVP", G)
R("R653", "20k", "EFUSE_OVP", "GND", G)
R("R654", "88.7k", "EFUSE_ILIM", "GND", G)
R("R655", "133k", "EFUSE_ILIM", "EFUSE_IL15", G)
NFET("M651", "HOST15", "EFUSE_IL15", "GND", G)
R("R656", "52.3k", "EFUSE_ILIM", "EFUSE_IL30", G)
NFET("M652", "HOST30", "EFUSE_IL30", "GND", G)
R("R657", "10k", "HOST_IMON", "GND", G)
C("C652", "10nF", "EFUSE_DVDT", "GND", G, v=50)
for i, ref in enumerate(["C653", "C654", "C655"]):
    C(ref, "22uF", "5V_SYS", "GND", G, v=25, dec="U650")
C("C656", "100nF", "5V_SYS", "GND", G, v=16, dec="U650")

# ---------------------------------------------------------------- 5V_SYS buck
G = "buck_5v"
note("---- 5V_SYS buck: AO4407A input isolation (no bus-mode back-feed into VIN20), LM61460AFS 5.1 V 400 kHz FPWM")
SO8FET("M680", "P", "B5_G", "VIN20", "BUCK5_VIN", G)
R("R680", "100k", "BUCK5_VIN", "B5_G", G)
ZENER("D680", "BZT52C12", "BUCK5_VIN", "B5_G", G, "DZ12")
R("R681", "47k", "B5_G", "B5_GD", G)
NFET("M681", "PDS_G", "B5_GD", "GND", G)
BUCK_LM("U680", G, "BUCK5_VIN", "BUCK_EN", "B5_RT", "B5_FB", "5V_SYS", "B5_SW", "B5_CB", "B5_VCC", 0.95, 0.95)
R("R682", "33.2k", "B5_RT", "GND", G)
R("R683", "102k", "5V_SYS", "B5_FB", G)
R("R684", "24.9k", "B5_FB", "GND", G)
C("C680", "22pF", "5V_SYS", "B5_FB", G, v=50)
C("C681", "100nF", "B5_CB", "B5_SW", G, v=16)
C("C682", "1uF", "B5_VCC", "GND", G, v=16, dec="U680")
C("C683", "4.7uF", "BUCK5_VIN", "GND", G, v=50, dec="U680")
C("C684", "4.7uF", "BUCK5_VIN", "GND", G, v=50, dec="U680")
C("C685", "100nF", "BUCK5_VIN", "GND", G, v=50, dec="U680")
C("C686", "100nF", "BUCK5_VIN", "GND", G, v=50, dec="U680")
L82 = {"value": "8.2uH", "mpn": "FEXL0660A-8R2M", "lcsc": "C5378547", "price_usd": 1.09, "symbol": "Device:L",
       "footprint": "Inductor_SMD:L_Sunlord_MWSA0605S", "spice": {"lib": LIB, "subckt": "LDCR", "ports": ["1", "2"], "source": "behavioral"}, "height_mm": 6.0}
emit("L680", dict(L82, group=G, pins={"1": "B5_SW", "2": "5V_SYS"}))
for ref in ["C687", "C688", "C689", "C690"]:
    C(ref, "22uF", "5V_SYS", "GND", G, v=25)


# ---------------------------------------------------------------- front USB-C PD source x2
def c_port(n, base, pwren, ovcur):
    G = f"pd_src{n}"
    P = f"C{n}_"
    vb = f"VBUS_C{n}"
    cc1, cc2 = f"CC1_C{n}", f"CC2_C{n}"
    b = base
    note(f"---- USB-C{n} PD source: LM61460AFS (FB driven by IP2736U_EF), AO4406A back-to-back, 10 mohm low-side shunt")
    BUCK_LM(f"U{b}", G, "VIN20", "BUCK_EN", P + "RT", P + "FBN", P + "CONV", P + "SW", P + "CB", P + "VCC",
            0.95, 0.945)
    R(f"R{b}", "33.2k", P + "RT", "GND", G)
    R(f"R{b+1}", "100k", P + "CONV", P + "FBN", G)
    R(f"R{b+2}", "24.9k", P + "FBN", "GND", G)
    C(f"C{b}", "22pF", P + "CONV", P + "FBN", G, v=50)
    C(f"C{b+1}", "100nF", P + "CB", P + "SW", G, v=16)
    C(f"C{b+2}", "1uF", P + "VCC", "GND", G, v=16, dec=f"U{b}")
    C(f"C{b+3}", "4.7uF", "VIN20", "GND", G, v=50, dec=f"U{b}")
    C(f"C{b+4}", "4.7uF", "VIN20", "GND", G, v=50, dec=f"U{b}")
    C(f"C{b+5}", "100nF", "VIN20", "GND", G, v=50, dec=f"U{b}")
    C(f"C{b+6}", "100nF", "VIN20", "GND", G, v=50, dec=f"U{b}")
    emit(f"L{b}", dict(L82, group=G, pins={"1": P + "SW", "2": P + "CONV"}))
    for k in range(4):
        C(f"C{b+7+k}", "22uF", P + "CONV", "GND", G, v=25)
    emit(f"U{b+1}", {"value": "IP2736U_EF", "mpn": "IP2736U_EF", "lcsc": "C42437970", "price_usd": 0.5634,
                     "datasheet": "notes/datasheets/ip2736u.md", "symbol": "kb_power:IP2736U",
                     "footprint": "kb_power:QFN-24-1EP_4x4mm_P0.5mm_EP2.7x2.7mm_IP2736U", "group": G,
                     "theta_ja": 90, "tj_max_c": 150, "spice": False,
                     "thermal_vias": {"pad": "25", "count": 9},
                     "pins": {"24": P + "IPVIN", "1": P + "IPVCC", "5": P + "IPFB", "2": P + "CMPV",
                              "3": P + "CMPI", "19": P + "NTC", "23": P + "NG", "22": P + "VS", "11": P + "RTN",
                              "10": "GND", "17": cc1, "14": cc2, "18": "GND", "25": "GND"},
                     "nc": ["4", "6", "7", "8", "9", "12", "13", "15", "16", "20", "21"]})
    R(f"R{b+3}", "27", P + "CONV", P + "IPVIN", G)
    C(f"C{b+11}", "1uF", P + "IPVIN", "GND", G, v=25, dec=f"U{b+1}")
    C(f"C{b+12}", "2.2uF", P + "IPVCC", "GND", G, v=16, dec=f"U{b+1}")
    R(f"R{b+4}", "1k", P + "IPFB", P + "FBN", G)
    R(f"R{b+5}", "1k", P + "CMPV", P + "CMPVR", G)
    C(f"C{b+14}", "3.3nF", P + "CMPVR", "GND", G, v=50)
    R(f"R{b+6}", "200k", P + "CMPI", P + "CMPIR", G)
    C(f"C{b+15}", "3.3nF", P + "CMPIR", "GND", G, v=50)
    emit(f"TH{b}", {"value": "10k B3380", "mpn": "NCP15XH103F03RC", "lcsc": "C77131", "price_usd": 0.01,
                    "symbol": "Device:Thermistor_NTC", "footprint": R0402, "group": G, "spice": False,
                    "pins": {"1": P + "NTC", "2": "GND"}})
    C(f"C{b+16}", "330pF", cc1, "GND", G, v=50)
    C(f"C{b+17}", "330pF", cc2, "GND", G, v=50)
    R(f"R{b+7}", "1k", vb, P + "VS", G)
    SO8FET(f"M{b}", "N", P + "NG", P + "CONV", P + "NS", G)
    SO8FET(f"M{b+1}", "N", P + "NG", vb, P + "NS", G)
    R(f"R{b+8}", "100k", P + "NG", P + "NS", G)
    R(f"R{b+9}", "0.01", P + "RTN", "GND", G, mpn="2512 1% 10mR 1W", fp="Resistor_SMD:R_2512_6332Metric",
      price=0.03, tol=0.01)
    note(f"bus mode (no PD input): 5V_SYS -> TPS2553 (ILIM 232 k / || 196 k / || 84.5 k) -> AO3401A -> {vb}")
    emit(f"U{b+2}", {"value": "TPS2553", "mpn": "TPS2553DBVR", "lcsc": "C55266", "price_usd": 0.3296,
                     "datasheet": "notes/datasheets/tps2553.md", "symbol": "kb_power:TPS2553",
                     "footprint": "Package_TO_SOT_SMD:SOT-23-6", "group": G, "theta_ja": 200, "tj_max_c": 125,
                     "spice": {"lib": LIB, "subckt": "ISW_LIM", "ports": ["1", "6", "3", "5", "2"],
                               "source": "fit",
                               "params": {"RON": 0.085, "K": 24.23, "XP": 0.978, "VEN": 1.1, "IQ": 120e-6,
                                          "THETA": 200}},
                     "pins": {"1": "5V_SYS", "6": P + "BUSOUT", "3": P + "BUSEN", "4": ovcur, "5": P + "ILIM",
                              "2": "GND"}})
    C(f"C{b+18}", "100nF", "5V_SYS", "GND", G, v=16, dec=f"U{b+2}")
    C(f"C{b+19}", "1uF", P + "BUSOUT", "GND", G, v=16, dec=f"U{b+2}")
    R(f"R{b+10}", "232k", P + "ILIM", "GND", G)
    R(f"R{b+11}", "200k", P + "ILIM", P + "IL15", G)
    NFET(f"M{b+2}", "HOST15", P + "IL15", "GND", G)
    R(f"R{b+12}", "84.5k", P + "ILIM", P + "IL30", G)
    NFET(f"M{b+3}", "HOST30", P + "IL30", "GND", G)
    R(f"R{b+13}", "10k", pwren, P + "BUSEN", G)
    NFET(f"M{b+4}", "PD_STAT", P + "BUSEN", "GND", G)
    PFET_S(f"M{b+5}", P + "QBG", P + "BUSOUT", vb, G)
    R(f"R{b+14}", "100k", vb, P + "QBG", G)
    NFET(f"M{b+6}", "PDMODE_N", P + "QBG", "GND", G)
    note(f"bus-mode Rp 56 k (default USB) from 5V_SYS, top pulled to GND by 100 k in PD mode")
    PFET_S(f"M{b+7}", "RP_G", f"RP_C{n}", "5V_SYS", G)
    R(f"R{b+15}", "56k", f"RP_C{n}", cc1, G)
    R(f"R{b+16}", "56k", f"RP_C{n}", cc2, G)
    R(f"R{b+17}", "100k", f"RP_C{n}", "GND", G)
    note(f"orientation: CC1 below CC2 (Rd on CC1) -> C{n}_ORIENT low = TX1/RX1; 10 ms RC against BMC traffic")
    emit(f"U{b+3}", {"value": "LMV331", "mpn": "LMV331IDCKR", "price_usd": 0.1, "symbol": "Comparator:LMV331",
                     "footprint": "Package_TO_SOT_SMD:SOT-353_SC-70-5", "group": G, "spice": False,
                     "pins": {"1": P + "CC1D", "3": P + "CC2D", "2": "GND", "5": "3V3_D", "4": P + "CMPO"}})
    R(f"R{b+18}", "100k", cc1, P + "CC1D", G)
    R(f"R{b+19}", "100k", P + "CC1D", "GND", G)
    R(f"R{b+20}", "100k", cc2, P + "CC2D", G)
    R(f"R{b+21}", "100k", P + "CC2D", "GND", G)
    R(f"R{b+22}", "100k", "3V3_D", P + "CMPO", G)
    R(f"R{b+23}", "100k", P + "CMPO", f"C{n}_ORIENT", G)
    C(f"C{b+20}", "100nF", f"C{n}_ORIENT", "GND", G, v=16)
    C(f"C{b+21}", "100nF", "3V3_D", "GND", G, v=16, dec=f"U{b+3}")
    note(f"R15: TVS on {vb} and the CC lines; VBUS bulk at the receptacle")
    TVS(f"D{b}", "SMF12A", vb, G, "Diode_SMD:D_SOD-123F", 0.03)
    TVS(f"D{b+1}", "PESD12VS1UB", cc1, G, "Diode_SMD:D_SOD-523", 0.03)
    TVS(f"D{b+2}", "PESD12VS1UB", cc2, G, "Diode_SMD:D_SOD-523", 0.03)
    C(f"C{b+22}", "10uF", vb, "GND", G, v=25)


c_port(1, 700, "HUB1_PWREN1", "HUB1_OVCUR1")
c_port(2, 750, "HUB1_PWREN2", "HUB1_OVCUR2")


# ---------------------------------------------------------------- USB-A port switches
def a_port(n, b, pwren, ovcur):
    G = "usba_sw"
    P = f"A{n}_"
    vb = f"VBUS_A{n}"
    note(f"---- USB-A{n}: TPS2546 (PD mode ILIM_HI 28.0 k CDP; bus mode ILIM_LO 562 k / || 309 k / || 162 k SDP)")
    emit(f"U{b}", {"value": "TPS2546", "mpn": "TPS2546RTER", "lcsc": "C117009", "price_usd": 0.5212,
                   "datasheet": "notes/datasheets/tps2546.md", "symbol": "kb_power:TPS2546",
                   "footprint": "Package_DFN_QFN:Texas_RTE_WQFN-16-1EP_3x3mm_P0.5mm_EP1.2x0.8mm", "group": G,
                   "theta_ja": 46, "tj_max_c": 125,
                   "spice": {"lib": LIB, "subckt": "TPS2546_FIT", "ports": ["1", "12", "5", "4", "16", "15", "14"],
                             "source": "fit", "params": {"RON": 0.073, "IQ": 200e-6, "THETA": 46}},
                   "pins": {"1": "5V_SYS", "12": vb, "5": pwren, "4": "PD_STAT", "6": "3V3_D", "7": "3V3_D",
                            "8": "3V3_D", "16": P + "ILHI", "15": P + "ILLO", "13": ovcur, "11": P + "CDP",
                            "10": P + "CDM", "3": P + "DP", "2": P + "DM", "14": "GND", "17": "GND"},
                   "nc": ["9"]})
    R(f"R{b}", "28k", P + "ILHI", "GND", G)
    R(f"R{b+1}", "562k", P + "ILLO", "GND", G)
    R(f"R{b+2}", "309k", P + "ILLO", P + "IL15", G)
    NFET(f"M{b}", "HOST15", P + "IL15", "GND", G)
    R(f"R{b+3}", "162k", P + "ILLO", P + "IL30", G)
    NFET(f"M{b+1}", "HOST30", P + "IL30", "GND", G)
    C(f"C{b}", "100nF", "5V_SYS", "GND", G, v=16, dec=f"U{b}")
    C(f"C{b+1}", "1uF", "5V_SYS", "GND", G, v=16, dec=f"U{b}")
    C(f"C{b+2}", "22uF", vb, "GND", G, v=25)
    C(f"C{b+3}", "22uF", vb, "GND", G, v=25)
    TVS(f"D{b}", "SMF5.0A", vb, G, "Diode_SMD:D_SOD-123F", 0.03)


a_port(1, 800, "HUB1_PWREN3", "HUB1_OVCUR3")
a_port(2, 820, "HUB2_PWREN1", "HUB2_OVCUR1")

# ---------------------------------------------------------------- digital rails
G = "buck_dig"
note("---- 3V3_D: TPS62933F 1.2 MHz (RT to GND), 2.2 uH, FB 31.6 k / 10.0 k = 3.33 V")
TPS62933 = {"value": "TPS62933F", "mpn": "TPS62933FDRLR", "lcsc": "C5219272", "price_usd": 0.7244,
            "datasheet": "notes/datasheets/tps62933.md", "symbol": "Regulator_Switching:TPS62933F",
            "footprint": "Package_TO_SOT_SMD:SOT-583-8", "theta_ja": 100, "tj_max_c": 150}


def tps62933(ref, eff, pins, nc):
    d = dict(TPS62933, group=G)
    d["spice"] = {"lib": LIB, "subckt": "BUCK_FB", "ports": ["3", "5", "8", "2", "4"], "source": "fit",
                  "params": {"VREF": 0.8, "EFF5": eff, "EFF9": eff, "ILIM": 4.2, "IQ": 125e-6, "VEN": 1.1,
                             "THETA": 100, "VUV": 3.8}}
    d["pins"] = pins
    if nc:
        d["nc"] = nc
    emit(ref, d)


tps62933("U830", 0.72, {"1": "GND", "2": "D3_EN", "3": "5V_SYS", "4": "GND", "5": "D3_SW", "6": "D3_BST",
                        "7": "D3_SS", "8": "D3_FB"}, None)
R("R830", "100k", "5V_SYS", "D3_EN", G)
R("R831", "31.6k", "3V3_D", "D3_FB", G, tol=0.005, price=0.004)
R("R832", "10k", "D3_FB", "GND", G, tol=0.005, price=0.004)
C("C830", "10uF", "5V_SYS", "GND", G, v=25, dec="U830")
C("C831", "100nF", "5V_SYS", "GND", G, v=16, dec="U830")
C("C832", "100nF", "D3_BST", "D3_SW", G, v=16)
C("C833", "10nF", "D3_SS", "GND", G, v=50)
emit("L830", {"value": "2.2uH", "mpn": "FSA0630-2R2MT", "lcsc": "C41384288", "price_usd": 0.0518,
              "symbol": "Device:L", "footprint": "Inductor_SMD:L_Sunlord_MWSA0603S", "spice": {"lib": LIB, "subckt": "LDCR", "ports": ["1", "2"], "source": "behavioral"}, "group": G, "height_mm": 3.0,
              "pins": {"1": "D3_SW", "2": "3V3_D"}})
C("C834", "22uF", "3V3_D", "GND", G, v=25)
C("C835", "22uF", "3V3_D", "GND", G, v=25)
note("---- 0V9_CORE (XU316 VDD): TPS62933F 500 kHz (RT open), 4.7 uH, FB 10.0 k / 80.6 k 0.5 % = 0.8993 V, EN = 1V8_D")
tps62933("U840", 0.78, {"2": "1V8_D", "3": "5V_SYS", "4": "GND", "5": "D09_SW", "6": "D09_BST", "7": "D09_SS",
                        "8": "D09_FB"}, ["1"])
R("R840", "10k", "0V9_CORE", "D09_FB", G, tol=0.005)
R("R841", "80.6k", "D09_FB", "GND", G, tol=0.005)
C("C840", "10uF", "5V_SYS", "GND", G, v=25, dec="U840")
C("C841", "100nF", "5V_SYS", "GND", G, v=16, dec="U840")
C("C842", "100nF", "D09_BST", "D09_SW", G, v=16)
C("C843", "10nF", "D09_SS", "GND", G, v=50)
L47 = {"value": "4.7uH", "mpn": "CKCS5040-4.7uH/M", "lcsc": "C354606", "price_usd": 0.0405, "symbol": "Device:L",
       "footprint": "Inductor_SMD:L_Sunlord_MWSA0503S", "spice": {"lib": LIB, "subckt": "LDCR", "ports": ["1", "2"], "source": "behavioral"}, "height_mm": 4.0}
emit("L840", dict(L47, group=G, pins={"1": "D09_SW", "2": "0V9_CORE"}))
for ref in ["C844", "C845", "C846"]:
    C(ref, "22uF", "0V9_CORE", "GND", G, v=25)
note("---- 1V8_D (XU316 VDDIOB18, USB_VDD18): AP2112K-1.8 from 3V3_D, EN = VIN")
emit("U850", {"value": "AP2112K-1.8", "mpn": "AP2112K-1.8TRG1", "lcsc": "C176944", "price_usd": 0.09,
              "symbol": "Regulator_Linear:AP2112K-1.8", "footprint": "Package_TO_SOT_SMD:SOT-23-5", "group": G,
              "theta_ja": 250, "tj_max_c": 125,
              "spice": {"subckt": "LDO_GENERIC", "ports": ["1", "5", "2"], "source": "fit",
                        "params": {"VOUT": 1.8, "VDROP": 0.25, "IQ": 55e-6, "ROUT": 0.05, "ILIM": 0.6}},
              "pins": {"1": "3V3_D", "3": "3V3_D", "2": "GND", "5": "1V8_D"}})
C("C850", "1uF", "3V3_D", "GND", G, v=16, dec="U850")
C("C851", "1uF", "1V8_D", "GND", G, v=16, dec="U850")

# ---------------------------------------------------------------- +-5.5 V audio pre-regulator
G = "aux_pm"
note("---- AUD_P5V5 / AUD_N5V5: TPS65131 (1.38 MHz, PSP/PSN low), 4.7 uH x2, Schottky x2, 2.2 k dummy loads")
emit("U860", {"value": "TPS65131", "mpn": "TPS65131RGER", "lcsc": "C87663", "price_usd": 2.28,
              "datasheet": "notes/datasheets/tps65131.md", "symbol": "Regulator_Switching:TPS65131RGE",
              "footprint": "Package_DFN_QFN:VQFN-24-1EP_4x4mm_P0.5mm_EP2.45x2.45mm_ThermalVias", "group": G,
              "theta_ja": 40, "tj_max_c": 150,
              "spice": {"lib": LIB, "subckt": "TPS65131_FIT", "ports": ["4", "23", "22", "15", "16", "17", "19"],
                        "source": "fit", "params": {"EFFP": 0.80, "EFFN": 0.75, "ILIM": 1.0, "IQ": 0.5e-3,
                                                    "THETA": 40}},
              "pins": {"1": "AUX_LP", "24": "AUX_LP", "2": "GND", "3": "GND", "4": "5V_SYS", "5": "5V_SYS",
                       "6": "5V_SYS", "8": "5V_SYS", "9": "GND", "10": "5V_SYS", "11": "GND", "13": "AUX_LN",
                       "14": "AUX_LN", "15": "AUD_N5V5", "16": "AUX_FBN", "17": "AUX_VREF", "18": "AUX_CN",
                       "19": "GND", "21": "AUX_CP", "22": "AUX_FBP", "23": "AUD_P5V5", "25": "GND"},
              "nc": ["7"]})
emit("L860", dict(L47, group=G, pins={"1": "5V_SYS", "2": "AUX_LP"}))
emit("L861", dict(L47, group=G, pins={"1": "AUX_LN", "2": "GND"}))
for ref, a, k in [("D860", "AUX_LP", "AUD_P5V5"), ("D861", "AUD_N5V5", "AUX_LN")]:
    emit(ref, {"value": "B5819W", "mpn": "B5819W", "price_usd": 0.02, "symbol": "Device:D_Schottky",
               "footprint": "Diode_SMD:D_SOD-123", "group": G,
               "spice": {"model": "DSCH40", "lib": LIB, "source": "fit"}, "pins": {"1": k, "2": a}})
R("R860", "715k", "AUD_P5V5", "AUX_FBP", G, tol=0.001, mpn="0402 0.1% 715k", price=0.01)
R("R861", "200k", "AUX_FBP", "GND", G, tol=0.001, mpn="0402 0.1% 200k", price=0.01)
R("R862", "909k", "AUD_N5V5", "AUX_FBN", G, tol=0.001, mpn="0402 0.1% 909k", price=0.01)
R("R863", "200k", "AUX_FBN", "AUX_VREF", G, tol=0.001, mpn="0402 0.1% 200k", price=0.01)
C("C860", "220nF", "AUX_VREF", "GND", G, v=16)
C("C861", "10nF", "AUX_CP", "GND", G, v=50)
C("C862", "10nF", "AUX_CN", "GND", G, v=50)
C("C863", "10uF", "5V_SYS", "GND", G, v=25, dec="U860")
C("C864", "100nF", "5V_SYS", "GND", G, v=16, dec="U860")
C("C865", "10uF", "AUD_P5V5", "GND", G, v=25)
C("C866", "10uF", "AUD_P5V5", "GND", G, v=25)
C("C867", "10uF", "AUD_N5V5", "GND", G, v=25)
C("C868", "10uF", "AUD_N5V5", "GND", G, v=25)
R("R864", "2.2k", "AUD_P5V5", "GND", G)
R("R865", "2.2k", "GND", "AUD_N5V5", G)

# =====================================================================================================
NETS = {
    "VIN20": {"class": "power", "current_a": 3.3, "external_supply": True},
    "PDIN_VBUS": {},
    "SINK_S": {"class": "power", "current_a": 3.3},
    "BUCK5_VIN": {"class": "power", "current_a": 1.5, "external_supply": True},
    "CH224_VBUS": {"external_supply": True},
    "C1_IPVIN": {"external_supply": True},
    "C2_IPVIN": {"external_supply": True},
    "B5_SW": {"class": "switching", "current_a": 4.5},
    "C1_SW": {"class": "switching", "current_a": 3.5},
    "C2_SW": {"class": "switching", "current_a": 3.5},
    "D3_SW": {"class": "switching", "current_a": 0.5},
    "D09_SW": {"class": "switching", "current_a": 1.0},
    "AUX_LP": {"class": "switching", "current_a": 0.5},
    "AUX_LN": {"class": "switching", "current_a": 0.5},
    "C1_CONV": {"class": "power", "current_a": 3.0, "external_supply": True},
    "C2_CONV": {"class": "power", "current_a": 3.0, "external_supply": True},
    "C1_NS": {"class": "power", "current_a": 3.0},
    "C2_NS": {"class": "power", "current_a": 3.0},
    "C1_RTN": {"current_a": 3.0},
    "C2_RTN": {"current_a": 3.0},
    "0V9_CORE": {"current_a": 0.85},
    "1V8_D": {"current_a": 0.05},
    "AUD_P5V5": {"current_a": 0.1, "allow_single": True},
    "AUD_N5V5": {"current_a": 0.1, "allow_single": True},
    "A1_CDP": {"class": "high_speed", "edge_ns": 0.5, "impedance_ohm": 45},
    "A1_CDM": {"class": "high_speed", "edge_ns": 0.5, "impedance_ohm": 45},
    "A2_CDP": {"class": "high_speed", "edge_ns": 0.5, "impedance_ohm": 45},
    "A2_CDM": {"class": "high_speed", "edge_ns": 0.5, "impedance_ohm": 45},
    "HOST_IMON": {"allow_single": True},
}

# =====================================================================================================
TYP = {"ild5": 0.268, "ild09": 0.236, "ild18": 0.0315, "ild33": 0.019, "ildp": 0.030, "ildn": 0.030}
DESIGN600 = {"ild5": 0.385, "ild09": 0.236, "ild18": 0.0315, "ild33": 0.019, "ildp": 0.030, "ildn": 0.030}
HEAVY = {"ild5": 0.348, "ild09": 0.330, "ild18": 0.039, "ild33": 0.035, "ildp": 0.045, "ildn": 0.045}


def alters(d):
    return [f"alter {k} dc={v}" for k, v in d.items()]


BUS = ["alter vpd dc=0", "alter vhost dc=4.75", "alter ilc1 dc=0", "alter ilc2 dc=0", "alter ila1 dc=0",
       "alter ila2 dc=0"]
OVERLOAD = ["alter rlc1 = 2", "alter rlc2 = 2", "alter rla1 = 2", "alter rla2 = 2"]
TEMPS = [0, 25, 40]


# Power-up transient (averaged models, all nodes from 0 V): the DC op of the full power tree has a false basin
# (3V3_D down -> PD-mode logic off -> host/bus paths on) that Newton, gmin and source stepping fall into; the
# start-up transient reaches the real steady state. Measures are the mean over the last 10 ms (140-150 ms).
TRAN = "tran 20u 150m 140m uic"


def cmd(lines):
    lines = [ln.replace("alter il", "alter vil") for ln in lines]
    return "\n".join(lines + [TRAN])


def port_i(n):
    return f"(v(VBUS_C{n})-v(C{n}_RTN))/2"


def tj(ref, lim, req="R16"):
    return {"name": f"tj_{ref.lower()}", "expr": f"v(x{ref.lower()}.tj)", "max": lim, "unit": "C", "req": req}


SOURCES = [
    {"line": "VPD vpd_src 0 DC 20"},
    {"line": "RPDC vpd_src $(J600.A4) 0.1"},
    {"line": "VHOST vh_src 0 DC 5.0"},
    {"line": "RHC vh_src $(J301.A4) 0.15"},
    # IP2736U FB sink current for the 9 V contract: (9 - 1)/100k - 1/24.9k = 39.84 uA (0 for 5 V: 5.016 V default)
    {"line": "VIFB1 cifb1 0 DC 39.84u"},
    {"line": "VIFB2 cifb2 0 DC 39.84u"},
    # IP2736U_EF behaviour (spice: false part): FB sink current (contract voltage), VOUT1G gate drive
    {"line": "BFB1 $(C1_IPFB) 0 I = min(max(v(C1_IPVIN)-3,0),1)*v(cifb1)"},
    {"line": "BFB2 $(C2_IPFB) 0 I = min(max(v(C2_IPVIN)-3,0),1)*v(cifb2)"},
    {"line": "BG1 $(C1_NG) $(C1_NS) V = 5*min(max(v(C1_IPVIN)-3,0),1)"},
    {"line": "BG2 $(C2_NG) $(C2_NS) V = 5*min(max(v(C2_IPVIN)-3,0),1)"},
    # port loads at the connector pins (current sinks, plus resistors used for the at-the-limit cases)
    {"line": "VILC1 cilc1 0 DC 2.22"},
    {"line": "BLC1 $(J311.A4) $(J311.A1) I = v(cilc1)*min(max((v(VBUS_C1)-v(C1_RTN))/2,0),1)"},
    {"line": "VILC2 cilc2 0 DC 2.22"},
    {"line": "BLC2 $(J312.A4) $(J312.A1) I = v(cilc2)*min(max((v(VBUS_C2)-v(C2_RTN))/2,0),1)"},
    {"line": "VILA1 cila1 0 DC 1.5"},
    {"line": "BLA1 $(J321.1) $(J321.4) I = v(cila1)*min(max(v(VBUS_A1)/2,0),1)"},
    {"line": "VILA2 cila2 0 DC 1.5"},
    {"line": "BLA2 $(J322.1) $(J322.4) I = v(cila2)*min(max(v(VBUS_A2)/2,0),1)"},
    {"line": "RLC1 $(J311.A4) $(J311.A1) 1e6"},
    {"line": "RLC2 $(J312.A4) $(J312.A1) 1e6"},
    {"line": "RLA1 $(J321.1) $(J321.4) 1e6"},
    {"line": "RLA2 $(J322.1) $(J322.4) 1e6"},
    {"line": "RLX $(5V_SYS) 0 1e6"},
    # internal loads (notes/parts.md sec. 6 and architecture 4.1; default = PD-mode peak incl. R29 full scale)
    {"line": "VILD5 cild5 0 DC 0.383"},
    {"line": "BLD5 $(5V_SYS) 0 I = v(cild5)*min(max(v(5V_SYS)/2.5,0),1)"},
    {"line": "VILD09 cild09 0 DC 0.33"},
    {"line": "BLD09 $(0V9_CORE) 0 I = v(cild09)*min(max(v(0V9_CORE)/0.45,0),1)"},
    {"line": "VILD18 cild18 0 DC 0.039"},
    {"line": "BLD18 $(1V8_D) 0 I = v(cild18)*min(max(v(1V8_D)/0.9,0),1)"},
    {"line": "VILD33 cild33 0 DC 0.035"},
    {"line": "BLD33 $(3V3_D) 0 I = v(cild33)*min(max(v(3V3_D)/1.6,0),1)"},
    {"line": "VILDP cildp 0 DC 0.085"},
    {"line": "BLDP $(AUD_P5V5) 0 I = v(cildp)*min(max(v(AUD_P5V5)/2.7,0),1)"},
    {"line": "VILDN cildn 0 DC 0.085"},
    {"line": "BLDN 0 $(AUD_N5V5) I = v(cildn)*min(max(-v(AUD_N5V5)/2.7,0),1)"},
    # GL3523 PWRENx (spice: false): the hub enables every downstream port (active high, PAMBER2 strap) after it
    # has started and enumerated: 40 ms after power-up here (3V3_D, the PD-mode logic and ILIM_SEL are settled)
    {"line": "VPWR11 $(HUB1_PWREN1) 0 DC 3.3 PWL(0 0 40m 0 41m 3.3)"},
    {"line": "VPWR12 $(HUB1_PWREN2) 0 DC 3.3 PWL(0 0 40m 0 41m 3.3)"},
    {"line": "VPWR13 $(HUB1_PWREN3) 0 DC 3.3 PWL(0 0 40m 0 41m 3.3)"},
    {"line": "VPWR21 $(HUB2_PWREN1) 0 DC 3.3 PWL(0 0 40m 0 41m 3.3)"},
    # HD3SS3220 OUT1/OUT2 (spice: false): a switch pulls them low for 1.5 A / 3 A hosts
    {"line": "SRP1 $(HOST_RP_OUT1) 0 rp1c 0 SW_TB"},
    {"line": "VRP1C rp1c 0 DC 0"},
    {"line": "SRP2 $(HOST_RP_OUT2) 0 rp2c 0 SW_TB"},
    {"line": "VRP2C rp2c 0 DC 0"},
]

PD_REAL = ("who: user, both USB-C phones on 20 W contracts and two USB-A devices charging at 1.5 A with the 65 W "
           "adapter connected; how often: daily; timing: steady state (DC); limits: R7 vSrcNew +-5 %, R8 4.75 V, "
           "R9 65 W, tj_max LM61460 150 C (datasheet p.5), TPS2546/TPS2553 125 C, AO4406A 150 C")
ANALYSES = [
    {"name": "pd_9v", "cmd": TRAN, "temps": TEMPS, "realism": PD_REAL,
     "measures": [
         {"name": "vc1_9v", "expr": "v(VBUS_C1)-v(C1_RTN)", "min": 8.55, "max": 9.45, "unit": "V", "req": "R7"},
         {"name": "vc2_9v", "expr": "v(VBUS_C2)-v(C2_RTN)", "min": 8.55, "max": 9.45, "unit": "V", "req": "R7"},
         {"name": "va1_pd", "expr": "v(VBUS_A1)", "min": 4.75, "unit": "V", "req": "R8"},
         {"name": "va2_pd", "expr": "v(VBUS_A2)", "min": 4.75, "unit": "V", "req": "R8"},
         {"name": "pin_pd_w", "expr": "v(PDIN_VBUS)*(-i(vpd))", "max": 65, "unit": "W", "req": "R9"},
         tj("U680", 150), tj("U700", 150), tj("U750", 150), tj("U800", 125), tj("U820", 125),
         tj("U830", 150), tj("U840", 150), tj("U860", 150),
         {"name": "tj_m700_m701", "expr": "40+(v(C1_CONV)-v(VBUS_C1))*2.22/2*75", "max": 150, "unit": "C",
          "req": "R16"},
         {"name": "tj_m600_m601", "expr": "40+(v(PDIN_VBUS)-v(VIN20))*(-i(vpd))/2*75", "max": 150, "unit": "C",
          "req": "R16"},
         {"name": "dbg_ihost_pd", "expr": "-i(vhost)", "unit": "A"},
         {"name": "dbg_vin20", "expr": "v(VIN20)", "unit": "V"},
         {"name": "dbg_5vsys", "expr": "v(5V_SYS)", "unit": "V"},
         {"name": "dbg_3v3", "expr": "v(3V3_D)", "unit": "V"},
         {"name": "dbg_0v9", "expr": "v(0V9_CORE)", "unit": "V"},
         {"name": "dbg_1v8", "expr": "v(1V8_D)", "unit": "V"},
         {"name": "dbg_aud_p", "expr": "v(AUD_P5V5)", "unit": "V"},
         {"name": "dbg_aud_n", "expr": "v(AUD_N5V5)", "unit": "V"},
         {"name": "dbg_pd_stat", "expr": "v(PD_STAT)", "unit": "V"},
     ]},
    {"name": "pd_5v", "cmd": cmd(["alter vifb1 dc=0", "alter vifb2 dc=0", "alter ilc1 dc=3", "alter ilc2 dc=3"]),
     "temps": TEMPS, "realism": PD_REAL,
     "measures": [
         {"name": "vc1_5v", "expr": "v(VBUS_C1)-v(C1_RTN)", "min": 4.75, "max": 5.25, "unit": "V", "req": "R7"},
         {"name": "vc2_5v", "expr": "v(VBUS_C2)-v(C2_RTN)", "min": 4.75, "max": 5.25, "unit": "V", "req": "R7"},
         {"name": "va1_pd5", "expr": "v(VBUS_A1)", "min": 4.75, "unit": "V", "req": "R8"},
         {"name": "va2_pd5", "expr": "v(VBUS_A2)", "min": 4.75, "unit": "V", "req": "R8"},
         {"name": "pin_pd5_w", "expr": "v(PDIN_VBUS)*(-i(vpd))", "max": 65, "unit": "W", "req": "R9"},
         tj("U700", 150), tj("U750", 150),
         {"name": "tj_m700_m701_5v", "expr": "40+(v(C1_CONV)-v(VBUS_C1))*3/2*75", "max": 150, "unit": "C",
          "req": "R16"},
     ]},
    {"name": "bus_limits", "cmd": cmd(BUS + alters(TYP) + OVERLOAD), "temps": TEMPS,
     "realism": "who: user, four devices on a 900 mA USB 3 host port, each asking more than its share (ports sit "
                "at their current limit); how often: whenever the PD adapter is absent; timing: steady state; "
                "limits: R19 host draw <= 900 mA nominal (typical limits, typical internal 483 mA)",
     "measures": [
         {"name": "ihost_ports_at_limit", "expr": "-i(vhost)", "max": 0.900, "unit": "A", "req": "R19"},
         {"name": "ic1_lim", "expr": port_i(1), "min": 0.075, "unit": "A", "req": "R19"},
         {"name": "ic2_lim", "expr": port_i(2), "min": 0.075, "unit": "A", "req": "R19"},
         {"name": "ia1_lim", "expr": "v(VBUS_A1)/2", "min": 0.075, "unit": "A", "req": "R19"},
         {"name": "ia2_lim", "expr": "v(VBUS_A2)/2", "min": 0.075, "unit": "A", "req": "R19"},
         {"name": "dbg_ipd_bus", "expr": "i(vpd)", "unit": "A"},
     ]},
    {"name": "bus_floor", "cmd": cmd(BUS + alters(DESIGN600) + ["alter ilc1 dc=0.075", "alter ilc2 dc=0.075",
                                                                 "alter ila1 dc=0.075", "alter ila2 dc=0.075"]),
     "temps": TEMPS,
     "realism": "who: user, four devices drawing 75 mA each with the internal loads at the 600 mA design worst, "
                "host at its 4.75 V minimum through a 2 x 75 mohm cable; limits: R19 VBUS >= 4.45 V, eFuse min "
                "trip 0.90 A (TPS25940A p.5 at 88.7 k) not reached",
     "measures": [
         {"name": "vc1_floor", "expr": "v(VBUS_C1)-v(C1_RTN)", "min": 4.45, "unit": "V", "req": "R19"},
         {"name": "vc2_floor", "expr": "v(VBUS_C2)-v(C2_RTN)", "min": 4.45, "unit": "V", "req": "R19"},
         {"name": "va1_floor", "expr": "v(VBUS_A1)", "min": 4.45, "unit": "V", "req": "R19"},
         {"name": "va2_floor", "expr": "v(VBUS_A2)", "min": 4.45, "unit": "V", "req": "R19"},
         {"name": "ihost_floor", "expr": "-i(vhost)", "max": 0.900, "unit": "A", "req": "R19"},
         {"name": "dbg_internal_floor", "expr": "-i(vhost)-0.3", "unit": "A"},
     ]},
    {"name": "bus_heavy", "cmd": cmd(BUS + alters(HEAVY) + ["alter ilc1 dc=0.06", "alter ilc2 dc=0.06",
                                                             "alter ila1 dc=0.06", "alter ila2 dc=0.06"]),
     "temps": TEMPS,
     "realism": "who: user, heavy case of R19 (4 SuperSpeed devices, mic recording, loud headphones, internal "
                "~657 mA) with 60 mA per port; limits: R19 VBUS >= 4.45 V, host <= 0.90 A (eFuse min trip)",
     "measures": [
         {"name": "vc1_heavy", "expr": "v(VBUS_C1)-v(C1_RTN)", "min": 4.45, "unit": "V", "req": "R19"},
         {"name": "va1_heavy", "expr": "v(VBUS_A1)", "min": 4.45, "unit": "V", "req": "R19"},
         {"name": "ihost_heavy", "expr": "-i(vhost)", "max": 0.900, "unit": "A", "req": "R19"},
         {"name": "dbg_internal_heavy", "expr": "-i(vhost)-0.24", "unit": "A"},
     ]},
    {"name": "bus_trip", "cmd": cmd(BUS + alters(TYP) + ["alter rlx = 2"]), "temps": TEMPS,
     "realism": "who: fault, a short or a failed device on 5V_SYS / a port beyond the port limits in bus mode; "
                "how often: rare; timing: steady state in current limit (thermal auto-retry not modelled); limit: "
                "R19 eFuse trip near 1.0 A, max <= 1.1 A (TPS25940A IOS 87.5/R, p.5)",
     "measures": [
         {"name": "efuse_lim_900", "expr": "-i(vhost)", "min": 0.90, "max": 1.10, "unit": "A", "req": "R19"},
     ]},
    {"name": "bus_1a5", "cmd": cmd(BUS + alters(TYP) + OVERLOAD + ["alter vrp1c dc=1"]), "temps": [25],
     "realism": "who: user, USB-C host advertising 1.5 A, four devices at their port limits; limits: R21 >= 225 "
                "mA per port, host draw <= 1.5 A nominal",
     "measures": [
         {"name": "ic1_1a5", "expr": port_i(1), "min": 0.225, "unit": "A", "req": "R21"},
         {"name": "ic2_1a5", "expr": port_i(2), "min": 0.225, "unit": "A", "req": "R21"},
         {"name": "ia1_1a5", "expr": "v(VBUS_A1)/2", "min": 0.225, "unit": "A", "req": "R21"},
         {"name": "ia2_1a5", "expr": "v(VBUS_A2)/2", "min": 0.225, "unit": "A", "req": "R21"},
         {"name": "ihost_1a5", "expr": "-i(vhost)", "max": 1.5, "unit": "A", "req": "R21"},
     ]},
    {"name": "bus_3a", "cmd": cmd(BUS + alters(TYP) + OVERLOAD + ["alter vrp1c dc=1", "alter vrp2c dc=1"]),
     "temps": [25],
     "realism": "who: user, USB-C host advertising 3 A, four devices at their port limits; limits: R21 >= 500 mA "
                "per port, host draw <= 3.0 A nominal",
     "measures": [
         {"name": "ic1_3a", "expr": port_i(1), "min": 0.5, "unit": "A", "req": "R21"},
         {"name": "ic2_3a", "expr": port_i(2), "min": 0.5, "unit": "A", "req": "R21"},
         {"name": "ia1_3a", "expr": "v(VBUS_A1)/2", "min": 0.5, "unit": "A", "req": "R21"},
         {"name": "ia2_3a", "expr": "v(VBUS_A2)/2", "min": 0.5, "unit": "A", "req": "R21"},
         {"name": "ihost_3a", "expr": "-i(vhost)", "max": 3.0, "unit": "A", "req": "R21"},
     ]},
]

for _an in ANALYSES:
    for _m in _an["measures"]:
        _m["expr"] = f"mean({_m['expr']})"

BUDGETS_FILE = PROJ / "tools" / "pass_b_budgets.json"   # written by hand after the pre-layout run


def yaml_comp(ref, d):
    lines = [f"  {ref}:"]
    for k, v in d.items():
        if k in ("pins", "nc", "spice", "thermal_vias", "placement"):
            if v is False:
                lines.append(f"    {k}: false")
            elif k == "pins":
                lines.append("    pins: {" + ", ".join(f"{q(p)}: {n}" for p, n in v.items()) + "}")
            else:
                lines.append(f"    {k}: {json.dumps(v)}")
        elif isinstance(v, bool):
            lines.append(f"    {k}: {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            lines.append(f"    {k}: {v}")
        else:
            lines.append(f"    {k}: {q(v)}")
    return lines


def build():
    comp = ["  # ==== PASS B COMPONENTS BEGIN (tools/gen_pass_b.py)"]
    for ref, d in OUT:
        if ref == "#":
            comp.append(f"  # {d}")
        else:
            comp += yaml_comp(ref, d)
    comp.append("  # ==== PASS B COMPONENTS END")
    nets = ["  # ==== PASS B NETS BEGIN (tools/gen_pass_b.py)"]
    nets += [f"  {n}: {json.dumps(v)}" for n, v in NETS.items()]
    nets.append("  # ==== PASS B NETS END")
    sim = {"ambient_c": 40, "options": ["rshunt=1e9", "itl1=1000"], "sources": SOURCES,
           "analyses": ANALYSES}
    simtxt = ["# ==== PASS B SIM BEGIN (tools/gen_pass_b.py)", "sim:", f"  ambient_c: {sim['ambient_c']}",
              f"  options: {json.dumps(sim['options'])}",
              "  sources:"]
    simtxt += [f"    - {json.dumps(s)}" for s in SOURCES]
    simtxt.append("  analyses:")
    for a in ANALYSES:
        simtxt.append(f"    - name: {a['name']}")
        simtxt.append(f"      cmd: {json.dumps(a['cmd'])}")
        if a.get("temps"):
            simtxt.append(f"      temps: {json.dumps(a['temps'])}")
        if a.get("realism"):
            simtxt.append(f"      realism: {json.dumps(a['realism'])}")
        simtxt.append("      measures:")
        simtxt += [f"        - {json.dumps(m)}" for m in a["measures"]]
    if BUDGETS_FILE.exists():
        simtxt.append("budgets:")
        simtxt += [f"  - {json.dumps(b)}" for b in json.loads(BUDGETS_FILE.read_text())]
    simtxt.append("# ==== PASS B SIM END")
    return comp, nets, simtxt


def replace_block(text, begin, end, new_lines, anchor_before=None, at_end=False):
    lines = text.split("\n")
    bi = next((i for i, ln in enumerate(lines) if ln.strip().startswith(begin)), None)
    if bi is not None:
        ei = next(i for i, ln in enumerate(lines) if ln.strip().startswith(end))
        lines[bi:ei + 1] = new_lines
    elif anchor_before is not None:
        ai = next(i for i, ln in enumerate(lines) if ln == anchor_before)
        lines[ai:ai] = new_lines
    else:
        while lines and lines[-1] == "":
            lines.pop()
        lines += new_lines + [""]
    return "\n".join(lines)


def main():
    comp, nets, simtxt = build()
    dy = PROJ / "design.yaml"
    t = dy.read_text()
    # sim block first (it sits at the very end), then components before it
    t = replace_block(t, "# ==== PASS B SIM BEGIN", "# ==== PASS B SIM END", simtxt)
    if "# ==== PASS B COMPONENTS BEGIN" in t:
        t = replace_block(t, "# ==== PASS B COMPONENTS BEGIN", "# ==== PASS B COMPONENTS END", comp)
    else:
        t = t.replace("# ==== PASS B SIM BEGIN", "\n".join(comp) + "\n# ==== PASS B SIM BEGIN", 1)
    t = replace_block(t, "# ==== PASS B NETS BEGIN", "# ==== PASS B NETS END", nets, anchor_before="components:")
    if "--apply" in sys.argv:
        dy.write_text(t)
        n = sum(1 for r, _ in OUT if r != "#")
        print(f"applied: {n} components, {len(NETS)} nets, {len(ANALYSES)} analyses")
    else:
        print("\n".join(comp[:40]))


if __name__ == "__main__":
    main()
