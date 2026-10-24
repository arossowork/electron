#!/usr/bin/env python
"""Generate the synthetic ~150 part, 2 layer benchmark board evals/bench/synth150/design.yaml (fixed seed).

Pin numbers come from the KiCad symbol (hwflow Libs.symbol, the code behind `hwflow lib show`) and pad numbers from
the footprint (Libs.footprint, behind `hwflow lib pads`); nothing is typed by hand. Usage:
    uv run python scripts/make_synth_board.py [--seed 7] [--parts 150] [--out evals/bench/synth150]
"""
import argparse
import math
import random
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys_path = str(ROOT / "src")
import sys  # noqa: E402

sys.path.insert(0, sys_path)
from hwflow.layout.place import placer  # noqa: E402
from hwflow.core.design import Design  # noqa: E402
from hwflow.core.libs import Libs  # noqa: E402

SOIC8 = "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
SOIC16 = "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm"
QFN32 = "Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.1x3.1mm"
R0402, C0402 = "Resistor_SMD:R_0402_1005Metric", "Capacitor_SMD:C_0402_1005Metric"
R0603, C0603 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric"
HDR = "Connector_PinHeader_2.54mm:PinHeader_1x0{n}_P2.54mm_Vertical"

# (name, symbol, footprint, supply rail) ; clusters of ICs share a placement group
ICS = [
    ("MCU", "MCU_Microchip_ATmega:ATmega328P-M", QFN32, "+3V3"),
    ("MCU", "MCU_Microchip_ATmega:ATmega328P-M", QFN32, "+3V3"),
    ("MCU", "MCU_Microchip_ATmega:ATmega328P-M", QFN32, "+3V3"),
    ("MCU", "MCU_Microchip_ATmega:ATmega328P-M", QFN32, "+3V3"),
    ("SR", "74xx:74HC595", SOIC16, "+3V3"),
    ("SR", "74xx:74HC595", SOIC16, "+3V3"),
    ("SR", "74xx:74HC595", SOIC16, "+3V3"),
    ("SR", "74xx:74HC595", SOIC16, "+3V3"),
    ("MEM", "Memory_EEPROM:24LC256", SOIC8, "+3V3"),
    ("MEM", "Memory_EEPROM:24LC256", SOIC8, "+3V3"),
    ("AN", "Amplifier_Operational:LM358", SOIC8, "+3V3"),
    ("AN", "Amplifier_Operational:LM358", SOIC8, "+3V3"),
    ("IF", "Interface_UART:MAX3232", SOIC16, "+5V"),
    ("IF", "Interface_CAN_LIN:MCP2551-I-SN", SOIC8, "+5V"),
]
ICS60 = [ICS[0], ICS[1], ICS[4], ICS[12], ICS[8], ICS[10]]  # 2 QFN-32, 2 SOIC-16, 2 SOIC-8
PROFILES = {  # name: (ICs, header list (pins, edge), parts, courtyard area factor for the board size)
    "synth150": (ICS, ((6, "left"), (6, "right"), (6, "top"), (4, "bottom")), 150, 3.2),
    "synth60": (ICS60, ((6, "left"), (4, "right")), 60, 4.0),
}
GND_NAMES = {"GND", "VSS", "V-"}
SUP_NAMES = {"VCC", "VDD", "AVCC", "V+"}
SKIP_TYPES = {"power_out", "no_connect", "power_in"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--profile", default="synth150", choices=list(PROFILES))
    ap.add_argument("--parts", type=int, default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    ics, headers, nparts, factor = PROFILES[a.profile]
    a.parts = a.parts or nparts
    a.out = a.out or str(ROOT / "evals/bench" / a.profile)
    rnd = random.Random(a.seed)
    libs = Libs()
    comps: dict[str, dict] = {}
    cnt: dict[str, int] = {}

    def ref(p):
        cnt[p] = cnt.get(p, 0) + 1
        return f"{p}{cnt[p]}"

    def passive(kind, value, fp, n1, n2, group, **kw):
        r = ref(kind)
        comps[r] = {"value": value, "symbol": f"Device:{kind}", "footprint": fp, "pins": {"1": n1, "2": n2},
                    "group": group, "spice": False, **kw}
        return r

    # ---- ICs: supply, ground and decoupling from the symbol pins
    pool = []  # free signal pins: (ref, pin number, pin type, cluster)
    ic_sup: dict[str, set] = {}
    seq: dict[str, int] = {}
    for grp, sym, fp, rail in ics:
        seq[grp] = seq.get(grp, 0) + 1
        g = f"{grp.lower()}{seq[grp]}"
        pins_sym = libs.symbol(sym)["pins"]
        pads = {p["number"] for p in libs.footprint(fp)["pads"] if p["number"]}
        r = ref("U")
        pins, nc, supply_pins = {}, [], []
        seen = set()
        for p in sorted(pins_sym, key=lambda p: [int(t) if t.isdigit() else t for t in re.findall(r"\d+|\D+", p["number"])]):
            n = p["number"]
            if n in seen:
                continue
            seen.add(n)
            assert n in pads, f"{sym} pin {n} has no pad in {fp}"
            nm = p["name"]
            if nm in GND_NAMES:
                pins[n] = "GND"
            elif nm in SUP_NAMES:
                pins[n] = rail
                supply_pins.append(n)
            elif p["type"] in SKIP_TYPES or nm in ("AREF", "Vref"):
                nc.append(n)
            else:
                pool.append((r, n, p["type"], g))
        comps[r] = {"value": sym.split(":")[1], "symbol": sym, "footprint": fp, "pins": pins, "nc": nc,
                    "group": g, "spice": False}
        ic_sup[r] = set(supply_pins)
        for n in supply_pins:  # one 100 nF decoupling cap per supply pin
            passive("C", "100n", C0402, rail, "GND", g, decouples=r, voltage_rating_v=16)
        if "MAX3232" in sym:  # charge pump capacitors
            for a_, b_ in (("1", "3"), ("4", "5")):
                passive("C", "100n", C0603, f"CP{a_}", f"CP{b_}", g)
                pins[a_], pins[b_] = f"CP{a_}", f"CP{b_}"
            for n_, net in (("2", "VSP"), ("6", "VSM")):
                pins[n_] = net
                passive("C", "100n", C0603, net, "GND", g)
            comps[r]["nc"] = [x for x in comps[r]["nc"] if x not in ("1", "3", "4", "5", "2", "6")]
            pool[:] = [x for x in pool if not (x[0] == r and x[1] in ("1", "3", "4", "5"))]
    # bulk caps at the rails and a 5 V -> 3.3 V LDO
    ldo = ref("U")
    comps[ldo] = {"value": "AP2112K-3.3", "symbol": "Regulator_Linear:AP2112K-3.3",
                  "footprint": "Package_TO_SOT_SMD:SOT-23-5",
                  "pins": {"1": "+5V", "2": "GND", "3": "+5V", "5": "+3V3"}, "nc": ["4"], "group": "pwr", "spice": False}
    for net, v in (("+5V", "10u"), ("+5V", "10u"), ("+3V3", "10u"), ("+3V3", "10u"), ("+3V3", "10u")):
        passive("C", v, C0603, net, "GND", "pwr", voltage_rating_v=16)
    # connectors: J1 power (+5V, GND, 4 signals), J2 to J4 signal headers; their signal pins join the pool first
    conns = []
    for i, (n, edge) in enumerate(headers):
        r = ref("J")
        pins = {}
        pm = [p["number"] for p in libs.symbol(f"Connector_Generic:Conn_01x0{n}")["pins"]]
        pm.sort(key=int)
        for k, num in enumerate(pm):
            if i == 0 and k == 0:
                pins[num] = "+5V"
            elif i == 0 and k == 1:
                pins[num] = "GND"
            elif k == len(pm) - 1 and i in (1, 2):
                pins[num] = "GND"
            else:
                conns.append((r, num, "passive", "io"))
        comps[r] = {"value": f"Conn_01x0{n}", "symbol": f"Connector_Generic:Conn_01x0{n}",
                    "footprint": HDR.format(n=n), "pins": pins, "group": "io", "spice": False,
                    "placement": {"edge": edge}}
    # ---- signal nets between ICs / connectors, ~75 % inside a cluster
    rnd.shuffle(pool)
    clusters: dict[str, list] = {}
    for p in pool:
        clusters.setdefault(re.sub(r"\d+$", "", p[3]), []).append(p)
    nets_meta: dict[str, dict] = {}
    n_net = 0
    free_io = list(conns)

    def take(avoid_ref=None, kinds=None, cl=None):
        src = clusters.get(cl) if cl and clusters.get(cl) else pool
        for i, p in enumerate(src):
            if (avoid_ref and p[0] == avoid_ref) or (kinds and p[2] not in kinds):
                continue
            src.remove(p)
            if src is not pool and p in pool:
                pool.remove(p)
            elif src is pool:
                clusters[re.sub(r"\d+$", "", p[3])].remove(p)
            return p
        return None

    def target():
        return len(comps)

    def assign(p, net):
        comps[p[0]]["pins"][p[1]] = net

    guard = 0
    while target() < a.parts - 1 and guard < 2000:
        guard += 1
        if free_io:
            a_ = free_io.pop(0)
            cl = None
        else:
            a_ = take()
            if a_ is None:
                break
            cl = re.sub(r"\d+$", "", a_[3])
        cl_b = cl if rnd.random() < 0.75 else None
        kinds = None if a_[2] != "output" else {"input", "bidirectional", "passive", "tri_state"}
        b_ = take(avoid_ref=a_[0], kinds=kinds, cl=cl_b)
        if b_ is None:
            b_ = take(avoid_ref=a_[0], kinds=kinds)
        if b_ is None:
            comps[a_[0]].setdefault("nc", []).append(a_[1])  # no partner left: open on purpose
            continue
        n_net += 1
        g = a_[3]
        name = f"S{n_net}"
        if rnd.random() < 0.6:  # series resistor
            assign(a_, name + "A")
            assign(b_, name + "B")
            passive("R", rnd.choice(["33", "100", "470"]), R0402, name + "A", name + "B", g)
            if rnd.random() < 0.15:
                nets_meta[name + "B"] = {"class": "signal", "sensitive": True}
        else:
            assign(a_, name)
            assign(b_, name)
            if rnd.random() < 0.3:
                passive("R", "10k", R0402, name, "+3V3", g)
        if rnd.random() < 0.08:
            nets_meta[name] = {"class": "clock"}
    # fill up to the part count with pull-up / pull-down resistors on existing signal nets (0402)
    sig = [n for n in dict.fromkeys(n for c in comps.values() for n in c["pins"].values()) if n.startswith("S")]
    while target() < a.parts and sig:
        net = rnd.choice(sig)
        owner = next(c for c in comps.values() if net in c["pins"].values())
        passive("R", "10k", R0402, net, rnd.choice(["+3V3", "GND"]), owner["group"])
    # leftovers are open on purpose
    for p in pool:
        comps[p[0]].setdefault("nc", []).append(p[1])
    for p in free_io:
        comps[p[0]].setdefault("nc", []).append(p[1])
    for c in comps.values():
        if "nc" in c:
            c["nc"] = sorted(set(c["nc"]), key=lambda s: [int(t) if t.isdigit() else t for t in re.findall(r"\d+|\D+", s)])
            if not c["nc"]:
                del c["nc"]
    for r, c in comps.items():  # connector pins not wired: list as nc
        if r.startswith("J"):
            allp = [p["number"] for p in libs.symbol(c["symbol"])["pins"]]
            rest = [p for p in allp if p not in c["pins"] and p not in c.get("nc", [])]
            if rest:
                c["nc"] = sorted(c.get("nc", []) + rest, key=int)

    design = {
        "project": a.profile,
        "revision": "A",
        "description": f"Synthetic benchmark board (seed {a.seed}): {len(comps)} parts, 2 layers, QFN/SOIC ICs with "
                       "0402/0603 passives. Generated by scripts/make_synth_board.py; not a product.",
        "board": {"width_mm": 90, "height_mm": 70, "layers": 2, "clearance_mm": 0.2, "track_mm": 0.25,
                  "optimize_area": False},
        "nets": {"+5V": {"class": "power", "current_a": 0.3, "external_supply": True},
                 "+3V3": {"class": "power", "current_a": 0.2},
                 "GND": {"class": "ground"}, **nets_meta},
        "components": comps,
    }
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    def write(d):
        hdr = ("# GENERATED by scripts/make_synth_board.py (seed %d). Do not edit; regenerate.\n" % a.seed)
        (out / "design.yaml").write_text(hdr + yaml.safe_dump(d, sort_keys=False, default_flow_style=None, width=160))

    write(design)
    d = Design.load(out)
    parts = placer.build_parts(d, libs)
    area = sum(p.w * p.h for p in parts.values())
    W = math.ceil(math.sqrt(area * factor * 4 / 3))
    H = math.ceil(area * factor / W)
    design["board"].update(width_mm=W + 2, height_mm=H + 2)
    write(design)
    d = Design.load(out)
    nets = d.nets()
    kinds: dict[str, int] = {}
    for r, c in comps.items():
        k = f"{re.sub(r'.*:', '', c['footprint']).split('_')[0] if c['footprint'].startswith('Package') else c['footprint'].split(':')[1]}"
        kinds[k] = kinds.get(k, 0) + 1
    print(f"{a.profile}: {len(comps)} parts, {len(nets)} nets, {sum(len(v) for v in nets.values())} connected pins, "
          f"board {W + 2}x{H + 2} mm (courtyard area {area:.0f} mm2)")
    print("parts by footprint:", dict(sorted(kinds.items())))


if __name__ == "__main__":
    main()
