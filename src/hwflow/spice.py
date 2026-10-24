"""SPICE netlist generation, ngspice execution, measures, Monte Carlo and derating audit."""

from __future__ import annotations

import hashlib
import json
import math
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

_MODEL_LINE = re.compile(r"^\s*\.model\s+(\S+)\s+([A-Za-z]+)", re.I | re.M)


def model_types(paths) -> dict[str, str]:
    """MODEL NAME -> device type (NMOS, PMOS, NPN, PNP, D, VDMOS, ...) from `.model` lines of the given files."""
    out = {}
    for f in paths:
        try:
            text = Path(f).read_text(errors="replace")
        except OSError:
            continue
        for m in _MODEL_LINE.finditer(text):
            t = m.group(2).upper()
            if t == "VDMOS":  # vdmos is n-channel unless the parameter list says pchan
                line = text[m.start():text.find("\n", m.start()) if "\n" in text[m.start():] else None]
                t = "PMOS" if re.search(r"\bpchan\b", line, re.I) else "NMOS"
            out[m.group(1).upper()] = t
    return out


def element_mismatch(design: Design, c) -> str | None:
    """A model whose device type does not match the element letter of the reference (backlog 17): ngspice reads
    `Q2 ... PMOS_LOGIC` as a bipolar transistor and aborts. Returns a message, or None."""
    sp = c.get("spice")
    if not isinstance(sp, dict) or not sp.get("model") or "subckt" in sp or c.kind not in ("Q", "M", "D"):
        return None
    files = [MODELS] + [design.dir / f for f in (design.raw.get("sim") or {}).get("includes") or []]
    if sp.get("lib"):
        files.append(design.dir / sp["lib"])
    t = model_types(files).get(str(sp["model"]).upper())
    want = {"Q": ("NPN", "PNP"), "M": ("NMOS", "PMOS"), "D": ("D",)}[c.kind]
    if t is None or t in want:
        return None
    letter = {"NMOS": "M", "PMOS": "M", "NPN": "Q", "PNP": "Q", "D": "D"}.get(t)
    fix = f"; rename it {letter}{c.ref[len(c.kind):]} (the first letter of the reference picks the SPICE element)" \
        if letter else ""
    return f"{c.ref} uses {t} model {sp['model']}, but a {c.kind} reference is netlisted as {'/'.join(want)}{fix}"


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


class Block:
    """The netlist lines of one element group: a design component (`eid` = its reference), a testbench
    element (`eid` = its lower-case name) or the node map's extra lines (`eid` = "~extra")."""

    __slots__ = ("eid", "lines", "nodes")

    def __init__(self, eid: str, lines: list[str], nodes: set[str] | None = None):
        self.eid, self.lines, self.nodes = eid, lines, set(nodes or ())


def component_blocks(design: Design, nm: NodeMap, values: dict[str, float] | None = None,
                     ammeters: bool = False) -> tuple[list[Block], list[str], set[str]]:
    """Return (one Block per simulated component, unmodeled refs, extra lib files)."""
    values = values or {}
    blocks, unmodeled, libs = [], [], set()
    for c in design.components.values():
        sp = c.get("spice")
        if sp is False or (isinstance(sp, dict) and sp.get("skip")) or c.get("virtual"):
            continue
        sp = sp if isinstance(sp, dict) else {}
        lines: list[str] = []
        nodes: set[str] = set()

        def node(pin):
            n = nm.pin(c.ref, pin)
            nodes.add(n)
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
            blocks.append(Block(c.ref, lines, nodes))
            continue
        if "subckt" in sp:
            if sp.get("lib"):
                libs.add(str((design.dir / sp["lib"]).resolve()))
            ports = [node(str(p)) for p in sp["ports"]]
            params = " ".join(f"{p}={parse_value(v):g}" for p, v in (sp.get("params") or {}).items())
            lines.append(f"X{c.ref} {' '.join(ports)} {sp['subckt']}" + (f" params: {params}" if params else ""))
            blocks.append(Block(c.ref, lines, nodes))
            continue
        if k in ("D", "Q", "M") and (sp.get("model") or k == "D"):
            ports = [node(str(p)) for p in sp.get("ports", DEFAULT_PORTS[k])]
            model = sp.get("model", "D_GENERIC")
            if sp.get("lib"):
                libs.add(str((design.dir / sp["lib"]).resolve()))
            if k == "M":
                ports.append(ports[2])  # bulk tied to source
            lines.append(f"{k}{c.ref} {' '.join(ports)} {model}")
            blocks.append(Block(c.ref, lines, nodes))
            continue
        unmodeled.append(c.ref)
    return blocks, unmodeled, libs


