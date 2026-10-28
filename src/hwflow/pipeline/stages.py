"""The pipeline stages: check, sch, sim, layout, post, fab."""

from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
from pathlib import Path

from ..sim import budgets
from ..layout.board import kicad, pcb
from ..layout.route import krt
from ..layout.place import placer
from ..verify import geomchecks, postlayout
from ..layout.place import barrier, corridor, pinaccess
from ..layout.route import regionroute, repair, seedroute
from ..layout.board import routing
from ..sim import simrobust, spice
from ..core import blocks as core_blocks
from ..core.design import Design
from ..verify.checks import check
from ..core.libs import Libs
from ..core.settings import get_settings
from .hooks import registry
from .status import _block_files, _design_hash, _out, _status, current_inputs


ERC_ERRORS = ("footprint_link_issues",)  # ERC warnings that hwflow treats as errors


def stage_check(d: Design, libs: Libs) -> dict:
    reg = registry()
    f = check(d, libs)
    for h in reg.check_findings:
        f += h(d)
    for flt in reg.check_filters:
        f = flt(d, f)
    # A finding with `decision: True` is a limit that only a product decision can settle (cost over a must limit): it
    # does not stop sim, layout, post or fab, but the report verdict stays FAIL until the requirement is verified.
    errs = [x for x in f if x["sev"] == "error" and not x.get("decision")]
    pending = [x for x in f if x["sev"] == "error" and x.get("decision")]
    warns = [x for x in f if x["sev"] == "warn"]
    res = {"ok": not errs, "errors": len(errs), "warnings": len(warns), "findings": f}
    if pending:
        res["decisions_pending"] = len(pending)
    for s in reg.check_state:
        res.update(s(d, f))
    return res


def stage_sch(d: Design, libs: Libs) -> dict:
    out = _out(d)
    kicad.write_lib_tables(d, libs, out)
    pcb.write_project(out, d.name, d)
    from ..schematic import sch
    sch.write_schematic(d, libs, out / f"{d.name}.kicad_sch")
    v = kicad.erc(out / f"{d.name}.kicad_sch")
    for x in v:  # a footprint KiCad cannot resolve leaves the part off the board: an error, not a warning
        if x.get("type") in ERC_ERRORS and x.get("sev") != "error":
            x["sev"], x["promoted"] = "error", True
    errs = [x for x in v if x["sev"] == "error"]
    return {"ok": not errs, "errors": len(errs), "warnings": len(v) - len(errs), "violations": v[:40]}


def stage_sim(d: Design, mc: int = 0) -> dict:
    w = _out(d) / "sim"
    t = time.time()
    res = spice.run_all(d, w, cache=_out(d) / "simcache")
    tm = {"sim_s": round(time.time() - t, 1)}  # wall time per step (build/status.json timing, `hwflow time`)
    t = time.time()
    allrows = [dict(r, analysis=a["name"]) for a in res for r in a["rows"]]
    rows = [r for r in allrows if not r.get("debug")]  # dbg_* probes never count (backlog 19)
    audit_errors = []
    audit_timeout = None
    try:
        der = spice.derating_audit(d, w)
    except RuntimeError as e:
        der = []
        audit_errors.append(str(e))
        if isinstance(e, spice.DeratingTimeoutError):
            audit_timeout = str(e)
    tm["derating_s"] = round(time.time() - t, 1)
    out = {"analyses": res, "passed": sum(r["pass"] for r in rows), "total": len(rows),
           "fails": [r for r in rows if not r["pass"]], "derating": der,
           "derating_status": "failed" if audit_errors else "ok",
           "debug": [r for r in allrows if r.get("debug")],
           "runs": _run_summary(res),
           "derating_issues": [f"{r['ref']}: {i}" for r in der for i in r["issues"]],
           "unmodeled": sorted({u for a in res for u in a["unmodeled"]}),
           "generic_models": sorted(c.ref for c in d.components.values() if spice.uses_generic_model(c)),
           "errors": audit_errors + [f"{a['name']}: {e}" for a in res for e in a["errors"]],
           "warnings": [f"{a['name']}: {e}" for a in res for e in a.get("warnings", [])]}
    if audit_timeout:
        out["exception"] = audit_timeout  # execution errors are never reused from the stage cache
    out.update(simrobust.sim_summary(d, res, w, _out(d) / "simcache"))
    prev = _status(d).get("sim") or {}
    cur = current_inputs(d)
    if mc and prev.get("monte_carlo") and cur and prev.get("inputs_hash") == cur and prev.get("mc_runs") == mc:
        out["monte_carlo"], out["mc_runs"] = prev["monte_carlo"], mc  # Monte Carlo once per design (backlog 27)
        print(f"[sim] Monte Carlo {mc} runs reused: inputs unchanged since the last run")
    elif mc:
        t = time.time()
        out["monte_carlo"], out["mc_runs"] = spice.monte_carlo(d, w, runs=mc), mc
        tm["mc_s"] = round(time.time() - t, 1)
    else:  # keep Monte Carlo results of this exact design (a --reuse or plain rebuild must not erase them)
        if prev.get("monte_carlo") and prev.get("design_hash") == _design_hash(d):
            out["monte_carlo"] = prev["monte_carlo"]
            if prev.get("mc_runs"):
                out["mc_runs"] = prev["mc_runs"]
    out["ok"] = out["passed"] == out["total"] and not out["errors"]
    out["timing"] = tm
    return out


def _run_summary(res: list[dict]) -> list[dict]:
    """Analyses that did not finish cleanly (timeout, crash, aborted, error) or needed a retry, with run time."""
    return [{k: a[k] for k in ("name", "status", "seconds", "reason", "flaky") if k in a}
            for a in res if a.get("status", "ok") != "ok" or a.get("flaky")]


def _route_once(d: Design, libs: Libs, parts, passes: int, out: Path | None = None,
                deadline: float | None = None) -> tuple[list, list, str, int]:
    """Route the board. With `d.route_groups` a list (the layout stage sets it), the nets route group by group
    (regionroute) when they form more than one group, each group's copper cached in build/routecache/groups/; the
    group rows are appended to that list."""
    out = out or _out(d)
    name = d.name
    groups = getattr(d, "route_groups", None)
    if deadline is not None and time.time() >= deadline:
        d.route_budget_exhausted = True
        return [], [], "stopped at layout wall-time budget before routing", len(pcb.net_classes(d))
    if d.board.get("router", "krt") == "krt":
        if (krt.krt_dir() / "py_router" / "route.py").exists():
            if groups is not None:
                from ..layout.route import regionroute
                res = regionroute.route(d, libs, parts, out, _out(d) / "routecache", deadline=deadline)
                if res is not None:
                    groups += res[4]
                    return res[:4]
            return krt.run_krt(d, libs, parts, out, deadline=deadline)
        print("KiCadRoutingTools not installed; falling back to Freerouting (run scripts/setup.sh)")
    pcb.export_dsn(d, libs, parts, out / f"{name}.dsn")
    try:
        log = krt.run_freerouting(out / f"{name}.dsn", out / f"{name}.ses", passes=passes,
                                 **({"timeout": max(0.001, min(900, deadline - time.time()))} if deadline is not None else {}))
    except RuntimeError:
        if deadline is None or time.time() < deadline:
            raise
        d.route_budget_exhausted = True
        return [], [], "stopped at layout wall-time budget; no completed Freerouting candidate", len(pcb.net_classes(d))
    if "TIMEOUT after" in log:
        d.route_budget_exhausted = True
        # A killed process may leave a partial session, not a completed checkpoint.
        return [], [], "stopped at router timeout; no completed Freerouting candidate", len(pcb.net_classes(d))
    tracks, vias = krt.import_ses(out / f"{name}.ses", d)
    import re as _re
    done = [ln for ln in log.splitlines() if "Auto-routing stage completed" in ln]
    m = _re.search(r"\((\d+) unrouted and (\d+) violations\)", done[0] if done else "")
    unrouted = int(m.group(1)) if m else 999
    summary = f"{m.group(1)} unrouted, {m.group(2)} violations" if m else "unknown"
    return tracks, vias, summary, unrouted


