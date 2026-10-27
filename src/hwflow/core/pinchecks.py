"""Parts-time pin checks (`hwflow check`, docs/reference-reuse.md section 3): datasheet limits against the net
voltages, and pin functions against the net roles. Deterministic, from design.yaml and the KiCad symbols' pin types.

Inputs a design gives:
- `limits:` on a component, from the datasheet (page in `source`):
    limits: {abs_max_v: 6.0, rec_max_v: 5.5, min_v: -0.3, pins: {"7": 5.5, "VIN*": 6.0}, hole_mm: 1.1,
             current_a: 2.0, source: "ds p.2"}
  `abs_max_v` holds for every pin to ground unless `pins` (pin number or pin-name glob) overrides it; `rec_max_v` is the
  recommended operating maximum (a warning); `hole_mm` is the datasheet's PCB hole for a through-hole lead;
  `current_a` is a fuse's rated current (also read from `current_rating_a`).
- `v_max` (and `v_min`) on a net: the highest voltage the net reaches (a regulator's maximum output with ripple, not its
  nominal). Without it, a net named like a rail (+3V3, 5V_SYS, 1V8, P10V5, N10V) takes its voltage from the name, a
  ground net is 0 V, and any other net gets an upper bound from what drives it (below).

Upper bound of a net without a voltage: the largest bound over the pins on it, where a resistor, inductor, ferrite,
fuse, diode or transistor pin passes the bound of its part's other nets, and an IC output, bidirectional or tri-state
pin passes the highest supply (power_in net) of its IC. A capacitor, a relay and an input pin add nothing. A connector pin, an
IC passive or power_out pin, or a pin whose source is unknown makes the net unknown: no bound is guessed, the net is
not checked.
"""

from __future__ import annotations

import fnmatch
import re

from .units import parse_value

DRIVE_TYPES = {"output", "bidirectional", "tri_state", "open_collector", "open_emitter"}
PASS_KINDS = {"R", "L", "FB", "F", "D", "M", "Q", "RN", "NT"}  # parts that pass a net's voltage to their other nets
FUSE_DERATE = 0.75  # largest steady load over the fuse rating at 25 C: Littelfuse Fuseology, "Operating current"
HOLE_TOL_MM = 0.05  # a footprint drill this far from the datasheet hole is reported
SOURCING_SKIP = {"R", "C", "TP", "MP", "H", "KO", "SLOT", "FID", "LOGO", "NT"}
CONTROL_PIN = re.compile(r"^~?\{?(N|X)?(EN|CE|RST|RESET|SHDN|SD|STBY|SLEEP|PD|BOOT\d*|nRST|nRESET|nSLEEP|nSHDN|EN\d)\}?B?$",
                         re.I)
GND_PIN = re.compile(r"^(A|D|P|S)?GND\d*$|^EP(AD)?$|^PAD$", re.I)
SUPPLY_PIN = re.compile(r"^(A|D|P)?(VCC|VDD|VIN|VBAT|VM|VS|VBUS)\d*$", re.I)
I2C_NET = re.compile(r"(^|_)(SDA|SCL)(\d|_|$)", re.I)
TVS_PART = re.compile(r"TVS|ESD|SMAJ|SMBJ|SMCJ|SMF\d|PESD|ESDA|TPD\d|SP0\d|PRTR|USBLC|SRV05", re.I)
RAIL_NAME = re.compile(r"^(?P<sign>[+\-PN]?)(?P<int>\d{1,3})V(?P<frac>\d{0,2})(?:_|$)", re.I)


def _kind(ref: str) -> str:
    return re.match(r"[A-Za-z]*", ref).group(0).upper()


def name_voltage(net: str) -> float | None:
    """A rail's voltage from its name (+3V3 3.3, 5V_SYS 5, 1V8 1.8, P10V5 10.5, N10V -10, -5V -5), else None."""
    m = RAIL_NAME.match(net)
    if not m:
        return None
    v = float(f"{m.group('int')}.{m.group('frac') or 0}")
    return -v if m.group("sign").upper() in ("-", "N") else v


def _f(x) -> float | None:
    if x is None:
        return None
    try:
        return float(parse_value(x)) if isinstance(x, str) else float(x)
    except (TypeError, ValueError):
        return None


