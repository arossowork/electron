"""HW/SW contract -> firmware board header (docs/hwsw-codesign.md).

`hwflow fw <proj>` reads `design.yaml` (as built) and `interface.yaml` (the HW/SW contract) and writes
`build/fw/board.h`. Pin numbers, divider ratios, I2C addresses and ADC mux channels are derived from design.yaml and
the KiCad symbols, never copied into the contract; the contract holds only what design.yaml cannot say (signal
roles, active levels, timings, power-state limits, datasheet tables such as an address map). Each constant carries a
comment with its source. Every mismatch between the two files is a finding; an error means the header is not
trustworthy (a pin moved in layout without the contract, a divider that is not a divider, an address strap that the
map does not know).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import yaml

from .design import Design, parse_value


class Contract:
    def __init__(self, d: Design, libs, raw: dict):
        self.d, self.libs, self.raw = d, libs, raw
        self.findings: list[dict] = []
        self.nets = d.nets()                       # net -> [(ref, pin)]
        self.gnd = d.ground_nets()
        self._pin_names: dict[str, dict[str, str]] = {}

    # ------------------------------------------------------------ helpers
    def add(self, sev: str, code: str, msg: str):
        self.findings.append({"severity": sev, "code": code, "message": msg})

    def comp(self, ref: str):
        c = self.d.components.get(ref)
        if c is None:
            self.add("error", "unknown-ref", f"contract names {ref}, which is not in design.yaml")
        return c

    def pin_names(self, ref: str) -> dict[str, str]:
        """Symbol pin number -> pin name, from the KiCad symbol (`hwflow lib show`)."""
        if ref not in self._pin_names:
            c = self.d.components[ref]
            sym = self.libs.symbol(c.get("symbol"))
            self._pin_names[ref] = {str(p["number"]): str(p["name"]) for p in sym["pins"]}
        return self._pin_names[ref]

    def net_of_pin_name(self, ref: str, name: str) -> tuple[str | None, str | None]:
        """(net, pin number) on the pin of `ref` whose symbol name is `name`."""
        c = self.d.components[ref]
        for num, nm in self.pin_names(ref).items():
            if nm == name:
                return c.pins.get(num), num
        return None, None

    def pins_of_net_on(self, ref: str, net: str) -> list[str]:
        return [p for r, p in self.nets.get(net, []) if r == ref]

    def two_pin_nets(self, ref: str) -> set[str]:
        c = self.comp(ref)
        return set(c.pins.values()) if c else set()

    def value(self, ref: str) -> float:
        return parse_value(self.d.components[ref].get("value"))

    def tol(self, ref: str) -> float | None:
        t = self.d.components[ref].get("tolerance")
        return float(t) if t is not None else None


def _src(x: dict) -> str:
    bits = []
    if x.get("req"):
        r = x["req"]
        bits.append(", ".join(r) if isinstance(r, list) else str(r))
    if x.get("source"):
        bits.append(str(x["source"]))
    return "; ".join(bits)


def _cname(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", s).strip("_").upper()


def _num(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    s = f"{float(v):.6g}"
    return s + ("" if any(ch in s for ch in ".e") else ".0") + "f"


def _comment(s: str) -> str:
    return s.replace("*/", "* /")


# ------------------------------------------------------------------ sections
def _mcu(c: Contract, out: list[str]) -> dict[str, int]:
    m = c.raw.get("mcu") or {}
    ref = m.get("ref")
    if not ref or not c.comp(ref):
        c.add("error", "mcu", "interface.yaml mcu.ref is missing or unknown")
        return {}
    pat = re.compile(m.get("gpio_from_pin_name", r"^IO(\d+)"))
    sym = c.d.components[ref].get("symbol")
    sigs = c.raw.get("signals") or {}
    names = c.pin_names(ref)
    on_mcu: dict[str, list[str]] = {}
    for num, net in c.d.components[ref].pins.items():
        if net in c.gnd or net in (m.get("power_nets") or []):
            continue
        on_mcu.setdefault(net, []).append(num)
    for net in sorted(set(on_mcu) - set(sigs)):
        c.add("error", "contract-missing-signal",
              f"net {net} reaches {ref} pin {', '.join(on_mcu[net])} but interface.yaml has no signal for it "
              f"(a pin added or moved in design.yaml: add it to the contract)")
    for net in sorted(set(sigs) - set(on_mcu)):
        c.add("error", "contract-stale-signal",
              f"interface.yaml signal {net} does not reach {ref} in design.yaml (renamed or removed net)")
    gpio: dict[str, int] = {}
    out.append(f"/* ---- MCU {ref} ({c.d.components[ref].get('value')}): GPIO from the pin name of symbol {sym},"
               f" pin number from design.yaml {ref}.pins */")
    for net in sorted(set(sigs) & set(on_mcu), key=lambda n: int(on_mcu[n][0])):
        s = sigs[net] or {}
        num = on_mcu[net][0]
        pname = names.get(num, "?")
        mm = pat.search(pname)
        cn = _cname(net)
        why = _src(s)
        if mm:
            g = int(mm.group(1))
            gpio[net] = g
            out.append(f"#define BOARD_GPIO_{cn:<10} {g:>3}   /* {ref} pin {num} ({pname}), net {net}; {s.get('dir', '?')}"
                       f"{'; ' + _comment(why) if why else ''} */")
        else:
            out.append(f"/* {net}: {ref} pin {num} ({pname}) is not a GPIO; {s.get('dir', '?')} */")
        if "active" in s:
            lvl = {"high": 1, "low": 0}.get(str(s["active"]))
            if lvl is None:
                c.add("error", "signal-active", f"signal {net}: active must be high or low")
            else:
                out.append(f"#define BOARD_{cn}_ACTIVE      {lvl}     /* active {s['active']} */")
        if s.get("pull"):
            pr = s["pull"]
            pc = c.comp(pr)
            if pc:
                other = c.two_pin_nets(pr) - {net}
                if net not in c.two_pin_nets(pr) or not other:
                    c.add("error", "signal-pull", f"signal {net}: pull {pr} does not connect {net} to another net")
                else:
                    o = next(iter(other))
                    kind = "pull-down" if o in c.gnd else "pull-up"
                    out.append(f"/*   {net} {kind} {pr} {pc.get('value')} to {o} (design.yaml) */")
        if s.get("strap"):
            out.append(f"/*   {net} is a strapping pin: {_comment(str(s['strap']))} */")
        if s.get("sleep"):
            out.append(f"/*   deep-sleep state of {net}: {_comment(str(s['sleep']))} */")
    adc1 = {int(k): int(v) for k, v in (m.get("adc1_channel_of_gpio") or {}).items()}
    for net, s in sigs.items():
        if (s or {}).get("dir") == "adc" and net in gpio:
            g = gpio[net]
            if g not in adc1:
                c.add("error", "adc-pin", f"signal {net} is an ADC input but GPIO{g} has no ADC1 channel "
                                          f"(mcu.adc1_channel_of_gpio)")
            else:
                out.append(f"#define BOARD_ADC1_CH_{_cname(net):<9} {adc1[g]:>3}   /* GPIO{g} = ADC1_CH{adc1[g]}; "
                           f"{_comment(str(m.get('adc1_source', '')))} */")
    out.append("")
    return gpio


def _dividers(c: Contract, out: list[str]):
    for a in c.raw.get("dividers") or []:
        name, net, top, bot = a["name"], a["net"], a["top"], a["bottom"]
        if not (c.comp(top) and c.comp(bot)):
            continue
        tn, bn = c.two_pin_nets(top), c.two_pin_nets(bot)
        if net not in tn or net not in bn:
            c.add("error", "divider", f"divider {name}: {top} and {bot} must both connect to {net}")
            continue
        if not ((bn - {net}) <= c.gnd):
            c.add("error", "divider", f"divider {name}: bottom {bot} must go from {net} to ground, not {bn - {net}}")
            continue
        src_net = next(iter(tn - {net}), None)
        rt, rb = c.value(top), c.value(bot)
        ratio = (rt + rb) / rb
        tt, tb = c.tol(top), c.tol(bot)
        cn = _cname(name)
        out.append(f"/* ---- divider {name}: {src_net} -> {top} {c.d.components[top].get('value')} -> {net} -> "
                   f"{bot} {c.d.components[bot].get('value')} -> GND (design.yaml); {_comment(_src(a))} */")
        out.append(f"#define BOARD_{cn}_DIV_RATIO   {_num(ratio)}   /* ({top} + {bot}) / {bot} = ({rt:g} + {rb:g}) / {rb:g} */")
        if tt is not None and tb is not None:
            err = (tt + tb) * rt / (rt + rb)
            out.append(f"#define BOARD_{cn}_DIV_TOL     {_num(err)}   /* worst ratio error: ({top} {tt:g} + {bot} {tb:g})"
                       f" x {top} / ({top} + {bot}) */")
        else:
            c.add("warning", "divider-tol", f"divider {name}: {top} or {bot} has no tolerance in design.yaml")
        if a.get("measures"):
            out.append(f"/*   measures {a['measures']}: {_comment(str(a.get('note', '')))} */")
        out.append("")


def _i2c(c: Contract, out: list[str]):
    for bus in c.raw.get("i2c") or []:
        out.append(f"/* ---- I2C bus {bus['name']}: SDA {bus['sda']}, SCL {bus['scl']} */")
        for pr in bus.get("pullups") or []:
            if c.comp(pr):
                nets = c.two_pin_nets(pr)
                if not (nets & {bus["sda"], bus["scl"]}):
                    c.add("error", "i2c-pullup", f"I2C {bus['name']}: pull-up {pr} is not on SDA or SCL")
                else:
                    out.append(f"/*   pull-up {pr} {c.d.components[pr].get('value')} between {' and '.join(sorted(nets))} */")
        for dev in bus.get("devices") or []:
            ref = dev["ref"]
            if not c.comp(ref):
                continue
            for sig, bnet in (("SDA", bus["sda"]), ("SCL", bus["scl"])):
                n, num = c.net_of_pin_name(ref, sig)
                if n != bnet:
                    c.add("error", "i2c-pin", f"I2C {bus['name']}: {ref} {sig} (pin {num}) is on {n}, not {bnet}")
            n, num = c.net_of_pin_name(ref, dev.get("addr_pin", "ADDR"))
            vdd, _ = c.net_of_pin_name(ref, dev.get("vdd_pin", "VDD"))
            key = ("GND" if n in c.gnd else "VDD" if n == vdd else "SDA" if n == bus["sda"]
                   else "SCL" if n == bus["scl"] else None)
            amap = {str(k): v for k, v in (dev.get("addr_map") or {}).items()}
            if key is None or key not in amap:
                c.add("error", "i2c-addr", f"{ref} address pin {num} is on net {n}, which the address map "
                                           f"({', '.join(amap)}) does not cover")
                continue
            addr = int(amap[key])
            out.append(f"#define BOARD_I2C_ADDR_{_cname(dev.get('name', ref)):<6} 0x{addr:02X}  /* {ref} pin {num} "
                       f"{dev.get('addr_pin', 'ADDR')} on {n} ({key}); {_comment(_src(dev))} */")
            for net, pin_label in (dev.get("mux_nets") or {}).items():
                pins = c.pins_of_net_on(ref, net)
                if not pins:
                    c.add("error", "adc-mux", f"{ref}: net {net} is not on any pin")
                    continue
                pn = c.pin_names(ref)[pins[0]]
                mm = re.fullmatch(r"AIN(\d+)", pn)
                if not mm:
                    c.add("error", "adc-mux", f"{ref}: net {net} is on pin {pins[0]} ({pn}), not an AINn input")
                    continue
                out.append(f"#define BOARD_{_cname(dev.get('name', ref))}_AIN_{_cname(pin_label):<5} {mm.group(1):>3}   "
                           f"/* {ref} pin {pins[0]} {pn}, net {net} */")
            if dev.get("supply_net"):
                if vdd != dev["supply_net"]:
                    c.add("error", "i2c-supply", f"{ref} VDD is on {vdd}, contract says {dev['supply_net']}")
                else:
                    out.append(f"/*   {ref} VDD on {vdd}: the device is unpowered while {vdd} is low; "
                               f"SDA/SCL must not be driven high then */")
        out.append("")


def _ntc(c: Contract, out: list[str]):
    n = c.raw.get("ntc")
    if not n:
        return
    r25, beta = float(n["r25_ohm"]), float(n["beta"])
    exc = n["excitation_net"]
    rfix = None
    out.append(f"/* ---- NTC inputs (ratiometric: x = V(AIN) / V(REF) = Rntc / (Rfix + Rntc)); {_comment(_src(n))} */")
    for ch in n["channels"]:
        fr = ch["fixed"]
        if not c.comp(fr):
            continue
        nets = c.two_pin_nets(fr)
        if nets != {exc, ch["node"]}:
            c.add("error", "ntc-fixed", f"NTC {ch['name']}: {fr} connects {sorted(nets)}, expected {exc} and {ch['node']}")
            continue
        v = c.value(fr)
        if rfix is not None and v != rfix:
            c.add("error", "ntc-fixed", "NTC channels use different fixed resistors; the prototype emits one table")
        rfix = v
        out.append(f"/*   {ch['name']}: {exc} -> {fr} {c.d.components[fr].get('value')} "
                   f"({(c.tol(fr) or 0) * 100:g} %) -> {ch['node']} -> probe -> GND */")
    if rfix is None:
        return
    out.append(f"#define BOARD_NTC_RFIX_OHM     {_num(rfix)}   /* fixed divider resistor value (design.yaml) */")
    out.append(f"#define BOARD_NTC_R25_OHM      {_num(r25)}   /* probe R25 (R17 acceptance) */")
    out.append(f"#define BOARD_NTC_BETA_K       {_num(beta)}   /* probe B value (R17 acceptance) */")
    t0, t1, st = int(n["t_min_c"]), int(n["t_max_c"]), int(n.get("step_c", 1))
    vals = []
    for t in range(t0, t1 + 1, st):
        r = r25 * math.exp(beta * (1 / (t + 273.15) - 1 / 298.15))
        vals.append(round(r / (rfix + r) * 65536))
    out.append(f"#define BOARD_NTC_T_MIN_C      {t0}   /* table range (interface.yaml ntc) */")
    out.append(f"#define BOARD_NTC_T_STEP_C     {st}   /* table step (interface.yaml ntc) */")
    out.append(f"#define BOARD_NTC_TABLE_LEN    {len(vals)}   /* ({t1} - {t0}) / {st} + 1 */")
    out.append(f"/* ratio x 65536 at T_MIN_C + i x T_STEP_C (falls with T); Beta model R = R25 exp(B (1/T - 1/298.15)) */")
    out.append("static const uint16_t BOARD_NTC_TABLE_Q16[BOARD_NTC_TABLE_LEN] = {")
    for i in range(0, len(vals), 10):
        out.append("    " + ", ".join(f"{min(v, 65535):5d}" for v in vals[i:i + 10]) + ",")
    out.append("};")
    out.append("")


def _relays(c: Contract, out: list[str]):
    for r in c.raw.get("relays") or []:
        name, drv = r["name"], r["driver"]
        if not (c.comp(drv) and c.comp(r["relay"])):
            continue
        out.append(f"/* ---- relay {name}: {r['relay']} {c.d.components[r['relay']].get('value')} driven by {drv} "
                   f"{c.d.components[drv].get('value')} */")
        for sig in ("IN1", "IN2"):
            n, num = c.net_of_pin_name(drv, sig)
            want = r[sig.lower()]
            if n != want:
                c.add("error", "relay-pin", f"relay {name}: {drv} {sig} (pin {num}) is on {n}, contract says {want}")
        n, num = c.net_of_pin_name(drv, r.get("sleep_pin", "~{SLEEP}"))
        if r.get("enable") and n != r["enable"]:
            c.add("error", "relay-pin", f"relay {name}: {drv} nSLEEP (pin {num}) is on {n}, contract says {r['enable']}")
        s = str(r.get("set", "in1")).lower()
        out.append(f"#define BOARD_{_cname(name)}_SET_IN        {1 if s == 'in1' else 2}     /* set (contact closes) drives "
                   f"IN{1 if s == 'in1' else 2} high; {_comment(_src(r))} */")
    out.append("")


def _limits(c: Contract, out: list[str]):
    for sect in ("timing", "power"):
        items = c.raw.get(sect) or {}
        if not items:
            continue
        out.append(f"/* ---- {sect} */")
        for k, x in items.items():
            x = x if isinstance(x, dict) else {"value": x}
            v = x.get("value")
            cn = _cname(k)
            if v is None:
                c.add("warning", "open", f"{sect}.{k} has no value yet ({_src(x) or 'no source'}): not emitted")
                out.append(f"/* BOARD_{cn}: OPEN, no value in interface.yaml ({_comment(_src(x))}) */")
                continue
            lo, hi = x.get("min"), x.get("max")
            if lo is not None and v < lo or hi is not None and v > hi:
                c.add("error", "limit", f"{sect}.{k} = {v} is outside [{lo}, {hi}] ({_src(x)})")
            rng = (f" [{lo if lo is not None else '-'} .. {hi if hi is not None else '-'}]"
                   if lo is not None or hi is not None else "")
            out.append(f"#define BOARD_{cn:<22} {_num(v):>10}   /* {x.get('unit', '')}{rng}; {_comment(_src(x))} */")
        out.append("")


def _derived(c: Contract, out: list[str]):
    """Limits computed from other contract numbers (shown with their arithmetic)."""
    t = c.raw.get("timing") or {}
    rel = c.raw.get("relay_coil") or {}
    if rel and "relay_pulse_us" in t:
        p_us = float(t["relay_pulse_us"]["value"])
        e_max = float(rel["energy_max_mj"]) * 1e-3
        v, r_min = float(rel["rail_max_v"]), float(rel["coil_min_ohm"])
        t_max = e_max * r_min / v ** 2 * 1e6
        t_min = float(rel["set_time_max_ms"]) * 1e3 * float(rel.get("set_time_factor", 2))
        out.append(f"/* ---- relay pulse window, derived: {_comment(_src(rel))} */")
        out.append(f"#define BOARD_RELAY_PULSE_MIN_US   {t_min:.0f}   /* {rel.get('set_time_factor', 2)} x set time max "
                   f"{rel['set_time_max_ms']} ms */")
        out.append(f"#define BOARD_RELAY_PULSE_MAX_US   {t_max:.0f}   /* {rel['energy_max_mj']} mJ x {r_min} ohm / "
                   f"{v} V^2 (coil minimum, rail maximum) */")
        out.append(f"#define BOARD_RELAY_COIL_MIN_OHM   {_num(r_min)}   /* interface.yaml relay_coil */")
        out.append(f"#define BOARD_RELAY_RAIL_MAX_V     {_num(v)}   /* interface.yaml relay_coil */")
        out.append(f"#define BOARD_RELAY_ENERGY_MAX_MJ  {_num(float(rel['energy_max_mj']))}   /* R9 */")
        if not t_min <= p_us <= t_max:
            c.add("error", "relay-window", f"relay pulse {p_us:.0f} us is outside the derived window "
                                           f"{t_min:.0f}..{t_max:.0f} us: a HW change (coil, rail) or a spec decision")
        elif t_max - t_min < 2000:
            c.add("warning", "relay-window", f"relay pulse window is {t_min:.0f}..{t_max:.0f} us "
                                             f"({(t_max - t_min) / 1000:.2f} ms): needs a us timer, not an OS tick")
        p = c.raw.get("power") or {}
        if "relay_max_pulses_per_day" in p and p["relay_max_pulses_per_day"].get("value"):
            n = float(p["relay_max_pulses_per_day"]["value"])
            nrel = max(1, len(c.raw.get("relays") or []))
            dwell = 86400 / (n / nrel)
            out.append(f"#define BOARD_RELAY_MIN_DWELL_S   {dwell:.0f}   /* 86400 s / ({n:g} pulses per day / {nrel} relays):"
                       f" each pulse is one state change */")
        out.append("")


def generate(d: Design, libs, contract_path: Path | None = None) -> tuple[str, list[dict]]:
    p = contract_path or d.dir / "interface.yaml"
    if not p.exists():
        return "", [{"severity": "error", "code": "no-contract",
                     "message": f"{p} not found: the partition stage writes it (docs/hwsw-codesign.md)"}]
    raw = yaml.safe_load(p.read_text()) or {}
    c = Contract(d, libs, raw)
    body: list[str] = []
    _mcu(c, body)
    _dividers(c, body)
    _i2c(c, body)
    _ntc(c, body)
    _relays(c, body)
    _limits(c, body)
    _derived(c, body)
    ident = hashlib.sha1((d.path.read_text() + p.read_text()).encode()).hexdigest()[:12]
    head = [
        "/* board.h: generated by `hwflow fw` from design.yaml and interface.yaml. Do not edit: change the sources and",
        " * regenerate (docs/hwsw-codesign.md). Every constant names its source. */",
        "#ifndef BOARD_H",
        "#define BOARD_H",
        "#include <stdint.h>",
        "",
        f'#define BOARD_PROJECT     "{d.name}"',
        f'#define BOARD_REVISION    "{d.raw.get("revision", "")}"',
        f'#define BOARD_CONTRACT_ID "{ident}"   /* sha1 of design.yaml + interface.yaml: a changed board changes it */',
        "",
    ]
    errs = [f for f in c.findings if f["severity"] == "error"]
    if errs:
        head.append("#error \"board.h has contract errors: run `uv run hwflow fw` and fix interface.yaml or design.yaml\"")
        head.append("")
    return "\n".join(head + body + ["#endif /* BOARD_H */", ""]), c.findings


def run(d: Design, libs, out_dir: Path) -> tuple[Path, list[dict]]:
    text, findings = generate(d, libs)
    fw = out_dir / "fw"
    fw.mkdir(parents=True, exist_ok=True)
    path = fw / "board.h"
    if text:
        path.write_text(text)
        (fw / "findings.json").write_text(json.dumps(findings, indent=1))
    return path, findings
