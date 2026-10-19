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


def _generic_names() -> set[str]:
    names = set()
    for ln in MODELS.read_text().splitlines():
        parts = ln.split()
        if len(parts) >= 2 and parts[0].lower() in (".subckt", ".model"):
            names.add(parts[1].upper())
    return names


GENERIC_NAMES = _generic_names()


def model_origin(c) -> str | None:
    """'vendor', 'fit', 'behavioral', 'builtin' or 'unclassified' for a simulated active part; None otherwise."""
    sp = c.get("spice")
    if sp is False or c.kind in ("R", "C", "L") or c.get("virtual"):
        return None
    sp = sp if isinstance(sp, dict) else {}
    if sp.get("source"):
        return str(sp["source"]).lower()
    if not sp.get("lib"):
        name = str(sp.get("subckt") or sp.get("model") or ("D_GENERIC" if c.kind == "D" else "")).upper()
        return "builtin" if name in GENERIC_NAMES else None
    return "unclassified"


def uses_generic_model(c) -> bool:
    """True when an active part's model is an approximation (built-in, datasheet fit, behavioral, or unknown)."""
    return model_origin(c) not in (None, "vendor")


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

    def extra(self, analysis: dict | None = None) -> list[str]:
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
    lines += comp + nm.extra(analysis) + testbench_lines(design, nm)
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


CRASH_RE = re.compile(r"free\(\): invalid|double free|corrupted (size|double)|malloc\(\)|segmentation fault|"
                      r"core dumped", re.I)
FLAKY_MARK = "hwflow: ngspice crashed"
DEFAULT_TIMEOUT_S = 600.0


def crashed(rc: int, out: str) -> bool:
    """ngspice died on a signal or a heap error: a tool failure, not a design result."""
    return rc < 0 or rc >= 128 or bool(CRASH_RE.search(out))


def run_ngspice(netlist: str, workdir: Path | None = None, name: str = "sim",
                timeout: float = DEFAULT_TIMEOUT_S, retry: bool = True) -> tuple[str, int]:
    """Run ngspice in batch mode. A crash (signal exit, heap error) is retried once; the output then starts
    with FLAKY_MARK so the caller can log the run as flaky. Raises subprocess.TimeoutExpired."""
    workdir = workdir or Path(tempfile.mkdtemp(prefix="hwf_"))
    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    cir = workdir / f"{name}.cir"
    cir.write_text(netlist)

    def once():
        p = subprocess.run([NGSPICE, "-b", str(cir)], capture_output=True, text=True, timeout=timeout, cwd=workdir)
        return p.stdout + "\n" + p.stderr, p.returncode

    out, rc = once()
    if retry and crashed(rc, out):
        first = next((ln.strip() for ln in out.splitlines() if CRASH_RE.search(ln)), f"exit {rc}")
        out2, rc = once()
        out = f"{FLAKY_MARK} ({first[:120]}, exit {rc}); retried once (flaky)\n" + out2
    (workdir / f"{name}.log").write_text(out)
    return out, rc


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
        row = {"name": m["name"], "value": v, "min": m.get("min"), "max": m.get("max"),
               "unit": m.get("unit", ""), "pass": ok, "req": m.get("req")}
        if is_debug(m):
            row["debug"] = True
        rows.append(row)
    return rows


def is_debug(m: dict) -> bool:
    """A debug probe (`dbg_*` name or `debug: true`): reported apart, never counted in pass/total or verdict."""
    return bool(m.get("debug")) or str(m.get("name", "")).startswith("dbg_")


# ngspice reports a `meas` that finds no crossing as "Error: measure X ... out of interval"; when every
# measure of the analysis still has a passing value, that line is a warning, not a design failure
MEAS_MSG_RE = re.compile(r"out of interval|^\s*error: measure\b|no such variable", re.I)

ABORT_RE = re.compile(r"timestep too small|simulation(s)? (interrupted|aborted)|transient (analysis )?failed|"
                      r"doAnalyses: .*(too small|failed)|run simulation\(s\) aborted", re.I)  # no "singular matrix": gmin stepping recovers from it


# ngspice lines that point at the element or node behind a failed run
DETAIL_RE = re.compile(r"trouble with|no such vector|not found|singular matrix|timestep too small", re.I)