def _minimize_area(d: Design, libs: Libs, seeds: int, iters: int, passes: int, weights, seed0: int = 1,
                   out: Path | None = None, deadline: float | None = None):
    """Shrink until placement or routing fails. Keep the last complete size, or the first partial size
    if its routing budget expires before any complete result. The selected size owns its budget flag."""
    b = d.raw.setdefault("board", {})
    W0, H0 = float(d.board["width_mm"]), float(d.board["height_mm"])
    probe = placer.build_parts(d, libs)
    parts_area = sum(p.w * p.h for p in probe.values())
    m = d.board["margin_mm"]
    best, tried = None, []
    for factor in (3.0, 2.4, 2.0, 1.7, 1.5, 1.35, 1.25):
        if deadline is not None and time.time() >= deadline:
            break
        area = parts_area * factor
        W = math.ceil(math.sqrt(area * W0 / H0) + 2 * m)
        H = math.ceil(area / max(W - 2 * m, 1) + 2 * m)
        if W > W0 or H > H0:
            continue
        b["width_mm"], b["height_mm"] = W, H
        parts, cost, viol = placer.place(d, libs, seeds=seeds, iters=iters, weights=weights, seed0=seed0)
        if viol:
            tried.append(f"{W}x{H}: placement fails")
            break
        d.budget_widths = budgets.widths(d, parts)[0]  # requirement budgets: track widths on this placement
        d.route_budget_exhausted = False
        tracks, vias, summary, unrouted = _route_once(d, libs, parts, passes, out,
                                                     **({"deadline": deadline} if deadline is not None else {}))
        candidate = (W, H, parts, cost, viol, tracks, vias, summary, unrouted, d.route_budget_exhausted)
        if unrouted or d.route_budget_exhausted:
            tried.append(f"{W}x{H}: {unrouted} unrouted" + ("; routing budget exhausted" if d.route_budget_exhausted else ""))
            if d.route_budget_exhausted and best is None:
                best = candidate  # preserve completed variants of this interrupted size, including their outline
            break
        tried.append(f"{W}x{H}: ok")
        best = candidate
    if best is None:
        b["width_mm"], b["height_mm"] = W0, H0
        return None, tried
    b["width_mm"], b["height_mm"] = best[0], best[1]
    d.route_budget_exhausted = best[-1]  # a later timeout must not mark an earlier complete result as truncated
    return best, tried


def _route_key(d: Design, libs: Libs, parts, out: Path, passes: int) -> str:
    """Hash of everything the routed and repaired copper depends on: the unrouted board text (placement, footprints,
    nets, outline), the rules files, the board settings (router and repair settings among them), the router
    version, the router environment knobs, and the repair and router-driver code. The router is deterministic for the same input, so the same key gives the same copper."""
    import hashlib
    h = hashlib.sha256(pcb.board_text(pcb.build_board(d, libs, parts)).encode())
    for suf in (".kicad_pro", ".kicad_dru"):
        f = out / f"{d.name}{suf}"
        h.update(f.read_bytes() if f.exists() else b"-")
    h.update(json.dumps([d.board, passes, krt.KRT_VARIANTS, krt.router_version(d)], sort_keys=True, default=str).encode())
    h.update((Path(repair.__file__)).read_bytes())
    h.update(Path(pcb.__file__).read_bytes())  # board generation used by the router
    h.update(Path(krt.__file__).read_bytes())  # router driver (passes, arguments, deadlines, env defaults)
    h.update(Path(routing.__file__).read_bytes())  # isolation keep-outs, neck-down widening
    h.update(Path(seedroute.__file__).read_bytes())  # seed routes of nets the router leaves open
    from ..layout.place import asicflow
    from ..layout.place import routability
    from ..layout.route import roads
    for m in (corridor, barrier, pinaccess, asicflow, routability, roads):  # which nets route narrow first
        h.update(Path(m.__file__).read_bytes())
    h.update(asicflow.flow(d).encode())  # the layout flow (HWFLOW_FLOW or placement.flow): pin access necked or not
    h.update(Path(regionroute.__file__).read_bytes())  # route groups and their cache
    from ..layout.route import pourstitch, taps
    h.update(Path(pourstitch.__file__).read_bytes())  # pour stitching after routing (settings.layout.pour_stitch)
    h.update(Path(taps.__file__).read_bytes())  # pour taps before routing (settings.layout.pour_taps)
    h.update(json.dumps(d.raw.get("floorplan"), sort_keys=True, default=str).encode())  # conductive keep-outs, regions
    h.update(json.dumps(pcb.net_min_widths(d), sort_keys=True).encode())  # nets.<name>.min_width_mm: no neck under it
    if d.raw.get("blocks"):  # layout blocks: the nets their copper closes are not routed
        h.update(Path(core_blocks.__file__).read_bytes())
        for f in _block_files(d):
            h.update(f.read_bytes())
    # resolved settings of the layout, repair and router groups (the router environment knobs among them)
    h.update(json.dumps(get_settings().key_dump(), sort_keys=True).encode())
    return h.hexdigest()[:24]


def net_stats(tracks: list, vias: list, classes: dict) -> dict:
    """Routed copper per net: narrowest and widest track, track length, the length narrower than the net's class
    width, layers and via count (status.json layout.nets, `hwflow nets`)."""
    import math
    out = {}
    for t in tracks:
        n = t["net"]
        r = out.setdefault(n, {"min_w": t["w"], "max_w": t["w"], "length_mm": 0.0, "narrow_mm": 0.0, "layers": set(),
                               "vias": 0})
        L = math.hypot(t["x2"] - t["x1"], t["y2"] - t["y1"])
        r["min_w"], r["max_w"] = min(r["min_w"], t["w"]), max(r["max_w"], t["w"])
        r["length_mm"] += L
        if t["w"] < (classes.get(n) or {}).get("width", 0.0) - 1e-6:
            r["narrow_mm"] += L
        r["layers"].add(t["layer"])
    for v in vias:
        out.setdefault(v["net"], {"min_w": None, "max_w": None, "length_mm": 0.0, "narrow_mm": 0.0, "layers": set(),
                                  "vias": 0})["vias"] += 1
    return {n: dict(r, length_mm=round(r["length_mm"], 2), narrow_mm=round(r["narrow_mm"], 2),
                    layers=sorted(r["layers"]), width=(classes.get(n) or {}).get("width")) for n, r in sorted(out.items())}


def _cache_save(cache: Path, key: str, data: dict):
    cache.mkdir(exist_ok=True)
    tmp = cache / f"{key}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(data))
    os.replace(tmp, cache / f"{key}.json")  # atomic: parallel attempts may write the same key


