"""Design IR: the single source of truth (design.yaml) and static checks."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_SI = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "": 1.0,
       "k": 1e3, "K": 1e3, "meg": 1e6, "M": 1e6, "g": 1e9, "G": 1e9}


def parse_value(v) -> float:
    """'4k7' -> 4700, '100n' -> 1e-7, '2.2meg' -> 2.2e6, 10 -> 10."""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "")
    s = re.sub(r"(ohm|Ω|F|H|V|A|W|Hz)$", "", s, flags=re.I) or s
    m = re.fullmatch(r"(\d+)([pnuµmkKMgG]|meg|R)(\d+)", s)  # 4k7, 4R7
    if m:
        mult = 1.0 if m.group(2) == "R" else _SI[m.group(2)]
        return float(f"{m.group(1)}.{m.group(3)}") * mult
    m = re.fullmatch(r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)(meg|[fpnuµmkKMgG]?)", s)
    if not m:
        raise ValueError(f"cannot parse value {v!r}")
    return float(m.group(1)) * _SI[m.group(2)]


def fmt_value(x: float, unit: str = "") -> str:
    if x == 0:
        return f"0{unit}"
    for suf, mult in (("G", 1e9), ("M", 1e6), ("k", 1e3), ("", 1), ("m", 1e-3), ("u", 1e-6), ("n", 1e-9), ("p", 1e-12)):
        if abs(x) >= mult * 0.9999:
            s = f"{x / mult:.3g}"
            return f"{s}{suf}{unit}"
    return f"{x:.3g}{unit}"


E_SERIES = {
    "E6": [1.0, 1.5, 2.2, 3.3, 4.7, 6.8],
    "E12": [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2],
    "E24": [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0, 3.3, 3.6, 3.9, 4.3, 4.7, 5.1,
            5.6, 6.2, 6.8, 7.5, 8.2, 9.1],
}
E_SERIES["E96"] = [round(10 ** (i / 96), 2) for i in range(96)]


def snap_e(x: float, series: str = "E24") -> float:
    dec = math.floor(math.log10(x))
    base = x / 10 ** dec
    vals = E_SERIES[series] + [10.0]
    best = min(vals, key=lambda v: abs(math.log(v / base)))
    return best * 10 ** dec


@dataclass
class Component:
    ref: str
    data: dict

    @property
    def pins(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in (self.data.get("pins") or {}).items()}

    @property
    def kind(self) -> str:
        return re.match(r"[A-Za-z]+", self.ref).group(0).upper()

    def get(self, k, d=None):
        return self.data.get(k, d)


@dataclass
class Design:
    path: Path
    raw: dict
    components: dict[str, Component] = field(default_factory=dict)
    budget_widths: dict[str, float] = field(default_factory=dict)  # net -> mm from budgets: on the current placement

    @classmethod
    def load(cls, path: str | Path) -> "Design":
        path = Path(path)
        if not path.exists() and (Path("projects") / path).exists():
            path = Path("projects") / path  # bare project name
        if path.is_dir():
            path = path / "design.yaml"
        raw = yaml.safe_load(path.read_text())
        d = cls(path=path, raw=raw)
        for ref, c in (raw.get("components") or {}).items():
            d.components[ref] = Component(ref, c or {})
        if raw.get("blocks"):
            from . import blocks
            blocks.expand(d)  # instantiate mode: components a block adds
        return d

    def save(self):
        """Write back only what changed, keeping comments, order and flow style (ruamel round-trip)."""
        import io

        from ruamel.yaml import YAML
        self.raw["components"] = {r: c.data for r, c in self.components.items() if not c.get("from_block")}
        rt = YAML()
        rt.preserve_quotes = True
        rt.width = 120
        doc = rt.load(self.path.read_text())

        def merge(dst, src):
            for k in list(dst.keys()):
                if k not in src:
                    del dst[k]
            for k, v in src.items():
                if isinstance(v, dict) and isinstance(dst.get(k), dict):
                    merge(dst[k], v)
                elif dst.get(k) != v or k not in dst:
                    dst[k] = v

        merge(doc, self.raw)
        buf = io.StringIO()
        rt.dump(doc, buf)
        self.path.write_text(buf.getvalue())

    @property
    def name(self) -> str:
        return self.raw.get("project", self.path.parent.name)

    @property
    def dir(self) -> Path:
        return self.path.parent

    @property
    def board(self) -> dict:
        b = {"width_mm": 50, "height_mm": 40, "layers": 2, "thickness_mm": 1.6, "copper_oz": 1.0,
             "clearance_mm": 0.2, "track_mm": 0.25, "via_drill_mm": 0.3, "via_dia_mm": 0.6,
             "gnd_pour": True, "er": 4.3, "margin_mm": 1.0, "router": "krt"}
        b.update(self.raw.get("board") or {})
        return b

    def net_info(self, net: str) -> dict:
        return (self.raw.get("nets") or {}).get(net, {}) or {}

    def nets(self) -> dict[str, list[tuple[str, str]]]:
        """net -> [(ref, pin)]"""
        out: dict[str, list[tuple[str, str]]] = {}
        for c in self.components.values():
            for pin, net in c.pins.items():
                if net.upper() in ("NC", "~"):
                    continue
                out.setdefault(net, []).append((c.ref, pin))
        return dict(sorted(out.items()))

    def ground_nets(self) -> set[str]:
        g = {n for n, i in (self.raw.get("nets") or {}).items() if (i or {}).get("class") == "ground"}
        return g or {n for n in self.nets() if n.upper() in ("GND", "0", "AGND", "DGND")}

    def bom(self) -> list[dict]:
        groups: dict[tuple, dict] = {}
        for c in self.components.values():
            if c.get("dnp") or c.get("virtual"):
                continue
            key = (c.get("value"), c.get("footprint"), c.get("mpn"), c.get("lcsc"))
            g = groups.setdefault(key, {"refs": [], "value": c.get("value"), "footprint": c.get("footprint"),
                                        "mpn": c.get("mpn"), "lcsc": c.get("lcsc"),
                                        "unit_price_usd": c.get("price_usd")})
            g["refs"].append(c.ref)
        rows = []
        for g in groups.values():
            g["qty"] = len(g["refs"])
            g["refs"] = ",".join(sorted(g["refs"], key=_natkey))
            rows.append(g)
        return sorted(rows, key=lambda r: _natkey(r["refs"]))

    def cost(self) -> tuple[float, list[str]]:
        total, missing = 0.0, []
        for r in self.bom():
            if r["unit_price_usd"] is None:
                missing.append(r["refs"])
            else:
                total += float(r["unit_price_usd"]) * r["qty"]
        return total, missing


def _natkey(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


# ---------------------------------------------------------------- static checks

# datasheet package names that KiCad footprints spell differently (normalized: upper case, alphanumerics only)
PACKAGE_ALIASES = [
    ["SOD882", "DFN10062", "DFN1006"], ["SOD523", "SC79"], ["SOD323", "SC76"], ["SOT323", "SC70"],
    ["SOT363", "SC706", "SC88"], ["SOT236", "TSOT236"], ["SOT235", "TSOT235"], ["TO252", "DPAK"],
    ["TO263", "D2PAK"], ["SMA", "DSMA", "DO214AC"], ["SMB", "DSMB", "DO214AA"], ["SMC", "DSMC", "DO214AB"],
    ["MSOP", "VSSOP"], ["SOT23", "SOT233", "TO236"],
]


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def package_findings(c: Component, fp: dict) -> list[tuple[str, str]]:
    """Compare the datasheet package (`package`, `package_pads`, `package_pitch_mm`) with the footprint (backlog 56)."""
    out = []
    pkg = c.get("package")
    name = str(c.get("footprint", "")).split(":")[-1]
    pads = [p for p in fp["pads"] if p.get("number")]
    numbers = {p["number"] for p in pads}
    if pkg:
        key = _norm(pkg)
        names = {key}.union(*[g for g in PACKAGE_ALIASES if key in g])
        if not any(n in _norm(name) for n in names):
            out.append(("warn", f"{c.ref}: datasheet package {pkg} but footprint {name} does not name it "
                                f"(check the land pattern against the datasheet)"))
    if c.get("package_pads") is not None and int(c.get("package_pads")) != len(numbers):
        out.append(("error", f"{c.ref}: package has {c.get('package_pads')} pads, footprint {name} has {len(numbers)}"))
    if c.get("package_pitch_mm") is not None and len(pads) >= 2:
        best = {}
        for p in pads:  # pitch = smallest centre distance between pads of different numbers
            for q in pads:
                if p["number"] != q["number"]:
                    dd = math.hypot(p["x"] - q["x"], p["y"] - q["y"])
                    best[p["number"]] = min(best.get(p["number"], dd), dd)
        pitch = min(best.values()) if best else None
        want = float(c.get("package_pitch_mm"))
        if pitch is not None and abs(pitch - want) > max(0.05, 0.1 * want):
            out.append(("error", f"{c.ref}: package pitch {want} mm, footprint {name} pitch {pitch:.2f} mm"))
    return out


def check(design: Design, libs=None) -> list[dict]:
    """Return findings: {sev: error|warn|info, code, msg}."""
    f: list[dict] = []

    def add(sev, code, msg):
        f.append({"sev": sev, "code": code, "msg": msg})

    nets = design.nets()
    for net, conns in nets.items():
        if len(conns) == 1 and not design.net_info(net).get("allow_single"):
            add("error", "single-pin-net", f"net {net} has only one connection ({conns[0][0]}.{conns[0][1]})")
    for c in design.components.values():
        if c.get("virtual"):
            continue
        for k in ("value", "footprint", "symbol"):
            if not c.get(k):
                add("error", "missing-field", f"{c.ref} has no {k}")
        if c.kind in ("R", "C", "L") and c.get("value"):
            try:
                parse_value(c.get("value"))
            except ValueError:
                add("error", "bad-value", f"{c.ref} value {c.get('value')!r} is not numeric")
        if c.get("role") not in (None, "temp_sensor", "antenna", "none"):
            add("error", "bad-role", f"{c.ref} role {c.get('role')!r} is not temp_sensor, antenna or none")
        if not c.pins:
            add("error", "no-pins", f"{c.ref} has no pin-to-net map")
        from .spice import element_mismatch
        msg = element_mismatch(design, c)
        if msg:
            add("error", "spice-element", msg)

    # library-backed checks: pins exist, every symbol pin is mapped, pads match
    if libs is not None:
        for c in design.components.values():
            if c.get("virtual"):
                continue
            try:
                sym = libs.symbol(c.get("symbol"))
            except Exception as e:  # noqa: BLE001
                add("error", "symbol-missing", f"{c.ref}: {e}")
                sym = None
            try:
                fp = libs.footprint(c.get("footprint"))
            except Exception as e:  # noqa: BLE001
                add("error", "footprint-missing", f"{c.ref}: {e}")
                fp = None
            if sym:
                spins = {p["number"]: p for p in sym["pins"]}
                for p in c.pins:
                    if p not in spins:
                        add("error", "unknown-pin", f"{c.ref} pin {p} is not in symbol {c.get('symbol')} "
                                                    f"(has {sorted(spins, key=_natkey)})")
                nc = {str(x) for x in (c.get("nc") or [])}
                for num, p in spins.items():
                    if num not in c.pins and num not in nc and p["type"] != "no_connect":
                        add("error", "unmapped-pin", f"{c.ref} pin {num} ({p['name']}, {p['type']}) is not "
                                                     f"connected and not listed in nc")
                if fp:
                    for sev, msg in package_findings(c, fp):
                        add(sev, "package-mismatch", msg)
                    pads = {p["number"] for p in fp["pads"] if p["number"]}
                    missing = [n for n in spins if n not in pads]
                    if missing:
                        add("error", "pad-mismatch", f"{c.ref}: symbol pins {missing} have no pad in "
                                                     f"{c.get('footprint')} (pads {sorted(pads, key=_natkey)})")
        # electrical rule checks on pin types
        drivers = {"power_out", "output"}
        for net, conns in nets.items():
            types = []
            for ref, pin in conns:
                c = design.components[ref]
                try:
                    p = next(p for p in libs.symbol(c.get("symbol"))["pins"] if p["number"] == pin)
                    types.append((ref, pin, p["type"]))
                except Exception:  # noqa: BLE001
                    pass
            outs = [t for t in types if t[2] in drivers]
            if len(outs) > 1 and not design.net_info(net).get("allow_multi_driver"):
                add("error", "multi-driver", f"net {net} has several drivers: {outs}")
            if net not in design.ground_nets() and any(t[2] == "power_in" for t in types) and not any(t[2] == "power_out" for t in types) \
                    and not design.net_info(net).get("external_supply"):
                add("warn", "no-power-source", f"net {net} feeds power_in pins but no power_out pin drives it "
                                               f"(set nets.{net}.external_supply: true if it comes from a connector)")
    from .floorplan import cut_pad_findings, cuts, validate
    for msg in validate(design):
        add("error", "floorplan", msg)
    from . import pcb
    if libs is not None and cuts(design):  # pads of fixed parts within the copper-to-edge clearance of a cut
        from . import placer
        try:
            parts = placer.build_parts(design, libs)
        except Exception:  # noqa: BLE001 - a missing footprint is reported above
            parts = {}
        edge = max(float(design.board["clearance_mm"]), pcb.EDGE_CLEARANCE_MM)
        for msg in cut_pad_findings(design, parts, edge):
            add("warn", "cut-pad", msg)
    # nets.<name>.layers: known copper layers, and an SMD pad of the net on one of them
    import fnmatch
    copper = pcb.cu_layers(int(design.board["layers"]))
    allowed = pcb.net_layers(design)
    for net, lays in allowed.items():
        pats = design.net_info(net).get("layers")
        pats = [pats] if isinstance(pats, str) else pats
        if not isinstance(pats, (list, tuple)) or not pats:
            add("error", "net-layers", f"nets.{net}.layers must be a list of copper layer names")
            continue
        for p in pats:
            if not any(fnmatch.fnmatchcase(L, str(p)) for L in copper):
                add("error", "net-layers", f"nets.{net}.layers: {p} is not a copper layer of this "
                                           f"{design.board['layers']}-layer board ({', '.join(copper)})")
        if libs is None or not lays:
            continue
        from .routing import pad_layers
        for ref, pin in nets.get(net, []):
            c = design.components[ref]
            if c.get("virtual"):
                continue
            try:
                pads = [p for p in libs.footprint(c.get("footprint"))["pads"] if p["number"] == pin]
            except Exception:  # noqa: BLE001 - reported as footprint-missing
                continue
            for p in pads:
                cu = pad_layers(p["layers"], copper)
                if p["kind"] == "smd" and cu and not set(cu) & set(lays):
                    add("error", "net-layers", f"{ref}.{pin} ({net}) is an SMD pad on {', '.join(cu)} only, not on "
                                               f"the net's layers {', '.join(lays)}")
                    break
    from .budgets import check as budget_check
    f += budget_check(design)
    if libs is not None:  # layout feasibility proofs from footprints, pin nets and net widths (feasibility.py)
        from .feasibility import check as feasibility_check
        f += feasibility_check(design, libs)
    # decoupling heuristic: each IC supply net has a cap to ground
    gnds = design.ground_nets()
    caps_on = {}
    for c in design.components.values():
        if c.kind == "C":
            ns = set(c.pins.values())
            if ns & gnds:
                for n in ns - gnds:
                    caps_on.setdefault(n, []).append(c.ref)
    for c in design.components.values():
        if c.kind == "U":
            for net in set(c.pins.values()) - gnds:
                if design.net_info(net).get("class") == "power" and net not in caps_on:
                    add("warn", "no-decoupling", f"{c.ref} on power net {net} has no capacitor to ground")
    if design.raw.get("blocks"):
        from . import blocks
        f += blocks.findings(design, libs)
    return f
