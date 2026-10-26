"""Routable first pass (experiment, off by default; HWFLOW_FIRSTPASS=1 or a comma list of steps): the order of the
commercial ASIC and FPGA flows, so that the first placement of an attempt is routable instead of relying on new
placer seeds.

1. roads: the constrained nets (isolation members, `layers` and region nets, min_width_mm; roads.classify) are
   planned before the other parts are placed (roads.plan, to max(clearance, creepage) of their isolation group), the
   plan is reserved in placement (roads.place: rule areas, zoning feedback), and the planned copper goes on the board
   before the router (roads.preseed; roads.on_route is on with this step). `preseed` alone: only the routing half.
2. hot: placement around the hot spots: the routability window cuts (routability.window_map) as a local penalty
   inside the placer's detailed moves and short anneal (place/hotspot.py), instead of a score on the finished
   placement. The legality gate is unchanged.
3. quick: a truncated route as a filter (Vivado's estimated router congestion, Innovus' global-route overflow): one
   KRT run in the first net order only, with a wall-time cap (HWFLOW_FIRSTPASS_QUICK_S) and fewer A* iterations
   (QUICK_ARGS). The nets it leaves open drive one targeted local placement fix (local_fix): the parts around their
   pads move away (hotspot.bumps), the parts that own them stay. A second quick route keeps the fix only when it
   leaves fewer nets open, else the placement stays as it was.
4. The full route (stage_layout, unchanged) runs on the placement kept.

`place` returns (parts, cost, violations, info); info goes to status.json layout.firstpass (quick-route open nets and
seconds, the fix, the roads), and the measurements are in docs/asic-ideas.md ("Routable first pass")."""
from __future__ import annotations

import re
import time
from pathlib import Path

from ...core.settings import get_settings
from ..board import pcb
from ..place import hotspot

QUICK_ARGS = ["--max-iterations", "50000"]   # A* iterations per net (router default 200000)
FIX_RADIUS = 4.0      # mm: the parts whose courtyard centre lies this close to an open net's pad move in the fix
FIX_ANNEAL = 0.1      # local anneal moves, as a share of the per-seed iterations (at most FIX_MOVES)
FIX_MOVES = 4000
FIX_W = 8.0           # weight of the hotspot term (with the bumps) in the push fix
FIX_HOT = 5.0         # replace fix: start temperature, x the flow's short anneal start
FIXES = (("push", 0), ("replace", 0), ("replace", 1))  # (mode, seed offset) tried in order, each checked by a quick route
FIX_MAX_OPEN = 4      # a quick route with more open signal nets is no local problem: no fix (the attempt goes on)


def on(design=None) -> bool:
    return bool(hotspot.steps(design))


def quick(design, libs, parts, workdir: Path, stem: str, budget_s: float | None = None) -> dict:
    """The truncated route of a placement: {open: [nets], unrouted, seconds, budget_exhausted}."""
    from . import krt, seedroute
    budget_s = get_settings().layout.firstpass_quick_s if budget_s is None else budget_s
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / f"{design.name}_{stem}_unrouted.kicad_pcb"
    pcb.write_board(src, pcb.build_board(design, libs, parts))
    pcb.write_project(workdir, design.name, design)
    (workdir / seedroute.RECORD).unlink(missing_ok=True)
    nets, keep = None, False
    if design.raw.get("blocks"):
        from ...core import blocks
        closed = blocks.closed_nets(design, libs)
        nets = set(design.nets()) - closed if closed else None
        keep = any(blocks.copper(design, parts))
    t = time.time()
    try:
        cur, last, notes, _ = krt.krt_route(design, src, workdir, f"{design.name}_{stem}_", nets=nets, keep_first=keep,
                                          variants=[[]], libs=libs, deadline=t + budget_s, keep_partial=True,
                                          extra_args=QUICK_ARGS)
    except Exception as e:  # noqa: BLE001 - a filter: the full route runs without it
        return {"error": f"{type(e).__name__}: {e}"[:300], "seconds": round(time.time() - t, 1)}
    router_open = sorted({n for v in last.values() for n in krt.krt_open_nets(v)})
    out = {"router_open": router_open, "unrouted": sum(krt.krt_unrouted(v) for v in last.values()),
           "budget_exhausted": any(v.get("budget_exhausted") for v in last.values()),
           "route_s": round(time.time() - t, 1)}
    # the router's own tally misses what only the board check sees (a pour island, a track the pour cuts off):
    # the fast DRC of the routed board (no creepage, no repair) gives the open nets and the error spots
    t2 = time.time()
    try:
        from ..board import kicad
        b = pcb.read_board(cur)
        chk = workdir / f"{design.name}_{stem}_check.kicad_pcb"
        pcb.write_board(chk, pcb.build_board(design, libs, parts, [dict(x) for x in b["segments"] if x["net"]],
                                             [dict(v) for v in b["vias"] if v["net"]]))
        for suf in (".kicad_pro", ".kicad_dru"):
            f = workdir / f"{design.name}{suf}"
            if f.exists():
                chk.with_suffix(suf).write_text(f.read_text())
        drc = kicad.drc_fast(chk)
        errs = [x for x in drc if x["sev"] == "error"]
        drc_open = {n for x in errs if x["type"] == "unconnected" for it in x["items"] or [] for n in _NET.findall(str(it))}
        out.update(drc_errors=len(errs), drc_open=sorted(drc_open),
                   spots=[list(q) for x in errs if x["type"] != "unconnected" for q in (x.get("at") or []) if q][:40])
    except Exception as e:  # noqa: BLE001 - the router's tally stands alone
        drc_open, out["drc_error"] = set(), f"{type(e).__name__}: {e}"[:300]
    out["check_s"] = round(time.time() - t2, 1)
    out["open"] = sorted(set(router_open) | drc_open)
    out["seconds"] = round(time.time() - t, 1)
    print(f"[firstpass] quick route {stem}: {len(out['open'])} nets open, {out.get('drc_errors', '?')} DRC errors in "
          f"{out['seconds']} s" + (f" ({', '.join(out['open'][:12])})" if out["open"] else "")
          + ("; stopped at its budget" if out["budget_exhausted"] else ""))
    return out