def stage_layout(d: Design, libs: Libs, reuse: bool = False, seeds: int = 4, iters: int = 30000,
                 passes: int = 30, seed0: int | None = None, workdir: Path | None = None, deadline: float | None = None,
                 shared: bool = False, eco: Path | None = None) -> dict:
    """Place, route, repair and check. `eco`: a last good placement (build/placement.good.json, layout/place/eco.py)
    that an incremental placement starts from; when the edit is too large for it, the parts are placed from scratch.
    `workdir` (default build/) holds this call's files; the route cache
    (build/routecache/<key>.json) is shared. A cache hit skips the router and the repair; the full DRC still runs.
    `deadline` (time.time()) bounds routing search, including the first pass and retries, and repair rounds.
    Final DRC, candidate preservation and diagnosis may continue afterwards; this is a search budget, not a
    hard whole-command timeout. A truncated candidate is checked and kept, but never saved to the board cache.
    `shared`: the placement.json in `workdir` was placed for this call (the layout portfolio's shared placement,
    place_shared) and is taken as it is, at the declared board size."""
    if seed0 is None:  # a plain `hwflow layout` starts at HWFLOW_SEED0 (default 1), as attempt 1 of --auto does
        seed0 = get_settings().layout.seed0
    out = workdir or _out(d)
    cache = _out(d) / "routecache"
    pj = out / "placement.json"
    t0 = time.time()
    placer.WORK.update(pack_evals=0, aborts=[])  # the placement's work counters and safety aborts (place_work)
    weights = (d.raw.get("placement") or {}).get("weights")
    declared = [d.board.get("width_mm"), d.board.get("height_mm")]  # before a saved layout size is applied
    area_log = None
    pre = None
    firstpass_info = None
    d.route_budget_exhausted = False
    eco_res, eco_info = None, None
    if eco is not None and not reuse and not shared:
        eco_res = _eco_place(d, libs, eco, declared, None if deadline is None else t0 + max(0.0, deadline - t0) / 2)
        if eco_res is not None:
            eco_info = eco_res[3]
    if eco_res is not None:
        parts, cost, viol = eco_res[:3]
    elif d.board.get("optimize_area") and not reuse:
        pre, area_log = _minimize_area(d, libs, seeds, iters, passes, weights, seed0, out, deadline=deadline)
    if eco_res is not None:
        pass  # placed incrementally above
    elif pre:
        _, _, parts, cost, viol, tracks, vias, summary, _, d.route_budget_exhausted = pre
    elif (reuse and pj.exists() and _saved_board(pj) in (None, declared)) or (shared and pj.exists()):
        if not shared:
            _apply_saved_size(d, pj)
        parts = placer.build_parts(d, libs)
        saved = json.loads(pj.read_text())
        for r, p in parts.items():
            if r in saved and not p.fixed:  # a fixed part keeps the position the design gives it now
                p.x, p.y, p.r = saved[r]
        pl = placer.Placer(d, parts, d.board["width_mm"], d.board["height_mm"])
        pl.legalize()
        viol, cost = pl.violations(), pl.cost()
        if viol and get_settings().layout.settle:  # a saved placement with violations: settle them
            viol, cost = pl.settle(), pl.cost()
    else:
        # placement gets half of the time left, so routing and repair still run inside the budget
        pdl = None if deadline is None else t0 + max(0.0, deadline - t0) / 2
        from ..layout.route import firstpass, roads
        if firstpass.on(d):  # routable first pass (experiment): roads, hotspot-aware placement, quick-route filter
            parts, cost, viol, firstpass_info = firstpass.place(d, libs, out, seeds=seeds, iters=iters,
                                                                weights=weights, seed0=seed0, deadline=pdl)
        elif roads.on(d):  # roads first (prototype): plan and reserve the roads, then place the parts around them
            parts, cost, viol, _ = roads.place(d, libs, seeds=seeds, iters=iters, weights=weights, seed0=seed0,
                                               deadline=pdl)
        else:
            parts, cost, viol = placer.place(d, libs, seeds=seeds, iters=iters, weights=weights, seed0=seed0,
                                             deadline=pdl)
    tm = {"place_s": round(time.time() - t0, 1)}  # wall time per step (experiment metrics, docs/asic-ideas.md)
    if eco_info is not None:
        tm["eco_s"] = eco_info["seconds"]
    pj.write_text(json.dumps({"__design_board__": list(declared),
                              "__board_mm__": [d.board["width_mm"], d.board["height_mm"]],
                              **{r: [p.x, p.y, p.r] for r, p in parts.items()}}, indent=1))
    d.budget_widths, bud_w = budgets.widths(d, parts)  # requirement budgets: track widths on this placement
    for r in bud_w:
        print(f"[layout] budget {r['name']}: {r['msg']}")
    name = d.name
    board = out / f"{name}.kicad_pcb"
    corridors = placer.Placer(d, parts, d.board["width_mm"], d.board["height_mm"]).corridors()
    for c in corridors:
        print(f"[layout] corridor {c['msg']}")
    blocks = [v for v in viol if v.startswith("corridor ")]
    verdict = None
    if viol and not blocks and not get_settings().layout.route_illegal:
        # an illegal placement is never routed: hours of routing cannot make it a valid board (HWFLOW_ROUTE_ILLEGAL=1
        # routes it anyway, for debugging only)
        from ..layout.place.legality import placement_verdict
        verdict = placement_verdict(d, parts, d.board["width_mm"], d.board["height_mm"], viol)
        blocks = [verdict["msg"]]
    if blocks:  # a channel the fixed parts close, or an illegal placement: no router run is started
        msg = "not routed: " + "; ".join(blocks)
        print(f"[layout] {msg}")
        pcb.write_project(out, name, d)
        pcb.write_board(board, pcb.build_board(d, libs, parts, [], []))
        return {"failing": {}, "ok": False, "blocked": msg, "isolation": [], "off_layer": [], "too_narrow": [], "conductive_isolation": [],
                "budget_widths": bud_w, "budgets": [], "board_mm": [d.board["width_mm"], d.board["height_mm"]],
                "area_search": area_log, "placement_cost": round(cost, 1), "placement_violations": viol,
                "corridors": corridors, "router": "-", "segments": 0, "vias": 0, "drc_errors": 0, "drc_warnings": 0,
                "unrouted": 0, "drc": [], "repair": None, "route_cache": None,
                "fixed_parts_only": blocks if verdict is None or verdict["back_to"] == "architecture" else [],
                "placement_verdict": verdict, "eco": eco_info, "timing": tm,
                "place_work": _place_work(), "seconds": round(time.time() - t0, 1)}

    # the routability prediction of this legal placement (seconds, no routing): a report and a run log
    # (build/routab/), never a gate; HWFLOW_ROUTAB_PREDICT=0 turns it off
    routab, routab_full = None, None
    if get_settings().layout.routab_predict and not pre:
        from ..layout.place import routability
        t = time.time()
        try:
            routab_full = routability.predict(d, libs, parts=parts)
            routab = routability.brief(routab_full)
            print(f"[layout] {routab['message']} ({routab['seconds']} s)")
        except Exception as e:  # noqa: BLE001 - an estimate: the router runs without it
            print(f"[layout] routability prediction skipped: {type(e).__name__}: {e}")
        tm["predict_s"] = round(time.time() - t, 1)

    def route_check(tr, vi, island=0, fast=False):
        t = time.time()
        pcb.write_board(board, pcb.build_board(d, libs, parts, tr, vi, island_removal=island))
        try:
            return kicad.drc_fast(board) if fast else kicad.drc(board, refill=True, parity=True)
        finally:
            tm["drc_s"] = round(tm.get("drc_s", 0.0) + time.time() - t, 1)

    pcb.write_project(out, name, d)  # the .kicad_dru with board.drc_rules and board.isolation, for the router and DRC
    key = _route_key(d, libs, parts, out, passes)
    hit = json.loads((cache / f"{key}.json").read_text()) if (cache / f"{key}.json").exists() else None
    rep, island, groups, stitch = None, 0, [], None
    if hit:
        d.route_budget_exhausted = False  # the cached complete route replaces any partial area-search result
        tracks, vias, summary, island, rep = hit["tracks"], hit["vias"], hit["summary"] + " (route cache)", hit["island"], hit["rep"]
        seeds = hit.get("seeds") or {}
        groups = [dict(g, cache="board cache", seconds=0.0) for g in hit.get("groups") or []]
        drc = route_check(tracks, vias, island)  # the full DRC runs again: it is the check of the board kept
        print(f"[layout] route cache hit {key}: router and repair skipped")
    else:
        if not pre:
            d.route_groups = groups  # regionroute: the group rows land in this list
            t = time.time()
            try:
                tracks, vias, summary, _ = _route_once(d, libs, parts, passes, out,
                                                            **({"deadline": deadline} if deadline is not None else {}))
            finally:
                d.route_groups = None
                tm["route_s"] = round(time.time() - t, 1)
        seeds = seedroute.load(out)  # the seed copper of this route (krt.krt_route)
        drc = route_check(tracks, vias)
        if any(g["cache"] == "hit" for g in groups):  # reused group copper: the joined board's DRC judges it
            from ..layout.route import regionroute
            tracks, vias, drc = regionroute.validate(d, libs, parts, out, _out(d) / "routecache", tracks, vias, drc,
                                                     groups, route_check, deadline=deadline)
        if (get_settings().layout.pour_stitch and d.board["gnd_pour"] and not d.route_budget_exhausted
                and d.board.get("router", "krt") == "krt"):
            from ..layout.route import pourstitch
            t = time.time()
            ps = pourstitch.stitch(d, libs, parts, board, tracks, vias, drc, route_check, deadline=deadline)
            tracks, vias, drc = ps["tracks"], ps["vias"], ps["drc"]
            tm["stitch_s"] = round(time.time() - t, 1)
            for line in ps["log"]:
                print(f"[layout] pour stitch: {line}")
            summary += f"; pour stitch: {ps['added'][1]} vias, {ps['added'][0]} stubs"
            stitch = {"vias": ps["added"][1], "stubs": ps["added"][0], "log": ps["log"], "aborts": ps.get("aborts", [])}
        rounds = int(d.board.get("repair_rounds", 3))
        if (not d.route_budget_exhausted and any(x["sev"] == "error" for x in drc) and rounds > 0
                and d.board.get("router", "krt") == "krt"
                and (krt.krt_dir() / "py_router" / "route.py").exists()):
            pre = (tracks, vias, drc, board.read_bytes())
            t = time.time()
            r = repair.repair(d, libs, parts, tracks, vias, drc, board,
                              lambda *a: route_check(*a, fast=True), rounds, deadline=deadline)  # local repair, not a rebuild
            tracks, vias, rep, island = r["tracks"], r["vias"], r["report"], r["island"]
            tm["repair_s"] = round(time.time() - t, 1)  # router calls and fast DRC of the repair included
            if rep["rounds"] and (tracks, vias) != pre[:2]:
                drc = kicad.drc(board, refill=True, parity=True)  # the full DRC, once, on the board kept
                if len([x for x in drc if x["sev"] == "error"]) > len([x for x in pre[2] if x["sev"] == "error"]):
                    tracks, vias, drc, island = pre[0], pre[1], pre[2], 0  # a creepage error the fast check cannot see
                    board.write_bytes(pre[3])
                    rep["log"].append("rejected by the full DRC")
                    drc = kicad.drc(board, refill=True, parity=True)
            rep["before"] = len([x for x in pre[2] if x["sev"] == "error"])
            rep["after"] = len([x for x in drc if x["sev"] == "error"])
            rep["text"] = repair.summary(rep)
            print(f"[layout] {rep['text']}")
        if not d.route_budget_exhausted:
            _cache_save(cache, key, {"tracks": tracks, "vias": vias, "summary": summary, "island": island, "rep": rep,
                                     "seeds": seeds, "groups": groups})
    if seeds:  # the final DRC judges the seed copper: an error that touches it removes it
        from ..layout.route import roads
        tr, vi, dropped = seedroute.drop_on_drc(seeds, tracks, vias, drc, copper_only=roads.on_route(d))
        if dropped:
            tracks, vias = tr, vi
            summary += f"; seed route {', '.join(dropped)} removed (a DRC error touches it)"
            print(f"[layout] seed route {', '.join(dropped)}: removed, a DRC error touches it")
            if roads.on_route(d) and d.board.get("router", "krt") == "krt":  # back to the router as ordinary nets
                try:
                    tracks, vias, note = roads.reroute(d, libs, parts, out, tracks, vias, dropped)
                    summary += f"; {note}"
                    print(f"[layout] {note}")
                except Exception as e:  # noqa: BLE001 - the nets stay open; the DRC reports them
                    print(f"[layout] roads {', '.join(dropped)}: reroute failed ({type(e).__name__}: {e})")
            drc = route_check(tracks, vias, island)
    from ..layout.route.prune import prune
    tracks, vias, drc, pruned = prune(tracks, vias, drc, lambda tr, vi: route_check(tr, vi, island))
    if pruned:
        print(f"[layout] removed {pruned} dangling tracks or vias")
    errs = [x for x in drc if x["sev"] == "error"]
    unrouted = [x for x in drc if x["type"] == "unconnected"]
    lays = pcb.net_layers(d)  # nets.<name>.layers: a track on another layer fails the layout
    off_layer = sorted({f"{t['net']} on {t['layer']}" for t in tracks if t["net"] in lays and t["layer"] not in lays[t["net"]]})
    minw = pcb.net_min_widths(d)  # nets.<name>.min_width_mm: a narrower track fails the layout
    too_narrow = sorted({f"{t['net']} {t['w']:g} mm (min {minw[t['net']]:g})" for t in tracks
                         if t["net"] in minw and float(t["w"]) < minw[t["net"]] - 1e-6})
    # current_a: a stretch under its IPC-2221 width longer than a pad-entry neck (the post-layout track-current error)
    too_narrow += [f"{x['net']} {x['wmin']:g} mm for {x['neck']:.1f} mm (IPC-2221 {x['need']:g} mm for "
                   f"{x['current_a']} A; a neck of at most {x['neck_max']:g} mm)"
                   for x in postlayout.current_shortfalls(d, tracks) if x["sev"] == "error"]
    for x in too_narrow:
        print(f"[layout] track under its net's min_width_mm or current width: {x}")
    nstats = net_stats(tracks, vias, pcb.net_classes(d))
    iso = isolation_distances(d, board, drc)
    # mains copper (tracks, vias, pads) near a conductive keep-out: the router keeps it out, this makes sure
    conductive = postlayout.conductive_clearances(d, pcb.read_board(board))
    for x in conductive:
        print(f"[layout] conductive isolation: {x['net']} {x['item']} {x['mm']:.2f} mm from {x['keepout']} "
              f"(need {x['need']:g} mm)")
    bud = budgets.measure(d, postlayout.Extraction(d, board)) if budgets.budgets(d) else []
    from ..layout import diagnose
    failing = diagnose.failing_parts(drc, pcb.read_board(board))  # the parts each error touches (layout diagnosis)
    if routab_full is not None:  # the (cheap features, outcome) row of this run: build/routab/ (a dataset)
        from ..layout.place import routability
        routab["outcome"] = routability.outcome_check(routab, unrouted, errs)
        routability.log_run(_out(d), d, routab_full, {"unrouted": len(unrouted), "drc_errors": len(errs),
                                                     "vias": len(vias), "route_s": tm.get("route_s"),
                                                     "repair_s": tm.get("repair_s"), "open": unrouted, "drc": errs,
                                                     "placement_violations": viol})
    return {"failing": failing, "budget_exhausted": d.route_budget_exhausted, "routability": routab,
            **({"firstpass": firstpass_info} if firstpass_info is not None else {}),
            "ok": not d.route_budget_exhausted and not errs and not viol and not off_layer and not too_narrow
            and not conductive and all(r["ok"] for r in iso) and all(r["ok"] for r in bud),
            "isolation": iso, "off_layer": off_layer, "too_narrow": too_narrow, "conductive_isolation": conductive,
            "nets": nstats,
            "budget_widths": bud_w, "budgets": bud,
            "board_mm": [d.board["width_mm"], d.board["height_mm"]],
            "area_search": area_log, "placement_cost": round(cost, 1), "placement_violations": viol,
            "corridors": corridors, "router": summary, "segments": len(tracks), "vias": len(vias), "drc_errors": len(errs),
            "drc_warnings": len(drc) - len(errs), "unrouted": len(unrouted), "drc": drc[:40], "repair": rep,
            "route_cache": key if hit else None, "pour_stitch": stitch, "fixed_parts_only": repair.fixed_part_errors(drc, parts),
            "route_groups": [{k: g[k] for k in ("name", "kind", "nets", "cache", "seconds", "unrouted", "open")}
                             for g in groups],
            "eco": eco_info, "timing": tm, "place_work": _place_work(), "seconds": round(time.time() - t0, 1)}


