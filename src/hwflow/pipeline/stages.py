"""The pipeline stages: check, sch, sim, layout, post, fab."""

from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
from pathlib import Path

from .. import budgets, kicad, pcb, placer, postlayout, repair, seedroute, simrobust, spice
from ..design import Design, check
from ..libs import Libs
from ..settings import get_settings
from .hooks import registry
from .status import _block_files, _design_hash, _inputs_hash, _out, _status


def stage_check(d: Design, libs: Libs) -> dict:
    reg = registry()
    f = check(d, libs)
    for h in reg.check_findings:
        f += h(d)
    errs = [x for x in f if x["sev"] == "error"]
    warns = [x for x in f if x["sev"] == "warn"]
    res = {"ok": not errs, "errors": len(errs), "warnings": len(warns), "findings": f}
    for s in reg.check_state:
        res.update(s(d, f))
    return res


def stage_sch(d: Design, libs: Libs) -> dict:
    out = _out(d)
    kicad.write_lib_tables(d, libs, out)
    pcb.write_project(out, d.name, d)
    from .. import sch
    sch.write_schematic(d, libs, out / f"{d.name}.kicad_sch")
    v = kicad.erc(out / f"{d.name}.kicad_sch")
    errs = [x for x in v if x["sev"] == "error"]
    return {"ok": not errs, "errors": len(errs), "warnings": len(v) - len(errs), "violations": v[:40]}


def stage_sim(d: Design, mc: int = 0) -> dict:
    w = _out(d) / "sim"
    res = spice.run_all(d, w, cache=_out(d) / "simcache")
    allrows = [dict(r, analysis=a["name"]) for a in res for r in a["rows"]]
    rows = [r for r in allrows if not r.get("debug")]  # dbg_* probes never count (backlog 19)
    der = spice.derating_audit(d, w)
    out = {"analyses": res, "passed": sum(r["pass"] for r in rows), "total": len(rows),
           "fails": [r for r in rows if not r["pass"]], "derating": der,
           "debug": [r for r in allrows if r.get("debug")],
           "runs": _run_summary(res),
           "derating_issues": [f"{r['ref']}: {i}" for r in der for i in r["issues"]],
           "unmodeled": sorted({u for a in res for u in a["unmodeled"]}),
           "generic_models": sorted(c.ref for c in d.components.values() if spice.uses_generic_model(c)),
           "errors": [f"{a['name']}: {e}" for a in res for e in a["errors"]],
           "warnings": [f"{a['name']}: {e}" for a in res for e in a.get("warnings", [])]}
    out.update(simrobust.sim_summary(d, res, w, _out(d) / "simcache"))
    prev = _status(d).get("sim") or {}
    if mc and prev.get("monte_carlo") and prev.get("inputs_hash") == _inputs_hash(d) and prev.get("mc_runs") == mc:
        out["monte_carlo"], out["mc_runs"] = prev["monte_carlo"], mc  # Monte Carlo once per design (backlog 27)
        print(f"[sim] Monte Carlo {mc} runs reused: inputs unchanged since the last run")
    elif mc:
        out["monte_carlo"], out["mc_runs"] = spice.monte_carlo(d, w, runs=mc), mc
    else:  # keep Monte Carlo results of this exact design (a --reuse or plain rebuild must not erase them)
        if prev.get("monte_carlo") and prev.get("design_hash") == _design_hash(d):
            out["monte_carlo"] = prev["monte_carlo"]
            if prev.get("mc_runs"):
                out["mc_runs"] = prev["mc_runs"]
    out["ok"] = out["passed"] == out["total"] and not out["errors"]
    return out


def _run_summary(res: list[dict]) -> list[dict]:
    """Analyses that did not finish cleanly (timeout, crash, aborted, error) or needed a retry, with run time."""
    return [{k: a[k] for k in ("name", "status", "seconds", "reason", "flaky") if k in a}
            for a in res if a.get("status", "ok") != "ok" or a.get("flaky")]