def net_bounds(design, pin_types) -> tuple[dict[str, float], dict[str, str]]:
    """(net -> upper voltage bound, net -> where it comes from) for the nets with a known bound.
    `pin_types(ref, pin) -> (electrical type, pin name) | None`."""
    nets = design.nets()
    gnd = design.ground_nets()
    known: dict[str, float] = {}
    why: dict[str, str] = {}
    for n in nets:
        info = design.net_info(n)
        if _f(info.get("v_max")) is not None:
            known[n], why[n] = _f(info["v_max"]), "v_max"
        elif n in gnd:
            known[n], why[n] = 0.0, "ground"
        elif (v := name_voltage(n)) is not None:
            known[n], why[n] = max(v, 0.0), "name"
    comps = design.components
    supply: dict[str, list[str]] = {}  # IC ref -> its power_in nets
    for ref, c in comps.items():
        for pin, n in c.pins.items():
            t = pin_types(ref, pin)
            if t and t[0] == "power_in":
                supply.setdefault(ref, []).append(n)
    for _ in range(len(nets) + 1):
        changed = False
        for n, conns in nets.items():
            if n in known:
                continue
            vals, unknown = [], False
            for ref, pin in conns:
                c = comps[ref]
                k = _kind(ref)
                if k in ("C", "K") or c.get("virtual"):  # a capacitor or a relay coil is a load, not a source
                    continue
                if k in PASS_KINDS:
                    others = [m for p, m in c.pins.items() if p != pin and m != n]
                    if not others or any(m not in known for m in others):
                        unknown = True
                        break
                    vals.append(max(known[m] for m in others))
                    continue
                t = pin_types(ref, pin)
                if t is None:
                    unknown = True
                    break
                if t[0] in ("input", "no_connect", "power_in"):
                    continue
                if t[0] in DRIVE_TYPES and supply.get(ref) and all(m in known for m in supply[ref]):
                    vals.append(max(known[m] for m in supply[ref]))
                    continue
                unknown = True
                break
            if not unknown and vals:
                known[n], why[n] = max(vals), "driven"
                changed = True
        if not changed:
            break
    return known, why


def _pin_limit(limits: dict, pin: str, name: str, key: str = "pins") -> float | None:
    pins = limits.get(key) or {}
    if pin in {str(k) for k in pins}:
        return _f(next(v for k, v in pins.items() if str(k) == pin))
    for k, v in pins.items():
        if name and fnmatch.fnmatchcase(name.upper(), str(k).upper()):
            return _f(v)
    return None


