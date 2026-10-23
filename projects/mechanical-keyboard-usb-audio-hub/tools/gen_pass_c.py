"""Pass C (audio) generator: writes the marked PASS C blocks of design.yaml (nets, components, sim sources and
analyses). Re-running replaces only the text between the PASS C markers; edit this file, not those blocks.
Usage: uv run python tools/gen_pass_c.py [--apply]"""
import json, sys, re
from pathlib import Path

P = Path(__file__).resolve().parents[1]
DY = P / "design.yaml"
LIB = "lib/spice/audio_fit.lib"
comps: list[tuple[str, dict, str]] = []   # (ref, fields, comment)


def q(v):
    return json.dumps(v)


def add(ref, comment="", **f):
    comps.append((ref, f, comment))


def R(ref, val, a, b, grp, tol=None, fp="Resistor_SMD:R_0402_1005Metric", mpn=None, price=0.002, comment="", **kw):
    f = dict(value=val, mpn=mpn or f"{fp.split('_')[2] if 'R_0' in fp else ''} {('%g%%' % (tol * 100)) if tol else '1%'} {val}".strip(),
             price_usd=price, symbol="Device:R", footprint=fp, group=grp, pins={"1": a, "2": b})
    if tol:
        f["tolerance"] = tol
    f.update(kw)
    add(ref, comment, **f)


def C(ref, val, a, b, grp, mpn, lcsc=None, fp="Capacitor_SMD:C_0402_1005Metric", vr=16, price=0.003, dec=None, tol=None,
      comment="", symbol="Device:C", **kw):
    f = dict(value=val, mpn=mpn, price_usd=price)
    if lcsc:
        f["lcsc"] = lcsc
    f.update(symbol=symbol, footprint=fp, group=grp)
    if dec:
        f["decouples"] = dec
    f["voltage_rating_v"] = vr
    if tol:
        f["tolerance"] = tol
    f["pins"] = {"1": a, "2": b}
    f.update(kw)
    add(ref, comment, **f)


C100N = dict(mpn="CL05B104KO5NNNC", lcsc="C1525")
C1U = dict(mpn="CL05A105KO5NNNC", lcsc="C52923", price=0.004)
C470N = dict(mpn="CL05A474KP5NNNC", price=0.004)   # 470 nF 0402 X5R 10 V; LCSC code to confirm at the parts stage
C10U = dict(mpn="CL21A106KAYNNNE", lcsc="C15850", fp="Capacitor_SMD:C_0805_2012Metric", vr=25, price=0.012)
C4U7 = dict(mpn="CL10A475KO8NNNC", lcsc="C19666", fp="Capacitor_SMD:C_0603_1608Metric", vr=16, price=0.006)
C47U = dict(mpn="CL31A476MPHNNNE", lcsc="C96123", fp="Capacitor_SMD:C_1206_3216Metric", vr=10, price=0.05)
R06 = "Resistor_SMD:R_0603_1608Metric"
OPA1612 = dict(value="OPA1612", mpn="OPA1612AIDR", lcsc="C94590", price_usd=0.93, datasheet="https://www.ti.com/product/OPA1612",
               symbol="Amplifier_Operational:OPA1612AxD", footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
               theta_ja=120, tj_max_c=150)
SP_OPA1612 = {"lib": LIB, "subckt": "OPA1612_DUAL_FIT", "ports": ["3", "2", "1", "5", "6", "7", "8", "4"], "source": "fit"}
TVS = dict(value="TPD1E10B06", mpn="TPD1E10B06DPYT", lcsc="C1847652", price_usd=0.24,
           symbol="Power_Protection:TPD1E05U06DPY", footprint="Package_SON:Texas_DPY0002A_0.6x1mm_P0.65mm",
           spice={"lib": LIB, "subckt": "TVS_FIT", "ports": ["1", "2"], "source": "fit"})
FBI = dict(value="BLM18PG121SN1D", mpn="BLM18PG121SN1D", lcsc="C14709", price_usd=0.015, symbol="Device:FerriteBead",
           footprint="Inductor_SMD:L_0603_1608Metric", spice={"lib": LIB, "subckt": "FB_INJ", "ports": ["1", "2"], "source": "behavioral"})

# ================================================================ DAC ES9039Q2M (group audio_dac)
G = "audio_dac"
add("U900", "ES9039Q2M (datasheet v0.2.3 p.10-12 pins, p.97-100 application): I2S/I2C slave of the XU316 (XMOS USB Audio reference: xcore is the I2S master, CODEC_MASTER=0), MODE=GND -> I2C, ADDR1/ADDR0 = GND -> 0x90",
    value="ES9039Q2M", mpn="ES9039Q2M", price_usd=9.0, datasheet="notes/datasheets/es9039q2m.md",
    symbol="kb_audio:ES9039Q2M", footprint="Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.3x3.3mm",
    package="QFN-32", package_pads=33, package_pitch_mm=0.5, group=G, theta_ja=40, tj_max_c=125, spice=False,
    pins={"1": "3V3_A", "8": "3V3_A", "31": "3V3_A", "3": "DAC_LP", "2": "DAC_LN", "7": "DAC_RP", "6": "DAC_RN",
          "4": "AGND", "5": "AGND", "11": "AGND", "30": "AGND", "33": "AGND", "17": "AGND", "19": "AGND", "20": "AGND",
          "29": "AGND", "9": "DAC_DVDD", "10": "3V3_DA", "12": "I2S_BCLK", "13": "I2S_LRCK", "14": "I2S_DOUT",
          "15": "I2C_SCL", "16": "I2C_SDA", "18": "DAC_EN", "32": "MCLK"},
    nc=["21", "22", "23", "24", "25", "26", "27", "28"])
C("C900", "4.7uF", "3V3_A", "AGND", G, dec="U900", **C4U7, comment="AVCC_DAC1: 4.7 uF + 100 nF (p.100)")
C("C901", "100nF", "3V3_A", "AGND", G, dec="U900", **C100N)
C("C902", "4.7uF", "3V3_A", "AGND", G, dec="U900", **C4U7, comment="AVCC_DAC2")
C("C903", "100nF", "3V3_A", "AGND", G, dec="U900", **C100N)
C("C904", "1uF", "3V3_A", "AGND", G, dec="U900", **C1U, comment="VCCA 1 uF (p.100)")
C("C905", "1uF", "3V3_DA", "AGND", G, dec="U900", **C1U, comment="AVDD (digital supply) 1 uF + 100 nF")
C("C906", "100nF", "3V3_DA", "AGND", G, dec="U900", **C100N)
C("C907", "1uF", "DAC_DVDD", "AGND", G, dec="U900", **C1U, comment="DVDD 1.2 V internal regulator output, 1 uF (p.97; ESS shows tantalum, X7R MLCC used)")
R("R901", "100k", "DAC_EN", "AGND", G, comment="CHIP_EN held low until the XU316 enables it after the supplies (power-up order p.51)")
R("R900", "0", "AGND", "GND", G, fp=R06, mpn="0603 jumper 0R", price=0.002, spice=False,
  comment="single-point audio ground: AGND joined to GND only here (R35, C11). Layout: place under the DAC/ADC digital edge, both nets pour on their own side")

