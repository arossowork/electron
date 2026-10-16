"""SPICE netlist generation, ngspice execution, measures, Monte Carlo and derating audit."""

from __future__ import annotations

import random
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .design import Design, parse_value

MODELS = Path(__file__).parent / "models" / "generic.lib"
NGSPICE = shutil.which("ngspice") or "/opt/homebrew/bin/ngspice"


def sanitize(net: str, grounds: set[str]) -> str:
    if net in grounds:
        return "0"
    s = net.replace("+", "p").replace("-", "n").replace("/", "_").replace(".", "_")
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    return ("n_" + s if s[0].isdigit() else s).lower()


class NodeMap:
    """Maps design pins and nets to SPICE node names (schematic level: one node per net)."""

    def __init__(self, design: Design):
        self.d = design
        self.grounds = design.ground_nets()

    def pin(self, ref: str, pin: str) -> str:
        net = self.d.components[ref].pins.get(pin)
        if net is None or net.upper() in ("NC", "~"):
            return f"nc_{ref}_{pin}".lower()
        return sanitize(net, self.grounds)

    def net(self, net: str) -> str:
        return sanitize(net, self.grounds)

    def term(self, t: str) -> str:
        """Testbench terminal: a net name, or REF.PIN."""
        if "." in t:
            ref, pin = t.split(".", 1)
            if ref in self.d.components:
                return self.pin(ref, pin)
        return self.net(t)

    def extra(self) -> list[str]:
        return []


DEFAULT_TOL = {"R": 0.01, "C": 0.10, "L": 0.20}
DEFAULT_PORTS = {"D": ["2", "1"], "Q": ["1", "2", "3"], "M": ["1", "2", "3"]}


def component_lines(design: Design, nm: NodeMap, values: dict[str, float] | None = None,
                    ammeters: bool = False) -> tuple[list[str], list[str], set[str]]:
    """Return (element lines, unmodeled refs, extra lib files)."""
    values = values or {}
    lines, unmodeled, libs = [], [], set()
    for c in design.components.values():
        sp = c.get("spice")
        if sp is False or (isinstance(sp, dict) and sp.get("skip")) or c.get("virtual"):
            continue
        sp = sp if isinstance(sp, dict) else {}

        def node(pin):
            n = nm.pin(c.ref, pin)
            if ammeters and not n.startswith("nc_"):
                a = f"am_{c.ref}_{pin}".lower()
                lines.append(f"Vam_{c.ref}_{pin} {n} {a} 0")
                return a
            return n

        k = c.kind
        if k in ("R", "C", "L") and "subckt" not in sp:
            pins = sorted(c.pins)[:2]
            if len(pins) < 2:
                continue
            a, b = node(pins[0]), node(pins[1])
            val = values.get(c.ref, parse_value(c.get("value")))
            if k == "C" and (sp.get("esr") or sp.get("esl")):
                mid = f"esr_{c.ref}".lower()
                lines.append(f"C{c.ref} {a} {mid} {val:g}")
                if sp.get("esl"):
                    mid2 = f"esl_{c.ref}".lower()
                    lines.append(f"R{c.ref}_esr {mid} {mid2} {parse_value(sp.get('esr', 1e-3)):g}")
                    lines.append(f"L{c.ref}_esl {mid2} {b} {parse_value(sp['esl']):g}")
                else:
                    lines.append(f"R{c.ref}_esr {mid} {b} {parse_value(sp['esr']):g}")
            else:
                lines.append(f"{k}{c.ref} {a} {b} {val:g}")
            continue
        if "subckt" in sp:
            if sp.get("lib"):
                libs.add(str((design.dir / sp["lib"]).resolve()))
            ports = [node(str(p)) for p in sp["ports"]]
            params = " ".join(f"{p}={parse_value(v):g}" for p, v in (sp.get("params") or {}).items())
            lines.append(f"X{c.ref} {' '.join(ports)} {sp['subckt']}" + (f" params: {params}" if params else ""))
            continue
        if k in ("D", "Q", "M") and (sp.get("model") or k == "D"):
            ports = [node(str(p)) for p in sp.get("ports", DEFAULT_PORTS[k])]
            model = sp.get("model", "D_GENERIC")
            if sp.get("lib"):
                libs.add(str((design.dir / sp["lib"]).resolve()))
            if k == "M":
                ports.append(ports[2])  # bulk tied to source
            lines.append(f"{k}{c.ref} {' '.join(ports)} {model}")
            continue
        unmodeled.append(c.ref)
    return lines, unmodeled, libs


