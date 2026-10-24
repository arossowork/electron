"""Convergence fallback, floating-node leaks and scoped analyses for the SPICE stage.

Every simplification here must prove itself or be refused:

- The convergence fallback does not change the circuit. It retries a run whose DC operating point (OP) did not
  converge cleanly with a `.nodeset` taken from a clean OP of the same netlist (25 C or the nearest temperature that
  converges), then with gmin and source stepping, then with both. A fallback result counts only when its OP is clean
  and plausible: every supply net (`class: power`), every boundary net and every measured net stays within
  `sim.fallback_tol` of the reference OP.
- A floating node (no DC path to ground) gets a noiseless 1 Gohm leak to ground, one per floating island. Each is
  reported with its OP current and its AC corner and is a `hwflow check` warning, so the designer fixes the circuit.
- A scoped analysis keeps only the parts reachable from the measured nets without crossing a boundary net and
  replaces each boundary net with a Thevenin source (DC from the full-board OP, impedance fitted to the full board's
  small-signal output impedance). Its result is used only after the validation gate: at every temperature where the
  full board converges, every measure of the scoped run matches the full run within `scope.tol`.
"""

from __future__ import annotations

import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import spice
from ..core.design import Design, parse_value

# ----------------------------------------------------------------------------------------------- DC graph

_SUBCKTS: dict[str, tuple[float, dict]] = {}
_SUB_LOCK = threading.Lock()
_VREF = re.compile(r"\bv(?:db|m|p|r|i)?\(\s*([^,\s()]+)\s*(?:,\s*([^,\s()]+)\s*)?\)", re.I)


def _logical_lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        ln = raw.split(";")[0].rstrip()
        if not ln.strip() or ln.lstrip().startswith("*"):
            continue
        if ln.lstrip().startswith("+") and out:
            out[-1] += " " + ln.lstrip()[1:]
        else:
            out.append(ln.strip())
    return out


def parse_subckts(files) -> dict[str, tuple[list[str], list[str]]]:
    """NAME -> (ports, body element lines) of every `.subckt` in the files (nested definitions included)."""
    out: dict[str, tuple[list[str], list[str]]] = {}
    for f in files:
        p = Path(f)
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        with _SUB_LOCK:
            hit = _SUBCKTS.get(str(p))
        if hit and hit[0] == mt:
            out.update(hit[1])
            continue
        defs: dict[str, tuple[list[str], list[str]]] = {}
        stack: list[tuple[str, list[str], list[str]]] = []
        for ln in _logical_lines(p.read_text(errors="replace")):
            low = ln.lower()
            if low.startswith(".subckt"):
                t = ln.split()
                ports = []
                for x in t[2:]:
                    if x.lower().startswith("params:") or "=" in x:
                        break
                    ports.append(x.lower())
                stack.append((t[1].upper() if len(t) > 1 else "?", ports, []))
            elif low.startswith(".ends"):
                if stack:
                    name, ports, body = stack.pop()
                    defs[name] = (ports, body)
            elif stack and not ln.startswith("."):
                stack[-1][2].append(ln)
        with _SUB_LOCK:
            _SUBCKTS[str(p)] = (mt, defs)
        out.update(defs)
    return out


def _x_parts(t: list[str]) -> tuple[list[str], str]:
    """(nodes, subckt name) of an X line."""
    end = len(t)
    for i, x in enumerate(t[1:], 1):
        if x.lower().startswith("params:") or "=" in x:
            end = i
            break
    return [x.lower() for x in t[1:end - 1]], (t[end - 1].upper() if end - 1 >= 1 else "?")


def element_dc(line: str, subs: dict, memo: dict) -> tuple[str, list[str], list[tuple[str, str]], bool]:
    """(name, nodes, DC-conducting node pairs, unknown) of one element line. `unknown` marks an element whose DC
    behaviour is not known (its nodes are then treated as having a DC path: no leak is ever added on a guess)."""
    t = line.split()
    if not t or t[0][0] in "*.+":
        return "", [], [], False
    name, k = t[0].lower(), t[0][0].upper()
    refs = [n.lower() for m in _VREF.finditer(line) for n in m.groups() if n and "." not in n]

    def nodes(n):
        return [x.lower() for x in t[1:1 + n]]

    if k in "RLVD" and len(t) >= 3:
        a, b = nodes(2)
        return name, [a, b], [(a, b)], False
    if k in "CI" and len(t) >= 3:
        return name, nodes(2), [], False
    if k == "Q" and len(t) >= 4:
        c, b, e = nodes(3)
        return name, [c, b, e], [(c, b), (b, e)], False
    if k == "M" and len(t) >= 5:
        d, g, s, b = nodes(4)
        return name, [d, g, s, b], [(d, s), (s, b), (d, b)], False
    if k == "J" and len(t) >= 4:
        d, g, s = nodes(3)
        return name, [d, g, s], [(d, g), (g, s)], False
    if k in "EGFH" and len(t) >= 3:
        out = nodes(2)
        ctrl = []
        if k in "EG" and len(t) >= 5 and not re.match(r"(value|vol|cur|poly|table|laplace)\b|.*=", t[3].lower()):
            ctrl = [t[3].lower(), t[4].lower()]
        edges = [(out[0], out[1])] if k in "EH" else []
        return name, out + ctrl + refs, edges, False
    if k == "B" and len(t) >= 3:
        out = nodes(2)
        is_v = re.search(r"\bv\s*=", " ".join(t[3:]), re.I) is not None
        return name, out + refs, [(out[0], out[1])] if is_v else [], False
    if k == "S" and len(t) >= 5:
        a, b, c, d = nodes(4)
        return name, [a, b, c, d], [(a, b)], False
    if k == "W" and len(t) >= 3:
        a, b = nodes(2)
        return name, [a, b], [(a, b)], False
    if k == "K":
        return name, [], [], False
    if k == "X" and len(t) >= 3:
        ns, sub = _x_parts(t)
        conn = subckt_conn(sub, subs, memo)
        if conn is None:
            return name, ns, [], True
        edges = []
        for i, j in conn:
            a = "0" if i == -1 else (ns[i] if i < len(ns) else None)
            b = "0" if j == -1 else (ns[j] if j < len(ns) else None)
            if a is not None and b is not None:
                edges.append((a, b))
        return name, ns, edges, False
    return name, [x.lower() for x in t[1:3]], [], True


class _UF:
    def __init__(self):
        self.p: dict[str, str] = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def subckt_conn(name: str, subs: dict, memo: dict, depth: int = 0):
    """DC connectivity of a subckt's ports: pairs (i, j) of port indices joined by a DC path (-1 = ground), or None
    when the subckt is unknown or holds an element whose DC behaviour is unknown."""
    if name in memo:
        return memo[name]
    if name not in subs or depth > 20:
        return None
    memo[name] = None  # guards recursion
    ports, body = subs[name]
    uf = _UF()
    for ln in body:
        _, ns, edges, unknown = element_dc(ln, subs, memo) if not ln.upper().startswith("X") else \
            _x_dc(ln, subs, memo, depth)
        for n in ns:
            uf.find(n)
        if unknown:
            memo[name] = None
            return None
        for a, b in edges:
            uf.union(a, b)
    pairs = []
    roots = [uf.find(p) for p in ports]
    g = uf.find("0")
    for i, r in enumerate(roots):
        if r == g:
            pairs.append((i, -1))
        for j in range(i + 1, len(roots)):
            if roots[j] == r:
                pairs.append((i, j))
    memo[name] = pairs
    return pairs