# ---------------------------------------------------------------- I/V (2 x OPA1612), DC-blocked DAC outputs, Vg = 0
for ch, U, rb, cb in (("L", "U910", 910, 910), ("R", "U911", 914, 914)):
    p, n = f"DAC_{ch}P", f"DAC_{ch}N"
    sp, sn = f"IVS_{ch}P", f"IVS_{ch}N"
    op, on = f"IV_{ch}P", f"IV_{ch}N"
    add(U, f"I/V {ch}: Rf 383 ohm 0.1 % || 1.8 nF C0G (231 kHz); + inputs at AGND (Vg = 0); DAC pins DC-blocked by 330 uF so no AVCC/2 offset current reaches the 100 ohm diff network",
        **OPA1612, group=G, spice=SP_OPA1612,
        pins={"3": "AGND", "2": sp, "1": op, "5": "AGND", "6": sn, "7": on, "8": "AUD_P4V5", "4": "AUD_N4V5"})
    C(f"C{cb}", "330uF", p, sp, G, mpn="T520B337M006ATE025", fp="Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B",
      vr=6.3, price=0.30, symbol="Device:C_Polarized", tol=0.2,
      comment=f"DC block DAC{'1' if ch=='L' else '2'} -> I/V summing node; + at the DAC pin (1.65 V DC); -0.017 dB at 20 Hz with Rdac 390")
    C(f"C{cb+1}", "330uF", n, sn, G, mpn="T520B337M006ATE025", fp="Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B",
      vr=6.3, price=0.30, symbol="Device:C_Polarized", tol=0.2)
    R(f"R{rb}", "383", op, sp, G, tol=0.001, fp=R06, mpn="0603 0.1% 25ppm 383R thin film", price=0.02)
    R(f"R{rb+1}", "383", on, sn, G, tol=0.001, fp=R06, mpn="0603 0.1% 25ppm 383R thin film", price=0.02)
    C(f"C{cb+2}", "1.8nF", op, sp, G, mpn="0603 C0G 1.8nF 50V 5%", fp="Capacitor_SMD:C_0603_1608Metric", vr=50, tol=0.05, price=0.005)
    C(f"C{cb+3}", "1.8nF", on, sn, G, mpn="0603 C0G 1.8nF 50V 5%", fp="Capacitor_SMD:C_0603_1608Metric", vr=50, tol=0.05, price=0.005)
C("C918", "100nF", "AUD_P4V5", "AGND", G, dec="U910", **C100N)
C("C919", "100nF", "AUD_N4V5", "AGND", G, dec="U910", **C100N)
C("C920", "100nF", "AUD_P4V5", "AGND", G, dec="U911", **C100N)
C("C921", "100nF", "AUD_N4V5", "AGND", G, dec="U911", **C100N)

# ---------------------------------------------------------------- diff-to-SE, 100 ohm network, 6.8 nF (234 kHz)
add("U912", "diff-to-SE L (A) and R (B), gain 1: out = IV_xN - IV_xP; 100 ohm 0.1 % network (parts-audio noise budget needs 100 ohm, 200 ohm fails)",
    **OPA1612, group=G, spice=SP_OPA1612,
    pins={"3": "DS_LP", "2": "DS_LM", "1": "DS_LO", "5": "DS_RP", "6": "DS_RM", "7": "DS_RO", "8": "AUD_P4V5", "4": "AUD_N4V5"})
k = 922
for ch in ("L", "R"):
    R(f"R{k}", "100", f"IV_{ch}P", f"DS_{ch}M", G, tol=0.001, fp=R06, mpn="0603 0.1% 25ppm 100R thin film", price=0.02)
    R(f"R{k+1}", "100", f"DS_{ch}M", f"DS_{ch}O", G, tol=0.001, fp=R06, mpn="0603 0.1% 25ppm 100R thin film", price=0.02)
    R(f"R{k+2}", "100", f"IV_{ch}N", f"DS_{ch}P", G, tol=0.001, fp=R06, mpn="0603 0.1% 25ppm 100R thin film", price=0.02)
    R(f"R{k+3}", "100", f"DS_{ch}P", "AGND", G, tol=0.001, fp=R06, mpn="0603 0.1% 25ppm 100R thin film", price=0.02)
    C(f"C{k}", "6.8nF", f"DS_{ch}M", f"DS_{ch}O", G, mpn="0805 C0G 6.8nF 50V 5%", fp="Capacitor_SMD:C_0805_2012Metric", vr=50, tol=0.05, price=0.01)
    C(f"C{k+1}", "6.8nF", f"DS_{ch}P", "AGND", G, mpn="0805 C0G 6.8nF 50V 5%", fp="Capacitor_SMD:C_0805_2012Metric", vr=50, tol=0.05, price=0.01)
    k += 4
C("C930", "100nF", "AUD_P4V5", "AGND", G, dec="U912", **C100N)
C("C931", "100nF", "AUD_N4V5", "AGND", G, dec="U912", **C100N)

# ================================================================ headphone buffer, output, jack (group audio_hp)
G = "audio_hp"
add("U913", "OPA1622 gain-1 buffers, EN = HP_TN (high with a plug in: 1 M to 3V3_A; low with the jack empty: TN shorted to T, 10 k to AGND)",
    value="OPA1622", mpn="OPA1622IDRCR", price_usd=2.5, datasheet="https://www.ti.com/product/OPA1622",
    symbol="Amplifier_Audio:OPA1622", footprint="Package_SON:Texas_S-PVSON-N10", group=G, theta_ja=50, tj_max_c=150,
    spice={"lib": LIB, "subckt": "OPA1622_DUAL_FIT", "ports": ["1", "10", "9", "5", "6", "7", "2", "4", "8", "3"], "source": "fit"},
    pins={"1": "DS_LO", "10": "HPA_L", "9": "HPA_L", "5": "DS_RO", "6": "HPA_R", "7": "HPA_R", "2": "AUD_P4V5",
          "4": "AUD_N4V5", "11": "AUD_N4V5", "3": "AGND", "8": "HP_TN"})
C("C932", "100nF", "AUD_P4V5", "AGND", G, dec="U913", **C100N)
C("C933", "100nF", "AUD_N4V5", "AGND", G, dec="U913", **C100N)
C("C934", "10uF", "AUD_P4V5", "AGND", G, dec="U913", **C10U)
C("C935", "10uF", "AUD_N4V5", "AGND", G, dec="U913", **C10U)
R("R930", "0.47", "HPA_L", "HP_L", G, fp=R06, mpn="0603 1% 0.47R", price=0.003, comment="output series 0.47 ohm (arch 5.1, R32)")
R("R931", "0.47", "HPA_R", "HP_R", G, fp=R06, mpn="0603 1% 0.47R", price=0.003)
R("R932", "10k", "HP_L", "AGND", G, comment="defines T when the amp is off (detect: empty jack shorts TN to T -> EN low)")
R("R933", "10k", "HP_R", "AGND", G)
R("R934", "1M", "3V3_A", "HP_TN", G)
add("J930", "headphone jack SJ-3524-SMT: T = left, R = right, S = AGND, TN opens with a plug in",
    value="SJ-3524-SMT", mpn="SJ-3524-SMT-TR", price_usd=1.0, symbol="Connector_Audio:AudioJack3_SwitchT",
    footprint="Connector_Audio:Jack_3.5mm_CUI_SJ-3524-SMT_Horizontal", group=G, height_mm=5.37, spice=False,
    pins={"T": "HP_L", "R": "HP_R", "S": "AGND", "TN": "HP_TN"})
add("D930", "ESD at HP tip", **TVS, group=G, pins={"1": "HP_L", "2": "AGND"})
add("D931", "ESD at HP ring", **TVS, group=G, pins={"1": "HP_R", "2": "AGND"})
add("D932", "ESD at HP detect contact", **TVS, group=G, pins={"1": "HP_TN", "2": "AGND"})
add("M930", "JACK_DET buffer for the RP2040 GPIO26 (TN swings with T when the jack is empty): JACK_DET low = headphones in",
    value="2N7002", mpn="2N7002", lcsc="C8545", price_usd=0.0155, symbol="Transistor_FET:2N7002",
    footprint="Package_TO_SOT_SMD:SOT-23", group=G, spice={"model": "NMOS_LOGIC", "ports": ["3", "1", "2"]},
    pins={"1": "HP_TN", "2": "AGND", "3": "JACK_DET"})
R("R935", "100k", "3V3_D", "JACK_DET", G)

