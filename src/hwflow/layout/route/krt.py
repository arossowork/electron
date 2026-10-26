"""The KiCadRoutingTools and Freerouting drivers: one router pass and its retries, the narrow-first (necked) routing,
the Specctra session import, the router version and its environment. The board file, its rules and its reader are
`layout.board.pcb`; what hwflow adds around each pass is `layout.board.routing`; the repair loop is `repair`."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ...core import sexpr
from ...core.design import Design
from ...core.geometry import Part
from ...core.settings import get_settings
from ..board import pcb

def run_freerouting(dsn: Path, ses: Path, passes: int = 30, timeout: int = 900) -> str:
    jar = freerouting_jar()
    if not Path(jar).exists():
        raise FileNotFoundError(f"freerouting jar not found at {jar}; set FREEROUTING")
    cmd = [get_settings().tools.java, "-Djava.awt.headless=true", "-jar", jar, "--gui.enabled=false",
           "-de", str(dsn.resolve()), "-do", str(ses.resolve()), "--router.autorouter.max_passes", str(passes)]
    ses.unlink(missing_ok=True)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=dsn.parent, stdin=subprocess.DEVNULL)
        log = p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        log = f"TIMEOUT after {timeout}s\n" + (e.stdout or b"").decode(errors="replace")
    (dsn.parent / "freerouting.log").write_text(log)
    if not ses.exists():
        raise RuntimeError("freerouting produced no session file:\n" + log[-2000:])
    return log


def krt_dir() -> Path:
    """KiCadRoutingTools checkout (settings.paths.krt_dir, KRT_DIR)."""
    return Path(get_settings().paths.krt_dir)


def freerouting_jar() -> str:
    return get_settings().paths.freerouting


def run_krt(design: Design, libs, parts: dict[str, Part], workdir: Path, timeout: int = 1800,
            deadline: float | None = None) -> tuple[list, list, str, int]:
    """Route with KiCadRoutingTools: one pass per track-width class (widest first), keeping earlier copper.

    Power-tap mode is not used, because it places vias inside same-net SMD pads.
    Returns (tracks, vias, summary, unrouted) like the Freerouting path.
    """
    workdir = Path(workdir).resolve()  # the router runs with cwd=KRT_DIR, so relative paths would not be found
    name = design.name
    src = workdir / f"{name}_unrouted.kicad_pcb"
    pcb.write_board(src, pcb.build_board(design, libs, parts))
    (workdir / "krt.log").unlink(missing_ok=True)  # krt_route appends: repair rounds add to the same log
    from . import seedroute
    (workdir / seedroute.RECORD).unlink(missing_ok=True)  # the seed copper of this run (krt_route adds to it)
    pcb.write_project(workdir, name, design)  # the rules DRC will use: net classes (.kicad_pro) and .kicad_dru
    nets, keep = None, False
    if design.raw.get("blocks"):  # layout blocks: keep their copper from the first pass, skip the nets it closes
        from ...core import blocks
        closed = blocks.closed_nets(design, libs)
        nets = set(design.nets()) - closed if closed else None
        keep = any(blocks.copper(design, parts))
    cur, last, retried, ngroups = krt_route(design, src, workdir, f"{name}_krt", nets=nets, keep_first=keep,
                                            timeout=timeout, libs=libs, deadline=deadline, keep_partial=deadline is not None)
    design.route_budget_exhausted = any(v.get("budget_exhausted") for v in last.values())
    routed = pcb.read_board(cur)
    tracks = [dict(s) for s in routed["segments"] if s["net"]]
    vias = [dict(v) for v in routed["vias"] if v["net"]]
    unrouted = sum(krt_unrouted(v) for v in last.values())
    extra = f"; retried in another net order ({'; '.join(retried)})" if retried else ""
    return tracks, vias, f"{unrouted} unrouted, krt {ngroups} completed pass(es){extra}", unrouted


def krt_route(design: Design, src: Path, workdir: Path, stem: str, nets=None, keep_first: bool = False,
              variants: list | None = None, timeout: int = 1800, libs=None,
              deadline: float | None = None, keep_partial: bool = False,
              extra_args: list | None = None) -> tuple[Path, dict, list, int]:
    """The router invocation: one KiCadRoutingTools pass per track-width class (widest first) on the board `src`.
    `nets` limits the nets to route (default: every non-pour net), `keep_first` keeps the copper already in `src`
    in the first pass too (repair), `variants` replaces the net orders tried (KRT_VARIANTS). `deadline` (time.time())
    bounds every router call: a call past it raises subprocess.TimeoutExpired (the repair rejects that round).
    `extra_args` go to every router call (the truncated quick route of firstpass.py).
    `keep_partial` is for initial routing: a timeout keeps only completed, accepted candidates, marks remaining
    nets open and sets last["budget"]["budget_exhausted"]. It never reads a timed-out subprocess output board.
    The rules files `<name>.kicad_pro` and `.kicad_dru` in `workdir` are copied next to every pass input.

    With `board.isolation` groups, the member nets route first, and every pass input gets the isolation keep-outs
    of routing.isolation_keepouts. A net a pass wider than the board track width leaves open is routed again as a
    router power net (neck-down for at most 2 mm at a pad entry); then any net a pass leaves open (signal or power)
    is routed at the track width and at routing.neck_floor and widened where it fits (routing.widen), accepted when
    no narrow stretch is over NECK_MAX_MM. Nets the corridor proof connects only with a neck (corridor.neck_needed)
    route in a pass of their own ahead of the other nets of their isolation side, narrow first (no full-width pass).
    A constrained net (corridor.constrained) still open after the neck-down gets a seed route through the proof's
    free space (seedroute.seed, recorded in seedroute.RECORD; HWFLOW_SEEDROUTE=0 switches it off), so the later
    passes route around it.
    A net walled in by the others of its pass is routed first. HWFLOW_NECKDOWN=0 switches the neck-down off. Nets
    with `nets.<name>.layers` route in passes of their own, kept on those layers (layer_args).
    Returns (last board, {pass: summary}, [notes: other net orders, neck-downs], fully completed width passes).
    An interrupted width pass can contribute accepted copper without counting as fully completed."""
    from ...core import floorplan
    from ..board import keepout
    from ..board import routing
    b = design.board
    name = design.name
    src, workdir = Path(src).resolve(), Path(workdir).resolve()  # the router runs with cwd=KRT_DIR (a relative project path would not be found)
    from . import roads
    if roads.on_route(design):  # roads first: the planned copper of the constrained roads goes on the board first
        src, nets, pre = roads.preseed(design, libs, src, workdir, stem, nets)
        keep_first = keep_first or bool(pre)
    rule_files = [workdir / f"{name}.kicad_pro", workdir / f"{name}.kicad_dru"]
    classes = pcb.net_classes(design)
    gnds = design.ground_nets() if b["gnd_pour"] else set()
    iso = pcb.isolation_needs(design)
    members = {n for g in iso for n in g["members"]}
    copper = pcb.cu_layers(b["layers"])
    # nets.<name>.layers: those nets route in passes of their own, the other layers forbidden (router --layer-costs -1:
    # an obstacle that through vias may cross, with no track on it)
    only = {n: tuple(L) for n, L in pcb.net_layers(design).items() if L and set(L) != set(copper)}
    neck = float(b["track_mm"])
    floor = routing.neck_floor(b)
    neckdown = get_settings().layout.neckdown
    # nets the corridor proof connects only with a neck: a full-width pass cannot route them, so they route narrow
    # first (then widened), in a pass of their own ahead of the other nets of their isolation side
    minw = pcb.net_min_widths(design)  # nets.<name>.min_width_mm: no neck under it

    def neckable(pnets, nw):
        """The nets of `pnets` that may neck to width `nw`."""
        return [n for n in pnets if minw.get(n, 0.0) <= nw + 1e-9]
    necked = _neck_needed(design, src, libs) if neckdown else {}
    # pin access (layout flow, asicflow.necked): nets with a pad without an exit at their width route narrow first too
    from ..place import asicflow
    necked = {**asicflow.necked(design, libs, src, nets), **necked}
    necked = {n: v for n, v in necked.items() if minw.get(n, 0.0) <= floor + 1e-9}
    # the corridor proof's nets: one the router leaves open gets a seed route (HWFLOW_SEEDROUTE=0: off)
    seedable = set()
    if neckdown and get_settings().layout.seedroute:
        from ..place import corridor
        seedable = set(neckable(corridor.constrained(design), floor))
    groups: dict[tuple, list[str]] = {}
    for net, cl in classes.items():
        if net not in gnds and (nets is None or net in nets):
            groups.setdefault((net not in members, net not in necked, -cl["width"], net not in only,
                               only.get(net, ())), []).append(net)
    passes = [(-k[2], groups[k], k[4]) for k in sorted(groups)]  # isolation members first, then necked, then widest
    # subprocess environment: the router inherits the parent environment (KICAD_*/KRT_* knobs) on purpose
    env = dict(os.environ, KICAD_CLI=_kicad_cli())
    # Pre-existing copper from the earlier (wider) passes as rip candidates boxes in the pads of the narrow pass:
    # home-thermostat-3 0.25 mm pass 5 of 26 nets routed with it, 25 without. Set to 1 to restore the router default.
    env.setdefault("KICAD_RIP_PREEXISTING", "0")
    common = ["--clearance", str(b["clearance_mm"]), "--via-size", str(b["via_dia_mm"]),
              "--via-drill", str(b["via_drill_mm"]), "--same-net-pad-clearance", "0.15", "--fab-tier", "standard",
              "--escalation", "off", "--no-fix-drc-settings", "--strict-sizes", *(extra_args or [])]
    cur, log, last, retried = src, [], {}, []
    budget_exhausted = False
    completed = 0
    retries = int(b.get("router_retries", 3))
    route_py = krt_dir() / "py_router" / "route.py"
    if not route_py.exists():
        raise FileNotFoundError(f"KiCadRoutingTools not found at {krt_dir()}; run scripts/setup.sh or set KRT_DIR")

    def run(args):
        nonlocal budget_exhausted
        t = timeout
        try:
            if keep_partial and budget_exhausted:
                raise subprocess.TimeoutExpired(args, 0)
            if deadline is not None:
                t = min(t, deadline - time.time())
                if t <= 0:
                    raise subprocess.TimeoutExpired(args, 0)
            p = run_group(args, timeout=t, cwd=krt_dir(), env=env)
            return p.stdout + p.stderr, p.returncode
        except subprocess.TimeoutExpired as exc:
            if keep_partial:
                budget_exhausted = True
                for text in (exc.stdout, exc.stderr):
                    if text:
                        log.append(text.decode(errors="replace") if isinstance(text, bytes) else text)
                log.append("stopped at layout wall-time budget; keeping completed accepted copper")
            raise

    cond = floorplan.conductive_needs(design)

    def pass_input(cur, tag, pnets):
        """The pass input: `cur` with the isolation and conductive keep-outs of this pass, and the rules files beside
        it."""
        rules = []
        if iso or cond:
            zones, rules = routing.isolation_keepouts(iso, pcb.read_board(cur), copper, b["via_dia_mm"],
                                                      b["clearance_mm"], set(pnets)) if iso else ([], [])
            cz, cr = routing.conductive_keepouts(cond, copper, b["clearance_mm"], set(pnets))
            zones, rules = zones + cz, rules + cr
            cur = routing.pass_input(cur, workdir / f"{stem}{tag}_in.kicad_pcb", zones)
        for f in rule_files:  # the router reads net-class clearances and custom rules from the input's siblings
            dst = f.with_name(cur.stem + f.suffix)
            if rules and f.suffix == ".kicad_dru":
                text = f.read_text().rstrip("\n") if f.exists() else "(version 1)"
                dst.write_text(text + "\n" + "\n".join(rules) + "\n")
            elif not f.exists():
                dst.unlink(missing_ok=True)
            elif dst != f:
                shutil.copy(f, dst)
        return cur

    def run_pass0(cur, tag, pnets, args, i):
        base = [sys.executable, str(route_py), str(pass_input(cur, tag, pnets)), None, "--nets", *pnets, *args, *common]
        if i or keep_first:
            base.append("--keep-input-copper")
        from ..place import routability
        stop = routability.retry_stop(design, args, len(pnets)) if variants is None else None
        kw = {"stop": stop} if stop is not None else {}  # the retry policy only when it is on (HWFLOW_ROUTAB_RETRY)
        if keep_partial:
            kw["keep_partial"] = True
        best, tried, logs = route_class(base, workdir / f"{stem}{tag}", retries, run, variants, **kw)
        log.extend(logs)
        if best is None:
            with (workdir / "krt.log").open("a") as f:
                f.write("\n".join(log))
            raise RuntimeError(f"KiCadRoutingTools failed on pass {tag} ({' '.join(args)}); see krt.log")
        return best, tried

    def fit(path, fnets, w):
        """Fit the tracks of `fnets` in the pass result `path` to width `w` where clearances allow (routing.widen).
        Accepted when no narrow stretch is longer than NECK_MAX_MM. Returns (accepted, note text)."""
        ko = {}
        for a in keepout.rule_areas(sexpr.loads(path.read_text()), ("tracks",)):
            for L in routing.pad_layers(a["layers"], copper):
                ko.setdefault(L, []).append(a["poly"])
        outline = floorplan.outline(design).buffer(-pcb.EDGE_CLEARANCE_MM, join_style="mitre")
        stats = routing.widen(path, pcb.read_board(path), {n: w for n in fnets},
                              _pair_clearance(design, classes, iso), copper, b["via_dia_mm"], outline, ko, floor)
        # the widened width is the class width (never under the IPC width of its current), so a stretch under the
        # IPC width is part of a narrow stretch: a neck within NECK_MAX_MM keeps track-current a warning
        text = ", ".join(f"{n} {s[0]:.1f} of {s[1]:.1f} mm narrow, longest neck {s[2]:.1f} mm"
                         for n, s in sorted(stats.items()))
        return all(s[2] <= routing.NECK_MAX_MM for s in stats.values()), text

    def route_group(cur, tag, pnets, w, i, lays=()):
        """Route `pnets` at width `w`: as router power nets when wider than the track width, then the nets left open
        (any net, signal or power) at the track width and at the neck floor, widened back where they fit. Nets the
        corridor proof marks as needing a neck skip the full-width pass. `lays`: the only layers the nets may use
        (all when empty). Returns ((board, summary, unrouted), notes)."""
        wide = neckdown and w > neck + 1e-9
        widths = [x for x in dict.fromkeys((neck, floor)) if x < w - 1e-9] if neckdown else []
        largs = layer_args(copper, lays)

        def run_pass(cur, tag, pnets, args, i):
            try:
                return run_pass0(cur, tag, pnets, args + largs, i)
            except subprocess.TimeoutExpired:
                if not keep_partial:
                    raise
                return None, []
        notes = []
        if widths and set(pnets) <= set(necked):
            # every net open at the start: the board as it is, its summary listing them as failed
            best = (cur, {"failed_single": list(pnets), "failed": len(pnets)}, len(pnets))
            notes.append(f"{w} mm: {', '.join(pnets)} routed narrow first (corridor proof, exact spacings; the router "
                         f"keeps {routing.ISO_SLACK:g} mm more from isolation keep-outs: "
                         + ", ".join(f"{n} {necked[n]['plain_mm']:g} mm without a neck" for n in pnets) + ")")
        else:
            best, tried = run_pass(cur, tag, pnets, ["--track-width", str(w)], i)
            if best is None:  # no completed candidate in this group; retain its input copper
                return (cur, {"failed_single": list(pnets), "failed": len(pnets)}, len(pnets)), notes
            notes += [f"{w} mm: " + ", ".join(f"{v} {n}" for v, n in tried)] if len(tried) > 1 else []
        # neck-down, only for the nets left open (a board that routes at full width keeps that copper): first the
        # router's own pad-entry neck (power nets, at most 2 mm narrow), then the nets at the track width (then at
        # the board's minimum width), back to their width wherever it fits
        open_nets = neckable(sorted(set(krt_open_nets(best[1])) & set(pnets)), neck) if wide and best[2] and not budget_exhausted else []
        if open_nets:
            pb, _ = run_pass(best[0], f"{tag}w", open_nets, ["--track-width", str(neck), "--power-nets", *open_nets,
                                                           "--power-nets-widths", *[str(w)] * len(open_nets)], 1)
            if pb is not None and pb[2] < best[2]:
                ok, text = fit(pb[0], open_nets, w)
                if ok:
                    best = pb
                    notes.append(f"{w} mm: {', '.join(open_nets)} routed with the router's pad-entry neck-down: {text}")
                else:
                    notes.append(f"{w} mm: router pad-entry neck-down rejected (a neck over {routing.NECK_MAX_MM:g} mm): {text}")
        for k, nw in enumerate(widths):
            if budget_exhausted:
                break
            open_nets = neckable(sorted(set(krt_open_nets(best[1])) & set(pnets)), nw) if best[2] else []
            if not open_nets:
                break
            nb, _ = run_pass(best[0], f"{tag}n{k}", open_nets, ["--track-width", str(nw)], 1)
            if nb is None:
                break
            if nb[2] >= best[2]:
                notes.append(f"{w} mm: neck-down to {nw:g} mm: {', '.join(open_nets)} still open")
                continue
            ok, text = fit(nb[0], open_nets, w)
            if ok:
                best = nb
                notes.append(f"{w} mm: neck-down to {nw:g} mm: {text}")
            else:  # a long narrow stretch is a thinner net, not a neck: leave the net open and say so
                notes.append(f"{w} mm: neck-down to {nw:g} mm rejected (a neck over {routing.NECK_MAX_MM:g} mm): {text}")
        # a constrained net still open: a seed route through the corridor proof's free space (seedroute)
        open_nets = sorted(set(krt_open_nets(best[1])) & set(pnets) & seedable) if best[2] and not budget_exhausted else []
        if open_nets:
            best, text = seed_open(best, f"{tag}s", open_nets)
            notes += [f"{w} mm: {t}" for t in text]
        return best, notes

    def seed_open(best, tag, snets):
        """Seed routes for the open nets `snets` on the board of `best`; the accepted ones close in the summary."""
        from . import seedroute
        dst = workdir / f"{stem}{tag}.kicad_pcb"
        try:
            res = seedroute.seed(design, libs, best[0], dst, snets)
        except Exception as e:  # noqa: BLE001 - a geometry the seed route cannot build: the nets stay open
            return best, [f"seed route {', '.join(snets)}: not possible ({type(e).__name__}: {e})"]
        done = [n for n, r in res.items() if r["ok"]]
        if done:
            seedroute.record(workdir, res)
            summ = seeded(best[1], done)
            best = (dst, summ, krt_unrouted(summ))
        return best, [r["msg"] for r in res.values()]

    for i, (w, gnets, lays) in enumerate(passes):
        best, notes = route_group(cur, i, gnets, w, i, lays)
        got = {f"{i}: {w} mm" + (f" on {' '.join(lays)}" if lays else ""): best[1]}
        open_nets = sorted(set(krt_open_nets(best[1])) & set(gnets)) if best[2] else []
        if open_nets and len(open_nets) < len(gnets) and not budget_exhausted:
            # a net walled in by the others of its pass (a pin between two pins of a wider net): route it first
            first, n1 = route_group(cur, f"{i}p", open_nets, w, i, lays)
            rest, n2 = route_group(first[0], f"{i}q", [n for n in gnets if n not in open_nets], w, 1, lays)
            if first[2] + rest[2] < best[2]:
                best = (rest[0], rest[1], first[2] + rest[2])
                got = {f"{i}: {w} mm, open nets first": first[1], f"{i}: {w} mm, the others": rest[1]}
                notes = n1 + n2 + [f"{w} mm: {', '.join(open_nets)} routed before the other nets of the pass"]
        cur = best[0]
        last.update(got)
        retried += notes
        if budget_exhausted:
            remaining = sorted({n for _, ns, _ in passes[i + 1:] for n in ns})
            last["budget"] = {"budget_exhausted": True, "failed_single": remaining, "failed": len(remaining)}
            retried.append("stopped at layout wall-time budget; retained completed accepted copper")
            break
        completed += 1
    with (workdir / "krt.log").open("a") as f:
        f.write("\n".join(log))
    return cur, last, retried, completed


def seeded(summary: dict, nets) -> dict:
    """A router summary with `nets` no longer open (a seed route connected them)."""
    nets = set(nets)
    s = dict(summary)
    fs = [n for n in s.get("failed_single") or [] if n not in nets]
    fm = [x for x in s.get("failed_multipoint") or [] if not (isinstance(x, dict) and x.get("net_name") in nets)]
    gone = len(s.get("failed_single") or []) - len(fs) + len(s.get("failed_multipoint") or []) - len(fm)
    s["failed"] = max(0, int(s.get("failed", 0)) - gone)
    s["failed_single"], s["failed_multipoint"] = fs, fm
    s["open_single"] = [n for n in s.get("open_single") or [] if n not in nets]
    if s.get("pad_pairs_open"):
        s["pad_pairs_open"] = dict(s["pad_pairs_open"], nets=[n for n in s["pad_pairs_open"].get("nets") or [] if n not in nets])
    return s


def _neck_needed(design: Design, src: Path, libs=None) -> dict[str, dict]:
    """corridor.neck_needed at the placement of the board `src` ({} when the design has no constrained net or the
    proof cannot be built: the nets then route at full width first, as before)."""
    from ..place import corridor
    if not corridor.constrained(design):
        return {}
    try:
        if libs is None:
            from ...core.libs import Libs
            libs = Libs(design=design)
        return corridor.neck_needed(design, libs, board=pcb.read_board(Path(src)))
    except Exception:  # noqa: BLE001 - a board or footprint the proof cannot read: no reordering
        return {}


def layer_args(copper: list[str], lays) -> list[str]:
    """Router arguments that keep a pass on `lays`: every board layer listed, the others with cost -1 (forbidden:
    an obstacle, crossed only by through vias). The router's default costs otherwise (F.Cu 1, the others 3)."""
    if not lays or set(lays) >= set(copper):
        return []
    costs = ["-1" if L not in lays else "1.0" if L == "F.Cu" else "3.0" for L in copper]
    return ["--layers", *copper, "--layer-costs", *costs]