def _x_dc(ln, subs, memo, depth):
    t = ln.split()
    ns, sub = _x_parts(t)
    conn = subckt_conn(sub, subs, memo, depth + 1)
    if conn is None:
        return t[0].lower(), ns, [], True
    edges = []
    for i, j in conn:
        a = "0" if i == -1 else (ns[i] if i < len(ns) else None)
        b = "0" if j == -1 else (ns[j] if j < len(ns) else None)
        if a is not None and b is not None:
            edges.append((a, b))
    return t[0].lower(), ns, edges, False


def floating_islands(body: list[str], files) -> list[list[str]]:
    """Groups of netlist nodes with no DC path to ground (sorted, largest first). Conservative: a node on an element
    whose DC behaviour is unknown (unknown subckt, XSPICE, transmission line) is taken as grounded."""
    subs = parse_subckts(files)
    memo: dict = {}
    uf = _UF()
    uf.find("0")
    for ln in body:
        _, ns, edges, unknown = element_dc(ln, subs, memo)
        for n in ns:
            uf.find(n)
        for a, b in edges:
            uf.union(a, b)
        if unknown:
            for n in ns:
                uf.union(n, "0")
    g = uf.find("0")
    groups: dict[str, list[str]] = {}
    for n in list(uf.p):
        r = uf.find(n)
        if r != g:
            groups.setdefault(r, []).append(n)
    return sorted((sorted(v) for v in groups.values()), key=lambda v: (-len(v), v))


LEAK_PREFIX = "Rhwf_leak_"


def _leak_node(island: list[str]) -> str:
    top = [n for n in island if "." not in n and not n.startswith("nc_")]
    return (top or island)[0]


def leak_lines(body: list[str], files, ohm: float = 1e9) -> list[str]:
    """One noiseless leak resistor to ground per floating island (none when the netlist has none)."""
    isl = floating_islands(body, files)
    if not isl:
        return []
    out = ["* hwflow: leak to ground on each node with no DC path (sim.leaks: false turns it off)"]
    for i in isl:
        n = _leak_node(i)
        out.append(f"{LEAK_PREFIX}{re.sub(r'[^A-Za-z0-9_]', '_', n)} {n} 0 {ohm:g} noisy=0")
    return out


def leak_ohm(design: Design) -> float:
    lk = (design.raw.get("sim") or {}).get("leaks", True)
    return float(parse_value(lk.get("ohm", 1e9))) if isinstance(lk, dict) else 1e9


def design_islands(design: Design, nm=None) -> tuple[list[list[str]], dict]:
    """Floating islands of the design's plain netlist and the files read, for `hwflow check` and the report."""
    nm = nm or spice.NodeMap(design)
    blocks, _, libs = spice.netlist_blocks(design, {"name": "op", "cmd": "op"}, nm)
    sim = design.raw.get("sim") or {}
    files = [str(spice.MODELS)] + sorted(libs) + [str((design.dir / i).resolve()) for i in sim.get("includes") or []]
    body = [ln for b in blocks for ln in b.lines]
    caps: dict[str, float] = {}
    for b in blocks:
        for ln in b.lines:
            t = ln.split()
            if t and t[0][0].upper() == "C" and len(t) >= 4:
                try:
                    v = parse_value(t[3])
                except ValueError:
                    continue
                for n in (t[1].lower(), t[2].lower()):
                    caps[n] = caps.get(n, 0.0) + v
    return floating_islands(body, files), caps


def net_names(design: Design, nm=None) -> dict[str, str]:
    """SPICE node -> design net name."""
    nm = nm or spice.NodeMap(design)
    out = {}
    for n in design.nets():
        out.setdefault(nm.net(n), n)
    return out


def check_findings(design: Design) -> list[dict]:
    """`hwflow check` warnings: nets with no DC path to ground in the SPICE netlist."""
    if not (design.raw.get("sim") or {}).get("analyses"):
        return []
    try:
        isl, _ = design_islands(design)
    except Exception:  # a netlist that cannot be built is reported by the sim stage
        return []
    names = net_names(design)
    whats = []
    for i in isl:
        nets = [names.get(n, n) for n in i if not n.startswith("nc_") and "." not in n]
        whats.append("+".join(nets[:4]) or "+".join(i[:2]))
    how = (f"(only capacitors, MOSFET gates, unsimulated parts or high-impedance model inputs): "
           f"{_leak_note(design)}; give the node a real DC path (bias resistor) if the circuit needs one")
    if len(whats) <= 5:
        return [{"sev": "warn", "code": "floating-node", "msg": f"no DC path to ground in the SPICE netlist at {w} "
                 + how} for w in whats]
    return [{"sev": "warn", "code": "floating-node", "msg": f"{len(whats)} nodes have no DC path to ground in the "
             f"SPICE netlist ({', '.join(whats[:8])}{f' and {len(whats) - 8} more' if len(whats) > 8 else ''}) " + how}]


def _rshunt(design: Design) -> bool:
    return any("rshunt" in str(o).lower() for o in (design.raw.get("sim") or {}).get("options") or [])


def _leak_note(design: Design) -> str:
    if (design.raw.get("sim") or {}).get("leaks", True) is False:
        return "sim.leaks is false, so ngspice may not converge"
    if _rshunt(design):
        return "sim.options rshunt already ties every node to ground"
    return f"the simulation adds a noiseless {leak_ohm(design):g} ohm leak to ground there (sim.leaks: false turns it off)"


def leak_report(design: Design, workdir: Path, cache: Path | None) -> list[dict]:
    """Each leak with its OP voltage, its current and its AC corner 1/(2 pi R C_node). The bound: at DC the island
    has no other conducting element, so the leak only defines its level; in AC it is negligible (< 0.5 % on the node
    impedance) at frequencies above 10 x the corner."""
    sim = design.raw.get("sim") or {}
    if sim.get("leaks", True) is False or _rshunt(design):
        return []
    isl, caps = design_islands(design)
    if not isl:
        return []
    ohm = leak_ohm(design)
    probe = op_probe(design, {"name": "leak_op", "cmd": "op"}, None, None, workdir, cache)
    if not probe["clean"]:
        rung, _, p2, _, _ = find_op(design, {"name": "leak_op", "cmd": "op"}, None, None, workdir, cache, "_leak")
        if rung is not None:
            probe = p2
    names = net_names(design)
    fmin = _min_ac_freq(design)
    rows = []
    for i in isl:
        n = _leak_node(i)
        v = probe["volts"].get(n)
        c = sum(caps.get(x, 0.0) for x in i)
        fc = 1 / (2 * math.pi * ohm * c) if c > 0 else None
        row = {"node": n, "net": names.get(n, n), "island": [names.get(x, x) for x in i][:8], "ohm": ohm,
               "v": v, "i_a": None if v is None else v / ohm, "c_f": c, "f_corner_hz": fc,
               "op_clean": probe["clean"]}
        if fc is not None and fmin is not None and 10 * fc > fmin:
            row["warn"] = f"corner {fc:.3g} Hz is within 10x of the lowest analysis frequency {fmin:g} Hz"
        rows.append(row)
    return rows