# ================================================================ audio rails (group audio_ldo)
G = "audio_ldo"
add("FB950", "CLC pre-filter (TPS65131 2 x 10 uF / bead / 10 uF) for the + LDOs; SPICE carries the R22 noise injection", **FBI, group=G, pins={"1": "AUD_P5V5", "2": "AUD_P5V5F"})
C("C950", "10uF", "AUD_P5V5F", "AGND", G, dec="U950", **C10U)
add("U950", "+4.5 V op-amp rail: ADP7118 adj, VOUT = 1.2 x (1 + 27.4k/10.0k) = 4.488 V",
    value="ADP7118", mpn="ADP7118ACPZN-R7", lcsc="C579266", price_usd=1.9, symbol="Regulator_Linear:ADP7118ACPZN",
    footprint="Package_CSP:LFCSP-6-1EP_2x2mm_P0.65mm_EP1x1.6mm", group=G, theta_ja=110, tj_max_c=125,
    spice={"lib": LIB, "subckt": "ADP7118_FIT", "ports": ["6", "1", "3", "2", "4"], "source": "fit"},
    pins={"6": "AUD_P5V5F", "4": "AUD_P5V5F", "1": "AUD_P4V5", "2": "P45_ADJ", "3": "AGND", "7": "AGND", "5": "P45_SS"})
R("R950", "27.4k", "AUD_P4V5", "P45_ADJ", G, tol=0.001, mpn="0402 0.1% 27.4k", price=0.01)
R("R951", "10k", "P45_ADJ", "AGND", G, tol=0.001, mpn="0402 0.1% 10k", price=0.01)
C("C951", "10nF", "P45_SS", "AGND", G, mpn="CL05B103KB5NNNC", lcsc="C15195", vr=50, price=0.002)
C("C952", "10uF", "AUD_P4V5", "AGND", G, dec="U950", **C10U)
C("C953", "100nF", "AUD_P4V5", "AGND", G, dec="U950", **C100N)
add("FB951", "CLC pre-filter for the -4.5 V LDO", **FBI, group=G, pins={"1": "AUD_N5V5", "2": "AUD_N5V5F"})
C("C954", "10uF", "AGND", "AUD_N5V5F", G, dec="U951", **C10U)
add("U951", "-4.5 V op-amp rail: LT3094, VOUT = -100 uA x 45.3 k = -4.53 V, CSET 470 nF",
    value="LT3094", mpn="LT3094EMSE#TRPBF", lcsc="C670494", price_usd=5.48, symbol="Regulator_Linear:LT3094xMSE",
    footprint="Package_SO:MSOP-12-1EP_3x4mm_P0.65mm_EP1.65x2.85mm", group=G, theta_ja=40, tj_max_c=125,
    spice={"lib": LIB, "subckt": "LT3094_FIT", "ports": ["1", "11", "9", "8", "3"], "source": "fit"},
    pins={"1": "AUD_N5V5F", "2": "AUD_N5V5F", "13": "AUD_N5V5F", "3": "AUD_N5V5F", "11": "AUD_N4V5", "12": "AUD_N4V5",
          "10": "AUD_N4V5", "9": "AGND", "8": "N45_SET", "6": "AGND", "5": "AUD_N5V5F"},
    nc=["4", "7"])
R("R952", "45.3k", "N45_SET", "AGND", G, tol=0.001, mpn="0402 0.1% 45.3k", price=0.01)
C("C955", "470nF", "AGND", "N45_SET", G, **C470N, comment="CSET 470 nF: 21 ms start (4.7 uF took 0.2 s and 1 uF 45 ms, still charging the rail in the 150 ms bus transients, +0.5 mA host); RSET noise corner 7.5 Hz")
C("C956", "10uF", "AGND", "AUD_N4V5", G, dec="U951", **C10U)
C("C957", "100nF", "AGND", "AUD_N4V5", G, dec="U951", **C100N)
add("FB952", "CLC pre-filter 5V_SYS -> 3V3_A LDO (DAC analog)", **FBI, group=G, pins={"1": "5V_SYS", "2": "5V_AF"})
C("C958", "10uF", "5V_AF", "AGND", G, dec="U952", **C10U)
add("U952", "3V3_A (DAC AVCC_DAC1/2, VCCA, mic bias): LT3045, 100 uA x 33.2 k = 3.32 V, CSET 470 nF (ESS p.97: ultra-low-noise regulator on AVCC_DAC)",
    value="LT3045", mpn="LT3045EMSE#TRPBF", lcsc="C666577", price_usd=5.77, symbol="Regulator_Linear:LT3045xMSE",
    footprint="Package_SO:MSOP-12-1EP_3x4mm_P0.65mm_EP1.65x2.85mm", group=G, theta_ja=40, tj_max_c=125,
    spice={"lib": LIB, "subckt": "LT3045_FIT", "ports": ["1", "11", "9", "8", "4"], "source": "fit"},
    pins={"1": "5V_AF", "2": "5V_AF", "3": "5V_AF", "4": "5V_AF", "7": "5V_AF", "11": "3V3_A", "12": "3V3_A",
          "10": "3V3_A", "9": "AGND", "13": "AGND", "8": "A33_SET", "6": "AGND"},
    nc=["5"])
R("R953", "33.2k", "A33_SET", "AGND", G, tol=0.001, mpn="0402 0.1% 33.2k", price=0.01)
C("C959", "470nF", "A33_SET", "AGND", G, **C470N, comment="CSET 470 nF: 16 ms start; RSET noise corner 10 Hz")
C("C960", "10uF", "3V3_A", "AGND", G, dec="U952", **C10U)
C("C961", "100nF", "3V3_A", "AGND", G, dec="U952", **C100N)
add("U953", "ADC_4V5 (ES9822PRO AVCC 4.5 V, mic preamp single supply): LT3045, 100 uA x 45.3 k = 4.53 V; EN/UV = ADC_PWR_EN (off when not recording: bus-mode budget)",
    value="LT3045", mpn="LT3045EMSE#TRPBF", lcsc="C666577", price_usd=5.77, symbol="Regulator_Linear:LT3045xMSE",
    footprint="Package_SO:MSOP-12-1EP_3x4mm_P0.65mm_EP1.65x2.85mm", group=G, theta_ja=40, tj_max_c=125,
    spice={"lib": LIB, "subckt": "LT3045_FIT", "ports": ["1", "11", "9", "8", "4"], "source": "fit"},
    pins={"1": "AUD_P5V5F", "2": "AUD_P5V5F", "3": "AUD_P5V5F", "7": "AUD_P5V5F", "4": "ADC_PWR_EN", "11": "ADC_4V5",
          "12": "ADC_4V5", "10": "ADC_4V5", "9": "AGND", "13": "AGND", "8": "A45_SET", "6": "AGND"},
    nc=["5"])
R("R954", "45.3k", "A45_SET", "AGND", G, tol=0.001, mpn="0402 0.1% 45.3k", price=0.01)
C("C962", "470nF", "A45_SET", "AGND", G, **C470N, comment="CSET 470 nF: 21 ms start; RSET noise corner 7.5 Hz")
C("C963", "10uF", "ADC_4V5", "AGND", G, dec="U953", **C10U)
C("C964", "100nF", "ADC_4V5", "AGND", G, dec="U953", **C100N)
C("C968", "1uF", "AUD_P5V5F", "AGND", G, dec="U953", **C1U)
R("R955", "100k", "ADC_PWR_EN", "AGND", G)
add("FB953", "3V3_D -> 3V3_DA (DAC/ADC AVDD digital supplies)", value="BLM18PG121SN1D", mpn="BLM18PG121SN1D", lcsc="C14709",
    price_usd=0.015, symbol="Device:FerriteBead", footprint="Inductor_SMD:L_0603_1608Metric", group=G, spice=False,
    pins={"1": "3V3_D", "2": "3V3_DA"})