def _place_work() -> dict:
    """The placement's work counters and its wall-clock safety aborts (placer.WORK): an empty `aborts` means the
    placement stopped on counts only, so the same design and seed reproduce it bit for bit."""
    return {"pack_evals": placer.WORK["pack_evals"], "aborts": list(placer.WORK["aborts"])}


def isolation_distances(d: Design, board: Path, drc: list[dict]) -> list[dict]:
    """Measured minimum copper distance of every isolation group (board.isolation, clearance rules in
    board.drc_rules) to all other nets, next to its limit, and the DRC clearance and creepage violations that
    touch the group (backlog 38, 39). A row with `req` verifies that requirement in `trace`."""
    import fnmatch
    import re
    if not postlayout.clearance_rules(d):
        return []
    rows = postlayout.group_clearances(d, postlayout.Extraction(d, board))
    rules = {r["rule"]: r for r in postlayout.clearance_rules(d)}
    for row in rows:
        r = rules.get(row["rule"], {})
        inside = (lambda n: n in r["members"]) if r.get("members") else (
            lambda n: fnmatch.fnmatchcase(n, row["pattern"]))  # noqa: E731
        def touches(x):  # a between row: a violation between two different members; else one touching the group
            got = {n for it in x["items"] for n in re.findall(r"\[([^\]]+)\]", str(it)) if inside(n)}
            return len(got) > 1 if r.get("between") else bool(got)
        hits = [x for x in drc if x["sev"] == "error" and x["type"] in ("clearance", "creepage") and touches(x)]
        row["drc_clearance"] = sum(x["type"] == "clearance" for x in hits)
        row["drc_creepage"] = sum(x["type"] == "creepage" for x in hits)
        row["ok"] = row["ok"] and not hits
    return rows