_NET = re.compile(r"\[([^\]]+)\]")


def bad(q: dict, pour=()) -> tuple:
    """Order of quick-route results (lower is better): open signal nets, open pour nets (an island the repair often
    stitches), DRC errors."""
    o = q.get("open") or []
    return (len([n for n in o if n not in pour]), len([n for n in o if n in pour]), q.get("drc_errors", 0))


def _pour(design) -> set:
    return design.ground_nets() if design.board.get("gnd_pour") else set()


def local_fix(design, libs, parts, open_nets, iters: int, seed: int = 1, spots=(), mode: str = "push") -> dict:
    """One targeted placement fix around the pads of `open_nets` (pour nets left out) and the quick route's DRC error
    spots. `push`: the movable parts near them (FIX_RADIUS), except the parts that own those pads, move by detailed
    moves and a short anneal with the hotspot term plus a bump at each such pad. `replace`: every movable part near
    them, owners included, is re-placed by a hotter local anneal (FIX_HOT x the flow's anneal temperature) with the
    hotspot term, then detailed moves. The moved parts are then legalized (settle on a violation). Works on a copy
    of the positions: returns {parts (the fixed copy), moved, violations, seconds} or {skipped}."""
    from ..place import asicflow, placer
    t = time.time()
    b = design.board
    conns = design.nets()
    pts, owners = [], set()
    pour = _pour(design)
    for n in (n for n in open_nets if n not in pour):
        for r, p in conns.get(n, []):
            if r in parts and p in parts[r].pads:
                pts.append(parts[r].pad(p))
                owners.add(r)
    pts += [tuple(q) for q in spots]  # DRC error spots of the quick route
    if not pts:
        return {"skipped": "no pad of an open net and no error spot on the board"}
    roads_ = [a for a in getattr(parts, "areas", ()) if str(a.get("owner", "")).startswith("road:")]
    new = placer.build_parts(design, libs, areas=roads_)  # the road reservations of step 1 still hold
    for r, p in new.items():
        if r in parts:
            p.x, p.y, p.r = parts[r].x, parts[r].y, parts[r].r
    pl = placer.Placer(design, new, b["width_mm"], b["height_mm"], seed=seed,
                       weights=(design.raw.get("placement") or {}).get("weights"))  # block origins: Rigid.pull
    v0 = pl.violations()

    def near(u):
        y = getattr(u, "yard", u)
        return any(abs(u.x - x) <= FIX_RADIUS + y.w / 2 and abs(u.y - yy) <= FIX_RADIUS + y.h / 2 for x, yy in pts)
    local = [u for u in pl._movable() if near(u) and (mode == "replace" or not any(r in owners for r in pl._refs(u)))]
    if not local:
        return {"skipped": "no movable part near the open nets' pads"}
    from ..place import routability
    W, H = float(b["width_mm"]), float(b["height_mm"])
    pos = routability.POS_MM
    shape = (int(-(-W // pos)), int(-(-H // pos)))
    extra = hotspot.bumps(pts, shape, pos) if mode == "push" else None
    if not hotspot.install(pl, extra, weight=FIX_W if mode == "push" else None):
        return {"skipped": "no hotspot map"}
    before = [(u.x, u.y, u.r) for u in local]
    n = min(FIX_MOVES, max(200, int(iters * FIX_ANNEAL)))
    t0 = max(W, H) * 2 * asicflow.ANNEAL_T0
    if mode == "push":
        pl.detailed(local)
        hotspot.refresh(pl, extra)
        pl._anneal(local, n, t0, 0.01, ramp=False)
    else:
        pl._anneal(local, 2 * n, t0 * FIX_HOT, 0.01, ramp=False)
        hotspot.refresh(pl)
        pl.detailed(local)
    hotspot.remove(pl)
    pl.legalize(refs={r for u in local for r in pl._refs(u)})
    viol = pl.violations()
    if viol and get_settings().layout.settle:
        viol = pl.settle()
    moved = sorted(u.ref for u, (x, y, r) in zip(local, before)
                   if abs(u.x - x) > 1e-3 or abs(u.y - y) > 1e-3 or u.r != r)
    return {"parts": new, "mode": mode, "moved": moved, "local": len(local), "violations": viol, "violations_before": len(v0),
            "cost": pl.cost(), "seconds": round(time.time() - t, 1)}


def place(design, libs, out: Path, seeds: int = 4, iters: int = 30000, weights=None, seed0: int = 1,
          deadline: float | None = None):
    """Placement of the routable first pass (module text). Returns (parts, cost, violations, info)."""
    from ..place import placer
    from . import roads
    st = hotspot.steps(design)
    info: dict = {"steps": sorted(st)}
    t = time.time()
    hotspot.LOG.clear()
    if "roads" in st:
        parts, cost, viol, rinfo = roads.place(design, libs, seeds=seeds, iters=iters, weights=weights, seed0=seed0,
                                               deadline=deadline)
        info["roads"] = {"plan_s": rinfo.get("plan_s"), "planned": [r["net"] for r in rinfo["roads"] if r.get("ok")],
                         "rounds": [{k: x[k] for k in ("boxes", "closed")} for x in rinfo["rounds"]]}
    else:
        parts, cost, viol = placer.place(design, libs, seeds=seeds, iters=iters, weights=weights, seed0=seed0,
                                         deadline=deadline)
    info["place_s"] = round(time.time() - t, 1)
    if hotspot.LOG:
        info["hot_maps"] = {"n": len(hotspot.LOG), "s": round(sum(x["s"] for x in hotspot.LOG), 1)}
    movable = any(not p.fixed for p in parts.values())
    if "quick" not in st or viol or not movable:
        if "quick" in st:
            info["quick"] = {"skipped": "placement violations" if viol else "no movable part"}
        return parts, cost, viol, info
    wd = Path(out) / "firstpass"
    q1 = quick(design, libs, parts, wd, "q1")
    info["quick"] = [q1]
    pour = _pour(design)
    if "open" not in q1 or bad(q1, pour)[0] == 0 and bad(q1, pour)[2] == 0:
        return parts, cost, viol, info  # clean, or only pour islands (the repair stitches them)
    if bad(q1, pour)[0] > FIX_MAX_OPEN:
        info["fix"] = f"skipped: {bad(q1, pour)[0]} signal nets open (more than {FIX_MAX_OPEN}), not a local problem"
        print(f"[firstpass] {info['fix']}")
        return parts, cost, viol, info
    best = (bad(q1, pour), parts, cost, viol, "first placement")
    info["fix"] = []
    for k, (mode, ds) in enumerate(FIXES):
        if deadline is not None and time.time() >= deadline:
            break
        fx = local_fix(design, libs, parts, q1["open"], iters // max(seeds, 1), seed=seed0 + ds,
                       spots=q1.get("spots") or (), mode=mode)
        info["fix"].append({k_: v for k_, v in fx.items() if k_ != "parts"})
        print(f"[firstpass] local fix {mode}: " + (fx.get("skipped") or f"{len(fx['moved'])} of {fx['local']} parts "
                                                   f"moved, {len(fx['violations'])} violations, {fx['seconds']} s"))
        if fx.get("skipped") or fx["violations"] or not fx["moved"]:
            continue
        q = quick(design, libs, fx["parts"], wd, f"q{k + 2}")
        info["quick"].append(q)
        if "open" in q and bad(q, pour) < best[0]:
            best = (bad(q, pour), fx["parts"], fx["cost"], fx["violations"], f"fix {k + 1} ({mode})")
            if best[0][0] == 0 and best[0][2] == 0:
                break
    info["kept"] = best[4]
    return best[1], best[2], best[3], info