C("C965", "10uF", "3V3_DA", "AGND", G, **C10U)
C("C966", "100nF", "3V3_DA", "AGND", G, **C100N)
R("R956", "470", "3V3_A", "2V5_BIAS", G, tol=0.001, mpn="0402 0.1% 470R", price=0.01, comment="2V5_BIAS = 3.32 x 1.43/1.90 = 2.50 V, 354 ohm source, 47 uF (9.6 Hz)")
R("R957", "1.43k", "2V5_BIAS", "AGND", G, tol=0.001, mpn="0402 0.1% 1.43k", price=0.01)
C("C967", "47uF", "2V5_BIAS", "AGND", G, **C47U)

# ================================================================ mic front end (group audio_mic)
G = "audio_mic"
add("J980", "mic jack SJ-3524-SMT: T and R = mic + bias (PC plug-in power; a ring-bias mic is also fed), S = AGND, TN = detect",
    value="SJ-3524-SMT", mpn="SJ-3524-SMT-TR", price_usd=1.0, symbol="Connector_Audio:AudioJack3_SwitchT",
    footprint="Connector_Audio:Jack_3.5mm_CUI_SJ-3524-SMT_Horizontal", group=G, height_mm=5.37, spice=False,
    pins={"T": "MIC_T", "R": "MIC_T", "S": "AGND", "TN": "MIC_TN"})
R("R980", "2.2k", "2V5_BIAS", "MIC_T", G, tol=0.001, fp=R06, mpn="0603 0.1% 2.2k thin film", price=0.02, comment="R33 plug-in power 2.2 k")
R("R981", "1M", "MIC_TN", "AGND", G, comment="detect: empty jack -> TN = T (bias 2.5 V, high); plug in -> TN open, 1 M low")
R("R982", "10k", "MIC_TN", "MIC_DET", G)
add("D980", "ESD at mic tip/ring", **TVS, group=G, pins={"1": "MIC_T", "2": "AGND"})
add("D981", "ESD at mic detect contact", **TVS, group=G, pins={"1": "MIC_TN", "2": "AGND"})
R("R983", "47", "MIC_T", "MIC_RF", G, tol=0.001, fp=R06, mpn="0603 0.1% 47R thin film", price=0.02, comment="RF filter 47 ohm + 1 nF (3.4 MHz, arch 7 corner kept; 47 ohm for the R11 noise)")
C("C980", "1nF", "MIC_RF", "AGND", G, mpn="0402 C0G 1nF 50V", vr=50, tol=0.05)
C("C981", "10uF", "MIC_RF", "MIC_IN", G, **C10U, comment="AC coupling (0.16 Hz with 100 k)")
R("R984", "100k", "MIC_IN", "MIC_CM", G)
R("R985", "10k", "ADC_4V5", "MIC_CM", G, tol=0.001, mpn="0402 0.1% 10k", price=0.01, comment="MIC_CM = ADC_4V5 / 2 = 2.27 V: preamp mid-supply and ADC input common mode [assumed, B6]")
R("R986", "10k", "MIC_CM", "AGND", G, tol=0.001, mpn="0402 0.1% 10k", price=0.01)
C("C982", "47uF", "MIC_CM", "AGND", G, **C47U)
add("U980", "mic preamp: A non-inverting gain 1 (FET off) or 1 + 1.69k/15.4 = 110 (FET on, 15 ohm + 4 x 100 uF + Ron), single 4.5 V supply around MIC_CM; B inverter (220 / 220) for the differential ADC drive. Low-impedance feedback: at gain 1 the Rf thermal noise is in series with the output (15 k gave 2.03 uV input-referred, notes/circuit.md)",
    **OPA1612, group=G, spice=SP_OPA1612,
    pins={"3": "MIC_IN", "2": "MIC_FB", "1": "MIC_PA", "5": "MIC_CM", "6": "MIC_INV", "7": "MIC_PB", "8": "ADC_4V5", "4": "AGND"})
C("C983", "100nF", "ADC_4V5", "AGND", G, dec="U980", **C100N)
R("R987", "1.69k", "MIC_PA", "MIC_FB", G, tol=0.001, fp=R06, mpn="0603 0.1% 1.69k thin film", price=0.02)
R("R988", "15", "MIC_FB", "MIC_RG", G, tol=0.001, fp=R06, mpn="0603 0.1% 15R thin film", price=0.02)
C("C984", "100uF", "MIC_RG", "MIC_SW", G, mpn="CL32A107MQVNNNE", lcsc="C15008", fp="Capacitor_SMD:C_1210_3225Metric", vr=6.3, price=0.12, tol=0.2,
  comment="DC block of the gain leg, 4 x 100 uF with C988/C989/C995 (26.5 Hz nominal with 15 ohm, about 40 Hz with X5R bias derating), DC gain stays 1")
for _r in ("C988", "C989", "C995"):
    C(_r, "100uF", "MIC_RG", "MIC_SW", G, mpn="CL32A107MQVNNNE", lcsc="C15008", fp="Capacitor_SMD:C_1210_3225Metric", vr=6.3, price=0.12, tol=0.2)
add("M980", "gain switch, MIC_GAIN high = +40 dB (R11 max gain)", value="AO3400A", mpn="AO3400A", lcsc="C20917", price_usd=0.02,
    symbol="Transistor_FET:2N7002", footprint="Package_TO_SOT_SMD:SOT-23", group=G,
    spice={"model": "NMOS_LOGIC", "ports": ["3", "1", "2"]}, pins={"1": "MIC_GAIN", "2": "AGND", "3": "MIC_SW"})
R("R989", "100k", "MIC_GAIN", "AGND", G)
R("R994", "100k", "MIC_SW", "AGND", G, comment="bleeder: holds the gain-leg capacitors charged to MIC_CM with the FET off, so switching to +40 dB gives no step (pop); also defines the node in SPICE")
R("R990", "220", "MIC_PA", "MIC_INV", G, tol=0.001, fp=R06, mpn="0603 0.1% 220R thin film", price=0.02)
R("R991", "220", "MIC_INV", "MIC_PB", G, tol=0.001, fp=R06, mpn="0603 0.1% 220R thin film", price=0.02)
R("R992", "100", "MIC_PA", "ADC_INP", G, tol=0.001, fp=R06, mpn="0603 0.1% 100R thin film", price=0.02, comment="anti-alias: 2 x 100 ohm, 2.2 nF diff + 2 x 220 pF: 344 kHz")
R("R993", "100", "MIC_PB", "ADC_INM", G, tol=0.001, fp=R06, mpn="0603 0.1% 100R thin film", price=0.02)
C("C985", "2.2nF", "ADC_INP", "ADC_INM", G, mpn="0603 C0G 2.2nF 50V 5%", fp="Capacitor_SMD:C_0603_1608Metric", vr=50, tol=0.05, price=0.005)
C("C986", "220pF", "ADC_INP", "AGND", G, mpn="0402 C0G 220pF 50V 5%", vr=50, tol=0.05)
C("C987", "220pF", "ADC_INM", "AGND", G, mpn="0402 C0G 220pF 50V 5%", vr=50, tol=0.05)

# ---------------------------------------------------------------- ADC ES9822PRO (pins 24-41 from a third-party symbol: unverified, B6)
add("U1000", "ES9822PRO: both channels in parallel on the one mic (mono, +3 dB DNR); I2S slave of the XU316; pins relied on that are unverified (24-41): 25 SDA, 26 SCL, 27/28 ADDR, 29 DVDD, 30 AVDD, 31/40/41 grounds, 37-39 GPIO3/2/1 as I2S DATA/LRCK/BCLK [assumed]",
    value="ES9822PRO", mpn="ES9822PRO", price_usd=7.0, symbol="kb_audio:ES9822PRO",
    footprint="Package_DFN_QFN:QFN-40-1EP_5x5mm_P0.4mm_EP3.8x3.8mm", package="QFN-40", package_pads=41, package_pitch_mm=0.4,
    group=G, theta_ja=40, tj_max_c=125, spice=False,
    pins={"5": "ADC_4V5", "10": "ADC_4V5", "21": "ADC_4V5", "6": "AGND", "11": "AGND", "20": "AGND", "31": "AGND",
          "40": "AGND", "41": "AGND", "3": "AGND", "9": "ADC_VREFL", "22": "ADC_VREFR", "12": "ADC_INP", "19": "ADC_INP",
          "13": "ADC_INM", "18": "ADC_INM", "23": "ADC_EN", "25": "I2C_SDA", "26": "I2C_SCL", "27": "3V3_DA",
          "28": "AGND", "29": "ADC_DVDD", "30": "3V3_DA", "8": "MCLK", "39": "I2S_BCLK", "38": "I2S_LRCK", "37": "I2S_DIN"},
    nc=["1", "2", "4", "7", "14", "15", "16", "17", "24", "32", "33", "34", "35", "36"])