_AUTO: dict = {}  # what the attempts of layout_auto need; set before the workers start (fork copies it)


def _post_warnings(d: Design, w: Path) -> int:
    """Number of post-layout layout-check warnings on the board in `w`; 0 when the board cannot be read."""
    try:
        return sum(x["sev"] == "warn" for x in postlayout.layout_checks(d, postlayout.Extraction(d, w / f"{d.name}.kicad_pcb")))
    except Exception:  # noqa: BLE001 - a ranking hint, never a failure
        return 0


def _auto_attempt(k: int, workdir: Path | None = None, env: dict | None = None, placement: Path | None = None) -> dict:
    """Attempt `k` (0-based) of layout_auto in its own work directory build/auto/a<k+1>/ (or `workdir`: a layout
    portfolio strategy, which runs under its knobs `env`). Runs in a worker process, so the design it mutates (board
    size) is that worker's own copy. Without `env` it runs under _AUTO["knobs"]: the knobs of the portfolio's best
    strategy, for the fallback attempts. `placement`: the portfolio's shared placement (place_shared)."""
    from ..core.settings import use, with_layout_env
    c = _AUTO
    with use(with_layout_env(get_settings(), (c.get("knobs") or {}) if env is None else {})):
        return run_attempt(c["d"], c["libs"], c["out"], k, size0=c["size0"], seeds=c["seeds"], iters=c["iters"],
                           passes=c["passes"], reuse=c["reuse"], deadline=c.get("deadline"), workdir=workdir,
                           placement=placement)


def _auto_place(k: int, path: Path) -> bool:
    """place_shared for attempt `k` of layout_auto (the portfolio's shared placement)."""
    c = _AUTO
    return place_shared(c["d"], c["libs"], c["out"], k, path, size0=c["size0"], seeds=c["seeds"], iters=c["iters"],
                        reuse=c["reuse"])


def _reset_size(d: Design, size0):
    b = d.raw.setdefault("board", {})
    for key, v in zip(("width_mm", "height_mm"), size0):  # every attempt starts from the requested outline
        if v is None:
            b.pop(key, None)
        else:
            b[key] = v


def place_shared(d: Design, libs: Libs, out: Path, k: int, path: Path, *, size0, seeds: int, iters: int,
                 reuse: bool) -> bool:
    """The placement of attempt `k` alone, written to `path` (placement.json format), for the layout portfolio
    strategies that route the same placement (pipeline.portfolio). False when it is not shared: an area search (each
    strategy runs its own) or a saved placement that --reuse takes."""
    _reset_size(d, size0)
    if d.board.get("optimize_area") or (reuse and k == 0 and (out / "placement.json").exists()):
        return False
    seed0 = get_settings().layout.seed0 + k * seeds
    weights = (d.raw.get("placement") or {}).get("weights")
    parts, _, _ = placer.place(d, libs, seeds=seeds, iters=iters, weights=weights, seed0=seed0)
    declared = [d.board.get("width_mm"), d.board.get("height_mm")]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"__design_board__": declared, "__board_mm__": declared,
                                **{r: [p.x, p.y, p.r] for r, p in parts.items()}}))
    return True


def eco_path(out: Path) -> Path | None:
    """build/placement.good.json when the incremental placement may start from it (HWFLOW_ECO=0: never)."""
    from ..layout.place import eco
    p = Path(out) / eco.GOOD
    return p if get_settings().layout.eco and p.is_file() else None


def _eco_place(d: Design, libs: Libs, path: Path, declared: list, deadline: float | None):
    """The incremental placement from the last good placement at `path` (layout/place/eco.py), or None to place from
    scratch: no file, a placement made for another declared board size, an edit that touches too many parts, or a
    placement with a violation left. An area-searched board keeps the size the good layout found."""
    from ..layout.place import eco
    good = eco.load_good(path)
    if good is None:
        return None
    if good.get("__design_board__") not in (None, declared):
        print(f"[layout] incremental: the last good layout was made for a {good['__design_board__']} mm board, "
              f"the design now declares {declared}: placing from scratch")
        return None
    size0 = d.board["width_mm"], d.board["height_mm"]
    if d.board.get("optimize_area") and good.get("__board_mm__"):
        d.raw.setdefault("board", {})["width_mm"], d.raw["board"]["height_mm"] = good["__board_mm__"]
    res = eco.place(d, libs, good, deadline=deadline)
    if res is None or res[2]:
        if res is not None:
            print(f"[layout] incremental placement left {len(res[2])} violations ({'; '.join(res[2][:3])}): "
                  "placing from scratch")
        d.raw.setdefault("board", {})["width_mm"], d.raw["board"]["height_mm"] = size0
        return None
    return res


def run_attempt(d: Design, libs: Libs, out: Path, k: int, *, size0, seeds: int, iters: int, passes: int,
                reuse: bool, deadline: float | None = None, workdir: Path | None = None,
                placement: Path | None = None) -> dict:
    """Attempt `k` (0-based) of the layout search in build/auto/a<k+1>/ (or `workdir`: a layout portfolio strategy):
    placer seeds `seed0 + k*seeds`, the saved placement for attempt 1 with `reuse`, or the shared `placement` of the
    portfolio. Attempt 1 without `reuse` starts from the last good layout (eco_path) when there is one. Returns
    {"k", "r", "line"}, or {"k", "error"} when the attempt crashed."""
    import shutil
    _reset_size(d, size0)
    w = Path(workdir) if workdir else out / "auto" / f"a{k + 1}"
    shutil.rmtree(w, ignore_errors=True)
    w.mkdir(parents=True)
    if libs is not None and (out / "fp-lib-table").exists():  # DRC reads the library tables next to the board;
        kicad.write_lib_tables(d, libs, w)  # written again here: project libraries are relative to the tables
    else:
        for f in ("fp-lib-table", "sym-lib-table"):
            if (out / f).exists():
                shutil.copy(out / f, w / f)
    seed0 = get_settings().layout.seed0 + k * seeds  # HWFLOW_SEED0: first placer seed (A/B spread runs)
    use_saved = reuse and k == 0 and (out / "placement.json").exists()
    if use_saved:
        shutil.copy(out / "placement.json", w / "placement.json")
    elif placement:
        shutil.copy(placement, w / "placement.json")
    eco = eco_path(out) if k == 0 and not use_saved and not placement else None
    try:
        r = stage_layout(d, libs, reuse=use_saved, seeds=seeds, iters=iters, passes=passes, seed0=seed0,
                         workdir=w, deadline=deadline, **({"eco": eco} if eco is not None else {}),
                         **({"shared": True} if placement and not use_saved else {}))
    except Exception as e:  # noqa: BLE001 - a router or DRC crash ends this attempt, not the search
        return {"k": k + 1, "error": f"attempt {k + 1}: {type(e).__name__}: {str(e)[:120]}"}
    r["post_warnings"] = _post_warnings(d, w)  # tie-break of layout_auto: post-layout layout checks (no SPICE)
    try:  # per-attempt numbers for the placer experiments (docs/asic-ideas.md); build/auto/attempt<k>.metrics.json
        keep = ("ok", "placement_cost", "placement_violations", "router", "segments", "vias", "drc_errors", "unrouted",
                "repair", "timing", "seconds", "route_groups", "post_warnings", "budget_exhausted")
        m = {key: r.get(key) for key in keep}
        m["track_mm"] = round(sum(v.get("length_mm", 0) for v in (r.get("nets") or {}).values()), 1)
        m["drc_types"] = sorted(x["type"] for x in r.get("drc") or [] if x["sev"] == "error")
        (w / "metrics.json" if workdir else out / "auto" / f"attempt{k + 1}.metrics.json").write_text(
            json.dumps(m, indent=1))
    except Exception:  # noqa: BLE001 - metrics only
        pass
    how = ("saved placement" if use_saved else "incremental from the last good layout" if r.get("eco")
           else f"{'shared ' if placement else ''}placer seeds {seed0}-{seed0 + seeds - 1}")
    line = (f"attempt {k + 1} ({how}): board {r['board_mm'][0]}x{r['board_mm'][1]} mm, {r['unrouted']} unrouted, "
            f"DRC {r['drc_errors']} errors, {len(r['placement_violations'])} placement violations, "
            f"{r['vias']} vias" + (f", {r['repair']['text']}" if r.get("repair") else "")
            + (", route cache" if r.get("route_cache") else "")
            + (", routing budget exhausted; partial candidate retained" if r.get("budget_exhausted") else "")
            + "".join(f", group {g['name']} {g['cache'] if len(g['cache']) < 12 else 're-routed'}" for g in r.get("route_groups") or [])
            + "".join(f", budget {b['name']} {b['value']}/{b['limit']:g} {b['unit']}" + ("" if b["ok"] else " FAIL")
                      for b in r.get("budgets") or []))
    return {"k": k + 1, "r": r, "line": line}