def _subst(text: str, nm: NodeMap, nets: set[str]) -> str:
    text = re.sub(r"\$\(([^)]+)\)", lambda m: nm.term(m.group(1)), text)

    def vrep(m):
        args = [a.strip() for a in m.group(2).split(",")]
        if all(a in nets or ("." in a and a.split(".")[0] in nm.d.components) for a in args):
            return f"{m.group(1)}(" + ",".join(nm.term(a) for a in args) + ")"
        return m.group(0)

    return re.sub(r"\b(v|vdb|vp|vm|vr|vi)\(([^()]+)\)", vrep, text, flags=re.I)


def testbench_lines(design: Design, nm: NodeMap) -> list[str]:
    sim = design.raw.get("sim") or {}
    nets = set(design.nets())
    out = []
    for s in sim.get("sources") or []:
        if "line" in s:
            out.append(_subst(s["line"], nm, nets))
        else:
            out.append(f"{s['name']} {nm.term(str(s['p']))} {nm.term(str(s['n']))} {s['value']}")
    return out


def build_netlist(design: Design, analysis: dict, nm: NodeMap | None = None, values=None,
                  temp: float | None = None, ammeters: bool = False) -> tuple[str, list[str]]:
    nm = nm or NodeMap(design)
    nets = set(design.nets())
    comp, unmodeled, libs = component_lines(design, nm, values, ammeters)
    sim = design.raw.get("sim") or {}
    lines = [f"* {design.name} :: {analysis.get('name', 'op')}", f".include {MODELS}"]
    lines += [f".include {lib}" for lib in sorted(libs)]
    for inc in sim.get("includes") or []:
        lines.append(f".include {(design.dir / inc).resolve()}")
    lines += comp + nm.extra() + testbench_lines(design, nm)
    lines.append(f".options temp={temp if temp is not None else sim.get('temp', 25)}")
    for opt in sim.get("options") or []:
        lines.append(f".options {opt}")
    lines.append(".control")
    lines.append("set noaskquit")
    lines.append(_subst(analysis.get("cmd", "op"), nm, nets))
    if analysis.get("cmd", "op").split()[0].lower() == "tran":
        lines.append("let hwf_tend = time[length(time)-1]")
        lines.append("echo HWF __tend $&hwf_tend")
    for m in analysis.get("measures") or []:
        if m.get("plot"):
            lines.append(f"setplot {m['plot']}")
        if "meas" in m:
            kind = analysis.get("cmd", "op").split()[0].lower()
            kind = {"noise": "ac"}.get(kind, kind)
            lines.append(f"meas {kind} {m['name']} {_subst(m['meas'], nm, nets)}")
        else:
            lines.append(f"let {m['name']} = {_subst(m['expr'], nm, nets)}")
        lines.append(f"echo HWF {m['name']} $&{m['name']}")
    for ln in analysis.get("control") or []:
        lines.append(_subst(ln, nm, nets))
    lines += [".endc", ".end", ""]
    return "\n".join(lines), unmodeled


_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def run_ngspice(netlist: str, workdir: Path | None = None, name: str = "sim") -> tuple[str, int]:
    workdir = workdir or Path(tempfile.mkdtemp(prefix="hwf_"))
    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    cir = workdir / f"{name}.cir"
    cir.write_text(netlist)
    p = subprocess.run([NGSPICE, "-b", str(cir)], capture_output=True, text=True, timeout=600, cwd=workdir)
    out = p.stdout + "\n" + p.stderr
    (workdir / f"{name}.log").write_text(out)
    return out, p.returncode