C("C1000", "100nF", "ADC_4V5", "AGND", G, dec="U1000", **C100N)
C("C1001", "100nF", "ADC_4V5", "AGND", G, dec="U1000", **C100N)
C("C1002", "100nF", "ADC_4V5", "AGND", G, dec="U1000", **C100N)
C("C1003", "10uF", "ADC_4V5", "AGND", G, dec="U1000", **C10U)
C("C1004", "10uF", "ADC_VREFL", "AGND", G, dec="U1000", **C10U, comment="VREF_L/R reference decoupling [assumed, B6]")
C("C1005", "10uF", "ADC_VREFR", "AGND", G, dec="U1000", **C10U)
C("C1006", "1uF", "ADC_DVDD", "AGND", G, dec="U1000", **C1U, comment="DVDD treated as an internal regulator output like the DAC [assumed, B6]")
C("C1007", "1uF", "3V3_DA", "AGND", G, dec="U1000", **C1U)
C("C1008", "100nF", "3V3_DA", "AGND", G, dec="U1000", **C100N)
R("R1000", "100k", "ADC_EN", "AGND", G)

# ================================================================ USB audio bridge XU316 (group audio_bridge)
G = "audio_bridge"
xu_pins = {"1": "XQ_D1", "2": "XQ_D3", "3": "XQ_CS", "5": "XQ_CLK", "59": "XQ_D0", "60": "XQ_D2", "6": "I2C_SDA", "7": "I2C_SCL",
           "8": "3V3_D", "38": "3V3_D", "52": "3V3_D", "30": "3V3_D", "17": "1V8_D", "26": "1V8_D", "31": "1V8_D", "65": "GND",
           "22": "XU_PLL", "15": "XU_XO", "16": "XU_XI", "18": "XU_TDI", "20": "XU_TDO", "21": "XU_RST_N", "23": "XU_TMS",
           "24": "XU_TCK", "28": "AUD_DM", "29": "AUD_DP", "32": "MIC_GAIN", "40": "ADC_PWR_EN", "41": "MCLK",
           "43": "XI2S_BCLK", "44": "XI2S_LRCK", "45": "XI2S_DOUT", "46": "OSC48_EN", "47": "I2S_DIN", "48": "DAC_EN",
           "50": "OSC44_EN", "51": "ADC_EN", "53": "MIC_DET"}
for p in ("4", "12", "19", "27", "34", "42", "49", "57", "61", "62", "63", "64"):
    xu_pins[p] = "0V9_CORE"
add("U1100", "XU316-1024-QF60B (datasheet XM014429B p.5-7 pins, p.14-19 boot/clock, p.26-29 integration): USB HS on USB_DP/DM direct (no series parts), QSPI boot (X0D04/05/06 low at reset = 000), 24 MHz crystal, I2S master on X0D35 MCLK in / X0D36-39, I2C on X0D00/X0D11",
    value="XU316-1024-QF60B", mpn="XU316-1024-QF60B-C24", price_usd=6.8, datasheet="notes/datasheets/xu316-1024-qf60b.md",
    symbol="kb_audio:XU316-1024-QF60B", footprint="kb_audio:QFN-60-5EP_7x7mm_P0.4mm_XU316", package="QFN-60", package_pads=65,
    package_pitch_mm=0.4, group=G, theta_ja=30, tj_max_c=125, spice=False, pins=xu_pins,
    nc=["9", "10", "11", "13", "14", "25", "33", "35", "36", "37", "39", "54", "55", "56", "58"])
for i in range(8):
    C(f"C{1110+i}", "100nF", "0V9_CORE", "GND", G, dec="U1100", **C100N, comment="8 x 100 nF on VDD (p.26)" if i == 0 else "")
C("C1118", "10uF", "0V9_CORE", "GND", G, dec="U1100", **C10U, comment="bulk >= 10 uF per supply (p.26, G.6)")
add("FB1100", "PLL_AVDD filter: 600 ohm at 100 MHz, DCR < 1 ohm (p.26; BKH1005LM601-T class)", value="BLM15AG601SN1D",
    mpn="BLM15AG601SN1D", price_usd=0.02, symbol="Device:FerriteBead", footprint="Inductor_SMD:L_0402_1005Metric", group=G,
    spice=False, pins={"1": "0V9_CORE", "2": "XU_PLL"})
C("C1119", "1uF", "XU_PLL", "GND", G, dec="U1100", **C1U, comment="PLL_AVDD 1 uF at the pin")
for i in range(4):
    C(f"C{1120+i}", "100nF", "3V3_D", "GND", G, dec="U1100", **C100N, comment="VDDIOL/R/T, USB_VDD33: 100 nF each" if i == 0 else "")
C("C1124", "10uF", "3V3_D", "GND", G, dec="U1100", **C10U)
for i in range(3):
    C(f"C{1125+i}", "100nF", "1V8_D", "GND", G, dec="U1100", **C100N, comment="VDDIOB18 x2, USB_VDD18" if i == 0 else "")
C("C1128", "10uF", "1V8_D", "GND", G, dec="U1100", **C10U)
add("Y1100", "24 MHz crystal CL 12 pF: 22 pF + 22 pF, 680 ohm in XOUT (p.15 Fig 11)", value="24MHz", mpn="X322524MOB4SI", lcsc="C70590",
    price_usd=0.058, symbol="Device:Crystal_GND24", footprint="Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", group=G, spice=False,
    pins={"1": "XU_XI", "3": "XU_XOR", "2": "GND", "4": "GND"})
R("R1101", "680", "XU_XO", "XU_XOR", G)
C("C1101", "22pF", "XU_XI", "GND", G, mpn="CL05C220JB5NNNC", lcsc="C1555", vr=50, price=0.002)
C("C1102", "22pF", "XU_XOR", "GND", G, mpn="CL05C220JB5NNNC", lcsc="C1555", vr=50, price=0.002)
add("U1101", "QSPI boot flash W25Q64JVSSIM (QE = 1 from the factory, p.18), CS pull-up 10 k to VDDIOL", value="W25Q64JVSSIM",
    mpn="W25Q64JVSSIM", price_usd=1.6, symbol="Memory_Flash:W25Q32JVSS", footprint="Package_SO:SOIC-8_5.275x5.275mm_P1.27mm",
    group=G, spice=False, pins={"1": "XQ_CS", "2": "XQ_D1", "3": "XQ_D2", "4": "GND", "5": "XQ_D0", "6": "XQ_CLK", "7": "XQ_D3", "8": "3V3_D"})