def component_lines(design: Design, nm: NodeMap, values: dict[str, float] | None = None,
                    ammeters: bool = False) -> tuple[list[str], list[str], set[str]]:
    """Return (element lines, unmodeled refs, extra lib files)."""
    blocks, unmodeled, libs = component_blocks(design, nm, values, ammeters)
    return [ln for b in blocks for ln in b.lines], unmodeled, libs


def _subst(text: str, nm: NodeMap, nets: set[str]) -> str:
    text = re.sub(r"\$\(([^)]+)\)", lambda m: nm.term(m.group(1)), text)

    def vrep(m):
        args = [a.strip() for a in m.group(2).split(",")]
        if all(a in nets or ("." in a and a.split(".")[0] in nm.d.components) for a in args):
            return f"{m.group(1)}(" + ",".join(nm.term(a) for a in args) + ")"
        return m.group(0)

    return re.sub(r"\b(v|vdb|vp|vm|vr|vi)\(([^()]+)\)", vrep, text, flags=re.I)


def testbench_blocks(design: Design, nm: NodeMap) -> list[Block]:
    sim = design.raw.get("sim") or {}
    nets = set(design.nets())
    out = []
    for s in sim.get("sources") or []:
        if "line" in s:
            ln = _subst(s["line"], nm, nets)
        else:
            ln = f"{s['name']} {nm.term(str(s['p']))} {nm.term(str(s['n']))} {s['value']}"
        name = ln.split()[0].lower() if ln.split() else "?"
        out.append(Block(name, [ln]))
    return out


def testbench_lines(design: Design, nm: NodeMap) -> list[str]:
    return [b.lines[0] for b in testbench_blocks(design, nm)]


class NetMod:
    """A change to the netlist of one run, used by the convergence fallback and by scoped analyses:
    `keep` (element ids to keep, None = all), `lines` (extra netlist lines: .nodeset, Thevenin sources),
    `pre` (control lines inserted right before the first analysis command, after the analysis' own options),
    `control` (replaces the analysis command, its measures and control lines; the analysis' preamble stays),
    `drop_alters` (drop `alter` lines whose device is not in the netlist), `title` (header text)."""

    def __init__(self, keep=None, lines=None, pre=None, control=None, drop_alters=False, title=None):
        self.keep, self.lines, self.pre = keep, list(lines or []), list(pre or [])
        self.control, self.drop_alters, self.title = control, drop_alters, title


ANALYSIS_LINE = re.compile(r"^\s*(op|ac|dc|tran|noise|pz|tf|sens|disto)\b", re.I)


def split_cmd(lines: list[str]) -> tuple[list[str], list[str]]:
    """(preamble, rest): the control lines before the first analysis command, and the rest."""
    for i, ln in enumerate(lines):
        if ANALYSIS_LINE.match(ln):
            return lines[:i], lines[i:]
    return lines, []


def netlist_blocks(design: Design, analysis: dict, nm: NodeMap, values=None, ammeters: bool = False):
    """(blocks in netlist order, unmodeled refs, lib files): components, node-map extras, testbench."""
    comp, unmodeled, libs = component_blocks(design, nm, values, ammeters)
    extra = nm.extra(analysis)
    blocks = comp + ([Block("~extra", list(extra))] if extra else []) + testbench_blocks(design, nm)
    return blocks, unmodeled, libs