def _route_once(d: Design, libs: Libs, parts, passes: int, out: Path | None = None) -> tuple[list, list, str, int]:
    """Route the board. With `d.route_groups` a list (the layout stage sets it), the nets route group by group
    (regionroute) when they form more than one group, each group's copper cached in build/routecache/groups/; the
    group rows are appended to that list."""
    out = out or _out(d)
    name = d.name
    groups = getattr(d, "route_groups", None)
    if d.board.get("router", "krt") == "krt":
        if (pcb.krt_dir() / "py_router" / "route.py").exists():
            if groups is not None:
                from .. import regionroute
                res = regionroute.route(d, libs, parts, out, _out(d) / "routecache")
                if res is not None:
                    groups += res[4]
                    return res[:4]
            return pcb.run_krt(d, libs, parts, out)
        print("KiCadRoutingTools not installed; falling back to Freerouting (run scripts/setup.sh)")
    pcb.export_dsn(d, libs, parts, out / f"{name}.dsn")
    log = pcb.run_freerouting(out / f"{name}.dsn", out / f"{name}.ses", passes=passes)
    tracks, vias = pcb.import_ses(out / f"{name}.ses", d)
    import re as _re
    done = [ln for ln in log.splitlines() if "Auto-routing stage completed" in ln]
    m = _re.search(r"\((\d+) unrouted and (\d+) violations\)", done[0] if done else "")
    unrouted = int(m.group(1)) if m else 999
    summary = f"{m.group(1)} unrouted, {m.group(2)} violations" if m else "unknown"
    return tracks, vias, summary, unrouted


