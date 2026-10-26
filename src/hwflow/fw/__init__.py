"""HW/SW contract -> firmware headers and module skeletons (docs/hwsw-codesign.md).

`hwflow fw <proj>` reads `design.yaml` (as built) and `interface.yaml` (the HW/SW contract) and writes, under
`build/fw/`:
  board.h    pins, buses, divider ratios, tables and limits, each with a source comment
  events.h   the event list (typed constructors, names, per-module subscription masks) for fw/framework (fwk)
  modules.h  one task declaration per module
`--scaffold` also writes `fw/` module skeletons, the app wiring, a Makefile and a host-test stub, without
overwriting a file that exists.

Pin numbers, part values, address straps and ADC inputs come from design.yaml and the KiCad symbols, never from the
contract. The MCU's pin data comes from a small per-family table (FAMILIES). Every mismatch between the two files is
a finding; an error puts `#error` in board.h so firmware cannot build against a stale contract.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import operator
import re
from pathlib import Path

import yaml

from ..core.settings import get_settings
from ..core.design import Design, parse_value

# ------------------------------------------------------------------ MCU families
# GPIO number from the symbol pin name; ADC channel per GPIO; GPIOs that can wake the chip from deep sleep; strapping
# pins. Add a family by adding an entry.
FAMILIES = {
    "esp32c3": {
        "match": r"ESP32-?C3",
        "gpio_from_pin_name": r"^IO(\d+)",
        "adc": {0: "ADC1_CH0", 1: "ADC1_CH1", 2: "ADC1_CH2", 3: "ADC1_CH3", 4: "ADC1_CH4"},
        "deep_sleep_wake": [0, 1, 2, 3, 4, 5],
        "strapping": {2: "GPIO2: pull high recommended (glitches)", 8: "GPIO8: must be 1 for download boot",
                      9: "GPIO9: 0 at reset selects download boot"},
        "source": "ESP32-C3 datasheet: ADC1 on GPIO0-4 (ADC2 not usable), deep-sleep GPIO wake on GPIO0-5 "
                  "(VDD3P3_RTC domain), strapping pins GPIO2/8/9",
    },
    "rp2040": {
        "match": r"RP2040",
        "gpio_from_pin_name": r"^GPIO(\d+)",
        "adc": {26: "ADC_CH0", 27: "ADC_CH1", 28: "ADC_CH2", 29: "ADC_CH3"},
        "deep_sleep_wake": list(range(30)),
        "strapping": {},
        "source": "RP2040 datasheet: ADC inputs on GPIO26-29 (ADC0-3), DORMANT wake on any GPIO edge or level, "
                  "no GPIO strapping pins (boot select is QSPI_SS)",
    },
}

SYSTEM_EVENTS = {"INIT", "TIMER"}      # fwk built-ins, never in the contract's event list


def detect_family(c) -> str | None:
    blob = " ".join(str(c.get(k, "")) for k in ("value", "mpn", "symbol"))
    for name, f in FAMILIES.items():
        if re.search(f["match"], blob, re.I):
            return name
    return None


# ------------------------------------------------------------------ expressions over params and limits
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.FloorDiv: operator.floordiv}
_FUNCS = {"min": min, "max": max, "ceil": math.ceil, "floor": math.floor, "sqrt": math.sqrt}


def evaluate(expr, names: dict[str, float]) -> float:
    """Arithmetic on numbers and named contract values only (no attributes, no calls but min/max/ceil/floor/sqrt)."""
    if isinstance(expr, (int, float)):
        return float(expr)

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id not in names:
                raise KeyError(f"unknown name {n.id}")
            return float(names[n.id])
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS:
            return float(_FUNCS[n.func.id](*[ev(a) for a in n.args]))
        raise ValueError(f"unsupported expression element {type(n).__name__}")
    return ev(ast.parse(str(expr), mode="eval"))


# ------------------------------------------------------------------ contract model
class Contract:
    def __init__(self, d: Design, libs, raw: dict):
        self.d, self.libs, self.raw = d, libs, raw
        self.findings: list[dict] = []
        self.nets = d.nets()
        self.gnd = d.ground_nets()
        self._pin_names: dict[str, dict[str, str]] = {}
        self.gpio: dict[str, int] = {}

    def add(self, sev: str, code: str, msg: str):
        self.findings.append({"severity": sev, "code": code, "message": msg})

    def comp(self, ref: str):
        c = self.d.components.get(ref)
        if c is None:
            self.add("error", "unknown-ref", f"contract names {ref}, which is not in design.yaml")
        return c

    def pin_names(self, ref: str) -> dict[str, str]:
        if ref not in self._pin_names:
            sym = self.libs.symbol(self.d.components[ref].get("symbol"))
            self._pin_names[ref] = {str(p["number"]): str(p["name"]) for p in sym["pins"]}
        return self._pin_names[ref]

    def net_of_pin_name(self, ref: str, name: str):
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

    def tol(self, ref: str):
        t = self.d.components[ref].get("tolerance")
        return float(t) if t is not None else None


def _src(x: dict) -> str:
    bits = []
    if x.get("req"):
        r = x["req"]
        bits.append(", ".join(r) if isinstance(r, list) else str(r))
    if x.get("source"):
        bits.append(str(x["source"]))
    return "; ".join(bits).replace("*/", "* /")


def _cname(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", str(s)).strip("_").upper()


def _fnum(v) -> str:
    s = f"{float(v):.6g}"
    return s + ("" if any(ch in s for ch in ".e") else ".0") + "f"


def _num(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer() and abs(v) < 2 ** 31):
        return str(int(v))
    return _fnum(v)


# ------------------------------------------------------------------ board.h sections
def _mcu(c: Contract, out: list[str]):
    m = c.raw.get("mcu") or {}
    ref = m.get("ref")
    if not ref or not c.comp(ref):
        c.add("error", "mcu", "interface.yaml mcu.ref is missing or unknown")
        return
    fam_name = m.get("family") or detect_family(c.d.components[ref])
    if fam_name not in FAMILIES:
        c.add("error", "mcu-family", f"{ref}: no MCU family table matches ({fam_name or 'not detected'}); "
                                     f"known: {', '.join(FAMILIES)}")
        return
    fam = FAMILIES[fam_name]
    pat = re.compile(fam["gpio_from_pin_name"])
    sigs = c.raw.get("signals") or {}
    names = c.pin_names(ref)
    power = set(m.get("power_nets") or [])
    on_mcu: dict[str, list[str]] = {}
    for num, net in c.d.components[ref].pins.items():
        if net in c.gnd or net in power:
            continue
        on_mcu.setdefault(net, []).append(num)
    for net in sorted(set(on_mcu) - set(sigs)):
        c.add("error", "contract-missing-signal", f"net {net} reaches {ref} pin {', '.join(on_mcu[net])} but "
                                                  f"interface.yaml has no signal for it (add it to the contract)")
    for net in sorted(set(sigs) - set(on_mcu)):
        c.add("error", "contract-stale-signal", f"interface.yaml signal {net} does not reach {ref} in design.yaml")
    out.append(f"/* ---- MCU {ref} ({c.d.components[ref].get('value')}), family {fam_name}: GPIO from the pin names of "
               f"symbol {c.d.components[ref].get('symbol')}, pin numbers from design.yaml {ref}.pins */")
    out.append(f"#define BOARD_MCU_FAMILY_{_cname(fam_name)} 1   /* {fam['source']} */")
    for net in sorted(set(sigs) & set(on_mcu), key=lambda n: int(re.sub(r'\D', '', on_mcu[n][0]) or 0)):
        s = sigs[net] or {}
        num = on_mcu[net][0]
        pname = names.get(num, "?")
        mm = pat.search(pname)
        cn = _cname(net)
        why = _src(s)
        if not mm:
            out.append(f"/* {net}: {ref} pin {num} ({pname}) is not a GPIO; {s.get('dir', '?')} */")
            continue
        g = int(mm.group(1))
        c.gpio[net] = g
        out.append(f"#define BOARD_GPIO_{cn:<10} {g:>3}   /* {ref} pin {num} ({pname}), net {net}; {s.get('dir', '?')}"
                   f"{'; ' + why if why else ''} */")
        if "active" in s:
            lvl = {"high": 1, "low": 0}.get(str(s["active"]))
            if lvl is None:
                c.add("error", "signal-active", f"signal {net}: active must be high or low")
            else:
                out.append(f"#define BOARD_{cn}_ACTIVE {lvl}   /* active {s['active']} */")
        if s.get("pull"):
            pr = s["pull"]
            if c.comp(pr):
                other = c.two_pin_nets(pr) - {net}
                if net not in c.two_pin_nets(pr) or not other:
                    c.add("error", "signal-pull", f"signal {net}: pull {pr} does not connect {net} to another net")
                else:
                    o = next(iter(other))
                    out.append(f"/*   {net} {'pull-down' if o in c.gnd else 'pull-up'} {pr} "
                               f"{c.d.components[pr].get('value')} to {o} (design.yaml) */")
        if g in fam["strapping"]:
            if not s.get("strap"):
                c.add("warning", "strap", f"{net} is on strapping pin GPIO{g} ({fam['strapping'][g]}): state its "
                                          f"level at reset in `strap`")
            out.append(f"/*   strapping pin: {fam['strapping'][g]}; {str(s.get('strap', 'not stated')).replace('*/', '')} */")
        if s.get("sleep"):
            out.append(f"/*   deep-sleep state: {str(s['sleep']).replace('*/', '')} */")
        if s.get("wake"):
            if g in fam["deep_sleep_wake"]:
                lvl = {"high": 1, "low": 0}.get(str(s["wake"]), 0)
                out.append(f"#define BOARD_{cn}_WAKE_LEVEL {lvl}   /* deep-sleep wake on {s['wake']} (GPIO{g} is "
                           f"wake-capable) */")
            else:
                msg = (f"signal {net} must wake the MCU from deep sleep but GPIO{g} cannot (wake-capable: "
                       f"{fam['deep_sleep_wake']}): a pin swap in design.yaml")
                if s.get("hw_change"):
                    c.add("warning", "wake-pin", msg + f", pending {s['hw_change']}")
                    out.append(f"/*   wake requested but GPIO{g} cannot wake: pending HW change {s['hw_change']} */")
                else:
                    c.add("error", "wake-pin", msg)
        if s.get("dir") == "adc":
            ch = fam["adc"].get(g)
            if ch is None:
                c.add("error", "adc-pin", f"signal {net} is an ADC input but GPIO{g} has no usable ADC channel")
            else:
                out.append(f"#define BOARD_ADC_{cn} {ch.split('CH')[-1]}   /* GPIO{g} = {ch} */")
    out.append("")


def _dividers(c: Contract, out: list[str]):
    for a in c.raw.get("dividers") or []:
        name, net, top, bot = a["name"], a["net"], a["top"], a["bottom"]
        if not (c.comp(top) and c.comp(bot)):
            continue
        tn, bn = c.two_pin_nets(top), c.two_pin_nets(bot)
        if net not in tn or net not in bn or not ((bn - {net}) <= c.gnd):
            c.add("error", "divider", f"divider {name}: {top} must join {net} to the source and {bot} must join "
                                      f"{net} to ground")
            continue
        src_net = next(iter(tn - {net}), None)
        rt, rb = c.value(top), c.value(bot)
        cn = _cname(name)
        out.append(f"/* ---- divider {name}: {src_net} -> {top} {c.d.components[top].get('value')} -> {net} -> {bot} "
                   f"{c.d.components[bot].get('value')} -> GND (design.yaml); {_src(a)} */")
        out.append(f"#define BOARD_{cn}_DIV_RATIO {_fnum((rt + rb) / rb)}   /* ({top} + {bot}) / {bot} */")
        tt, tb = c.tol(top), c.tol(bot)
        if tt is not None and tb is not None:
            out.append(f"#define BOARD_{cn}_DIV_TOL {_fnum((tt + tb) * rt / (rt + rb))}   /* ({top} {tt:g} + {bot} {tb:g})"
                       f" x {top} / ({top} + {bot}) */")
        else:
            c.add("warning", "divider-tol", f"divider {name}: {top} or {bot} has no tolerance in design.yaml")
        out.append("")


def _buses(c: Contract, out: list[str]):
    for bus in c.raw.get("buses") or []:
        if bus.get("kind", "i2c") != "i2c":
            c.add("warning", "bus-kind", f"bus {bus.get('name')}: only i2c is checked by this prototype")
            continue
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
            dn = _cname(dev.get("name", ref))
            for sig, bnet in (("SDA", bus["sda"]), ("SCL", bus["scl"])):
                n, num = c.net_of_pin_name(ref, dev.get(f"{sig.lower()}_pin", sig))
                if n != bnet:
                    c.add("error", "i2c-pin", f"I2C {bus['name']}: {ref} {sig} (pin {num}) is on {n}, not {bnet}")
            if "addr" in dev:                        # fixed address
                out.append(f"#define BOARD_I2C_ADDR_{dn} 0x{int(dev['addr']):02X}   /* {ref} fixed address; {_src(dev)} */")
            else:
                n, num = c.net_of_pin_name(ref, dev.get("addr_pin", "ADDR"))
                vdd, _ = c.net_of_pin_name(ref, dev.get("vdd_pin", "VDD"))
                key = ("GND" if n in c.gnd else "VDD" if n == vdd else "SDA" if n == bus["sda"]
                       else "SCL" if n == bus["scl"] else None)
                amap = {str(k): v for k, v in (dev.get("addr_map") or {}).items()}
                if key is None or key not in amap:
                    c.add("error", "i2c-addr", f"{ref} address pin {num} is on net {n}, which the address map "
                                               f"({', '.join(amap)}) does not cover")
                    continue
                out.append(f"#define BOARD_I2C_ADDR_{dn} 0x{int(amap[key]):02X}   /* {ref} pin {num} on {n} ({key}); "
                           f"{_src(dev)} */")
            mp = re.compile(dev.get("mux_pin_pattern", r"^AIN(\d+)$"))
            for net, label in (dev.get("mux_nets") or {}).items():
                pins = c.pins_of_net_on(ref, net)
                mm = mp.search(c.pin_names(ref).get(pins[0], "")) if pins else None
                if not mm:
                    c.add("error", "adc-mux", f"{ref}: net {net} is not on an input pin matching {mp.pattern}")
                    continue
                out.append(f"#define BOARD_{dn}_IN_{_cname(label)} {mm.group(1)}   /* {ref} pin {pins[0]} "
                           f"{c.pin_names(ref)[pins[0]]}, net {net} */")
            if dev.get("supply_net"):
                vdd, _ = c.net_of_pin_name(ref, dev.get("vdd_pin", "VDD"))
                if vdd != dev["supply_net"]:
                    c.add("error", "i2c-supply", f"{ref} VDD is on {vdd}, contract says {dev['supply_net']}")
                else:
                    out.append(f"/*   {ref} is powered from {vdd}: do not drive SDA/SCL high while it is off */")
        out.append("")


def _bindings(c: Contract, out: list[str]):
    """Device pins the firmware relies on (a driver's inputs, an enable), checked against the device symbol."""
    for b in c.raw.get("bindings") or []:
        ref = b["ref"]
        if not c.comp(ref):
            continue
        name = b.get("name", ref)
        for pin, want in (b.get("pins") or {}).items():
            n, num = c.net_of_pin_name(ref, pin)
            if num is None:
                c.add("error", "binding", f"binding {name}: {ref} has no pin named {pin}")
            elif n != want:
                c.add("error", "binding", f"binding {name}: {ref} {pin} (pin {num}) is on {n}, contract says {want}")
        out.append(f"/* ---- {name}: {ref} {c.d.components[ref].get('value')} "
                   f"{', '.join(f'{k}={v}' for k, v in (b.get('pins') or {}).items())}; {_src(b)} */")
        for k, v in (b.get("defines") or {}).items():
            out.append(f"#define BOARD_{_cname(name)}_{_cname(k)} {_num(v)}   /* binding {name} */")
    if c.raw.get("bindings"):
        out.append("")


def _tables(c: Contract, out: list[str]):
    for t in c.raw.get("tables") or []:
        if t.get("kind") != "ntc_beta":
            c.add("error", "table-kind", f"table {t.get('name')}: unknown kind {t.get('kind')} (known: ntc_beta)")
            continue
        n = _cname(t["name"])
        r25, beta, exc = float(t["r25_ohm"]), float(t["beta"]), t["excitation_net"]
        rfix = None
        for fr, node in zip(t["fixed"], t["nodes"]):
            if not c.comp(fr):
                continue
            if c.two_pin_nets(fr) != {exc, node}:
                c.add("error", "table-fixed", f"table {t['name']}: {fr} must join {exc} and {node}")
                continue
            v = c.value(fr)
            if rfix is not None and v != rfix:
                c.add("error", "table-fixed", f"table {t['name']}: the fixed resistors differ; one table per value")
            rfix = v
        if rfix is None:
            continue
        t0, t1, st = int(t["t_min_c"]), int(t["t_max_c"]), int(t.get("step_c", 1))
        vals = [round(r / (rfix + r) * 65536) for r in
                (r25 * math.exp(beta * (1 / (x + 273.15) - 1 / 298.15)) for x in range(t0, t1 + 1, st))]
        out.append(f"/* ---- table {t['name']}: divider ratio Rntc / (Rfix + Rntc) x 65536, {exc} -> "
                   f"{'/'.join(t['fixed'])} -> probe -> GND; Beta model; {_src(t)} */")
        out.append(f"#define BOARD_{n}_RFIX_OHM {_fnum(rfix)}   /* {'/'.join(t['fixed'])} (design.yaml) */")
        out.append(f"#define BOARD_{n}_R25_OHM {_fnum(r25)}   /* table {t['name']} */")
        out.append(f"#define BOARD_{n}_BETA_K {_fnum(beta)}   /* table {t['name']} */")
        out.append(f"#define BOARD_{n}_T_MIN_C {t0}   /* table start */")
        out.append(f"#define BOARD_{n}_T_STEP_C {st}   /* table step */")
        out.append(f"#define BOARD_{n}_TABLE_LEN {len(vals)}   /* ({t1} - {t0}) / {st} + 1 */")
        out.append(f"static const uint16_t BOARD_{n}_TABLE_Q16[{len(vals)}] = {{")
        for i in range(0, len(vals), 10):
            out.append("    " + ", ".join(f"{min(v, 65535):5d}" for v in vals[i:i + 10]) + ",")
        out.append("};")
        out.append("")


def _limits(c: Contract, out: list[str]):
    params = c.raw.get("params") or {}
    limits = c.raw.get("limits") or {}
    names: dict[str, float] = {}
    for k, x in params.items():
        x = x if isinstance(x, dict) else {"value": x}
        if x.get("value") is not None:
            names[k] = float(x["value"])
    for k, x in limits.items():
        x = x if isinstance(x, dict) else {"value": x}
        if isinstance(x.get("value"), (int, float)) and not isinstance(x.get("value"), bool):
            names[k] = float(x["value"])
    if params:
        out.append("/* ---- params (inputs of the limit expressions) */")
        for k, x in params.items():
            x = x if isinstance(x, dict) else {"value": x}
            out.append(f"#define BOARD_P_{_cname(k)} {_num(x['value'])}   /* {x.get('unit', '')}; {_src(x)} */")
        out.append("")
    out.append("/* ---- limits */")
    for k, x in limits.items():
        x = x if isinstance(x, dict) else {"value": x}
        cn = _cname(k)
        v = x.get("value")
        if v is None:
            c.add("warning", "open", f"limits.{k} has no value yet ({_src(x) or 'no source'}): not emitted")
            out.append(f"/* BOARD_{cn}: OPEN, no value in interface.yaml ({_src(x)}) */")
            continue
        if isinstance(v, str):
            try:
                v = evaluate(v, names)
                names[k] = v
            except (KeyError, ValueError, ZeroDivisionError) as e:
                c.add("error", "limit-expr", f"limits.{k} value {x['value']!r}: {e}")
                continue
        bounds = []
        for side in ("min", "max"):
            if x.get(side) is None:
                continue
            try:
                b = evaluate(x[side], names)
            except (KeyError, ValueError, ZeroDivisionError) as e:
                c.add("error", "limit-expr", f"limits.{k}.{side} {x[side]!r}: {e}")
                continue
            bounds.append(b)
            out.append(f"#define BOARD_{cn}_{side.upper()} {_num(round(b, 6))}   /* {x[side]} */")
            if (side == "min" and v < b) or (side == "max" and v > b):
                c.add("error", "limit", f"limits.{k} = {v:g} is {'below' if side == 'min' else 'above'} its {side} "
                                        f"{b:g} ({x[side]}): a HW change or a spec decision ({_src(x)})")
        if len(bounds) == 2 and x.get("window_warn") is not None and bounds[1] - bounds[0] < x["window_warn"]:
            c.add("warning", "window", f"limits.{k}: window {bounds[0]:g}..{bounds[1]:g} {x.get('unit', '')} is narrow "
                                       f"({_src(x)})")
        out.append(f"#define BOARD_{cn} {_num(v)}   /* {x.get('unit', '')}; {_src(x)} */")
    out.append("")


# ------------------------------------------------------------------ events and modules
def _events(c: Contract) -> tuple[str, str]:
    events = c.raw.get("events") or {}
    modules = c.raw.get("modules") or {}
    if not events and not modules:
        return "", ""
    sigs = c.raw.get("signals") or {}
    emitters: dict[str, list[str]] = {}
    for m, x in modules.items():
        x = x or {}
        for ev in x.get("emits") or []:
            emitters.setdefault(ev, []).append(m)
        for ev in (x.get("emits") or []) + (x.get("consumes") or []):
            if ev not in events and ev not in SYSTEM_EVENTS:
                c.add("error", "event-unknown", f"module {m} names event {ev}, which `events` does not list")
    for ev, x in events.items():
        x = x or {}
        if not emitters.get(ev) and not x.get("isr"):
            c.add("error", "event-no-source", f"event {ev}: no module emits it and it is not an `isr` event")
        if not any(ev in ((y or {}).get("consumes") or []) for y in modules.values()):
            c.add("warning", "event-unused", f"event {ev}: no module consumes it")
    owners: dict[str, list[str]] = {}
    for m, x in modules.items():
        for net in (x or {}).get("owns") or []:
            owners.setdefault(net, []).append(m)
            if net not in sigs:
                c.add("error", "owns-unknown", f"module {m} owns {net}, which is not a signal")
    for net, s in sigs.items():
        if (s or {}).get("dir") == "out" and net not in owners:
            c.add("warning", "unowned-output", f"output {net} has no owning module (`owns`)")
    for net, ms in owners.items():
        if len(ms) > 1:
            c.add("error", "shared-pin", f"{net} is owned by {', '.join(ms)}: one owner, the others ask it by event")
    if len(events) + 3 > 64:
        c.add("error", "event-count", "more than 61 project events: the subscription mask is 64 bits")

    names = list(events)
    h = ["/* events.h: generated by `hwflow fw` from interface.yaml `events` and `modules`. Do not edit. */",
         "#ifndef EVENTS_H", "#define EVENTS_H", '#include "fwk.h"', "", "enum {"]
    for i, ev in enumerate(names):
        x = events[ev] or {}
        frm = ", ".join(emitters.get(ev, []) + (["isr"] if x.get("isr") else []))
        h.append(f"    EV_{_cname(ev)} = FWK_EV_USER + {i},   /* a: {x.get('a', '-')}; b: {x.get('b', '-')}; from {frm}"
                 f"{'; ' + _src(x) if _src(x) else ''} */")
    h += [f"    EV_COUNT = FWK_EV_USER + {len(names)}", "};", ""]
    for ev in names:
        x = events[ev] or {}
        args = {a: (_cname(x[a]).lower() or a) for a in ("a", "b") if x.get(a)}
        params = ", ".join(f"int32_t {v}" for v in args.values()) or "void"
        vals = [args.get(a, "0") for a in ("a", "b")]
        h.append(f"static inline fwk_event_t ev_{ev.lower()}({params}) "
                 f"{{ return fwk_ev(EV_{_cname(ev)}, FWK_BROADCAST, {vals[0]}, {vals[1]}); }}")
    h += ["", "static const char *const EV_NAMES[] = {\"NONE\", \"INIT\", \"TIMER\", "
          + ", ".join(f'"{ev}"' for ev in names) + "};", ""]
    for m, x in modules.items():
        cons = [e for e in ((x or {}).get("consumes") or []) if e not in SYSTEM_EVENTS]
        mask = " | ".join(f"(1ull << EV_{_cname(e)})" for e in cons) or "0"
        h.append(f"#define SUBS_{_cname(m)} ({mask})")
    h += ["", "#endif", ""]

    mh = ["/* modules.h: generated by `hwflow fw` from interface.yaml `modules`. Do not edit. */",
          "#ifndef MODULES_H", "#define MODULES_H", '#include "events.h"', ""]
    for m, x in modules.items():
        x = x or {}
        mh.append(f"/* {m}: {x.get('doc', '')}; emits {', '.join(x.get('emits') or []) or '-'}; consumes "
                  f"{', '.join(x.get('consumes') or []) or '-'}; owns {', '.join(x.get('owns') or []) or '-'} */")
        mh.append(f"int mod_{m}_task(fwk_task_t *t, const fwk_event_t *e);")
    mh += ["", "#endif", ""]
    return "\n".join(h), "\n".join(mh)


# ------------------------------------------------------------------ entry points
def generate(d: Design, libs, contract_path: Path | None = None) -> tuple[dict[str, str], list[dict]]:
    p = contract_path or d.dir / "interface.yaml"
    if not p.exists():
        return {}, [{"severity": "error", "code": "no-contract",
                     "message": f"{p} not found: the partition stage writes it (docs/hwsw-codesign.md)"}]
    raw = yaml.safe_load(p.read_text()) or {}
    c = Contract(d, libs, raw)
    body: list[str] = []
    _mcu(c, body)
    _dividers(c, body)
    _buses(c, body)
    _bindings(c, body)
    _tables(c, body)
    _limits(c, body)
    events_h, modules_h = _events(c)
    ident = hashlib.sha1((d.path.read_text() + p.read_text()).encode()).hexdigest()[:12]
    head = ["/* board.h: generated by `hwflow fw` from design.yaml and interface.yaml. Do not edit: change the sources",
            " * and regenerate (docs/hwsw-codesign.md). Every constant names its source. */",
            "#ifndef BOARD_H", "#define BOARD_H", "#include <stdint.h>", "",
            f'#define BOARD_PROJECT "{d.name}"', f'#define BOARD_REVISION "{d.raw.get("revision", "")}"',
            f'#define BOARD_CONTRACT_ID "{ident}"   /* sha1 of design.yaml + interface.yaml */', ""]
    if any(f["severity"] == "error" for f in c.findings):
        head += ['#error "contract errors: run `uv run hwflow fw` and fix interface.yaml or design.yaml"', ""]
    files = {"board.h": "\n".join(head + body + ["#endif /* BOARD_H */", ""])}
    if events_h:
        files["events.h"], files["modules.h"] = events_h, modules_h
    return files, c.findings


def repo_root() -> Path:
    return Path(get_settings().paths.root)



def scaffold(d: Design, fw_dir: Path) -> list[Path]:
    """Module skeletons, app wiring, Makefile and a host-test stub; never overwrites an existing file."""
    raw = yaml.safe_load((d.dir / "interface.yaml").read_text()) or {}
    modules = raw.get("modules") or {}
    try:
        depth = len(fw_dir.resolve().relative_to(repo_root()).parts)
        root = "$(FW)/" + "/".join([".."] * depth)
    except ValueError:
        root = str(repo_root())
    files: dict[Path, str] = {}
    for m, x in modules.items():
        x = x or {}
        cases = "\n".join(f"    case EV_{_cname(e)}:\n        /* TODO */\n        break;"
                          for e in (x.get("consumes") or []) if e not in SYSTEM_EVENTS)
        files[fw_dir / "modules" / f"{m}.c"] = (
            f"/* module {m}: {x.get('doc', '')}\n * emits: {', '.join(x.get('emits') or []) or '-'}; owns: "
            f"{', '.join(x.get('owns') or []) or '-'}. Talks to other modules only through events. */\n"
            f'#include "board.h"\n#include "modules.h"\n\n'
            f"int mod_{m}_task(fwk_task_t *t, const fwk_event_t *e)\n{{\n    (void)t;\n    switch (e->type) {{\n"
            f"    case FWK_EV_INIT:\n        break;\n{cases}\n    default:\n        break;\n    }}\n"
            f"    return FWK_WAIT;\n}}\n")
    tasks = "\n".join(f'static fwk_task_t t_{m} = {{"{m}", mod_{m}_task, 0, SUBS_{_cname(m)}, 0, 0, 0}};'
                      for m in modules)
    adds = "\n".join(f"    fwk_add_task(&t_{m});" for m in modules)
    files[fw_dir / "app.c"] = (f"/* app wiring: one task per module. */\n#include \"modules.h\"\n\n{tasks}\n\n"
                               f"void app_init(void)\n{{\n{adds}\n}}\n")
    files[fw_dir / "Makefile"] = (
        f"FW := $(CURDIR)\nPROJ := $(abspath $(FW)/..)\nROOT := $(abspath {root})\nFWK := $(ROOT)/fw/framework\n"
        f"GEN := $(PROJ)/build/fw\n"
        f"CFLAGS := -std=c11 -Wall -Wextra -Werror -Wno-unused-const-variable -I$(FWK)/include -I$(FWK)/test -I$(GEN) -I$(FW)\n"
        f"SRC := $(FWK)/src/fwk.c $(FWK)/test/fwk_sim.c $(wildcard $(FW)/modules/*.c) $(FW)/app.c $(FW)/test/test_app.c\n"
        f".PHONY: test\ntest: $(GEN)/board.h\n\t$(CC) $(CFLAGS) -o $(GEN)/app_tests $(SRC) -lm && $(GEN)/app_tests\n"
        f"$(GEN)/board.h: $(PROJ)/design.yaml $(PROJ)/interface.yaml\n\tcd $(ROOT) && uv run hwflow fw $(PROJ)\n")
    files[fw_dir / "test" / "test_app.c"] = (
        '#include "fwk_sim.h"\n#include "modules.h"\n\nvoid app_init(void);\n\nint main(void)\n{\n'
        "    fwk_sim_reset(0);\n    app_init();\n    fwk_sim_run_until(1000000);\n"
        '    CHECK(fwk_dropped() == 0, "no event lost");\n    return fwk_fails ? 1 : 0;\n}\n')
    written = []
    for path, text in files.items():
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        written.append(path)
    return written


def run(d: Design, libs, out_dir: Path) -> tuple[Path, list[dict]]:
    files, findings = generate(d, libs)
    fw = out_dir / "fw"
    fw.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (fw / name).write_text(text)
    (fw / "findings.json").write_text(json.dumps(findings, indent=1))
    return fw / "board.h", findings