C("C1100", "100nF", "3V3_D", "GND", G, dec="U1101", **C100N)
R("R1100", "10k", "3V3_D", "XQ_CS", G)
for ref, f, en, out, rs, pu in (("Y1101", "24.576MHz", "OSC48_EN", "MCLK48_O", "R1102", ("R1104", "3V3_D", "OSC48_EN")),
                                ("Y1102", "22.5792MHz", "OSC44_EN", "MCLK44_O", "R1103", ("R1105", "OSC44_EN", "GND"))):
    add(ref, f"audio master clock {f} (one enabled at a time; OE low = output Hi-Z)", value=f,
        mpn="SIT1602BC-23-33E-24.576000E" if ref == "Y1101" else "SIT1602BC-23-33E-22.579200E", price_usd=1.2,
        symbol="Oscillator:ASE-xxxMHz", footprint="Oscillator:Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm", group=G, spice=False,
        pins={"1": en, "2": "GND", "3": out, "4": "3V3_D"})
    R(rs, "33", out, "MCLK", G, comment="series termination; the two oscillator outputs share MCLK through these")
    R(pu[0], "10k", pu[1], pu[2], G, comment="24.576 MHz runs by default (pull-up), 22.5792 MHz off (pull-down)" if ref == "Y1101" else "")
C("C1103", "100nF", "3V3_D", "GND", G, dec="Y1101", **C100N)
C("C1104", "100nF", "3V3_D", "GND", G, dec="Y1102", **C100N)
R("R1106", "2.2k", "3V3_D", "I2C_SCL", G)
R("R1107", "2.2k", "3V3_D", "I2C_SDA", G)
for i, s in enumerate(("BCLK", "LRCK", "DOUT")):
    R(f"R{1108+i}", "33", f"XI2S_{s}", f"I2S_{s}", G, comment="I2S series termination at the XU316 (edge rate into the audio region)" if i == 0 else "")
for i, (net, val) in enumerate((("XU_TDI", "TDI"), ("XU_TDO", "TDO"), ("XU_TMS", "TMS"), ("XU_TCK", "TCK"), ("XU_RST_N", "RST_N"), ("1V8_D", "VTREF_1V8"))):
    add(f"TP{1100+i}", "XMOS JTAG / xTAG access pads (G.6: a way to program flash/OTP); RST_N has an internal pull-up (internal POR)" if i == 0 else "",
        value=val, symbol="Connector:TestPoint", footprint="TestPoint:TestPoint_Pad_D1.0mm", group=G, spice=False, pins={"1": net})

# ================================================================ nets
nets = {
    "AGND": {"class": "ground"},
    "3V3_A": {"current_a": 0.05},
    "AUD_P4V5": {"current_a": 0.25},
    "AUD_N4V5": {"current_a": 0.25},
    "ADC_4V5": {"current_a": 0.06},
    "AUD_P5V5F": {"current_a": 0.25, "external_supply": True},
    "AUD_N5V5F": {"current_a": 0.25, "external_supply": True},
    "5V_AF": {"current_a": 0.05, "external_supply": True},
    "3V3_DA": {"current_a": 0.05, "external_supply": True},
    "XU_PLL": {"current_a": 0.01, "external_supply": True},
    "ADC_DVDD": {"external_supply": True},
    "HPA_L": {"current_a": 0.15}, "HPA_R": {"current_a": 0.15}, "HP_L": {"current_a": 0.15}, "HP_R": {"current_a": 0.15},
}
for n in ("DAC_LP", "DAC_LN", "DAC_RP", "DAC_RN", "IVS_LP", "IVS_LN", "IVS_RP", "IVS_RN", "IV_LP", "IV_LN", "IV_RP", "IV_RN",
          "DS_LP", "DS_LM", "DS_LO", "DS_RP", "DS_RM", "DS_RO", "MIC_T", "MIC_RF", "MIC_IN", "MIC_FB", "MIC_RG", "MIC_PA",
          "MIC_PB", "MIC_INV", "ADC_INP", "ADC_INM", "MIC_CM", "2V5_BIAS"):
    nets[n] = {"class": "signal", "sensitive": True}
for n in ("MCLK", "MCLK48_O", "MCLK44_O", "I2S_BCLK", "XI2S_BCLK", "XU_XI", "XU_XO", "XU_XOR", "XQ_CLK"):
    nets[n] = {"class": "clock"}

# ================================================================ testbench
NODESET = (P / "tools" / "pass_c_nodeset.txt").read_text().strip()
src = [".global vbn gdn", NODESET,
       # coupled-noise generators (R22): white noise of a 1 k resistor at 25 C (4.058 nV/rtHz) times the E gain
       "VVBN vbn_s 0 DC 0 AC 0", "RVBN vbn_s vbn_g 1k temp=25", "EVBN vbn 0 vbn_g 0 0",
       "VGDN gdn_s 0 DC 0 AC 0", "RGDN gdn_s gdn_g 1k temp=25", "EGDN gdn 0 gdn_g 0 0",
       # DAC (ES9039Q2M, spice false): per output pin Rdac 390 ohm (noiseless) to AVCC/2, 1.016e-11 A/rtHz noise current,
       # signal current (VSIGAMP x sin 1 kHz, 3.75 mA = 0 dBFS, 7.5 mApp per pin p.54), AC source
       "EDACB dacb 0 $(3V3_A) 0 0.5", "VSIGAMP sigamp 0 DC 0"]
for tag, pin, sgn in (("lp", "U900.3", 1), ("ln", "U900.2", -1), ("rp", "U900.7", 1), ("rn", "U900.6", -1)):
    src += [f"GRD{tag} $({pin}) dacb $({pin}) dacb 2.5641m",
            f"RDN{tag} dn{tag} 0 1k temp=25", f"GDN{tag} dacb $({pin}) dn{tag} 0 2.5033m",
            (f"BDS{tag} 0 $({pin}) I = v(sigamp)*sin(6.2831853e3*time)" if sgn > 0 else f"BDS{tag} $({pin}) 0 I = v(sigamp)*sin(6.2831853e3*time)"),
            (f"IAC{tag} 0 $({pin}) DC 0 AC {0.5 if tag[0] == 'l' else 0}" if sgn > 0 else f"IAC{tag} $({pin}) 0 DC 0 AC {0.5 if tag[0] == 'l' else 0}")]
src += ["RLL $(J930.T) 0 32", "RLR $(J930.R) 0 32", "IZT 0 $(J930.T) DC 0 AC 0",
        # mic: 150 ohm source (thermal noise kept), AC coupled, electret bias draw 0.5 mA
        "VMIC ms 0 DC 0 AC 0", "RMS ms ms2 150", "CMS ms2 $(J980.T) 1", "IEL $(J980.T) 0 DC 0.5m",
        "VADCEN $(ADC_PWR_EN) 0 DC 3.3", "VMICG $(MIC_GAIN) 0 DC 0",
        # ADC (ES9822PRO, spice false): differential input plus its input-referred noise, 125 dB re 2 Vrms = 1.125 uV A-wtd
        # = 10.07 nV/rtHz (A-weighted bandwidth 12.47 kHz) = 2.482 x the 1 k generator
        "RADN adn 0 1k temp=25", "BMADC madc 0 V = v(ADC_INP)-v(ADC_INM)+2.482*v(adn)"]


def aw(tag, inp):
    """IEC 61672 A-weighting: 4 zeros at 0, poles 20.6 (x2), 107.7, 737.9, 12194 (x2) Hz, +2.0 dB (1 kHz = 0 dB);
    ideal E buffers and noiseless G resistors (checked: 1 kHz -0.002 dB, 100 Hz -19.15 dB, 10 kHz -2.49 dB)."""
    t = tag
    L = [f"EA0{t} {t}a0 0 {inp} 1.258925" if "," not in inp else f"EA0{t} {t}a0 0 {inp} 1.258925"]
    poles = [("h", 20.598997), ("h", 20.598997), ("h", 107.65265), ("h", 737.86223), ("l", 12194.217), ("l", 12194.217)]
    prev = f"{t}a0"
    for i, (kind, fp) in enumerate(poles, 1):
        mid, out = f"{t}m{i}", (f"{t}aw" if i == 6 else f"{t}b{i}")
        if kind == "h":
            L += [f"CA{i}{t} {prev} {mid} 1u", f"GA{i}{t} {mid} 0 {mid} 0 {6.2831853 * fp * 1e-6:.9g}"]
        else:
            L += [f"GA{i}{t} {prev} {mid} {prev} {mid} {6.2831853 * fp * 1e-9:.9g}", f"CA{i}{t} {mid} 0 1n"]
        L.append(f"EA{i}{t} {out} 0 {mid} 0 1")
        prev = out
    return L