def _minimize_area(d: Design, libs: Libs, seeds: int, iters: int, passes: int, weights, seed0: int = 1,
                   out: Path | None = None):
    """Shrink the board (keeping the requested aspect ratio) until placement or routing fails."""
    b = d.raw.setdefault("board", {})
    W0, H0 = float(d.board["width_mm"]), float(d.board["height_mm"])
    probe = placer.build_parts(d, libs)
    parts_area = sum(p.w * p.h for p in probe.values())
    m = d.board["margin_mm"]
    best, tried = None, []
    for factor in (3.0, 2.4, 2.0, 1.7, 1.5, 1.35, 1.25):
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
        tracks, vias, summary, unrouted = _route_once(d, libs, parts, passes, out)
        if unrouted:
            tried.append(f"{W}x{H}: {unrouted} unrouted")
            break
        tried.append(f"{W}x{H}: ok")
        best = (W, H, parts, cost, viol, tracks, vias, summary)
    if best is None:
        b["width_mm"], b["height_mm"] = W0, H0
        return None, tried
    b["width_mm"], b["height_mm"] = best[0], best[1]
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
    h.update(json.dumps([d.board, passes, pcb.KRT_VARIANTS, pcb.router_version(d)], sort_keys=True, default=str).encode())
    h.update((Path(repair.__file__)).read_bytes())
    h.update((Path(pcb.__file__)).read_bytes())  # how hwflow drives the router (passes, arguments, env defaults)
    h.update((Path(pcb.__file__).with_name("routing.py")).read_bytes())  # isolation keep-outs, neck-down widening
    h.update((Path(pcb.__file__).with_name("seedroute.py")).read_bytes())  # seed routes of nets the router leaves open
    for f in ("corridor.py", "barrier.py", "pinaccess.py", "asicflow.py"):  # which nets route narrow first
        h.update((Path(pcb.__file__).with_name(f)).read_bytes())
    from .. import asicflow
    h.update(asicflow.flow(d).encode())  # the layout flow (HWFLOW_FLOW or placement.flow): pin access necked or not
    h.update((Path(pcb.__file__).with_name("regionroute.py")).read_bytes())  # route groups and their cache
    h.update(json.dumps(d.raw.get("floorplan"), sort_keys=True, default=str).encode())  # conductive keep-outs, regions
    h.update(json.dumps(pcb.net_min_widths(d), sort_keys=True).encode())  # nets.<name>.min_width_mm: no neck under it
    if d.raw.get("blocks"):  # layout blocks: the nets their copper closes are not routed
        h.update((Path(pcb.__file__).with_name("blocks.py")).read_bytes())
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
                 passes: int = 30, seed0: int = 1, workdir: Path | None = None, deadline: float | None = None) -> dict:
    """Place, route, repair and check. `workdir` (default build/) holds this call's files; the route cache
    (build/routecache/<key>.json) is shared. A cache hit skips the router and the repair; the full DRC still runs.
    `deadline` (time.time()) stops the repair rounds when the layout wall-time budget is used up."""
    out = workdir or _out(d)
    cache = _out(d) / "routecache"
    pj = out / "placement.json"
    t0 = time.time()
    weights = (d.raw.get("placement") or {}).get("weights")
    declared = [d.board.get("width_mm"), d.board.get("height_mm")]  # before a saved layout size is applied
    area_log = None
    pre = None
    if d.board.get("optimize_area") and not reuse:
        pre, area_log = _minimize_area(d, libs, seeds, iters, passes, weights, seed0, out)
    if pre:
        _, _, parts, cost, viol, tracks, vias, summary = pre
    elif reuse and pj.exists() and _saved_board(pj) in (None, declared):
        _apply_layout_size(d)
        parts = placer.build_parts(d, libs)
        saved = json.loads(pj.read_text())
        for r, p in parts.items():
            if r in saved and not p.fixed:  # a fixed part keeps the position the design gives it now
                p.x, p.y, p.r = saved[r]
        pl = placer.Placer(d, parts, d.board["width_mm"], d.board["height_mm"])
        pl.legalize()
        viol, cost = pl.violations(), pl.cost()
    else:
        parts, cost, viol = placer.place(d, libs, seeds=seeds, iters=iters, weights=weights, seed0=seed0)
    tm = {"place_s": round(time.time() - t0, 1)}  # wall time per step (experiment metrics, docs/asic-ideas.md)
    pj.write_text(json.dumps({"__design_board__": list(declared), **{r: [p.x, p.y, p.r] for r, p in parts.items()}},
                             indent=1))
    d.budget_widths, bud_w = budgets.widths(d, parts)  # requirement budgets: track widths on this placement
    for r in bud_w:
        print(f"[layout] budget {r['name']}: {r['msg']}")
    name = d.name
    board = out / f"{name}.kicad_pcb"
    corridors = placer.Placer(d, parts, d.board["width_mm"], d.board["height_mm"]).corridors()
    for c in corridors:
        print(f"[layout] corridor {c['msg']}")
    blocks = [v for v in viol if v.startswith("corridor ")]
    if blocks:  # a channel the fixed parts close: no router run can join the net, so none is started
        msg = "not routed: " + "; ".join(blocks)
        print(f"[layout] {msg}")
        pcb.write_project(out, name, d)
        pcb.write_board(board, pcb.build_board(d, libs, parts, [], []))
        return {"failing": {}, "ok": False, "blocked": msg, "isolation": [], "off_layer": [], "too_narrow": [], "conductive_isolation": [],
                "budget_widths": bud_w, "budgets": [], "board_mm": [d.board["width_mm"], d.board["height_mm"]],
                "area_search": area_log, "placement_cost": round(cost, 1), "placement_violations": viol,
                "corridors": corridors, "router": "-", "segments": 0, "vias": 0, "drc_errors": 0, "drc_warnings": 0,
                "unrouted": 0, "drc": [], "repair": None, "route_cache": None, "fixed_parts_only": blocks,
                "seconds": round(time.time() - t0, 1)}

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
    rep, island, groups = None, 0, []
    if hit:
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
                tracks, vias, summary, _ = _route_once(d, libs, parts, passes, out)
            finally:
                d.route_groups = None
                tm["route_s"] = round(time.time() - t, 1)
        seeds = seedroute.load(out)  # the seed copper of this route (pcb.krt_route)
        drc = route_check(tracks, vias)
        if any(g["cache"] == "hit" for g in groups):  # reused group copper: the joined board's DRC judges it
            from .. import regionroute
            tracks, vias, drc = regionroute.validate(d, libs, parts, out, _out(d) / "routecache", tracks, vias, drc,
                                                     groups, route_check)
        rounds = int(d.board.get("repair_rounds", 3))
        if (any(x["sev"] == "error" for x in drc) and rounds > 0 and d.board.get("router", "krt") == "krt"
                and (pcb.krt_dir() / "py_router" / "route.py").exists()):
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
        _cache_save(cache, key, {"tracks": tracks, "vias": vias, "summary": summary, "island": island, "rep": rep,
                                 "seeds": seeds, "groups": groups})
    if seeds:  # the final DRC judges the seed copper: an error that touches it removes it
        tr, vi, dropped = seedroute.drop_on_drc(seeds, tracks, vias, drc)
        if dropped:
            tracks, vias = tr, vi
            drc = route_check(tracks, vias, island)
            summary += f"; seed route {', '.join(dropped)} removed (a DRC error touches it)"
            print(f"[layout] seed route {', '.join(dropped)}: removed, a DRC error touches it")
    from ..prune import prune
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
    for x in too_narrow:
        print(f"[layout] track under its net's min_width_mm: {x}")
    nstats = net_stats(tracks, vias, pcb.net_classes(d))
    iso = isolation_distances(d, board, drc)
    # mains copper (tracks, vias, pads) near a conductive keep-out: the router keeps it out, this makes sure
    conductive = postlayout.conductive_clearances(d, pcb.read_board(board))
    for x in conductive:
        print(f"[layout] conductive isolation: {x['net']} {x['item']} {x['mm']:.2f} mm from {x['keepout']} "
              f"(need {x['need']:g} mm)")
    bud = budgets.measure(d, postlayout.Extraction(d, board)) if budgets.budgets(d) else []
    from .. import diagnose
    failing = diagnose.failing_parts(drc, pcb.read_board(board))  # the parts each error touches (layout diagnosis)
    return {"failing": failing, "ok": not errs and not viol and not off_layer and not too_narrow and not conductive
            and all(r["ok"] for r in iso) and all(r["ok"] for r in bud),
            "isolation": iso, "off_layer": off_layer, "too_narrow": too_narrow, "conductive_isolation": conductive,
            "nets": nstats,
            "budget_widths": bud_w, "budgets": bud,
            "board_mm": [d.board["width_mm"], d.board["height_mm"]],
            "area_search": area_log, "placement_cost": round(cost, 1), "placement_violations": viol,
            "corridors": corridors, "router": summary, "segments": len(tracks), "vias": len(vias), "drc_errors": len(errs),
            "drc_warnings": len(drc) - len(errs), "unrouted": len(unrouted), "drc": drc[:40], "repair": rep,
            "route_cache": key if hit else None, "fixed_parts_only": repair.fixed_part_errors(drc, parts),
            "route_groups": [{k: g[k] for k in ("name", "kind", "nets", "cache", "seconds", "unrouted", "open")}
                             for g in groups],
            "timing": tm, "seconds": round(time.time() - t0, 1)}


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