def krt_open_nets(summary: dict) -> list[str]:
    """Nets a router summary reports as not fully connected."""
    out = list(summary.get("failed_single") or []) + list(summary.get("open_single") or [])
    out += [x.get("net_name") for x in summary.get("failed_multipoint") or [] if isinstance(x, dict)]
    out += list((summary.get("pad_pairs_open") or {}).get("nets") or [])
    return [n for n in dict.fromkeys(out) if n]


def _pair_clearance(design: Design, classes: dict, iso: list[dict]):
    """clearance(a, b): the copper distance nets a and b keep (net classes, and the distance of an isolation group
    between a member and a non-member, and its between_mm between two members)."""
    default = float(design.board["clearance_mm"])

    def clearance(a, b):
        c = max(classes.get(a, {}).get("clearance", default), classes.get(b, {}).get("clearance", default))
        for g in iso:
            if (a in g["members"]) != (b in g["members"]):
                c = max(c, float(g["mm"]))
            elif a != b and a in g["members"] and g.get("between") is not None:
                c = max(c, float(g["between"]))
        return c
    return clearance


_ROUTER_VERSION: dict[str, str] = {}


def router_version(design: Design) -> str:
    """What the routing result depends on besides the input: a hash of the KiCadRoutingTools sources and its compiled
    extension (or the Freerouting jar), so a cached route is dropped when the router changes."""
    import hashlib
    krt = design.board.get("router", "krt") == "krt" and (krt_dir() / "py_router" / "route.py").exists()
    key = f"krt:{krt_dir()}" if krt else f"freerouting:{freerouting_jar()}"
    if key not in _ROUTER_VERSION:
        h = hashlib.sha256()
        files = sorted(list((krt_dir() / "py_router").rglob("*.py")) + list((krt_dir() / "rust_router").glob("*.so"))) if krt \
            else [Path(freerouting_jar())]
        for f in files:
            if f.exists():
                h.update(str(f.name).encode() + f.read_bytes())
        _ROUTER_VERSION[key] = h.hexdigest()[:16]
    return _ROUTER_VERSION[key]