def _min_ac_freq(design: Design) -> float | None:
    fs = [b[0] for a in (design.raw.get("sim") or {}).get("analyses") or [] for b in [_band(a.get("cmd", ""))] if b]
    return min(fs) if fs else None


# ----------------------------------------------------------------------------------------------- OP probe

OP_FAIL_RE = re.compile(r"gmin stepping failed|source stepping failed|dc solution failed|transient op failed|"
                        r"op(eration)? failed|no convergence|iteration limit reached", re.I)
CHECK_NODE_RE = re.compile(r"check nodes? (\S+)(?: and (\S+))?", re.I)
STEP_PRE = ["option gmin=1e-12", "option gminsteps=100", "option srcsteps=100"]
OP_CTRL = ["op", "echo HWFOP_BEGIN", "print all", "echo HWFOP_END"]
_NUMRE = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_LOCK = threading.Lock()


def _lock(key: str) -> threading.Lock:
    with _LOCKS_LOCK:
        return _LOCKS.setdefault(key, threading.Lock())


def _run(netlist: str, workdir: Path, name: str, cache, timeout: float):
    """run_cached with one run per identical netlist at a time (parallel jobs share OP probes)."""
    with _lock(spice.netlist_cache_key(netlist) if cache else name):
        return spice.run_cached(netlist, workdir, name, cache, timeout)


def _sim_temp(design: Design, temp) -> float:
    return float(temp if temp is not None else (design.raw.get("sim") or {}).get("temp", 25))


def op_probe(design: Design, analysis: dict, nm, temp, workdir: Path, cache, mod: spice.NetMod | None = None,
             tag: str = "") -> dict:
    """The DC operating point of the analysis' netlist (its preamble of options and alters included) at `temp`:
    node voltages, branch currents and whether it converged cleanly (no failed gmin/source stepping)."""
    m = spice.NetMod(keep=mod.keep if mod else None, lines=mod.lines if mod else [], pre=mod.pre if mod else [],
                     drop_alters=mod.drop_alters if mod else False, control=OP_CTRL, title="op probe")
    net, _ = spice.build_netlist(design, analysis, nm, temp=temp, mod=m)
    timeout = float(analysis.get("timeout_s", spice.DEFAULT_TIMEOUT_S))
    out, rc, hit, to = _run(net, Path(workdir), f"{analysis['name']}{tag}_op", cache, timeout)
    volts, cur = {}, {}
    sect = re.search(r"^HWFOP_BEGIN\s*$(.*?)^HWFOP_END\s*$", out, re.M | re.S)
    for mm in re.finditer(rf"^\s*(\S+)\s*=\s*({_NUMRE})\s*$", sect.group(1) if sect else "", re.M):
        k = mm.group(1).lower()
        (cur if "#" in k else volts)[k] = float(mm.group(2))
    fails = list(dict.fromkeys(ln.strip() for ln in out.splitlines() if OP_FAIL_RE.search(ln)))
    clean = bool(volts) and not fails and not to and not spice.crashed(rc, out)
    return {"temp": _sim_temp(design, temp), "volts": volts, "currents": cur, "clean": clean, "fails": fails[:4],
            "checks": check_nodes(out), "cached": hit}


def check_nodes(out: str) -> list[str]:
    """Top-level nodes named by ngspice 'singular matrix: check node ...' messages, most frequent first."""
    cnt: dict[str, int] = {}
    for m in CHECK_NODE_RE.finditer(out):
        for n in m.groups():
            if n and "#" not in n and "." not in n:
                cnt[n.lower()] = cnt.get(n.lower(), 0) + 1
    return sorted(cnt, key=lambda n: -cnt[n])


def reference_op(design: Design, analysis: dict, nm, temp, workdir: Path, cache, tag: str = ""):
    """A clean OP of the same netlist without any fallback: at 25 C, else at the nearest temperature of the analysis
    that converges cleanly. None when no temperature does."""
    t0 = _sim_temp(design, temp)
    cands = {25.0, t0} | {float(t) for t in analysis.get("temps") or [] if t is not None}
    for t in sorted(cands, key=lambda x: (abs(x - 25.0), x)):
        p = op_probe(design, analysis, nm, t, workdir, cache, tag=f"{tag}_ref{t:g}")
        if p["clean"]:
            return p
    return None


def nodeset_lines(ref: dict) -> list[str]:
    items = [f"v({n})={v:.6g}" for n, v in sorted(ref["volts"].items()) if "(" not in n]
    return [".nodeset " + " ".join(items[i:i + 40]) for i in range(0, len(items), 40)]


def gate_nodes(design: Design, analysis: dict, nm) -> set[str]:
    """Nets whose OP voltage must hold in a fallback: supply nets (class: power), scope boundaries, measured nets."""
    nm = nm or spice.NodeMap(design)
    out = {nm.net(n) for n in design.nets() if design.net_info(n).get("class") == "power"}
    sc = analysis.get("scope") or {}
    out |= {nm.net(str(n)) for n in (sc.get("boundary") or [])}
    out |= measured_nodes(design, analysis, nm)
    out.discard("0")
    return out


def measured_nodes(design: Design, analysis: dict, nm) -> set[str]:
    nets = set(design.nets())
    text = "\n".join([analysis.get("cmd", "")] + [str(m.get("meas") or m.get("expr") or "")
                                                   for m in analysis.get("measures") or []]
                     + [str(x) for x in analysis.get("control") or []])
    text = spice.subst_nets(text, nm, nets)
    return {n.lower() for m in _VREF.finditer(text) for n in m.groups() if n}


def _tol(design: Design) -> tuple[float, float]:
    ft = (design.raw.get("sim") or {}).get("fallback_tol") or {}
    return float(ft.get("v", 0.05)), float(ft.get("rel", 0.05))


def compare_op(design: Design, got: dict, ref: dict, gate: set[str], names: dict) -> dict:
    """Every top-level net of `got` against `ref`: the largest move, the nets beyond tolerance, and the gate nets
    among them (a fallback with one is rejected)."""
    tv, tr = _tol(design)
    rows = []
    for n, vr in ref["volts"].items():
        if "." in n or "(" in n:
            continue
        vg = got["volts"].get(n)
        if vg is None:
            rows.append((math.inf, n, vr, None))
        else:
            rows.append((abs(vg - vr), n, vr, vg))
    rows.sort(key=lambda r: -r[0])
    flagged = [r for r in rows if r[0] > max(tv, tr * abs(r[2]))]
    bad = [r for r in flagged if r[1] in gate]
    fmt = lambda r: {"net": names.get(r[1], r[1]), "ref_v": r[2], "v": r[3]}  # noqa: E731
    return {"ref_temp": ref["temp"], "max_dev_v": rows[0][0] if rows else 0.0,
            "max_dev_net": names.get(rows[0][1], rows[0][1]) if rows else None,
            "tol": f"max({tv:g} V, {tr * 100:g} %)", "n_nets": len(rows), "n_flagged": len(flagged),
            "flagged": [fmt(r) for r in flagged[:10]], "rejected_on": [fmt(r) for r in bad[:10]], "ok": not bad}