def attempt_summary(res: dict, fixed_all: bool = False) -> dict:
    """What the planner reads of an attempt (hwflow.buildplan.layout_search)."""
    if "error" in res:
        return {"error": res["error"], "fixed_all": fixed_all}
    r = res["r"]
    return {"ok": bool(r.get("ok")), "drc_errors": r.get("drc_errors", 0), "unrouted": r.get("unrouted", 0),
            "fixed_parts_only": bool(r.get("fixed_parts_only")), "fixed_all": fixed_all,
            "budget_exhausted": bool(r.get("budget_exhausted"))}


def all_fixed(d: Design, libs: Libs) -> bool:
    """No movable part: another placer seed gives the same placement."""
    return libs is not None and all(p.fixed for p in placer.build_parts(d, libs).values())


def _auto_pool(n: int):
    import concurrent.futures as cf
    import multiprocessing as mp
    if n <= 1 or "fork" not in mp.get_all_start_methods():
        return cf.ThreadPoolExecutor(1)
    return cf.ProcessPoolExecutor(n, mp_context=mp.get_context("fork"))


def layout_auto(d: Design, libs: Libs, reuse: bool = False, attempts: int = 4, seeds: int = 4, iters: int = 30000,
                passes: int = 30, workers: int | None = None) -> dict:
    """Placement, routing (with net-order retries), area search and DRC in one call (backlog 23, 25, 40), in this
    process: the library form of the layout search. `hwflow build` runs the same search as `layout_attempt` and
    `layout_select` stages; hwflow.buildplan.layout_search makes the decisions for both.

    Attempt 1 keeps the saved placement when `reuse` is set; every later attempt places again with new placer
    seeds. The attempts run in a process pool (`workers`, default cpu count - 1, at most `attempts`), each in its own
    directory build/auto/a<k>/. The result does not depend on completion order: an attempt is final when it has 0 DRC
    errors, 0 unrouted and no placement violations, or when its remaining errors are on fixed parts that no
    placement can move; the winner is the best (not ok, errors + unrouted, placement violations, vias, index) of the
    attempts up to the first final one, or of all of them. Later attempts are not waited for. Each attempt's board
    is kept in build/auto/. A wall-time budget (_layout_budget, HWFLOW_LAYOUT_BUDGET_S) bounds the search: past it no
    attempt starts, running ones are stopped, the repair stops its rounds, and the best attempt done is kept and
    diagnosed. The first attempt uses the same deadline as later attempts. Final DRC/cleanup may run beyond
    the search budget; callers can additionally impose a hard outer timeout. One progress line per attempt
    is printed as it ends."""
    import concurrent.futures as cf
    import shutil

    from .. import buildplan
    try:
        sys.stdout.reconfigure(line_buffering=True)  # progress lines reach a pipe (`timeout ... | tee`) as they come
    except (AttributeError, ValueError):
        pass
    out = _out(d)
    adir = out / "auto"
    if adir.exists():
        shutil.rmtree(adir)
    adir.mkdir()
    t0 = time.time()
    budget = _layout_budget()
    deadline = t0 + budget
    n = max(attempts, 1)
    workers = max(1, min(n, workers if workers else (os.cpu_count() or 2) - 1))
    size0 = (d.raw.get("board") or {}).get("width_mm"), (d.raw.get("board") or {}).get("height_mm")
    _AUTO.clear()
    _AUTO.update(d=d, libs=libs, out=out, size0=size0, seeds=seeds, iters=iters, passes=passes, reuse=reuse,
                 deadline=deadline)
    opts = {"auto": True, "attempts": n}
    lay = {"budget_s": budget, "auto_max_errors": _hopeless_limit()}
    results: dict[int, dict] = {}
    summaries: dict[int, dict] = {}
    pending: dict = {}

    def progress(res):
        print(f"[layout --auto] {res.get('line') or res.get('error')} ({time.time() - t0:.0f} s of {budget:.0f} s)",
              flush=True)
    fixed_all = all_fixed(d, libs)
    from . import portfolio
    if portfolio.on():  # HWFLOW_PORTFOLIO=1: attempt 1 is the strategy portfolio on one placement seed
        results[0] = portfolio.first_attempt(out, d, libs, deadline=deadline, attempt=_auto_attempt,
                                             place=_auto_place, log=lambda m: print(m, flush=True))
        if results[0].get("env"):
            _AUTO["knobs"] = results[0]["env"]  # the fallback attempts carry on with the best strategy's knobs
    else:
        results[0] = _auto_attempt(0)  # attempt 1 alone: when it is final the other attempts are never started
    summaries[1] = attempt_summary(results[0], fixed_all)
    progress(results[0])
    s = buildplan.layout_search(opts, summaries, lay, time.time(), t0)
    workers = max(1, min(workers, s.n - 1))
    pool = _auto_pool(workers) if s.wanted else None
    try:
        while pool is not None:
            s = buildplan.layout_search(opts, summaries, lay, time.time(), t0)
            if not s.wanted:
                break  # every attempt up to the cutoff is done, or the budget is used up
            running = set(pending.values())
            for k in s.wanted:
                if len(pending) >= workers:
                    break
                if k - 1 not in running:
                    pending[pool.submit(_auto_attempt, k - 1)] = k - 1
            if not pending:
                break
            done, _ = cf.wait(pending, timeout=max(0.0, deadline - time.time()), return_when=cf.FIRST_COMPLETED)
            for f in done:
                k = pending.pop(f)
                try:
                    results[k] = f.result()
                except Exception as e:  # noqa: BLE001 - a crashed worker ends this attempt
                    results[k] = {"k": k + 1, "error": f"attempt {k + 1}: {type(e).__name__}: {str(e)[:120]}"}
                summaries[k + 1] = attempt_summary(results[k])
                progress(results[k])
    finally:
        left = [f for f in pending if not f.done()]  # attempts after the first final one, or past the budget
        for f in left:
            f.cancel()
        for proc in (getattr(pool, "_processes", None) or {}).values() if left else ():
            if proc.is_alive():
                proc.terminate()
        if pool is not None:
            pool.shutdown(wait=not left, cancel_futures=True)
    s = buildplan.layout_search(opts, summaries, lay, time.time(), t0)
    return auto_select(d, libs, results, s, workers=workers, t0=t0)