def build_netlist(design: Design, analysis: dict, nm: NodeMap | None = None, values=None,
                  temp: float | None = None, ammeters: bool = False, mod: NetMod | None = None) -> tuple[str, list[str]]:
    nm = nm or NodeMap(design)
    nets = set(design.nets())
    blocks, unmodeled, libs = netlist_blocks(design, analysis, nm, values, ammeters)
    if mod is not None and mod.keep is not None:
        blocks = [b for b in blocks if b.eid in mod.keep or b.eid == "~extra"]
    sim = design.raw.get("sim") or {}
    title = mod.title if mod is not None and mod.title else analysis.get("name", "op")
    mode = sim.get("ngbehavior", "native")
    if mode not in ("native", "ps"):
        raise ValueError(f"sim.ngbehavior must be native or ps, got {mode!r}")
    lines = [f"* {design.name} :: {title}"]
    if mode == "ps":
        # Parse-time compatibility cannot be enabled later inside .control.
        # Keep it in the deck so cache keys, scoped runs, and post-layout agree.
        lines.append("* hwflow: ngbehavior=ps")
    lines.append(f".include {MODELS}")
    lines += [f".include {lib}" for lib in sorted(libs)]
    includes = [str((design.dir / inc).resolve()) for inc in sim.get("includes") or []]
    lines += [f".include {inc}" for inc in includes]
    body = [ln for b in blocks for ln in b.lines] + (mod.lines if mod is not None else [])
    lines += body
    if sim.get("leaks", True) is not False and not any("rshunt" in str(o).lower() for o in sim.get("options") or []):
        from . import simrobust  # sim.options rshunt already ties every node to ground
        lines += simrobust.leak_lines(body, [str(MODELS)] + sorted(libs) + includes, simrobust._leak_ohm(design))
    lines.append(f".options temp={temp if temp is not None else sim.get('temp', 25)}")
    for opt in sim.get("options") or []:
        lines.append(f".options {opt}")
    lines.append(".control")
    lines.append("set noaskquit")
    cmd_text = _subst(analysis.get("cmd", "op"), nm, nets)
    pre, rest = split_cmd(cmd_text.splitlines())
    if mod is not None and mod.drop_alters:
        present = {ln.split()[0].lower() for ln in body if ln.split() and ln[0] not in "*.+"}
        pre = [ln for ln in pre if not _alter_target(ln) or _alter_target(ln) in present]
        rest = [ln for ln in rest if not _alter_target(ln) or _alter_target(ln) in present]
    if mod is not None and mod.control is not None:
        lines += pre + mod.pre + mod.control
        lines += ["quit", ".endc", ".end", ""]
        return "\n".join(lines), unmodeled
    if mod is not None and (mod.pre or mod.drop_alters):
        cmd_text = "\n".join(pre + mod.pre + rest)
    lines.append(cmd_text)
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
    lines.append("quit")  # without it `ngspice -b` runs an op after the control section, which may not converge
    lines += [".endc", ".end", ""]
    return "\n".join(lines), unmodeled


def _alter_target(line: str) -> str | None:
    """The device an `alter`/`altermod` control line changes (lower case), or None."""
    m = re.match(r"^\s*alter(?:mod)?\s+@?([^\s\[=]+)", line, re.I)
    return m.group(1).lower() if m else None


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

    compatibility = ["-n", "-D", "ngbehavior=ps"] if re.search(
        r"^\* hwflow: ngbehavior=ps$", netlist, re.M) else []

    def once(path):
        try:
            p = subprocess.run([NGSPICE, *compatibility, "-b", str(path)], capture_output=True, text=True, timeout=timeout, cwd=workdir)
        except subprocess.TimeoutExpired as e:
            (workdir / f"{name}.log").write_text(_timeout_output(e, timeout))
            raise
        return p.stdout + "\n" + p.stderr, p.returncode

    out, rc = once(cir)
    if retry and crashed(rc, out):
        first = next((ln.strip() for ln in out.splitlines() if CRASH_RE.search(ln)), f"exit {rc}")
        # ngspice-42 corrupts its heap on some netlists with several analyses in one .control block; whether glibc
        # aborts depends on the heap layout, which the length of the file argument shifts (same netlist: crashes as
        # r.cir, runs as reverse_tran_post_T0.cir). The retry runs copies under names of other lengths, relative to cwd.
        for pad in (5, 11, 23, 41):
            alt = workdir / f"{name}.{'r' * pad}.cir"
            alt.write_text(netlist)
            out2, rc = once(alt.name)
            alt.unlink(missing_ok=True)
            if not crashed(rc, out2):
                break
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
# A behavioural source whose expression overflows on one Newton iteration (`Error: 1.3e+238, 2 out of range for ^`
# in line b.xu2.bio): ngspice rejects that iteration and goes on. On a run that reached its stop time with every
# measure passing, it is a warning, not a failed run (home-thermostat-3 burst_vbat20_corner post-layout at 0 and 45 C).
EVAL_MSG_RE = re.compile(r"out of range for\b", re.I)