def _auto_attempt(k: int) -> dict:
    """Attempt `k` (0-based) of layout_auto in its own work directory build/auto/a<k+1>/. Runs in a worker process,
    so the design it mutates (board size) is that worker's own copy."""
    import shutil
    c = _AUTO
    d, libs, out = c["d"], c["libs"], c["out"]
    b = d.raw.setdefault("board", {})
    for key, v in zip(("width_mm", "height_mm"), c["size0"]):  # every attempt starts from the requested outline
        if v is None:
            b.pop(key, None)
        else:
            b[key] = v
    w = out / "auto" / f"a{k + 1}"
    shutil.rmtree(w, ignore_errors=True)
    w.mkdir(parents=True)
    for f in ("fp-lib-table", "sym-lib-table"):  # DRC reads the library tables next to the board
        if (out / f).exists():
            shutil.copy(out / f, w / f)
    seeds = c["seeds"]
    seed0 = get_settings().layout.seed0 + k * seeds  # HWFLOW_SEED0: first placer seed (A/B spread runs)
    use_saved = c["reuse"] and k == 0 and (out / "placement.json").exists()
    if use_saved:
        shutil.copy(out / "placement.json", w / "placement.json")
    try:
        r = stage_layout(d, libs, reuse=use_saved, seeds=seeds, iters=c["iters"], passes=c["passes"], seed0=seed0,
                         workdir=w, deadline=c.get("deadline"))
    except Exception as e:  # noqa: BLE001 - a router or DRC crash ends this attempt, not the search
        return {"k": k + 1, "error": f"attempt {k + 1}: {type(e).__name__}: {str(e)[:120]}"}
    r["post_warnings"] = _post_warnings(d, w)  # tie-break of layout_auto: post-layout layout checks (no SPICE)
    try:  # per-attempt numbers for the placer experiments (docs/asic-ideas.md); build/auto/attempt<k>.metrics.json
        keep = ("ok", "placement_cost", "placement_violations", "router", "segments", "vias", "drc_errors", "unrouted",
                "repair", "timing", "seconds", "route_groups", "post_warnings")
        m = {key: r.get(key) for key in keep}
        m["track_mm"] = round(sum(v.get("length_mm", 0) for v in (r.get("nets") or {}).values()), 1)
        m["drc_types"] = sorted(x["type"] for x in r.get("drc") or [] if x["sev"] == "error")
        (out / "auto" / f"attempt{k + 1}.metrics.json").write_text(json.dumps(m, indent=1))
    except Exception:  # noqa: BLE001 - metrics only
        pass
    how = "saved placement" if use_saved else f"placer seeds {seed0}-{seed0 + seeds - 1}"
    line = (f"attempt {k + 1} ({how}): board {r['board_mm'][0]}x{r['board_mm'][1]} mm, {r['unrouted']} unrouted, "
            f"DRC {r['drc_errors']} errors, {len(r['placement_violations'])} placement violations, "
            f"{r['vias']} vias" + (f", {r['repair']['text']}" if r.get("repair") else "")
            + (", route cache" if r.get("route_cache") else "")
            + "".join(f", group {g['name']} {g['cache'] if len(g['cache']) < 12 else 're-routed'}" for g in r.get("route_groups") or [])
            + "".join(f", budget {b['name']} {b['value']}/{b['limit']:g} {b['unit']}" + ("" if b["ok"] else " FAIL")
                      for b in r.get("budgets") or []))
    return {"k": k + 1, "r": r, "line": line}