def auto_select(d: Design, libs: Libs, results: dict[int, dict], s, *, workers, t0: float) -> dict:
    """The end of the layout search: rank the attempts the search used (`s`: a buildplan.LayoutSearch), keep the best
    board in build/ and the used boards in build/auto/, say why the search stopped, and diagnose a failed board.
    `results`: 0-based attempt index -> {"k", "r", "line"} or {"k", "error"}; the boards are in build/auto/a<k>/."""
    import shutil
    out = _out(d)
    adir = out / "auto"
    adir.mkdir(exist_ok=True)
    n, budget, over, hopeless = s.n, s.budget, s.over, s.hopeless
    cutoff = None if s.cutoff is None else s.cutoff - 1
    r0 = (results.get(0) or {}).get("r") or {}
    used = [k - 1 for k in s.used]
    log, best = [], None
    for k in used:
        res = results[k]
        if "error" in res:
            log.append(res["error"])
            continue
        log.append(res["line"])
        r = res["r"]
        key = (not r["ok"], bool(r.get("blocked")), r["drc_errors"] + r["unrouted"],
               sum(not b["ok"] for b in r.get("budgets") or []),
               r.get("post_warnings", 0),
               len(r["placement_violations"]), r["vias"], k)
        if best is None or key < best[0]:
            best = (key, k + 1, r)
        for suf in (".kicad_pcb", ".json"):
            f = adir / f"a{k + 1}" / (f"{d.name}.kicad_pcb" if suf == ".kicad_pcb" else "placement.json")
            if f.exists():
                shutil.copy(f, adir / f"attempt{k + 1}{suf}")
    if best is None:
        raise RuntimeError("layout --auto: every attempt failed: " + " | ".join(log))
    _, k, r = best
    w = adir / f"a{k}"
    for f in (f"{d.name}.kicad_pcb", f"{d.name}.drc.json", "placement.json", "krt.log",
              f"{d.name}.kicad_pro", f"{d.name}.kicad_dru"):
        if (w / f).exists():
            shutil.copy(w / f, out / f)
    for x in adir.glob("a[0-9]*"):
        if x.is_dir():
            shutil.rmtree(x, ignore_errors=True)
    d.raw.setdefault("board", {})["width_mm"], d.raw["board"]["height_mm"] = r["board_mm"]
    stopped = None
    if cutoff is not None and (results[cutoff].get("r") or {}).get("placement_verdict"):
        stopped = "stopped: " + results[cutoff]["r"]["placement_verdict"]["msg"] + " (more seeds cannot fix it)"
    elif cutoff is not None and results[cutoff].get("r", {}).get("fixed_parts_only") and not results[cutoff]["r"]["ok"]:
        stopped = ("stopped: remaining errors are on fixed parts: "
                   + ", ".join(results[cutoff]["r"]["fixed_parts_only"]))
    elif hopeless:
        stopped = (f"stopped: attempt 1 has {r0['drc_errors'] + r0['unrouted']} errors (limit {_hopeless_limit()}); "
                   "more attempts will not fix it: a structural change is needed (more layers, fewer or smaller "
                   "parts, a larger board), which is a product decision")
    elif s.fixed_all and not r["ok"]:
        stopped = "all parts fixed: one attempt"
    if over:
        skipped = n - len(used)
        stopped = ((stopped + "; ") if stopped else "") + (
            f"stopped: wall-time budget {budget:g} s (HWFLOW_LAYOUT_BUDGET_S) used up after {len(used)} of {n} "
            f"attempts; {skipped} attempt{'s' if skipped != 1 else ''} skipped or stopped")
    r = dict(r, auto={"attempts": log, "kept": k, "stopped": stopped, "workers": workers},
             seconds=round(time.time() - t0, 1))
    if r0.get("portfolio") and k != 1:  # a fallback attempt won; it ran with the knobs of the best strategy
        r["portfolio"] = dict(r0["portfolio"], fallback={"attempt": k, "knobs_of": r0["portfolio"].get("strategy")},
                              strategy=None)
    if not r["ok"]:
        r["diagnosis"] = layout_diagnosis(d, libs, [results[i]["r"] for i in used if "r" in results[i]],
                                          kept=r.get("failing"))
    return r


def layout_diagnosis(d: Design, libs: Libs, attempts: list[dict], kept: dict | None = None) -> dict:
    """diagnose.diagnose on the kept board in build/ and the failing parts of every attempt."""
    from ..layout import diagnose
    pvs = [a["placement_verdict"] for a in attempts if a.get("placement_verdict")]
    # a verdict that names a design cause (over-full, fixed parts, an infeasible part) before a placer failure
    pv = next((v for v in pvs if v.get("back_to") != "layout"), pvs[0] if pvs else None)
    if pv and all(a.get("placement_verdict") for a in attempts):  # no attempt got past the placement check
        g = diagnose.placement_diagnosis(d, pv, len(attempts))
        if g["back_to"] == "layout" and len(attempts) > 1:
            # the area allows a legal placement and no constraint of the violating parts is infeasible
            # (legality.placement_verdict): a placer failure is an hwflow defect, never a design blockage
            g = dict(g, reasons=g["reasons"] + [f"no legal placement in {len(attempts)} attempts although one should "
                                             "exist: a placer failure (an hwflow defect, not a design blockage); "
                                             "the design needs no change for it: report it"])
        return g
    out = _out(d)
    if libs is None or not (out / f"{d.name}.kicad_pcb").exists():
        return {}  # no board to read (an attempt that crashed): no diagnosis
    board = pcb.read_board(out / f"{d.name}.kicad_pcb")
    parts = placer.build_parts(d, libs)
    for ref, fp in board["footprints"].items():
        if ref in parts:
            parts[ref].x, parts[ref].y, parts[ref].r = fp["x"], fp["y"], fp["r"]
    return diagnose.diagnose(d, parts, [{"failing": a.get("failing") or {}} for a in attempts], board, out, libs,
                             kept=kept)


def _layout_blocked(d: Design, retry: bool = False) -> dict | None:
    """The layout stage result when the layout diagnosis blocks this build (see diagnose.blocked), else None."""
    from ..layout import diagnose
    e = diagnose.blocked(d, _out(d))
    if e is None or retry:
        return None
    prev = dict(_status(d).get("layout") or {})
    msg = ("blocked: the last layout build of these same parts, nets and board was sent back to "
           f"{e['diagnosis'].get('stage')}; change the design there (or run with --retry-layout)")
    print(f"[layout] {msg}")
    keep = {"router": "-", "segments": 0, "vias": 0, "drc_errors": 0, "drc_warnings": 0, "unrouted": 0, "drc": [],
            "placement_violations": [], "board_mm": None, "isolation": [], "budgets": []}
    return {**{k: prev.get(k) or v for k, v in keep.items()},  # the numbers of the build that was sent back
            "ok": False, "blocked": msg, "diagnosis": e["diagnosis"]}


def _record_layout(d: Design, r: dict, override: bool):
    from ..layout import diagnose
    g = r.get("diagnosis") or {}
    if r.get("ok"):  # the placement of a clean layout: later small edits start from it (layout/place/eco.py)
        from ..layout.place import eco
        eco.save_good(_out(d), d, None)
    diagnose.record(_out(d), {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "structure": diagnose.structure_key(d),
                              "constraints": diagnose.constraints_key(d),
                              "ok": bool(r.get("ok")), "unrouted": r.get("unrouted"), "drc_errors": r.get("drc_errors"),
                              "persistent": g.get("persistent") or [], "blocked": bool(g.get("blocked")),
                              "override": override, "diagnosis": g})


def _layout_budget() -> float:
    """Wall time in seconds `layout --auto` may use for its attempts (HWFLOW_LAYOUT_BUDGET_S, default 3000: inside the
    3600 s an agent's `timeout` gives the call, with time left for the diagnosis, status.json and REPORT.md)."""
    return get_settings().layout.budget_s