def _is_uic_tran(analysis: dict) -> bool:
    _, rest = spice.split_cmd(analysis.get("cmd", "op").splitlines())
    return bool(rest) and rest[0].split()[0].lower() == "tran" and re.search(r"\buic\b", rest[0], re.I) is not None


def _op_candidates(design, analysis, nm, temp, workdir, cache, tag):
    """Yield (rung, mod, probe, check) for each way to a clean and plausible OP at `temp`, cheapest first:
    no change, .nodeset from the reference OP, gmin + source stepping, both. `check` is the plausibility record."""
    names = net_names(design, nm)
    gate = gate_nodes(design, analysis, nm)
    ref = reference_op(design, analysis, nm, temp, workdir, cache, tag)
    rungs = [("none", None)]
    if ref is not None:
        rungs.append(("nodeset", spice.NetMod(lines=nodeset_lines(ref))))
    rungs.append(("gmin+source stepping", spice.NetMod(pre=STEP_PRE)))
    if ref is not None:
        rungs.append(("nodeset+stepping", spice.NetMod(lines=nodeset_lines(ref), pre=STEP_PRE)))
    for i, (rung, mod) in enumerate(rungs):
        p = op_probe(design, analysis, nm, temp, workdir, cache, mod, tag=f"{tag}_fb{i}")
        if not p["clean"]:
            yield rung, mod, p, {"ok": False, "why": "OP did not converge cleanly" + (
                f" ({p['fails'][0]})" if p["fails"] else "")}
            continue
        if ref is None:
            yield rung, mod, p, {"ok": False, "why": "no temperature converges without fallback: "
                                 "no reference OP to check against"}
            continue
        chk = compare_op(design, p, ref, gate, names)
        if not chk["ok"]:
            chk["why"] = "OP not plausible: " + ", ".join(
                f"{r['net']} {_v(r['v'])} vs {_v(r['ref_v'])} V" for r in chk["rejected_on"][:3])
        yield rung, mod, p, chk


def _v(x):
    return "n/a" if x is None else f"{x:.4g}"


def find_op(design, analysis, nm, temp, workdir, cache, tag=""):
    """(rung, mod, probe, check, tried) of the first clean and plausible OP at `temp`; rung None when none."""
    tried = []
    for rung, mod, p, chk in _op_candidates(design, analysis, nm, temp, workdir, cache, tag):
        if chk["ok"]:
            return rung, mod, p, chk, tried
        tried.append({"rung": rung, "why": chk.get("why")})
    return None, None, None, None, tried


def recover(design: Design, analysis: dict, workdir: Path, nm, temp, tag: str, cache, res: dict) -> dict:
    """Convergence fallback for one run (see the module docstring). Returns `res` unchanged when the run's OP is not
    in doubt; otherwise the fallback result with `fallback` = {trigger, used, op_check, tried}, or `res` with its
    measures failed when no fallback gives a clean and plausible OP."""
    conv = res.get("conv") or []
    fails = [c for c in conv if OP_FAIL_RE.search(c)]
    if res["status"] == "ok":
        trouble = bool(fails)  # a singular matrix that gmin stepping recovered is not in doubt
    elif res["status"] in ("aborted", "error"):
        trouble = bool(fails) or any("singular matrix" in c.lower() for c in conv)
    else:
        trouble = False
    if not trouble or _is_uic_tran(analysis):
        return res
    t0 = time.time()
    trigger = (fails or conv)[:3]
    tried = []
    for i, (rung, mod, p, chk) in enumerate(_op_candidates(design, analysis, nm, temp, workdir, cache, tag)):
        if not chk["ok"]:
            tried.append({"rung": rung, "why": chk.get("why")})
            continue
        if rung == "none":
            if res["status"] == "ok":  # the run's own OP is clean (the messages came later) or plausible
                res["fallback"] = {"trigger": trigger, "used": "none", "op_check": _slim(chk),
                                   "note": "the run's own OP is clean and plausible: the messages came later"}
                return res
            tried.append({"rung": rung, "why": f"OP fine but the run {res['status']}: {res.get('reason', '')[:120]}"})
            continue
        r2 = spice.run_once(design, analysis, workdir, nm, None, temp, f"{tag}_fb{i}", cache, mod)
        bad2 = [c for c in r2.get("conv") or [] if OP_FAIL_RE.search(c)]
        if r2["status"] == "ok" and not bad2:
            r2["fallback"] = {"trigger": trigger, "used": rung, "op_check": _slim(chk), "tried": tried,
                              "seconds": round(time.time() - t0, 1)}
            r2["_mod"] = mod
            return r2
        tried.append({"rung": rung, "why": f"run {r2['status']}" + (f": {r2.get('reason', '')[:120]}"
                                                                    if r2.get("reason") else "")})
    res["fallback"] = {"trigger": trigger, "used": None, "tried": tried, "seconds": round(time.time() - t0, 1)}
    why = "; ".join(f"{t['rung']}: {t['why']}" for t in tried[:4])
    if res["status"] == "ok":
        res["status"] = "op-failed"
        res["reason"] = f"DC operating point not trustworthy ({trigger[0] if trigger else '?'}); fallbacks: {why}"
        for r in res["rows"]:
            r["raw_value"], r["value"], r["pass"] = r["value"], None, False
            r["na_reason"] = res["reason"]
    else:
        res["reason"] = (res.get("reason") or "") + f"; fallbacks: {why}"
        for r in res["rows"]:
            if r["value"] is None:
                r["na_reason"] = res["reason"]
    res["check_nets"] = _check_nets(design, nm, res, workdir)
    return res


def _slim(chk: dict) -> dict:
    return {k: chk[k] for k in ("ref_temp", "max_dev_v", "max_dev_net", "tol", "n_nets", "n_flagged", "flagged",
                                "rejected_on") if k in chk}


def _check_nets(design, nm, res, workdir) -> list[str]:
    names = net_names(design, nm)
    out = []
    for c in res.get("conv") or []:
        for n in check_nodes(c):
            nn = names.get(n, n)
            if nn not in out:
                out.append(nn)
    return out[:8]


# ----------------------------------------------------------------------------------------------- scope

def scope_cfg(analysis: dict) -> dict:
    sc = analysis.get("scope") or {}
    if isinstance(sc, list):
        sc = {"nets": sc}
    return {"nets": [str(n) for n in sc.get("nets") or []], "boundary": [str(n) for n in sc.get("boundary") or []],
            "tol": float(sc.get("tol", 0.02)), "atol": float(sc.get("atol", 0.0)), "band": sc.get("band"),
            "timeout_s": float(sc.get("timeout_s", 120))}