def _auto_final(res: dict) -> bool:
    """An attempt after which more attempts cannot help: it is clean, or its errors are on fixed parts."""
    r = res.get("r")
    return bool(r and (r["ok"] or r.get("fixed_parts_only")))


def _auto_pool(n: int):
    import concurrent.futures as cf
    import multiprocessing as mp
    if n <= 1 or "fork" not in mp.get_all_start_methods():
        return cf.ThreadPoolExecutor(1)
    return cf.ProcessPoolExecutor(n, mp_context=mp.get_context("fork"))


def layout_auto(d: Design, libs: Libs, reuse: bool = False, attempts: int = 4, seeds: int = 4, iters: int = 30000,
                passes: int = 30, workers: int | None = None) -> dict:
    """Placement, routing (with net-order retries), area search and DRC in one call (backlog 23, 25, 40).

    Attempt 1 keeps the saved placement when `reuse` is set; every later attempt places again with new placer
    seeds. The attempts run in a process pool (`workers`, default cpu count - 1, at most `attempts`), each in its own
    directory build/auto/a<k>/. The result does not depend on completion order: an attempt is final when it has 0 DRC
    errors, 0 unrouted and no placement violations, or when its remaining errors are on fixed parts that no
    placement can move; the winner is the best (not ok, errors + unrouted, placement violations, vias, index) of the
    attempts up to the first final one, or of all of them. Later attempts are not waited for. Each attempt's board
    is kept in build/auto/. A wall-time budget (_layout_budget, HWFLOW_LAYOUT_BUDGET_S) bounds the search: past it no
    attempt starts, running ones are stopped, the repair stops its rounds, and the best attempt done is kept and
    diagnosed. One progress line per attempt is printed as it ends."""
    import concurrent.futures as cf
    import shutil
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
    results: dict[int, dict] = {}
    pending: dict = {}

    def progress(res):
        print(f"[layout --auto] {res.get('line') or res.get('error')} ({time.time() - t0:.0f} s of {budget:.0f} s)",
              flush=True)
    fixed_all = libs is not None and all(p.fixed for p in placer.build_parts(d, libs).values())
    if fixed_all:
        n = 1  # no movable part: another seed gives the same placement
    results[0] = _auto_attempt(0)  # attempt 1 alone: when it is final the other attempts are never started
    progress(results[0])
    cutoff = 0 if _auto_final(results[0]) else None
    r0 = results[0].get("r") or {}
    hopeless = cutoff is None and r0 and r0["drc_errors"] + r0["unrouted"] > _hopeless_limit()
    if hopeless:
        n = 1  # far from routable: other seeds of the same placer and router do not close the gap (thermostat 3: 121)
    over = n > 1 and cutoff is None and time.time() >= deadline  # the budget is used up: the attempts left are skipped
    nxt = 1
    workers = max(1, min(workers, n - 1))
    pool = _auto_pool(workers) if n > 1 and cutoff is None and not over else None
    try:
        while pool is not None:
            while nxt < n and len(pending) < workers and (cutoff is None or nxt <= cutoff) and time.time() < deadline:
                pending[pool.submit(_auto_attempt, nxt)] = nxt
                nxt += 1
            if all(k in results for k in range(n if cutoff is None else cutoff + 1)):
                break
            if not pending:  # nothing running and nothing may start: the budget is used up
                over = True
                break
            done, _ = cf.wait(pending, timeout=max(0.0, deadline - time.time()), return_when=cf.FIRST_COMPLETED)
            for f in done:
                k = pending.pop(f)
                try:
                    results[k] = f.result()
                except Exception as e:  # noqa: BLE001 - a crashed worker ends this attempt
                    results[k] = {"k": k + 1, "error": f"attempt {k + 1}: {type(e).__name__}: {str(e)[:120]}"}
                progress(results[k])
            finals = [k for k, res in results.items() if _auto_final(res)]
            cutoff = min(finals) if finals else None
            if not done and time.time() >= deadline:  # the running attempts are stopped (finally below)
                over = True
                break
    finally:
        left = [f for f in pending if not f.done()]  # attempts after the first final one, or past the budget
        for f in left:
            f.cancel()
        for proc in (getattr(pool, "_processes", None) or {}).values() if left else ():
            if proc.is_alive():
                proc.terminate()
        if pool is not None:
            pool.shutdown(wait=not left, cancel_futures=True)
    used = sorted(k for k in results if cutoff is None or k <= cutoff)
    log, best = [], None
    for k in used:
        res = results[k]
        if "error" in res:
            log.append(res["error"])
            continue
        log.append(res["line"])
        r = res["r"]
        key = (not r["ok"], r["drc_errors"] + r["unrouted"], sum(not b["ok"] for b in r.get("budgets") or []),
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
    if cutoff is not None and results[cutoff].get("r", {}).get("fixed_parts_only") and not results[cutoff]["r"]["ok"]:
        stopped = ("stopped: remaining errors are on fixed parts: "
                   + ", ".join(results[cutoff]["r"]["fixed_parts_only"]))
    elif hopeless:
        stopped = (f"stopped: attempt 1 has {r0['drc_errors'] + r0['unrouted']} errors (limit {_hopeless_limit()}); "
                   "more attempts will not fix it: a structural change is needed (more layers, fewer or smaller "
                   "parts, a larger board), which is a product decision")
    elif fixed_all and not r["ok"]:
        stopped = "all parts fixed: one attempt"
    if over:
        skipped = n - len(used)
        stopped = ((stopped + "; ") if stopped else "") + (
            f"stopped: wall-time budget {budget:g} s (HWFLOW_LAYOUT_BUDGET_S) used up after {len(used)} of {n} "
            f"attempts; {skipped} attempt{'s' if skipped != 1 else ''} skipped or stopped")
    r = dict(r, auto={"attempts": log, "kept": k, "stopped": stopped, "workers": workers},
             seconds=round(time.time() - t0, 1))
    if not r["ok"]:
        r["diagnosis"] = layout_diagnosis(d, libs, [results[i]["r"] for i in used if "r" in results[i]])
    return r


def layout_diagnosis(d: Design, libs: Libs, attempts: list[dict]) -> dict:
    """diagnose.diagnose on the kept board in build/ and the failing parts of every attempt."""
    from .. import diagnose
    out = _out(d)
    if libs is None or not (out / f"{d.name}.kicad_pcb").exists():
        return {}  # no board to read (an attempt that crashed): no diagnosis
    board = pcb.read_board(out / f"{d.name}.kicad_pcb")
    parts = placer.build_parts(d, libs)
    for ref, fp in board["footprints"].items():
        if ref in parts:
            parts[ref].x, parts[ref].y, parts[ref].r = fp["x"], fp["y"], fp["r"]
    return diagnose.diagnose(d, parts, [{"failing": a.get("failing") or {}} for a in attempts], board, out, libs)


def _layout_blocked(d: Design, retry: bool = False) -> dict | None:
    """The layout stage result when the layout diagnosis blocks this build (see diagnose.blocked), else None."""
    from .. import diagnose
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
    from .. import diagnose
    g = r.get("diagnosis") or {}
    diagnose.record(_out(d), {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "structure": diagnose.structure_key(d),
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


def _apply_layout_size(d: Design):
    lay = _status(d).get("layout") or {}
    if lay.get("board_mm"):
        d.raw.setdefault("board", {})["width_mm"], d.raw["board"]["height_mm"] = lay["board_mm"]


def stage_post(d: Design) -> dict:
    _apply_layout_size(d)
    out = _out(d)
    board = out / f"{d.name}.kicad_pcb"
    ex = postlayout.Extraction(d, board)
    checks = postlayout.layout_checks(d, ex)
    w = out / "post"
    ir = postlayout.ir_drop(d, ex, w)
    nm = postlayout.PostLayoutNodeMap(d, ex)
    pre = _fresh_pre_results(d)
    if pre is None:
        pre = spice.run_all(d, out / "sim", cache=out / "simcache")
    post = spice.run_all(d, w, nm, tag="_post", cache=out / "simcache")
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
            "runs": _run_summary(post), "scope": _post_scope(d, nm), "lump_check": _lump_check(d, ex, w, post),
            "distances": {"decap": postlayout.decap_paths(d, ex), "clearance": postlayout.group_clearances(d, ex)}}


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