def _hopeless_limit() -> int:
    """Errors + unrouted of attempt 1 above which `--auto` starts no other attempt (HWFLOW_AUTO_MAX_ERRORS, default the
    repair limit: a board that repair skips is not one that a new seed fixes)."""
    return get_settings().layout.auto_max_errors


def _auto_summary(r: dict) -> str:
    a = r.get("auto") or {}
    return (f"[layout --auto] {'OK' if r['ok'] else 'FAIL'}: kept attempt {a.get('kept')} of {len(a.get('attempts', []))} "
            f"in {r['seconds']} s{' (' + a['stopped'] + ')' if a.get('stopped') else ''}; board {r['board_mm'][0]}x{r['board_mm'][1]} mm, {r['unrouted']} unrouted, "
            f"DRC {r['drc_errors']} errors / {r['drc_warnings']} warnings, {r['vias']} vias, {r['segments']} segments")


def _saved_board(pj: Path):
    """The board size the design declared when `placement.json` was written (None in files written before it was
    stored). `--reuse` ignores a placement made for another declared size."""
    try:
        return json.loads(pj.read_text()).get("__design_board__")
    except (OSError, ValueError):
        return None


def _apply_saved_size(d: Design, pj: Path):
    """--reuse: the board size the saved placement was made on (`__board_mm__`; older files: the last layout result
    in status.json)."""
    try:
        size = json.loads(pj.read_text()).get("__board_mm__")
    except (OSError, ValueError):
        size = None
    if size:
        d.raw.setdefault("board", {})["width_mm"], d.raw["board"]["height_mm"] = size
    else:
        _apply_layout_size(d)


def _apply_layout_size(d: Design):
    lay = _status(d).get("layout") or {}
    if lay.get("board_mm"):
        d.raw.setdefault("board", {})["width_mm"], d.raw["board"]["height_mm"] = lay["board_mm"]


def stage_post(d: Design) -> dict:
    _apply_layout_size(d)
    out = _out(d)
    board = out / f"{d.name}.kicad_pcb"
    t = time.time()
    ex = postlayout.Extraction(d, board)
    checks = postlayout.layout_checks(d, ex)
    geometry = geomchecks.evaluate(d, board)   # layout_checks: in design.yaml, measured on the built board
    checks += [{"sev": "error", "code": "layout-check", "msg": f"{g['name']}: {g['detail']}"} for g in geometry if not g["ok"]]
    w = out / "post"
    ir = postlayout.ir_drop(d, ex, w)
    nm = postlayout.PostLayoutNodeMap(d, ex)
    tm = {"extract_s": round(time.time() - t, 1)}  # extraction, layout checks, IR drop
    t = time.time()
    pre = _fresh_pre_results(d)
    if pre is None:
        pre = spice.run_all(d, out / "sim", cache=out / "simcache")
    post = spice.run_all(d, w, nm, tag="_post", cache=out / "simcache")
    tm["post_sim_s"] = round(time.time() - t, 1)
    deltas = []
    for a, b in zip(pre, post):
        for r1, r2 in zip(a["rows"], b["rows"]):
            if r1.get("debug"):
                continue
            v1, v2 = r1["value"], r2["value"]
            rel = None if v1 in (None, 0) or v2 is None else (v2 - v1) / abs(v1)
            x = {"analysis": a["name"], "name": r1["name"], "pre": v1, "post": v2, "rel": rel,
                 "pass_pre": r1["pass"], "pass_post": r2["pass"], "unit": r1["unit"]}
            if v2 is None:
                x["na_reason"] = r2.get("na_reason", "")
            deltas.append(x)
    errs = [c for c in checks if c["sev"] == "error"]
    regress = [x for x in deltas if x["pass_pre"] and not x["pass_post"]]
    return {"ok": not errs and not regress and all(x["pass_post"] for x in deltas), "checks": checks,
            "ir_drop": ir, "deltas": deltas, "regressions": regress, "metrics": postlayout.board_metrics(d, ex),
            "geometry": geometry, "runs": _run_summary(post), "scope": _post_scope(d, nm), "lump_check": _lump_check(d, ex, w, post),
            "distances": {"decap": postlayout.decap_paths(d, ex), "clearance": postlayout.group_clearances(d, ex)},
            "timing": tm}


def _post_scope(d: Design, nm) -> str:
    b = d.board
    if b.get("post_tran_l"):
        return "transients use the full extracted R, L, C network"
    if not b.get("post_tran_lump", True):
        return "transients use the extracted trace R and C (no L)"
    fast = sorted(nm.fast_nets())
    return ("transients: trace R reduced to the pads, no trace C or L" +
            (f" (full R and C kept for fast nets {', '.join(fast)})" if fast else "") +
            "; they check resistive drops and slow dynamics, not reflections, ringing, crosstalk or RC delay "
            "along a trace; AC and noise use the full R, L, C network")


def _lump_check(d: Design, ex, w: Path, post: list[dict]) -> dict | None:
    """board.post_lump_check: <analysis>: run that transient again with the full R and C network and compare."""
    name = d.board.get("post_lump_check")
    if not name or not d.board.get("post_tran_lump", True) or d.board.get("post_tran_l"):
        return None
    a = next((x for x in (d.raw.get("sim") or {}).get("analyses") or [] if x["name"] == name), None)
    temp = (a.get("temps") or [None])[0] if a else None
    label = name + (f" @ {temp}C" if temp is not None else "")
    lumped = next((x for x in post if x["name"] == label), None)
    if a is None or lumped is None:
        return {"analysis": name, "error": "no such analysis"}
    full = spice.run_analysis(d, a, w, postlayout.PostLayoutNodeMap(d, ex, lump=False), temp=temp,
                              tag="_post_full" + (f"_T{temp}" if temp is not None else ""),
                              cache=w.parent / "simcache")
    worst, rows = 0.0, []
    for r1, r2 in zip(lumped["rows"], full["rows"]):
        if r1["value"] is None or r2["value"] is None:
            continue
        rel = abs(r1["value"] - r2["value"]) / max(abs(r2["value"]), 1e-12)
        rows.append({"name": r1["name"], "lumped": r1["value"], "full": r2["value"], "rel": rel})
        worst = max(worst, rel)
    return {"analysis": label, "status": full["status"], "seconds_full": full["seconds"],
            "seconds_lumped": lumped.get("seconds"), "max_rel": worst, "rows": rows}


def _fresh_pre_results(d: Design) -> list[dict] | None:
    """Pre-layout results of this exact design from status.json, so post does not rerun them."""
    prev = _status(d).get("sim") or {}
    an = prev.get("analyses")
    if not an or prev.get("design_hash") != _design_hash(d):
        return None
    sim = d.raw.get("sim") or {}
    want = [a["name"] + (f" @ {t}C" if t is not None else "") for a in sim.get("analyses") or []
            for t in (a.get("temps") or [None])]
    return an if [a["name"] for a in an] == want else None


def stage_fab(d: Design) -> dict:
    out = _out(d)
    fab = out / "fab"
    msgs = kicad.export_fab(out / f"{d.name}.kicad_pcb", out / f"{d.name}.kicad_sch", fab)
    sides = {"bottom": [], "tht": []}
    if (fab / "positions.csv").exists():  # mirrored back-face footprints: bottom side, KiCad's flipped rotation
        sides = kicad.fix_back_positions(out / f"{d.name}.kicad_pcb", fab / "positions.csv", fab / "cpl.csv")
    with open(fab / "bom.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Comment", "Designator", "Footprint", "MPN", "LCSC", "Qty", "UnitPriceUSD"])
        for r in d.bom():
            w.writerow([r["value"], r["refs"], (r["footprint"] or "").split(":")[-1], r["mpn"] or "",
                        r["lcsc"] or "", r["qty"], r["unit_price_usd"] or ""])
    return {"ok": all(m.endswith("ok") for m in msgs), "steps": msgs, "bottom": sides["bottom"], "tht": sides["tht"]}