def _block_nodes(b: spice.Block, subs, memo) -> set[str]:
    if b.nodes:
        return set(b.nodes)
    out = set()
    for ln in b.lines:
        _, ns, _, _ = element_dc(ln, subs, memo)
        out |= set(ns)
    return out


def extract_scope(design: Design, analysis: dict, nm=None) -> dict:
    """The elements reachable from the measured nets without crossing a boundary net or ground, and the boundary
    nodes reached. Seeds: `scope.nets`, the nets the analysis measures and the elements it names (the noise input
    source, `i(X)` probes)."""
    nm = nm or spice.NodeMap(design)
    sc = scope_cfg(analysis)
    blocks, _, libs = spice.netlist_blocks(design, analysis, nm)
    sim = design.raw.get("sim") or {}
    subs = parse_subckts([str(spice.MODELS)] + sorted(libs) + [str((design.dir / i).resolve())
                                                                for i in sim.get("includes") or []])
    memo: dict = {}
    nodes_of = {b.eid: _block_nodes(b, subs, memo) for b in blocks}
    by_node: dict[str, list[str]] = {}
    for eid, ns in nodes_of.items():
        for n in ns:
            by_node.setdefault(n, []).append(eid)
    boundary = {nm.net(b) for b in sc["boundary"]}
    seeds = {nm.net(n) for n in sc["nets"]} | measured_nodes(design, analysis, nm)
    text = spice.subst_nets("\n".join([analysis.get("cmd", "")] + [str(m.get("meas") or m.get("expr") or "")
                                                                 for m in analysis.get("measures") or []]),
                        nm, set(design.nets()))
    named = {m.lower() for m in re.findall(r"\bi\(\s*([^\s()]+)\s*\)", text, re.I)}
    named |= {m.lower() for m in re.findall(r"(\S+)#branch", text, re.I)}
    for ln in text.splitlines():
        t = ln.split()
        if t and t[0].lower() == "noise" and len(t) >= 3:
            named.add(t[2].lower())
    eids = {ln.split()[0].lower(): b.eid for b in blocks for ln in b.lines if ln.split()}
    keep: set[str] = set()
    for nm_ in named:
        e = eids.get(nm_)
        if e:
            keep.add(e)
            seeds |= nodes_of[e]
    seen: set[str] = set()
    stack = [n for n in seeds if n != "0"]
    reached_b: set[str] = set()
    while stack:
        n = stack.pop()
        if n in seen or n == "0":
            continue
        seen.add(n)
        if n in boundary:
            reached_b.add(n)
            continue
        for e in by_node.get(n, []):
            if e in keep:
                continue
            keep.add(e)
            stack += [x for x in nodes_of[e] if x not in seen]
    knodes: set[str] = set()
    for e in keep:
        reached_b |= nodes_of[e] & boundary
        knodes |= nodes_of[e]
    xnames = {ln.split()[0].lower() for b in blocks if b.eid in keep for ln in b.lines
              if ln.split() and ln[0] in "Xx"}
    return {"keep": keep, "nodes": knodes, "xnames": xnames, "boundary": sorted(reached_b), "unreached": sorted(boundary - reached_b),
            "kept": len([e for e in keep if e in design.components]),
            "total": len([b for b in blocks if b.eid in design.components]), "seeds": sorted(seeds)}


def _band(cmd: str):
    for ln in cmd.splitlines():
        t = ln.split()
        if not t:
            continue
        k = t[0].lower()
        try:
            if k == "ac" and len(t) >= 5:
                return float(parse_value(t[3])), float(parse_value(t[4]))
            if k == "noise" and len(t) >= 7:
                return float(parse_value(t[5])), float(parse_value(t[6]))
        except ValueError:
            return None
    return None


def analysis_band(analysis: dict) -> tuple[float, float]:
    sc = scope_cfg(analysis)
    if sc["band"]:
        return float(parse_value(sc["band"][0])), float(parse_value(sc["band"][1]))
    b = _band(analysis.get("cmd", ""))
    if b:
        return b
    _, rest = spice.split_cmd(analysis.get("cmd", "op").splitlines())
    kind = rest[0].split()[0].lower() if rest else "op"
    return (0.01, 1.0) if kind in ("op", "dc") else (10.0, 1e6)