ABORT_RE = re.compile(r"timestep too small|simulation(s)? (interrupted|aborted)|transient (analysis )?failed|"
                      r"doAnalyses: .*(too small|failed)|run simulation\(s\) aborted", re.I)  # no "singular matrix": gmin stepping recovers from it


# ngspice lines that point at the element or node behind a failed run
DETAIL_RE = re.compile(r"trouble with|no such vector|not found|singular matrix|timestep too small", re.I)


_NGSPICE_VERSION: list[str] = []


def _cache_key(netlist: str) -> str:
    """Hash of everything that decides the ngspice output: the exact netlist text, the ngspice version and the
    content of every file the netlist includes (model files are named by path, so the path alone proves nothing)."""
    if not _NGSPICE_VERSION:
        try:
            p = subprocess.run([NGSPICE, "-v"], capture_output=True, text=True, timeout=20)
            _NGSPICE_VERSION.append(p.stdout + p.stderr)
        except (OSError, subprocess.SubprocessError):
            _NGSPICE_VERSION.append("?")
    h = hashlib.sha1(netlist.encode())
    h.update(_NGSPICE_VERSION[0].encode())
    for m in re.finditer(r"^\.(?:include|lib)\s+(\S+)", netlist, re.I | re.M):
        f = Path(m.group(1).strip("\"'"))
        h.update(f.read_bytes() if f.is_file() else b"-")
    return h.hexdigest()


def _timeout_output(error: subprocess.TimeoutExpired, timeout: float) -> str:
    # TimeoutExpired may contain bytes even when subprocess.run(text=True).
    def decode(value):
        return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")
    partial = "\n".join(filter(None, (decode(error.stdout), decode(error.stderr))))
    return partial + f"\nError: ngspice timed out after {timeout:g} s (simulation aborted)\n"


def run_cached(netlist: str, workdir: Path, name: str, cache: Path | None = None,
               timeout: float = DEFAULT_TIMEOUT_S) -> tuple[str, int, bool, bool]:
    """Run a netlist through the result cache: (ngspice output, return code, cache hit, timed out).
    The .cir and .log files land in `workdir` either way."""
    # Result cache (L6): a netlist that did not change is not simulated again. The measure definitions are
    # lines of the netlist; evaluation (min, max, scale) reruns on the stored ngspice output.
    cfile = cache / f"{_cache_key(netlist)}.json" if cache else None
    hit = None
    try:
        hit = cfile and json.loads(cfile.read_text())
    except (OSError, ValueError):
        pass
    if hit:
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / f"{name}.cir").write_text(netlist)
        (workdir / f"{name}.log").write_text(hit["out"])
        return hit["out"], hit["rc"], True, False
    timed_out = False
    try:
        out, rc = run_ngspice(netlist, workdir, name, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out, rc, timed_out = _timeout_output(e, timeout), -1, True
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / f"{name}.log").write_text(out)
    if cfile and not timed_out and not out.startswith(FLAKY_MARK) and not crashed(rc, out):
        try:
            cache.mkdir(parents=True, exist_ok=True)
            cfile.write_text(json.dumps({"out": out, "rc": rc}))
        except OSError:
            pass
    return out, rc, False, timed_out


def run_analysis(design: Design, analysis: dict, workdir: Path, nm: NodeMap | None = None, values=None,
                 temp=None, tag: str = "", cache: Path | None = None, robust: bool | None = None) -> dict:
    """One analysis at one temperature. `status` is ok, timeout, crash, aborted or error, and `reason` says why a
    row has no value. `analysis.timeout_s` (default 600) bounds the run.

    With `robust` (default: on unless `values` is set, i.e. not in Monte Carlo), a run whose DC operating point did
    not converge cleanly is retried through the convergence fallback (simrobust.recover). An analysis with `scope:`
    on the schematic-level node map runs scoped and validated against the full board (simrobust.run_scoped)."""
    from . import simrobust
    if analysis.get("scope") and values is None and (nm is None or type(nm) is NodeMap):
        return simrobust.run_scoped(design, analysis, workdir, [temp], tag, cache)[0]
    res = run_once(design, analysis, workdir, nm, values, temp, tag, cache)
    if robust is None:
        robust = values is None
    if robust and (design.raw.get("sim") or {}).get("fallback", True) is not False:
        res = simrobust.recover(design, analysis, workdir, nm, temp, tag, cache, res)
        res.pop("_mod", None)
    return res