def findings(design, libs) -> list[dict]:
    f: list[dict] = []

    def add(sev, code, msg):
        f.append({"sev": sev, "code": code, "msg": msg})

    cache: dict[str, dict] = {}

    def pin_types(ref, pin):
        c = design.components[ref]
        sym = c.get("symbol")
        if not sym or libs is None:
            return None
        if sym not in cache:
            try:
                cache[sym] = {p["number"]: (p["type"], p["name"]) for p in libs.symbol(sym)["pins"]}
            except Exception:  # noqa: BLE001 - a missing symbol is reported by the netlist checks
                cache[sym] = {}
        return cache[sym].get(pin)

    bounds, why = net_bounds(design, pin_types)
    nets = design.nets()
    gnd = design.ground_nets()

    # 1. datasheet limits against the net bounds
    for ref, c in design.components.items():
        lim = c.get("limits")
        if lim is None:
            if c.get("current_rating_a") is not None and _kind(ref) == "F":
                lim = {}
            else:
                continue
        if not isinstance(lim, dict):
            add("error", "bad-limits", f"{ref}: limits must be a mapping (abs_max_v, rec_max_v, pins, hole_mm, ...)")
            continue
        src = f" ({lim['source']})" if lim.get("source") else " (no source: cite the datasheet page)"
        amax, rmax, vmin = _f(lim.get("abs_max_v")), _f(lim.get("rec_max_v")), _f(lim.get("min_v"))
        for pin, n in c.pins.items():
            if n not in bounds:
                continue
            t = pin_types(ref, pin)
            name = t[1] if t else ""
            lab = f"{ref} pin {pin}" + (f" ({name})" if name and name not in (pin, "~") else "")
            v = bounds[n]
            lim_p = _pin_limit(lim, pin, name)
            a = lim_p if lim_p is not None else amax
            r = _pin_limit(lim, pin, name, "rec_pins")
            r = r if r is not None else rmax
            how = {"v_max": "v_max", "name": "from its name", "ground": "ground", "driven": "bound from its drivers"}[why[n]]
            if a is not None and v > a + 1e-9:
                add("error", "abs-max", f"{lab} on {n} reaches {v:g} V ({how}), above the absolute maximum {a:g} V{src}")
            elif r is not None and v > r + 1e-9:
                add("warn", "rec-max", f"{lab} on {n} reaches {v:g} V ({how}), above the recommended maximum {r:g} V{src}")
            vm = _f(design.net_info(n).get("v_min"))
            if vmin is not None and vm is not None and vm < vmin - 1e-9:
                add("error", "abs-min", f"{lab} on {n} goes down to {vm:g} V, below the absolute minimum {vmin:g} V{src}")
        # fuse: steady load over rating
        rating = _f(lim.get("current_a") if lim.get("current_a") is not None else c.get("current_rating_a"))
        if _kind(ref) == "F" and rating:
            load = max((_f(design.net_info(n).get("current_a")) or 0.0 for n in c.pins.values()), default=0.0)
            if load > FUSE_DERATE * rating:
                add("warn", "fuse-derating", f"{ref}: nets carry {load:g} A, {100 * load / rating:.0f} % of its "
                                             f"{rating:g} A rating (keep <= {FUSE_DERATE * 100:.0f} % at 25 C, less when "
                                             f"hotter: the datasheet derating curve){src}")
        # through-hole lead: footprint drill against the datasheet hole
        hole = _f(lim.get("hole_mm"))
        if hole and libs is not None and c.get("footprint"):
            try:
                fp = libs.footprint(c.get("footprint"))
            except Exception:  # noqa: BLE001 - reported by the netlist checks
                fp = None
            drills = sorted({round(p["drill"], 3) for p in (fp or {}).get("pads", []) if p.get("drill")})
            off = [dr for dr in drills if abs(dr - hole) > HOLE_TOL_MM]
            if off:
                add("warn", "hole-size", f"{ref}: footprint {c.get('footprint')} drills {', '.join(f'{x:g}' for x in off)}"
                                         f" mm, datasheet hole {hole:g} mm{src}")

    # 2. capacitor voltage rating against the voltage across it
    for ref, c in design.components.items():
        rv = _f(c.get("voltage_rating_v"))
        if _kind(ref) != "C" or not rv or len(c.pins) != 2:
            continue
        a, b = list(c.pins.values())
        if a in bounds and b in bounds:
            lo = {n: _f(design.net_info(n).get("v_min")) or min(0.0, name_voltage(n) or 0.0) for n in (a, b)}
            across = max(bounds[a] - lo[b], bounds[b] - lo[a])
            if across > rv + 1e-9:
                add("error", "cap-voltage", f"{ref}: up to {across:g} V across it ({a} / {b}), rated {rv:g} V")

    # 3. pin functions against net roles
    def types_on(n):
        return [(r, p, pin_types(r, p)) for r, p in nets.get(n, [])]

    def has_pull(n):
        """A resistor from `n` to a supply or ground net (a pull-up or pull-down)."""
        for r, _p in nets.get(n, []):
            c = design.components[r]
            if _kind(r) == "R" and len(c.pins) == 2:
                other = [m for m in c.pins.values() if m != n]
                if other and (other[0] in gnd or other[0] in bounds or design.net_info(other[0]).get("class") == "power"
                              or any((pin_types(rr, pp) or ("",))[0] in ("power_in", "power_out")
                                     for rr, pp in nets.get(other[0], []))):
                    return True
        return False

    for n, conns in nets.items():
        if n in gnd or design.net_info(n).get("external_supply"):
            continue
        tl = types_on(n)
        typed = [t for t in tl if t[2] is not None]
        pull_ok = design.net_info(n).get("pullup") in ("internal", "external", True)
        od = [f"{r}.{p}" for r, p, t in typed if t[0] in ("open_collector", "open_emitter")]
        if od and not has_pull(n) and not pull_ok:
            add("warn", "open-drain-pullup", f"net {n}: open-drain pins {', '.join(od)} and no pull-up resistor "
                                             f"(add one, or set nets.{n}.pullup: internal with the datasheet line)")
        elif I2C_NET.search(n) and len(conns) >= 2 and not has_pull(n) and not pull_ok:
            add("warn", "i2c-pullup", f"net {n}: an I2C line with no pull-up resistor (or nets.{n}.pullup: internal)")
        if typed and len(typed) == len(tl) and all(t[0] == "input" for _r, _p, t in typed):
            add("warn", "floating-input", f"net {n}: only input pins ({', '.join(f'{r}.{p}' for r, p, _t in typed)}), "
                                          f"nothing drives it")
        ctrl = [(r, p, t) for r, p, t in typed if t[0] == "input" and CONTROL_PIN.match(t[1] or "")]
        if ctrl and not has_pull(n) and why.get(n) not in ("v_max", "name", "ground"):
            drivers = [t for _r, _p, t in typed if t[0] != "input"]
            if drivers and len(typed) == len(tl) and all(t[0] in ("bidirectional", "tri_state") for t in drivers):
                add("warn", "reset-state", f"net {n}: control input {', '.join(f'{r}.{p} ({t[1]})' for r, p, t in ctrl)} "
                                           f"is driven only by GPIO-type pins, which float during reset: add a pull "
                                           f"resistor to its safe state")
    for ref, c in design.components.items():
        if c.get("virtual"):
            continue
        for pin, n in c.pins.items():
            t = pin_types(ref, pin)
            if not t:
                continue
            typ, name = t
            if GND_PIN.match(name or "") and typ in ("power_in", "passive") and n not in gnd \
                    and name.upper() not in ("EP", "EPAD", "PAD") and not (name_voltage(n) or 0) < 0:
                add("warn", "pin-role", f"{ref} pin {pin} ({name}) is on {n}, which is not a ground net")
            elif SUPPLY_PIN.match(name or "") and typ == "power_in" and n in gnd:
                add("warn", "pin-role", f"{ref} pin {pin} ({name}) is a supply input tied to ground net {n}")
            elif typ == "power_out" and n in gnd:
                add("warn", "pin-role", f"{ref} pin {pin} ({name}) is a power output tied to ground net {n}")

    # 3b. a TVS / ESD part only clamps what reaches it through short copper: a copper_len budget holds it to the pin
    # it protects (thermo-e2e-1 review 1 #14: "TVS distance not constrained")
    legs = " ".join(str(leg) for b in design.raw.get("budgets") or [] if (b or {}).get("kind") == "copper_len"
                    for leg in (b or {}).get("path") or [])
    tvs = [ref for ref, c in design.components.items()
           if _kind(ref) == "D" and (TVS_PART.search(blob := " ".join(str(c.get(k) or "") for k in ("value", "mpn", "symbol")))
                                     or "power_protection" in blob.lower())
           and not re.search(rf"(^|[\s,>]){re.escape(ref)}\.", legs)]
    if tvs:
        add("warn", "tvs-budget", f"TVS/ESD parts with no copper_len budget to the pin they protect: {', '.join(tvs)} "
                                  f"(budgets: {{kind: copper_len, path: ['{tvs[0]}.<pin> -> <connector pin>'], "
                                  f"max_mm: <datasheet or ESD note>}})")

    # 4. sourcing: active and electromechanical parts in the BOM without an LCSC number or a sourcing note (generic
    # resistors and capacitors are bought by value; test points and mechanical parts are not bought)
    from .design import in_bom
    miss = [r for r, c in design.components.items()
            if not c.get("virtual") and not c.get("dnp") and in_bom(c) and not c.get("lcsc") and not c.get("sourcing")
            and _kind(r) not in SOURCING_SKIP]
    if miss:
        add("warn", "sourcing", f"no `lcsc` (JLCPCB assembly) and no `sourcing:` note (where to buy it) on "
                                f"{len(miss)} parts: {', '.join(sorted(miss)[:12])}{' ...' if len(miss) > 12 else ''}")
    unknown_ext = [n for n in nets if design.net_info(n).get("external_supply") and n not in bounds]
    if unknown_ext:
        add("info", "net-voltage", f"external supply nets without v_max (no abs-max check on their pins): "
                                   f"{', '.join(unknown_ext)}")
    return f