def run_analysis(design: Design, analysis: dict, workdir: Path, nm: NodeMap | None = None, values=None,
                 temp=None, tag: str = "") -> dict:
    """One ngspice run. `status` is ok, timeout, crash, aborted or error, and `reason` says why a row has no
    value. `analysis.timeout_s` (default 600) bounds the run."""
    import time
    net, unmodeled = build_netlist(design, analysis, nm, values, temp)
    timeout = float(analysis.get("timeout_s", DEFAULT_TIMEOUT_S))
    t0 = time.time()
    timed_out = False
    try:
        out, rc = run_ngspice(net, workdir, f"{analysis['name']}{tag}", timeout=timeout)
    except subprocess.TimeoutExpired:
        out, rc, timed_out = f"Error: ngspice timed out after {timeout:g} s (simulation aborted)", -1, True
    seconds = round(time.time() - t0, 1)
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
    warnings = []
    real = [r for r in rows if not r.get("debug")]
    if not aborted and real and all(r["pass"] for r in real):
        warnings = [e for e in errors if MEAS_MSG_RE.search(e)]
        errors = [e for e in errors if e not in warnings]
    detail = [ln.strip() for ln in out.splitlines() if DETAIL_RE.search(ln) and "HWF" not in ln][:3]
    body = out.split("\n", 1)[-1] if out.startswith(FLAKY_MARK) else out
    is_crash = not timed_out and crashed(rc, body)
    if timed_out:
        status, reason = "timeout", f"timeout after {timeout:g} s (timeout_s)"
    elif is_crash:
        status, reason = "crash", f"ngspice crashed (exit {rc}) twice"
    elif aborted:
        status, reason = "aborted", f"aborted: {aborted[0]}"
    elif errors or any(r["value"] is None for r in real):
        status = "error"
        reason = f"error: {errors[0]}" if errors else "measure gave no value"
    else:
        status, reason = "ok", ""
    if detail and status != "ok":
        reason += f" [{'; '.join(detail)}]"
    for r in rows:
        if r["value"] is None:
            r["na_reason"] = reason or "measure gave no value"
    res = {"name": analysis["name"], "rows": rows, "unmodeled": unmodeled, "errors": errors, "rc": rc,
           "aborted": bool(aborted) or timed_out, "warnings": warnings, "status": status, "seconds": seconds}
    if reason:
        res["reason"] = reason
    if out.startswith(FLAKY_MARK):
        res["flaky"] = out.splitlines()[0]
    return res


def run_all(design: Design, workdir: Path, nm: NodeMap | None = None, tag: str = "", workers: int | None = None) -> list[dict]:
    """Every analysis at every temperature, in parallel; results keep the order of design.yaml."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    sim = design.raw.get("sim") or {}
    jobs = [(a, t) for a in sim.get("analyses") or [] for t in (a.get("temps") or [None])]
    workers = workers or max(1, (os.cpu_count() or 2) - 1)

    def one(job):
        a, t = job
        r = run_analysis(design, a, workdir, nm, temp=t, tag=tag + (f"_T{t}" if t is not None else ""))
        if t is not None:
            r["name"] += f" @ {t}C"
        return r

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(one, jobs))


# --------------------------------------------------------------- Monte Carlo

def monte_carlo(design: Design, workdir: Path, runs: int = 50, seed: int = 1, workers: int | None = None) -> list[dict]:
    """Tolerance Monte Carlo. Runs execute in parallel; the sampled values depend only on `seed`."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    rng = random.Random(seed)
    sim = design.raw.get("sim") or {}
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    # draw every sample up front so results do not depend on scheduling
    samples_values = []
    for _ in range(runs):
        values = {}
        for c in design.components.values():
            if c.kind in DEFAULT_TOL and c.get("value") and not (c.get("spice") or {}).get("subckt"):
                tol = float(c.get("tolerance", DEFAULT_TOL[c.kind]))
                values[c.ref] = parse_value(c.get("value")) * (1 + rng.gauss(0, tol / 3))
        samples_values.append(values)
    out = []
    for a in sim.get("analyses") or []:
        if not a.get("monte_carlo", True) or not a.get("measures"):
            continue
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(lambda iv: run_analysis(design, a, workdir / "mc", values=iv[1], tag=f"_mc{iv[0]}"),
                                  enumerate(samples_values)))
        samples: dict[str, list[float]] = {m["name"]: [] for m in a["measures"] if not is_debug(m)}
        fails = 0
        for r in results:
            if not all(row["pass"] for row in r["rows"] if not row.get("debug")):
                fails += 1
            for row in r["rows"]:
                if row["value"] is not None and not row.get("debug"):
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