CONV_RE = re.compile(r"singular matrix|gmin stepping failed|source stepping failed|dc solution failed|"
                     r"transient op failed|no convergence|timestep too small|op(eration)? failed|"
                     r"iteration limit reached", re.I)


def run_once(design: Design, analysis: dict, workdir: Path, nm: NodeMap | None = None, values=None,
             temp=None, tag: str = "", cache: Path | None = None, mod: NetMod | None = None) -> dict:
    """One ngspice run of an analysis, without fallback. `conv` lists the convergence messages of the log."""
    import time
    net, unmodeled = build_netlist(design, analysis, nm, values, temp, mod=mod)
    timeout = float(analysis.get("timeout_s", DEFAULT_TIMEOUT_S))
    t0 = time.time()
    out, rc, hit, timed_out = run_cached(net, workdir, f"{analysis['name']}{tag}", cache, timeout)
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
        warnings = [e for e in errors if MEAS_MSG_RE.search(e) or EVAL_MSG_RE.search(e)]
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
        ghosts = sorted({x for t in re.findall(r"vector (\S+) is not available|no such vector (\S+)", out)
                         for x in t if x})
        if ghosts:  # a probe on a node that the netlist does not have (backlog 46)
            reason = f"node not in the netlist: {', '.join(ghosts[:4])} ({reason})"
    else:
        status, reason = "ok", ""
    if detail and status != "ok":
        reason += f" [{'; '.join(detail)}]"
    for r in rows:
        if r["value"] is None:
            r["na_reason"] = reason or "measure gave no value"
    res = {"name": analysis["name"], "rows": rows, "unmodeled": unmodeled, "errors": errors, "rc": rc,
           "aborted": bool(aborted) or timed_out, "warnings": warnings, "status": status, "seconds": seconds}
    conv = list(dict.fromkeys(ln.strip() for ln in out.splitlines() if CONV_RE.search(ln) and "HWF" not in ln))
    if conv:
        res["conv"] = conv[:12]
    if hit:
        res["cached"] = True
    if reason:
        res["reason"] = reason
    if out.startswith(FLAKY_MARK):
        res["flaky"] = out.splitlines()[0]
    return res