def parse_measures(out: str) -> dict[str, float | None]:
    res: dict[str, float | None] = {}
    for m in re.finditer(r"^HWF (\S+) (.*)$", out, re.M):
        nums = re.findall(_NUM, m.group(2))
        res[m.group(1)] = float(nums[0]) if nums and "&" not in m.group(2) else None
    return res


def evaluate(measures: list[dict], values: dict[str, float | None]) -> list[dict]:
    rows = []
    for m in measures:
        v = values.get(m["name"])
        scale = float(m.get("scale", 1))
        v = None if v is None else v * scale
        ok = v is not None
        if ok and "min" in m and v < float(m["min"]):
            ok = False
        if ok and "max" in m and v > float(m["max"]):
            ok = False
        rows.append({"name": m["name"], "value": v, "min": m.get("min"), "max": m.get("max"),
                     "unit": m.get("unit", ""), "pass": ok, "req": m.get("req")})
    return rows


ABORT_RE = re.compile(r"timestep too small|simulation(s)? (interrupted|aborted)|transient (analysis )?failed|"
                      r"doAnalyses: .*(too small|failed)|run simulation\(s\) aborted|singular matrix", re.I)


def run_analysis(design: Design, analysis: dict, workdir: Path, nm: NodeMap | None = None, values=None,
                 temp=None, tag: str = "") -> dict:
    net, unmodeled = build_netlist(design, analysis, nm, values, temp)
    try:
        out, rc = run_ngspice(net, workdir, f"{analysis['name']}{tag}")
    except subprocess.TimeoutExpired:
        out, rc = "Error: ngspice timed out (simulation aborted)", -1
    vals = parse_measures(out)
    errors = [ln for ln in out.splitlines() if re.search(r"\berror\b", ln, re.I) and "HWF" not in ln][:8]
    rows = evaluate(analysis.get("measures") or [], vals)
    aborted = [ln.strip() for ln in out.splitlines() if ABORT_RE.search(ln)][:3]
    parts = analysis.get("cmd", "op").split()
    if parts[0].lower() == "tran" and len(parts) >= 3:
        tstop = parse_value(parts[2])
        tend = vals.get("__tend")
        if tend is None or tend < tstop * 0.999:
            aborted.insert(0, f"transient stopped at {tend if tend is not None else 'n/a'} s of {tstop:g} s")
    if aborted:
        # a measure on a cut waveform is not evidence: fail every row of this analysis
        for r in rows:
            r["pass"] = False
            r["aborted"] = True
        errors = [f"simulation aborted: {aborted[0]}"] + errors
    return {"name": analysis["name"], "rows": rows, "unmodeled": unmodeled, "errors": errors, "rc": rc,
            "aborted": bool(aborted)}


def run_all(design: Design, workdir: Path, nm: NodeMap | None = None, tag: str = "") -> list[dict]:
    sim = design.raw.get("sim") or {}
    results = []
    for a in sim.get("analyses") or []:
        temps = a.get("temps") or [None]
        for t in temps:
            r = run_analysis(design, a, workdir, nm, temp=t, tag=tag + (f"_T{t}" if t is not None else ""))
            if t is not None:
                r["name"] += f" @ {t}C"
            results.append(r)
    return results


# --------------------------------------------------------------- Monte Carlo