def _kicad_cli() -> str:
    from ..board import kicad
    return kicad.cli()


# net orders tried, in turn, when a width class leaves nets unrouted (backlog 40)
KRT_VARIANTS = [[], ["--ordering", "inside_out"], ["--mps-reverse-rounds"], ["--direction", "backward"],
                ["--ordering", "original"]]


def krt_unrouted(summary: dict) -> int:
    return int(summary.get("failed", 0)) + int(summary.get("multipoint_deficit", 0)) + len(summary.get("open_single") or [])


def run_group(args, timeout, cwd=None, env=None) -> subprocess.CompletedProcess:
    """subprocess.run in a session of its own: on a timeout the whole process group is killed. subprocess.run kills
    only the child and then waits for its worker processes, which hold the output pipes, to end by themselves (a
    120 s repair budget once took 7009 s that way). The TimeoutExpired raised carries the output read so far."""
    import signal
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd, env=env,
                         start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = p.communicate()  # after a timeout, communicate() again returns all the output (none is lost)
        raise subprocess.TimeoutExpired(args, timeout, output=out, stderr=err) from None
    return subprocess.CompletedProcess(args, p.returncode, out, err)


def krt_variants() -> list:
    """KRT_VARIANTS, rotated by HWFLOW_KRT_ROTATE (a layout portfolio strategy starts from another net order)."""
    k = get_settings().layout.krt_rotate % len(KRT_VARIANTS)
    return KRT_VARIANTS[k:] + KRT_VARIANTS[:k]