def run_all(design: Design, workdir: Path, nm: NodeMap | None = None, tag: str = "", workers: int | None = None,
            cache: Path | None = None) -> list[dict]:
    """Every analysis at every temperature, in parallel; results keep the order of design.yaml. A scoped analysis
    (`scope:`, schematic-level node map only) is one job over all its temperatures: its validation gate compares the
    scoped and the full-board run at every temperature where the full board converges."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    from . import simrobust
    sim = design.raw.get("sim") or {}
    plain = nm is None or type(nm) is NodeMap
    jobs = []
    for a in sim.get("analyses") or []:
        temps = a.get("temps") or [None]
        if a.get("scope") and plain:
            jobs.append((a, temps))
        else:
            jobs += [(a, t) for t in temps]
    workers = workers or max(1, (os.cpu_count() or 2) - 1)

    def one(job):
        a, t = job
        if isinstance(t, list):
            rs = simrobust.run_scoped(design, a, workdir, t, tag, cache, workers=max(1, min(len(t), workers)))
            for r, tt in zip(rs, t):
                if tt is not None:
                    r["name"] += f" @ {tt}C"
            return rs
        a2 = dict(a, scope=None) if a.get("scope") else a  # post-layout node map: scope does not apply
        r = run_analysis(design, a2, workdir, nm, temp=t, tag=tag + (f"_T{t}" if t is not None else ""),
                         cache=cache)
        if a.get("scope"):
            r["scope_note"] = "scope not applied with this node map: full-board run"
        if t is not None:
            r["name"] += f" @ {t}C"
        return [r]

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return [r for rs in ex.map(one, jobs) for r in rs]


# --------------------------------------------------------------- Monte Carlo

def monte_carlo(design: Design, workdir: Path, runs: int = 50, seed: int = 1, workers: int | None = None,
                fail_dir: Path | None = None) -> list[dict]:
    """Tolerance Monte Carlo. Runs execute in parallel; the sampled values depend only on `seed`.

    Every failing run is saved to `fail_dir` (default: `mc_failures/` next to `workdir`) with the seed, the run
    index, the sampled values and the ngspice log, so the corner can be reproduced. Runs that did not finish
    (timeout, crash, aborted) are left out of the min/max statistics."""
    import json
    import os
    from concurrent.futures import ThreadPoolExecutor
    rng = random.Random(seed)
    sim = design.raw.get("sim") or {}
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    fail_dir = Path(fail_dir or Path(workdir).parent / "mc_failures")
    if fail_dir.exists():
        shutil.rmtree(fail_dir)
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
        fails, failures, excluded = 0, [], 0
        for i, r in enumerate(results):
            bad = [row["name"] for row in r["rows"] if not row.get("debug") and not row["pass"]]
            finished = r.get("status", "ok") in ("ok", "error")
            if bad or not finished:
                fails += 1
                why = r.get("reason") or "fails " + ", ".join(bad[:4])
                name = f"{a['name']}_run{i}.json"
                failures.append({"run": i, "status": r.get("status", "ok"), "reason": why[:200],
                                 "file": f"{fail_dir.name}/{name}"})
                log = Path(workdir) / "mc" / f"{a['name']}_mc{i}.log"
                fail_dir.mkdir(parents=True, exist_ok=True)
                (fail_dir / name).write_text(json.dumps({
                    "analysis": a["name"], "run": i, "seed": seed, "runs": runs, "status": r.get("status"),
                    "reason": why, "failing": bad, "values": samples_values[i],
                    "rows": [{k: row.get(k) for k in ("name", "value", "min", "max", "pass")} for row in r["rows"]],
                    "netlist": str(log.with_suffix(".cir")),
                    "log": log.read_text()[-20000:] if log.exists() else ""}, indent=1, default=str))
            if not finished:
                excluded += 1  # values of a cut or missing waveform are not a tolerance spread
                continue
            for row in r["rows"]:
                if row["value"] is not None and not row.get("debug"):
                    samples[row["name"]].append(row["value"])
        stats = {}
        for k, v in samples.items():
            if v:
                v.sort()
                stats[k] = {"min": v[0], "max": v[-1], "p50": v[len(v) // 2]}
        res = {"analysis": a["name"], "runs": runs, "yield": (runs - fails) / runs, "stats": stats}
        if failures:
            res["failures"] = failures
            res["excluded_from_stats"] = excluded
        out.append(res)
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
    timeout = float(sim.get("derating_timeout_s", DEFAULT_TIMEOUT_S))
    out, rc, _, timed_out = run_cached(net, workdir, "derating", timeout=timeout)
    if timed_out:
        raise RuntimeError(f"derating: ngspice timed out after {timeout:g} s; see sim/derating.log")
    if rc or ABORT_RE.search(out) or re.search(r"^\s*error:", out, re.I | re.M):
        raise RuntimeError(f"derating: no valid operating point (ngspice exit {rc}); see sim/derating.log")
    vals = {}
    for m in re.finditer(rf"^\s*(\S+)\s*=\s*({_NUM})\s*$", out, re.M):
        vals[m.group(1).lower()] = float(m.group(2))
    ammeters = re.findall(r"^(Vam_\S+)\s+\S+\s+(\S+)\s", net, re.M)
    expected = {key.lower() for name, node in ammeters for key in (f"{name}#branch", node)}
    missing = expected - vals.keys()
    if missing:
        raise RuntimeError(f"derating: missing {len(missing)} pin-current/voltage results; see sim/derating.log")
    if any(not math.isfinite(vals[key]) for key in expected):
        raise RuntimeError("derating: nonfinite pin-current/voltage results; see sim/derating.log")
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