src += aw("h", "$(J930.T) 0")
src += aw("m", "madc 0")

GV = 1.7434e5     # 100 mV rms white over 20 Hz-20 kHz = 0.7075 mV/rtHz / 4.058 nV/rtHz
GG = 871.7        # 0.5 mV rms (0.5 mohm x 1 A charge-current swing) = 3.537 uV/rtHz
ON_REAL = f"alter evbn gain={GV:g}\nalter egdn gain={GG:g}"
ON_X1E4 = f"alter evbn gain={GV * 1e4:g}\nalter egdn gain={GG * 1e4:g}"
NZ_H = "noise v(haw) iaclp dec 40 20 20k"
NZ_M = "noise v(maw) vmic dec 40 20 20k"
AN = [
    {"name": "hp_noise_32", "cmd": f"alter vmic ac=1\n{NZ_H}", "temps": [0, 25, 40], "monte_carlo": False,
     "realism": "who: user, headphones (32 ohm) on, maximum analog gain (fixed), digital silence; how often: every listening session; limits: R10 0.944 uV intrinsic (C11 split of the 1.0 uVrms A-wtd total); DAC noise 1.016e-11 A/rtHz per pin from DNR 130 dB re 10 Vrms (datasheet p.54, notes/datasheets/es9039q2m.md: 0.60 uV at Rf 374)",
     "measures": [
         {"name": "hp_intrinsic_32", "plot": "noise2", "expr": "onoise_total*1e6", "max": 0.944, "unit": "uVrms", "req": "R10"}]},
    {"name": "hp_total_32", "cmd": f"alter vmic ac=1\n{ON_REAL}\n{NZ_H}", "temps": [0, 25, 40], "monte_carlo": False,
     "realism": "who: user and environment, as hp_noise_32 with the R22 coupled generators on (host VBUS 100 mV rms audio band, 0.5 mV rms ground drop); limits: R10 1.0 uVrms A-wtd total",
     "measures": [
         {"name": "hp_total_32", "plot": "noise2", "expr": "onoise_total*1e6", "max": 1.0, "unit": "uVrms", "req": "R10,R22"}]},
    {"name": "hp_noise_300", "cmd": f"alter rll = 300\n{NZ_H}", "temps": [40], "monte_carlo": False,
     "realism": "who: user, 300 ohm headphones, digital silence; limits as hp_noise_32",
     "measures": [
         {"name": "hp_intrinsic_300", "plot": "noise2", "expr": "onoise_total*1e6", "max": 0.944, "unit": "uVrms", "req": "R10"}]},
    {"name": "hp_total_300", "cmd": f"alter rll = 300\n{ON_REAL}\n{NZ_H}", "temps": [40], "monte_carlo": False,
     "realism": "who: user and environment, 300 ohm headphones with the R22 generators on; limits: R10 1.0 uVrms total",
     "measures": [
         {"name": "hp_total_300", "plot": "noise2", "expr": "onoise_total*1e6", "max": 1.0, "unit": "uVrms", "req": "R10,R22"}]},
    {"name": "r22_coupled", "cmd": f"alter vmic ac=1\nac lin 1 1k 1k\nlet gmic = mag(v(madc)/v(ms))\n{ON_X1E4}\n{NZ_H}\n{NZ_M}", "temps": [40], "monte_carlo": False,
     "realism": "who: environment, host VBUS audio-band noise 100 mV rms (R22) on every audio supply entry (5V_SYS -> 3V3_A LDO, TPS65131 outputs -> +-4.5 V and ADC LDOs: the TPS65131 fit has no line transfer, so 0 dB rejection is assumed) plus a correlated ground drop 0.5 mV rms (0.5 mohm shared plane square x 1 A audio-band charge swing, C11; post-layout Z replaces it); how often: always in bus mode; method: both generators x 1e4 so the intrinsic noise is < 1e-3 of the result, scale 1e-4",
     "measures": [
         {"name": "hp_coupled", "plot": "noise2", "expr": "onoise_total*1e6", "scale": 1e-4, "max": 0.33, "unit": "uVrms", "req": "R22"},
         {"name": "mic_coupled_min_gain", "plot": "noise4", "expr": "onoise_total*1e6/ac1.gmic", "scale": 1e-4, "max": 0.33, "unit": "uVrms", "req": "R22"}]},
    {"name": "r22_tones", "cmd": "alter iaclp ac=0\nalter evbn gain=1\nalter vvbn ac=1\nac lin 13 400k 1.6meg\nmeas ac m_t400 find vm(hp_l) at=400k\nmeas ac m_t1m4 find vm(hp_l) at=1.4meg",
     "temps": [25], "monte_carlo": False,
     "realism": "who: environment, converter ripple tones: LM61460 5V_SYS 400 kHz, TPS62933 3V3_D 1.2 MHz (no modelled analog path: DAC/ADC AVDD only), TPS65131 1.38 MHz [E] 20 mVpp on each audio supply entry; out of the 20 Hz-20 kHz band, so they add nothing to the A-weighted R22 figure in a linear simulation; listed for intermodulation awareness",
     "measures": [
         {"name": "dbg_tone_400k_uvpk", "expr": "m_t400", "scale": 1e6 * 0.01, "unit": "uVpk"},
         {"name": "dbg_tone_1m38_uvpk", "expr": "m_t1m4", "scale": 1e6 * 0.01, "unit": "uVpk"}]},
    {"name": "mic_noise_min", "cmd": f"alter vmic ac=1\nac lin 1 1k 1k\nlet gmic = mag(v(madc)/v(ms))\nprint gmic\n{NZ_M}", "temps": [0, 25, 40], "monte_carlo": False,
     "realism": "who: user, mic plugged (150 ohm source, 0.5 mA electret), minimum gain (full scale 1 Vrms at the jack), recording; limits: R11 <= 1.0 uVrms A-wtd input-referred (0.944 intrinsic, C11); ADC 125 dB re 2 Vrms [S] (alldatasheet snippet, datasheet not read: B6)",
     "measures": [
         {"name": "dbg_mic_gain_min", "plot": "ac1", "expr": "gmic", "unit": "V/V"},
         {"name": "mic_in_noise_min", "plot": "noise2", "expr": "onoise_total*1e6/ac1.gmic", "max": 0.944, "unit": "uVrms", "req": "R11,R33"}]},
    {"name": "mic_noise_max", "cmd": f"alter vmic ac=1\nalter vmicg dc=3.3\nac lin 1 1k 1k\nlet gmic = mag(v(madc)/v(ms))\nlet gtip = mag(v(madc)/v(mic_t))\nprint gmic gtip\n{NZ_M}", "temps": [0, 25, 40], "monte_carlo": False,
     "realism": "who: user, mic plugged, maximum gain (+40 dB class, 10 mVrms full scale); limits: R11 EIN <= -120 dBV (1.0 uVrms) A-wtd with a 150 ohm source",
     "measures": [
         {"name": "mic_gain_max_db", "plot": "ac1", "expr": "db(gtip/2)", "min": 40, "unit": "dB", "req": "R11"},
         {"name": "dbg_mic_gain_emf_db", "plot": "ac1", "expr": "db(gmic/2)", "unit": "dB"},
         {"name": "mic_ein_max", "plot": "noise2", "expr": "onoise_total*1e6/ac1.gmic", "max": 1.0, "unit": "uVrms", "req": "R11"}]},
    {"name": "mic_bias", "cmd": "op", "temps": [0, 40],
     "realism": "who: user, electret capsule drawing 0.5 mA through the 2.2 k plug-in power; limits: R33 2.0-3.0 V bias",
     "measures": [
         {"name": "bias_source_v", "expr": "v(2V5_BIAS)", "min": 2.0, "max": 3.0, "unit": "V", "req": "R33"},
         {"name": "dbg_tip_v", "expr": "v(MIC_T)", "unit": "V"},
         {"name": "dbg_3v3a", "expr": "v(3V3_A)", "unit": "V"},
         {"name": "dbg_p4v5", "expr": "v(AUD_P4V5)", "unit": "V"},
         {"name": "dbg_n4v5", "expr": "v(AUD_N4V5)", "unit": "V"},
         {"name": "dbg_adc4v5", "expr": "v(ADC_4V5)", "unit": "V"},
         {"name": "dbg_hp_dc_mv", "expr": "v(J930.T)*1e3", "unit": "mV"}]},
]
for load in (32, 300):
    lvl = 1.0 if load == 32 else 2.0
    AN.append({"name": f"hp_level_{load}", "cmd": f"alter rll = {load}\nalter rlr = {load}\nalter vsigamp dc=3.75m\ntran 0.5u 4m 0 0.5u\nmeas tran m_vl rms v(hp_l) from=2m to=4m\nmeas tran m_vr rms v(hp_r) from=2m to=4m",
               "temps": [0, 40],
               "realism": f"who: user, 0 dBFS 1 kHz sine on both channels into {load} ohm (a test signal; music sits >= 10 dB lower); how often: rare; timing: steady state; limits: R29 >= {lvl} Vrms, >= 0.5 V to each rail, no current limit (OPA1622 +145/-130 mA [V], OPA1612 +-30 mA [V])",
               "measures": [
                   {"name": f"hp_vrms_{load}", "expr": "m_vl", "min": lvl, "unit": "Vrms", "req": "R29"},
                   {"name": f"hp_head_pos_{load}", "expr": "vecmin(v(AUD_P4V5))-vecmax(v(HPA_L))", "min": 0.5, "unit": "V", "req": "R29"},
                   {"name": f"hp_head_neg_{load}", "expr": "vecmin(v(HPA_L))-vecmax(v(AUD_N4V5))", "min": 0.5, "unit": "V", "req": "R29"},
                   {"name": f"hp_ipk_{load}", "expr": "vecmax(abs(v(HPA_L)-v(J930.T)))/0.47*1e3", "max": 130, "unit": "mA", "req": "R29"},
                   {"name": f"iv_ipk_{load}", "expr": "vecmax(abs((v(IV_LP)-v(DS_LM))/100+(v(IV_LP)-v(IVS_LP))/383))*1e3", "max": 30, "unit": "mA", "req": "R29"},
                   {"name": f"dbg_p4v5_min_{load}", "expr": "vecmin(v(AUD_P4V5))", "unit": "V"},
                   {"name": f"dbg_vrms_r_{load}", "expr": "m_vr", "unit": "Vrms"}]})