def route_class(base: list, stem: Path, retries: int, run, variants: list | None = None,
                keep_partial: bool = False, stop=None) -> tuple:
    """Route one width class; while nets stay unrouted, retry in another net order (up to `retries` times).
    `stop(unrouted, tries)`: True when another net order cannot help (routability.retry_stop), so the class keeps its
    best try. Returns ((board, summary, unrouted) of the best attempt or None, [(order, unrouted)], logs)."""
    import json as _json
    best, tried, logs = None, [], []
    for j, extra in enumerate(variants or krt_variants()[:1 + max(retries, 0)]):
        if j and best is not None and stop is not None and stop(best[2], j):
            tried.append(("further net orders", f"skipped ({stop.why})"))
            break
        out = stem.with_name(stem.name + (f"_r{j}" if j else "") + ".kicad_pcb")
        args = [str(out) if a is None else a for a in base] + extra  # None marks the output board
        out.unlink(missing_ok=True)
        try:
            text, rc = run(args)
        except subprocess.TimeoutExpired:
            if not keep_partial or best is None:
                raise
            tried.append(("budget", "stopped; kept completed variant"))
            break
        logs.append(text)
        summ = {}
        for ln in text.splitlines():
            if ln.startswith("JSON_SUMMARY_MIN:"):
                summ = _json.loads(ln.split(":", 1)[1])
        label = " ".join(extra) or "default order"
        if not out.exists():
            tried.append((label, f"failed (exit {rc})"))
            continue
        n = krt_unrouted(summ)
        tried.append((label, f"{n} unrouted"))
        if best is None or n < best[2]:
            best = (out, summ, n)
        if n == 0:
            break
    return best, tried, logs


def import_ses(ses: Path, design: Design) -> tuple[list, list]:
    tree = sexpr.loads(ses.read_text())
    routes = sexpr.find(tree, "routes")
    res = sexpr.find(routes, "resolution")
    unit, per = str(res[1]), float(res[2])
    scale = {"um": 1e-3, "mm": 1.0, "mil": 0.0254, "inch": 25.4}[unit] / per
    tracks, vias = [], []
    for net in sexpr.find_all(sexpr.find(routes, "network_out"), "net"):
        name = str(net[1])
        for w in sexpr.find_all(net, "wire"):
            path = sexpr.find(w, "path")
            layer, width = str(path[1]), float(path[2]) * scale
            coords = [float(x) for x in path[3:] if not isinstance(x, list)]
            pts = [(coords[i] * scale, -coords[i + 1] * scale) for i in range(0, len(coords) - 1, 2)]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                tracks.append({"net": name, "layer": layer, "w": round(width, 4), "x1": x1, "y1": y1, "x2": x2, "y2": y2})
        for v in sexpr.find_all(net, "via"):
            vias.append({"net": name, "x": float(v[2]) * scale, "y": -float(v[3]) * scale})
    return tracks, vias


# ------------------------------------------------------------------ read back