def _zid(n: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", n)


def _parse_table(out: str, tag: str):
    m = re.search(rf"^HWFZ_{tag}_BEGIN\s*$(.*?)^HWFZ_{tag}_END\s*$", out, re.M | re.S)
    f, z = [], []
    for ln in (m.group(1) if m else "").splitlines():
        p = ln.split()
        if len(p) == 4 and p[0].isdigit():
            try:
                f.append(float(p[1]))
                z.append(complex(float(p[2]), float(p[3])))
            except ValueError:
                pass
    return f, z


def _z_control(nodes: list[str], band) -> list[str]:
    """AC injection of 1 A into each node in turn; the response without injection is subtracted, so other AC
    sources of the netlist do not bias the impedance."""
    f1, f2 = band
    ac = f"ac dec 10 {f1:g} {f2:g}"
    c = [ac]
    for n in nodes:
        k = _zid(n)
        c += [f"let hwfzr_{k} = real(v({n}))", f"let hwfzi_{k} = imag(v({n}))", f"echo HWFZ_B{k}_BEGIN",
              f"print hwfzr_{k} hwfzi_{k}", f"echo HWFZ_B{k}_END"]
    for n in nodes:
        k = _zid(n)
        c += [f"alter ihwfz_{k} ac=1", ac, f"let hwfzr_{k} = real(v({n}))", f"let hwfzi_{k} = imag(v({n}))",
              f"echo HWFZ_I{k}_BEGIN", f"print hwfzr_{k} hwfzi_{k}", f"echo HWFZ_I{k}_END", f"alter ihwfz_{k} ac=0"]
    return c


def measure_z(design, analysis, nm, temp, workdir, cache, nodes, band, mod: spice.NetMod, tag) -> dict:
    """node -> (freqs, complex Z) of the netlist given by `mod`."""
    inj = [f"Ihwfz_{_zid(n)} 0 {n} DC 0 AC 0" for n in nodes]
    m = spice.NetMod(keep=mod.keep, lines=mod.lines + inj, pre=mod.pre, drop_alters=mod.drop_alters,
                     control=_z_control(nodes, band), title="zout probe")
    net, _ = spice.build_netlist(design, analysis, nm, temp=temp, mod=m)
    out, rc, hit, to = _run(net, Path(workdir), f"{analysis['name']}{tag}_z", cache,
                            float(analysis.get("timeout_s", spice.DEFAULT_TIMEOUT_S)))
    res = {}
    for n in nodes:
        k = _zid(n)
        f, zb = _parse_table(out, f"B{k}")
        f2, zi = _parse_table(out, f"I{k}")
        if f and len(zi) == len(zb):
            res[n] = (f2, [a - b for a, b in zip(zi, zb)])
    return res


def fit_z(f: list[float], z: list[complex]) -> dict:
    """Fit Z(f) with R, R+L, R||C or (R+L)||C; the simplest model within 1.25x of the best max relative error
    (or under 2 %) wins. Returns {model, R, L, C, err}."""
    import numpy as np
    w = 2 * np.pi * np.asarray(f, float)
    Z = np.asarray(z, complex)
    ok = np.isfinite(Z) & (np.abs(Z) > 0)
    if len(Z) and not np.any(np.isfinite(Z) & (np.abs(Z) > 1e-9)):  # an ideal source on the full board
        return {"model": "R", "R": 1e-6, "L": 0.0, "C": 0.0, "err": 0.0, "ideal": True}
    w, Z = w[ok], Z[ok]
    if len(Z) == 0:
        return {"model": "R", "R": 1e-3, "L": 0.0, "C": 0.0, "err": math.inf}
    wt = 1 / np.abs(Z)

    def err(zf):
        return float(np.max(np.abs(zf - Z) / np.abs(Z)))

    def rl(Zt, wt_):
        A = np.vstack([np.concatenate([wt_, 0 * wt_]), np.concatenate([0 * wt_, w * wt_])]).T
        y = np.concatenate([Zt.real * wt_, Zt.imag * wt_])
        (r, l), *_ = np.linalg.lstsq(A, y, rcond=None)
        return max(r, 1e-6), max(l, 0.0)

    cands = []
    r = float(max(np.sum(Z.real * wt ** 2) / np.sum(wt ** 2), 1e-6))
    cands.append({"model": "R", "R": r, "L": 0.0, "C": 0.0, "err": err(np.full_like(Z, r))})
    r, l_ = rl(Z, wt)
    cands.append({"model": "R+L", "R": r, "L": l_, "C": 0.0, "err": err(r + 1j * w * l_)})
    Y = 1 / Z
    wy = 1 / np.abs(Y)
    A = np.vstack([np.concatenate([wy, 0 * wy]), np.concatenate([0 * wy, w * wy])]).T
    (g, c), *_ = np.linalg.lstsq(A, np.concatenate([Y.real * wy, Y.imag * wy]), rcond=None)
    g, c = max(g, 1e-12), max(c, 0.0)
    cands.append({"model": "R||C", "R": 1 / g, "L": 0.0, "C": c, "err": err(1 / (g + 1j * w * c))})
    best4 = None
    cmax = max(c, 1e-15) * 100 if c > 0 else 1.0
    for cc in np.logspace(math.log10(cmax) - 12, math.log10(cmax), 61):
        Zp = 1 / (Y - 1j * w * cc)
        if not np.all(np.isfinite(Zp)):
            continue
        r4, l4 = rl(Zp, 1 / np.abs(Zp))
        zf = 1 / (1 / (r4 + 1j * w * l4) + 1j * w * cc)
        e = err(zf)
        if best4 is None or e < best4["err"]:
            best4 = {"model": "(R+L)||C", "R": r4, "L": l4, "C": float(cc), "err": e}
    if best4:
        cands.append(best4)
    best = min(x["err"] for x in cands)
    for x in cands:  # simplest first
        if x["err"] <= max(1.25 * best, 0.02) or x["err"] == best:
            return x
    return cands[-1]


def z_eval(fit: dict, f: list[float]) -> list[complex]:
    out = []
    for fr in f:
        s = 2j * math.pi * fr
        zs = fit["R"] + s * fit["L"]
        out.append(1 / (1 / zs + s * fit["C"]) if fit["C"] > 0 else zs)
    return out


def thevenin_lines(node: str, vth: float, fit: dict | None) -> list[str]:
    k = _zid(node)
    if fit is None:  # ideal source (the DC current step)
        return [f"Vhwfth_{k} {node} 0 DC {vth:.9g}"]
    lines = [f"Vhwfth_{k} hwfth_{k}_s 0 DC {vth:.9g}"]
    if fit["L"] > 0:
        lines += [f"Rhwfth_{k} hwfth_{k}_s hwfth_{k}_m {fit['R']:.6g} noisy=0", f"Lhwfth_{k} hwfth_{k}_m {node} {fit['L']:.6g}"]
    else:
        lines.append(f"Rhwfth_{k} hwfth_{k}_s {node} {fit['R']:.6g} noisy=0")
    if fit["C"] > 0:
        lines.append(f"Chwfth_{k} {node} 0 {fit['C']:.6g}")
    return lines


def _measures_match(full_rows, sc_rows, tol, atol):
    cmp = []
    for fr in full_rows:
        sr = next((r for r in sc_rows if r["name"] == fr["name"]), None)
        fv, sv = fr.get("value"), sr.get("value") if sr else None
        if fv is None or sv is None:
            ok, d, rel = False, None, None
        else:
            d = sv - fv
            rel = abs(d) / abs(fv) if fv else (0.0 if d == 0 else math.inf)
            ok = abs(d) <= max(tol * abs(fv), atol)
        cmp.append({"name": fr["name"], "full": fv, "scoped": sv, "delta": d, "rel": rel, "ok": ok,
                    "debug": bool(fr.get("debug"))})
    return cmp


def _scoped_one(design: Design, analysis: dict, workdir: Path, temp, tag: str, cache) -> dict:
    """Full run, full OP, boundary impedances and scoped run at one temperature (no gate yet)."""
    t0 = time.time()
    full_a = {k: v for k, v in analysis.items() if k != "scope"}
    nm = spice.NodeMap(design)
    full = spice.run_once(design, full_a, workdir, nm, None, temp, f"{tag}_full", cache)
    cached = bool(full.get("cached"))
    if (design.raw.get("sim") or {}).get("fallback", True) is not False:
        full = recover(design, full_a, workdir, nm, temp, f"{tag}_full", cache, full)
    fmod = full.pop("_mod", None)
    full_ok = full["status"] == "ok" and (full.get("fallback") or {}).get("used", "none") is not None
    info = {"temp": _sim_temp(design, temp), "full_status": full["status"], "full_ok": full_ok,
            "full_seconds": round(time.time() - t0, 1),
            "full_cached": cached and bool(full.get("cached"))}
    if full.get("fallback"):
        info["full_fallback"] = full["fallback"].get("used")
    sc = extract_scope(design, analysis, nm)
    info.update({k: sc[k] for k in ("kept", "total", "boundary", "unreached")})
    names = net_names(design, nm)
    # full-board OP at this temperature (the fallback that the full run needed, else the ladder)
    op = op_probe(design, full_a, nm, temp, workdir, cache, fmod, tag=f"{tag}_full")
    if not op["clean"]:
        rung, fmod2, op2, chk, tried = find_op(design, full_a, nm, temp, workdir, cache, f"{tag}_full")
        if rung is None:
            info["error"] = "full-board OP does not converge at this temperature: boundary DC unknown"
            return {"full": full, "scoped": None, "info": info}
        fmod, op = fmod2, op2
        info["op_fallback"] = rung
    pre = list(fmod.pre) if fmod else []
    bnodes = sc["boundary"]
    missing = [n for n in bnodes if n not in op["volts"]]
    if missing:
        info["error"] = f"boundary not in the full-board OP: {', '.join(names.get(n, n) for n in missing)}"
        return {"full": full, "scoped": None, "info": info}
    keep = sc["keep"]
    sa = dict(full_a, timeout_s=min(float(full_a.get("timeout_s", spice.DEFAULT_TIMEOUT_S)),
                                    scope_cfg(analysis)["timeout_s"]))  # a scoped run is small by design
    # the full-board OP as the starting point of every scoped OP (a .nodeset is an initial guess, not a constraint)
    seed = {"temp": op["temp"], "volts": {n: v for n, v in op["volts"].items()
                                          if n in sc["nodes"] or n.split(".")[0] in sc["xnames"]}}
    nset = nodeset_lines(seed)
    # step 1: the block's DC current on each boundary, with each boundary held at its full-board OP voltage
    ideal = [ln for n in bnodes for ln in thevenin_lines(n, op["volts"][n], None)]
    p1 = op_probe(design, sa, nm, temp, workdir, cache,
                  spice.NetMod(keep=keep, lines=ideal + nset, pre=pre, drop_alters=True), tag=f"{tag}_sc1")
    ib = {n: -p1["currents"].get(f"vhwfth_{_zid(n)}#branch".lower(), 0.0) for n in bnodes}
    # step 2: the full board's small-signal impedance at each boundary, de-embedded from the block's own load
    band = analysis_band(analysis)
    zfull = measure_z(design, full_a, nm, temp, workdir, cache, bnodes, band,
                      fmod or spice.NetMod(), f"{tag}_full") if bnodes else {}
    fits, bd = {}, {}
    for n in bnodes:
        if n not in zfull:
            info["error"] = f"no impedance from the full board at {names.get(n, n)}"
            return {"full": full, "scoped": None, "info": info}
        fits[n] = fit_z(*zfull[n])

    def th_lines(fs):
        return [ln for n in bnodes for ln in thevenin_lines(n, op["volts"][n] + ib[n] * fs[n]["R"], fs[n])]

    if bnodes:
        zsc = measure_z(design, sa, nm, temp, workdir, cache, bnodes, band,
                        spice.NetMod(keep=keep, lines=th_lines(fits) + nset, pre=pre, drop_alters=True),
                        f"{tag}_sc2")
        for n in bnodes:
            f, zf = zfull[n]
            zth = z_eval(fits[n], f)
            rest = None
            if n in zsc and len(zsc[n][1]) == len(zf):
                try:
                    yb = [1 / zm - 1 / zt for zm, zt in zip(zsc[n][1], zth)]
                    rest = [1 / (1 / a - b) for a, b in zip(zf, yb)]
                except ZeroDivisionError:
                    rest = None
            if rest and all(math.isfinite(abs(x)) and x.real > 0 for x in rest):
                fr = fit_z(f, rest)
                if fr["err"] <= fits[n]["err"] * 2 + 0.05:
                    fits[n] = dict(fr, deembedded=True)
            fz = fits[n]
            bd[names.get(n, n)] = {"v_op": op["volts"][n], "i_block_a": ib[n], "model": fz["model"], "R": fz["R"],
                                   "L": fz["L"], "C": fz["C"], "fit_err": fz["err"],
                                   "deembedded": bool(fz.get("deembedded")),
                                   "z_abs_range": [min(abs(x) for x in zf), max(abs(x) for x in zf)],
                                   "band_hz": list(band)}
    info["boundary_model"] = bd
    smod = spice.NetMod(keep=keep, lines=(th_lines(fits) if bnodes else []) + nset, pre=pre, drop_alters=True)
    t1 = time.time()
    scoped = spice.run_once(design, sa, workdir, nm, None, temp, f"{tag}_scoped", cache, smod)
    info["scoped_seconds"] = round(time.time() - t1, 1)
    info["scoped_cached"] = bool(scoped.get("cached"))
    p2 = op_probe(design, sa, nm, temp, workdir, cache, smod, tag=f"{tag}_sc3")
    info["boundary_v_scoped"] = {names.get(n, n): p2["volts"].get(n) for n in bnodes}
    info["prep_seconds"] = round(t1 - t0 - info["full_seconds"], 1)  # boundary OP, impedances, DC currents
    return {"full": full, "scoped": scoped, "info": info}


def run_scoped(design: Design, analysis: dict, workdir: Path, temps: list, tag: str = "", cache=None,
               workers: int = 1) -> list[dict]:
    """A scoped analysis at each temperature with its validation gate. Validated: the scoped result at every
    temperature, with the comparison recorded under `scope`. Not validated: the full-board result where the full board
    converged, and `n/a: scope not validated (...)` on every measure elsewhere."""
    sc = scope_cfg(analysis)

    def one(t):
        return _scoped_one(design, analysis, workdir, t, tag + (f"_T{t}" if t is not None else ""), cache)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        parts = list(ex.map(one, temps))
    checks = []
    for p in parts:
        if p["info"]["full_ok"] and p["scoped"] is not None:
            cmp = _measures_match(p["full"]["rows"], p["scoped"]["rows"], sc["tol"], sc["atol"])
            p["info"]["compare"] = cmp
            checks.append((p["info"]["temp"], all(c["ok"] for c in cmp if not c["debug"])))
    vt = [t for t, ok in checks if ok]
    if not checks:
        why = "the full board converges at no temperature of the analysis" if not any(
            p["info"]["full_ok"] for p in parts) else "no scoped run finished where the full board converges"
    elif len(vt) < len(checks):
        bad = [(p["info"]["temp"], c) for p in parts for c in p["info"].get("compare") or []
               if not c["ok"] and not c["debug"]]
        t, c = bad[0]
        why = (f"{c['name']} @ {t}C scoped {_v(c['scoped'])} vs full {_v(c['full'])}, beyond "
               f"max({sc['tol'] * 100:g} %, {sc['atol']:g})")
    else:
        why = ""
    validated = bool(checks) and not why
    out = []
    for p in parts:
        info = dict(p["info"], validated=validated, validated_temps=vt, tol=sc["tol"], atol=sc["atol"])
        if validated and p["scoped"] is not None:
            r = p["scoped"]
            if r["status"] != "ok":
                info["error"] = f"scoped run {r['status']}: {r.get('reason', '')}"
        elif p["info"]["full_ok"]:
            r = p["full"]
            info["note"] = f"scope rejected ({why or info.get('error')}): full-board result"
        else:
            r = dict(p["scoped"] or p["full"])
            reason = f"scope not validated ({why or info.get('error') or 'no scoped run'})"
            if validated:  # validated elsewhere, but no scoped run here
                reason = f"scoped run unavailable at this temperature ({info.get('error')})"
            r["rows"] = [dict(x, value=None, na_reason=reason, **{"pass": False}) for x in r["rows"]]
            r["status"], r["reason"] = "scope-rejected", reason
        if not validated:
            info["reason"] = why or info.get("error")
        r = dict(r, scope=info)
        r.pop("_mod", None)
        out.append(r)
    return out


# ----------------------------------------------------------------------------------------------- status and report

def _base(name: str) -> str:
    return name.split(" @ ")[0]


def sim_summary(design: Design, res: list[dict], workdir: Path, cache) -> dict:
    """The fallback, scope, leak and convergence-hint records of a sim stage for build/status.json."""
    fb, scopes, hints = [], [], []
    for r in res:
        f = r.get("fallback")
        if f:
            ch = f.get("op_check") or {}
            fb.append({"analysis": r["name"], "used": f.get("used"), "trigger": (f.get("trigger") or [""])[0],
                       "ref_temp": ch.get("ref_temp"), "max_dev_v": ch.get("max_dev_v"),
                       "max_dev_net": ch.get("max_dev_net"), "tol": ch.get("tol"),
                       "flagged": [x["net"] for x in ch.get("flagged") or []], "n_flagged": ch.get("n_flagged"),
                       "tried": f.get("tried") or [], "note": f.get("note")})
        s = r.get("scope")
        if s:
            scopes.append({"analysis": r["name"], **{k: s.get(k) for k in (
                "temp", "validated", "validated_temps", "kept", "total", "boundary", "unreached", "boundary_model",
                "compare", "reason", "note", "error", "tol", "atol", "full_status", "full_fallback", "full_seconds",
                "full_cached", "scoped_seconds", "scoped_cached", "prep_seconds", "boundary_v_scoped")}})
        if r.get("status") not in ("ok",) and (r.get("conv") or r.get("check_nets")):
            nets = r.get("check_nets") or _check_nets(design, None, r, workdir)
            if nets or any(OP_FAIL_RE.search(c) for c in r.get("conv") or []):
                hints.append({"analysis": r["name"], "nets": nets})
    try:
        leaks = leak_report(design, workdir, cache)
    except Exception as e:  # the leak report never blocks the sim stage
        leaks = [{"error": str(e)[:200]}]
    return {"fallbacks": fb, "scopes": scopes, "leaks": leaks, "convergence_hints": hints}


def _pct(x):
    return "n/a" if x is None else f"{x * 100:.2f} %"


def report_lines(c: dict) -> list[str]:
    """REPORT.md lines of the sim stage for fallbacks, scopes, leaks and convergence hints."""
    L = []
    by: dict[str, list[dict]] = {}
    for f in c.get("fallbacks") or []:
        by.setdefault(_base(f["analysis"]), []).append(f)
    for name, fs in by.items():
        used = [f for f in fs if f["used"] not in (None, "none")]
        failed = [f for f in fs if f["used"] is None]
        if used:
            f = used[0]
            temps = ", ".join(x["analysis"].split(" @ ")[1] for x in used if " @ " in x["analysis"])
            fl = (f"; beyond tolerance but not a supply or measured net: {', '.join(f['flagged'][:4])}"
                  if f["flagged"] else "")
            L.append(f"  - convergence fallback {name}{f' @ {temps}' if temps else ''}: {f['used']} after "
                     f"`{f['trigger'][:70]}`; its OP against the {f['ref_temp']:g} C OP on every net: max move "
                     f"{f['max_dev_v']:.3g} V ({f['max_dev_net']}), tolerance {f['tol']}{fl}")
        for f in failed:
            why = "; ".join(f"{t['rung']}: {t['why']}" for t in f["tried"][:3])
            L.append(f"  - convergence fallback {f['analysis']}: no clean and plausible OP ({why[:300]})")
    sby: dict[str, list[dict]] = {}
    for s in c.get("scopes") or []:
        sby.setdefault(_base(s["analysis"]), []).append(s)
    for name, ss in sby.items():
        s0 = ss[0]
        bm = s0.get("boundary_model") or {}
        bnd = ", ".join(f"{n} ({b['model']}, {b['v_op']:.4g} V, fit {_pct(b['fit_err'])})" for n, b in bm.items()) \
            or ", ".join(s0.get("boundary") or []) or "none"
        tf = sum(s.get("full_seconds") or 0 for s in ss)
        tsc = sum((s.get("scoped_seconds") or 0) for s in ss)
        if s0.get("validated"):
            vt = ", ".join(f"{t:g}" for t in s0.get("validated_temps") or [])
            L.append(f"  - scope {name}: VALIDATED at {vt} C (every measure within max({s0['tol'] * 100:g} %, "
                     f"{s0['atol']:g}) of the full board); {s0['kept']}/{s0['total']} parts; boundaries {bnd}; "
                     f"run time full {tf:.1f} s{' (cached)' if all(s.get('full_cached') for s in ss) else ''}, "
                     f"scoped {tsc:.1f} s")
        else:
            L.append(f"  - scope {name}: REJECTED ({s0.get('reason')}); {s0['kept']}/{s0['total']} parts; "
                     f"boundaries {bnd}")
        for s in ss:
            t = s.get("temp")
            for x in s.get("compare") or []:
                L.append(f"    - {x['name']} @ {t:g}C: full {_fmtn(x['full'])} vs scoped {_fmtn(x['scoped'])} "
                         f"(delta {_pct(x['rel'])}){' FAIL' if not x['ok'] and not x['debug'] else ''}")
            if not s.get("compare"):
                L.append(f"    - @ {t:g}C: full board {s.get('full_status')}; "
                         + (s.get("error") or "scoped result used, validated at the other temperatures"))
    lk = [x for x in c.get("leaks") or [] if "node" in x]
    if lk:
        L.append(f"  - leaks: {len(lk)} nodes with no DC path to ground got a noiseless {lk[0]['ohm']:g} ohm to ground "
                 f"(fix the circuit; `hwflow check` lists them): " + ", ".join(
                     f"{x['net']} {_fmtn(x['v'])} V, {_fmtn(x['i_a'])} A, corner {_fmtn(x['f_corner_hz'])} Hz"
                     for x in lk[:6]) + (f" and {len(lk) - 6} more" if len(lk) > 6 else ""))
        for x in lk:
            if x.get("warn"):
                L.append(f"    - leak at {x['net']} not negligible: {x['warn']}")
    hs = c.get("convergence_hints") or []
    if hs:
        nets = list(dict.fromkeys(n for h in hs for n in h["nets"]))
        L.append(f"  - hint: the full-board DC operating point does not converge in "
                 f"{', '.join(_base(h['analysis']) for h in hs[:4])}{' and more' if len(hs) > 4 else ''}"
                 + (f" (ngspice names {', '.join(nets[:6])})" if nets else "")
                 + ". If a measure depends on one block only, add `scope: {nets: [...], boundary: [<supply nets>]}` "
                   "to the analysis: it simulates that block with Thevenin sources on the boundary and is validated "
                   "against the full board (docs/design-yaml.md, sim, scope)")
    return L


def _fmtn(x):
    return "n/a" if x is None else f"{x:.4g}"