def monte_carlo(design: Design, workdir: Path, runs: int = 50, seed: int = 1) -> list[dict]:
    rng = random.Random(seed)
    sim = design.raw.get("sim") or {}
    out = []
    for a in sim.get("analyses") or []:
        if not a.get("monte_carlo", True) or not a.get("measures"):
            continue
        samples: dict[str, list[float]] = {m["name"]: [] for m in a["measures"]}
        fails = 0
        for i in range(runs):
            values = {}
            for c in design.components.values():
                if c.kind in DEFAULT_TOL and c.get("value") and not (c.get("spice") or {}).get("subckt"):
                    tol = float(c.get("tolerance", DEFAULT_TOL[c.kind]))
                    values[c.ref] = parse_value(c.get("value")) * (1 + rng.gauss(0, tol / 3))
            r = run_analysis(design, a, workdir / "mc", values=values, tag=f"_mc{i}")
            if not all(row["pass"] for row in r["rows"]):
                fails += 1
            for row in r["rows"]:
                if row["value"] is not None:
                    samples[row["name"]].append(row["value"])
        stats = {}
        for k, v in samples.items():
            if v:
                v.sort()
                stats[k] = {"min": v[0], "max": v[-1], "p50": v[len(v) // 2]}
        out.append({"analysis": a["name"], "runs": runs, "yield": (runs - fails) / runs, "stats": stats})
    return out


# --------------------------------------------------------------- derating audit

RATED_POWER = {"0201": 0.05, "0402": 0.063, "0603": 0.1, "0805": 0.125, "1206": 0.25, "1210": 0.5,
               "2010": 0.75, "2512": 1.0}


def _pkg_power(fp: str) -> float | None:
    for k, v in RATED_POWER.items():
        if f"_{k}_" in fp or fp.endswith(f"_{k}"):
            return v
    return None


def derating_audit(design: Design, workdir: Path) -> list[dict]:
    """Operating point with an ammeter on every pin: power per part, voltage across caps."""
    sim = design.raw.get("sim") or {}
    op = {"name": "derating_op", "cmd": sim.get("derating_cmd", "op"),
          "control": ["print all"]}
    nm = NodeMap(design)
    net, _ = build_netlist(design, op, nm, ammeters=True)
    out, _ = run_ngspice(net, workdir, "derating")
    vals = {}
    for m in re.finditer(rf"^\s*(\S+)\s*=\s*({_NUM})\s*$", out, re.M):
        vals[m.group(1).lower()] = float(m.group(2))
    ta = float(sim.get("ambient_c", 25))
    rows = []
    for c in design.components.values():
        if c.get("virtual") or (c.get("spice") is False):
            continue
        p = 0.0
        seen = False
        for pin in c.pins:
            a = f"am_{c.ref}_{pin}".lower()
            v = vals.get(a, vals.get(f"v({a})"))
            i = vals.get(f"vam_{c.ref}_{pin}#branch".lower())
            if v is not None and i is not None:
                p += v * i
                seen = True
        if not seen:
            continue
        row = {"ref": c.ref, "p_w": p, "issues": []}
        rating = c.get("power_rating_w") or (_pkg_power(c.get("footprint", "")) if c.kind == "R" else None)
        if rating:
            row["rating_w"] = rating
            if p > 0.5 * float(rating):
                row["issues"].append(f"dissipates {p * 1e3:.1f} mW, over 50% of {float(rating) * 1e3:.0f} mW rating")
        if c.get("theta_ja") is not None:
            tj = ta + p * float(c.get("theta_ja"))
            row["tj_c"] = tj
            if tj > float(c.get("tj_max_c", 125)) - 20:
                row["issues"].append(f"Tj {tj:.0f}C is within 20C of tj_max")
        if c.kind == "C" and len(c.pins) == 2:
            pa, pb = sorted(c.pins)
            va = vals.get(f"am_{c.ref}_{pa}".lower(), 0.0)
            vb = vals.get(f"am_{c.ref}_{pb}".lower(), 0.0)
            vc = abs(va - vb)
            row["v"] = vc
            if c.get("voltage_rating_v"):
                lim = float(c.get("voltage_rating_v")) * float(c.get("derate", 0.5))
                if vc > lim:
                    row["issues"].append(f"{vc:.2f} V across, over derated limit {lim:.2f} V")
            else:
                row["issues"].append("no voltage_rating_v set")
        rows.append(row)
    return rows