for load in (32, 300):
    AN.append({"name": f"hp_response_{load}", "cmd": f"alter rll = {load}\nac dec 100 20 20k\nmeas ac m_g1k find vdb(hp_l) at=1k\nmeas ac m_rmax max vdb(hp_l) from=20 to=20k\nmeas ac m_rmin min vdb(hp_l) from=20 to=20k", "temps": [25, 40],
               "realism": f"who: user, {load} ohm headphones, 48 / 96 kHz streams (the analog chain is the same at both rates); limits: R31 +-0.1 dB minus the DAC filter ripple 0.0031 dB (ES9039Q2M p.35, worst listed filter): +-0.0969 dB",
               "measures": [
                   {"name": f"dbg_g1k_{load}", "expr": "m_g1k", "unit": "dB"},
                   {"name": f"resp_max_{load}", "expr": "m_rmax", "unit": "dB", "debug": True},
                   {"name": f"resp_min_{load}", "expr": "m_rmin", "unit": "dB", "debug": True},
                   {"name": f"resp_hi_{load}", "expr": f"resp_max_{load}-dbg_g1k_{load}", "max": 0.0969, "unit": "dB", "req": "R31"},
                   {"name": f"resp_lo_{load}", "expr": f"resp_min_{load}-dbg_g1k_{load}", "min": -0.0969, "unit": "dB", "req": "R31"}]})
AN.append({"name": "hp_zout", "cmd": "alter iaclp ac=0\nalter iacln ac=0\nalter rll = 1e9\nalter izt ac=1m\nac dec 20 20 20k\nmeas ac m_zmax max vm(hp_l) from=20 to=20k", "temps": [25, 40],
           "realism": "who: user, multi-driver IEM on the output; test: 1 mA AC into the jack tip, DAC at silence, no load; limits: R32 |Zout| <= 1 ohm incl. 0.47 ohm series, 10 k detect resistor and TVS",
           "measures": [{"name": "hp_zout_max", "expr": "m_zmax", "scale": 1000, "max": 1.0, "unit": "ohm", "req": "R32"}]})


def dump(ref, f, comment):
    out = []
    if comment:
        out.append(f"  # {comment}")
    out.append(f"  {ref}:")
    order = ["value", "mpn", "lcsc", "price_usd", "datasheet", "symbol", "footprint", "package", "package_pads", "package_pitch_mm",
             "group", "decouples", "tolerance", "voltage_rating_v", "height_mm", "theta_ja", "tj_max_c", "spice", "pins", "nc"]
    for key in order + [k for k in f if k not in order]:
        if key in f and f[key] is not None:
            v = f[key]
            out.append(f"    {key}: {q(v)}")
    return "\n".join(out)


comp_txt = "  # ==== PASS C COMPONENTS BEGIN (tools/gen_pass_c.py)\n" + "\n".join(dump(*c) for c in comps if c[0]) + "\n  # ==== PASS C COMPONENTS END\n"
net_txt = "  # ==== PASS C NETS BEGIN (tools/gen_pass_c.py)\n" + "\n".join(f"  {n}: {q(v)}" for n, v in nets.items()) + "\n  # ==== PASS C NETS END\n"
src_txt = "    # ==== PASS C SOURCES BEGIN (tools/gen_pass_c.py)\n" + "\n".join(f"    - {q({'line': s})}" for s in src) + "\n    # ==== PASS C SOURCES END\n"
an_txt = "    # ==== PASS C ANALYSES BEGIN (tools/gen_pass_c.py)\n"
for a in AN:
    # gmin 1e-9: the full-board operating point (pass B + audio) gives NaN in Newton with the default 1e-12 even from the
    # nodeset; 1 nS across the junctions moves the audio nodes by nA (noiseless). KLU also converges but has no noise analysis.
    an_txt += f"    - name: {a['name']}\n      cmd: {q('option gmin=1e-9' + chr(10) + a['cmd'])}\n"
    if "temps" in a:
        an_txt += f"      temps: {q(a['temps'])}\n"
    if a.get("monte_carlo") is False:
        an_txt += "      monte_carlo: false\n"
    an_txt += f"      realism: {q(a['realism'])}\n      measures:\n"
    for m in a["measures"]:
        an_txt += f"        - {q(m)}\n"
an_txt += "    # ==== PASS C ANALYSES END\n"

y = DY.read_text()


def put(text, block, anchor):
    tag = block.splitlines()[0].strip().split(" BEGIN")[0].replace("# ==== ", "")
    pat = re.compile(r"[ ]*# ==== " + re.escape(tag) + r" BEGIN.*?# ==== " + re.escape(tag) + r" END\n", re.S)
    if pat.search(text):
        return pat.sub(lambda m: block, text)
    i = text.index(anchor)
    return text[:i] + block + text[i:]


y = put(y, net_txt, "components:\n")
y = put(y, comp_txt, "# ==== PASS B SIM BEGIN")
y = put(y, src_txt, "  analyses:\n")
y = put(y, an_txt, "budgets:\n")
print(len(comps), "components,", len(nets), "nets,", len(src), "source lines,", len(AN), "analyses")
if "--apply" in sys.argv:
    DY.write_text(y)
    print("design.yaml written")
