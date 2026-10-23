"""hwflow command line. Every command prints a short, agent-friendly summary."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import time
from pathlib import Path

from . import budgets, kicad, pcb, placer, postlayout, repair, seedroute, spice
from .design import Design, check
from .libs import Libs


def _load(proj: str) -> Design:
    return Design.load(proj)


def _out(d: Design) -> Path:
    o = d.dir / "build"
    o.mkdir(exist_ok=True)
    return o


def _status_path(d):
    return _out(d) / "status.json"


def _status(d) -> dict:
    p = _status_path(d)
    return json.loads(p.read_text()) if p.exists() else {}


PROSE_KEYS = ("text", "evidence", "notes", "note", "source", "rationale", "comment")


def _design_hash(d) -> str:
    """Hash of the parsed design, so comments, whitespace and key order do not make results stale (backlog 58).

    design.yaml is parsed fresh from the file: `d.raw` is mutated in memory (layout board size). From
    requirements.yaml only the electrical content counts; prose fields (text, evidence, notes, ...) do not."""
    import hashlib

    import yaml
    raw = yaml.safe_load(d.path.read_text()) or {}
    h = hashlib.sha1(json.dumps(raw, sort_keys=True, default=str).encode())
    rq = d.dir / "requirements.yaml"
    if rq.exists():
        reqs = yaml.safe_load(rq.read_text()) or {}

        def strip(x):
            if isinstance(x, dict):
                return {k: strip(v) for k, v in x.items() if k not in PROSE_KEYS}
            if isinstance(x, list):
                return [strip(v) for v in x]
            return x
        h.update(json.dumps(strip(reqs), sort_keys=True, default=str).encode())
    return h.hexdigest()[:12]


def _inputs_hash(d) -> str:
    """Everything a build stage reads besides design.yaml: model files, project libraries and the hwflow code.
    Two builds with the same inputs give the same results, so `build` skips a stage whose inputs did not change
    (backlog 27)."""
    import hashlib

    from .libs import project_lib_dirs
    h = hashlib.sha1(_design_hash(d).encode())
    files = [d.dir / f for f in (d.raw.get("sim") or {}).get("includes") or []]
    files += [d.dir / c.get("spice")["lib"] for c in d.components.values()
              if isinstance(c.get("spice"), dict) and c.get("spice").get("lib")]
    files += _block_files(d)
    for f in sorted(set(files)):
        h.update(str(f).encode() + (f.read_bytes() if f.is_file() else b"-"))
    for root in project_lib_dirs(d):
        for f in sorted(root.rglob("*")):
            if f.is_file():
                st = f.stat()
                h.update(f"{f.relative_to(root)}:{st.st_size}:{st.st_mtime_ns}".encode())
    for f in sorted(Path(__file__).parent.rglob("*")):
        if f.suffix in (".py", ".lib") and f.is_file():
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def _block_files(d) -> list[Path]:
    """The block.yaml files of the layout blocks the design uses."""
    from . import blocks
    return [f for e in (d.raw.get("blocks") or {}).values() if (f := blocks.find(str((e or {}).get("use")), d))]


def _save_status(d, key, val):
    s = _status(d)
    if isinstance(val, dict) and key in ("check", "sch", "sim", "layout", "post", "fab", "optimize"):
        val["design_hash"] = _design_hash(d)
        val["at"] = time.time()
    s[key] = val
    s["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _status_path(d).write_text(json.dumps(s, indent=2, default=str))


def _fmt(v):
    if v is None:
        return "n/a"
    return f"{v:.4g}" if isinstance(v, float) else str(v)


# ------------------------------------------------------------------ stages

APPROVAL_DEFAULTS = {"cost_pct": 5.0, "size_mm": 0.5}


def _approval_state(d: Design) -> dict:
    """What `hwflow approve` records: layers, board size and BOM cost from design.yaml (backlog 44)."""
    import yaml
    b = dict(d.board, **((yaml.safe_load(d.path.read_text()) or {}).get("board") or {}))  # size as written
    cost, missing = d.cost()
    return {"layers": b["layers"], "width_mm": b["width_mm"], "height_mm": b["height_mm"],
            "cost_usd": round(cost, 4), "unpriced_lines": len(missing)}


def approve(d: Design, note: str = "") -> dict:
    """`hwflow approve <proj>`: the product person accepted the current layers, board size and cost.
    Written to build/approved.json; `check` compares later designs with it."""
    a = dict(_approval_state(d), at=time.strftime("%Y-%m-%d %H:%M:%S"), design_hash=_design_hash(d), note=note)
    (_out(d) / "approved.json").write_text(json.dumps(a, indent=2))
    return a


def _recorded_changes(d: Design) -> dict[str, str]:
    """Cost changes the product person knows about: a `change: <key>` line in conflicts.md (pending) or a question in
    inbox/questions*.json with `"change": "<key>"` (answered when inbox/answers.md has that round's answer)."""
    import re
    out = {}
    cf = d.dir / "conflicts.md"
    if cf.exists():
        for k in re.findall(r"change:\s*(layers|size|cost)\b", cf.read_text(), re.I):
            out[k.lower()] = "recorded in conflicts.md, waiting for the product person"
    inbox = d.dir / "inbox"
    answers = (inbox / "answers.md").read_text() if (inbox / "answers.md").exists() else ""
    for qf in sorted(inbox.glob("questions*.json")) if inbox.is_dir() else []:
        try:
            qs = json.loads(qf.read_text())
        except ValueError:
            continue
        rnd = qs.get("round")
        for q in qs.get("questions") or []:
            k = str(q.get("change") or "").lower()
            if k not in ("layers", "size", "cost"):
                continue
            sec = re.search(rf"^## Round {rnd}\b(.*?)(?=^## |\Z)", answers, re.M | re.S) if rnd is not None else None
            if sec and re.search(rf"^{re.escape(str(q.get('id')))}:\s*\S", sec.group(1), re.M):
                out[k] = f"answered in round {rnd} {q.get('id')}"
            else:
                out.setdefault(k, f"asked in {qf.name} {q.get('id')}, not answered yet")
    return out


def approval_findings(d: Design) -> list[dict]:
    """Backlog 44: a change of board.layers, board size or BOM cost (above `approval.cost_pct`) against
    build/approved.json is an error unless conflicts.md or an inbox question records it (then a warning until
    `hwflow approve` takes the new state as the baseline)."""
    p = _out(d) / "approved.json"
    if not p.exists():
        return [{"sev": "info", "code": "approval", "msg": "no approved baseline (build/approved.json); run "
                 "`hwflow approve <proj>` after the product person accepts layers, board size and cost"}]
    old = json.loads(p.read_text())
    new = _approval_state(d)
    lim = dict(APPROVAL_DEFAULTS, **(d.raw.get("approval") or {}))
    changes = []
    if new["layers"] != old.get("layers"):
        changes.append(("layers", f"board.layers {old.get('layers')} -> {new['layers']}"))
    dw, dh = new["width_mm"] - old.get("width_mm", 0), new["height_mm"] - old.get("height_mm", 0)
    if max(abs(dw), abs(dh)) > float(lim["size_mm"]):
        changes.append(("size", f"board {old.get('width_mm')} x {old.get('height_mm')} -> "
                                f"{new['width_mm']} x {new['height_mm']} mm"))
    c0 = float(old.get("cost_usd") or 0)
    if c0 and (new["cost_usd"] - c0) / c0 * 100 > float(lim["cost_pct"]):
        changes.append(("cost", f"BOM ${c0:.2f} -> ${new['cost_usd']:.2f} "
                                f"(+{(new['cost_usd'] - c0) / c0 * 100:.1f}%, limit {lim['cost_pct']}%)"))
    rec = _recorded_changes(d)
    f = []
    for k, msg in changes:
        if k in rec:
            f.append({"sev": "warn", "code": "approval", "msg": f"{msg} against the approved baseline of "
                      f"{old.get('at')}: {rec[k]}; run `hwflow approve` once the product person accepts it"})
        else:
            f.append({"sev": "error", "code": "approval", "msg": f"{msg} against the approved baseline of "
                      f"{old.get('at')} is a product decision: add a `change: {k}` entry to conflicts.md or ask "
                      f"the product person (inbox question with \"change\": \"{k}\")"})
    return f


def stage_check(d: Design, libs: Libs) -> dict:
    f = check(d, libs) + approval_findings(d)
    errs = [x for x in f if x["sev"] == "error"]
    warns = [x for x in f if x["sev"] == "warn"]
    return {"ok": not errs, "errors": len(errs), "warnings": len(warns), "findings": f,
            "approval": [x for x in f if x["code"] == "approval"]}


def stage_sch(d: Design, libs: Libs) -> dict:
    out = _out(d)
    kicad.write_lib_tables(d, libs, out)
    pcb.write_project(out, d.name, d)
    from . import sch
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
    out = out or _out(d)
    name = d.name
    if d.board.get("router", "krt") == "krt":
        if (pcb.KRT_DIR / "py_router" / "route.py").exists():
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
    for f in ("corridor.py", "barrier.py"):  # corridor.neck_needed: which nets route narrow first
        h.update((Path(pcb.__file__).with_name(f)).read_bytes())
    h.update(json.dumps(d.raw.get("floorplan"), sort_keys=True, default=str).encode())  # conductive keep-outs, regions
    if d.raw.get("blocks"):  # layout blocks: the nets their copper closes are not routed
        h.update((Path(pcb.__file__).with_name("blocks.py")).read_bytes())
        for f in _block_files(d):
            h.update(f.read_bytes())
    knobs = sorted((k, v) for k, v in os.environ.items()
                   if k.startswith(("KICAD_", "KRT_")) and k != "KICAD_CLI" or k == "HWFLOW_NECKDOWN")
    h.update(json.dumps(knobs).encode())  # router environment knobs (KICAD_RIP_PREEXISTING and the like)
    return h.hexdigest()[:24]


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
        return {"failing": {}, "ok": False, "blocked": msg, "isolation": [], "off_layer": [], "conductive_isolation": [],
                "budget_widths": bud_w, "budgets": [], "board_mm": [d.board["width_mm"], d.board["height_mm"]],
                "area_search": area_log, "placement_cost": round(cost, 1), "placement_violations": viol,
                "corridors": corridors, "router": "-", "segments": 0, "vias": 0, "drc_errors": 0, "drc_warnings": 0,
                "unrouted": 0, "drc": [], "repair": None, "route_cache": None, "fixed_parts_only": blocks,
                "seconds": round(time.time() - t0, 1)}

    def route_check(tr, vi, island=0, fast=False):
        pcb.write_board(board, pcb.build_board(d, libs, parts, tr, vi, island_removal=island))
        return kicad.drc_fast(board) if fast else kicad.drc(board, refill=True, parity=True)

    pcb.write_project(out, name, d)  # the .kicad_dru with board.drc_rules and board.isolation, for the router and DRC
    key = _route_key(d, libs, parts, out, passes)
    hit = json.loads((cache / f"{key}.json").read_text()) if (cache / f"{key}.json").exists() else None
    rep, island = None, 0
    if hit:
        tracks, vias, summary, island, rep = hit["tracks"], hit["vias"], hit["summary"] + " (route cache)", hit["island"], hit["rep"]
        seeds = hit.get("seeds") or {}
        drc = route_check(tracks, vias, island)  # the full DRC runs again: it is the check of the board kept
        print(f"[layout] route cache hit {key}: router and repair skipped")
    else:
        if not pre:
            tracks, vias, summary, _ = _route_once(d, libs, parts, passes, out)
        seeds = seedroute.load(out)  # the seed copper of this route (pcb.krt_route)
        drc = route_check(tracks, vias)
        rounds = int(d.board.get("repair_rounds", 3))
        if (any(x["sev"] == "error" for x in drc) and rounds > 0 and d.board.get("router", "krt") == "krt"
                and (pcb.KRT_DIR / "py_router" / "route.py").exists()):
            pre = (tracks, vias, drc, board.read_bytes())
            r = repair.repair(d, libs, parts, tracks, vias, drc, board,
                              lambda *a: route_check(*a, fast=True), rounds, deadline=deadline)  # local repair, not a rebuild
            tracks, vias, rep, island = r["tracks"], r["vias"], r["report"], r["island"]
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
                                 "seeds": seeds})
    if seeds:  # the final DRC judges the seed copper: an error that touches it removes it
        tr, vi, dropped = seedroute.drop_on_drc(seeds, tracks, vias, drc)
        if dropped:
            tracks, vias = tr, vi
            drc = route_check(tracks, vias, island)
            summary += f"; seed route {', '.join(dropped)} removed (a DRC error touches it)"
            print(f"[layout] seed route {', '.join(dropped)}: removed, a DRC error touches it")
    from .prune import prune
    tracks, vias, drc, pruned = prune(tracks, vias, drc, lambda tr, vi: route_check(tr, vi, island))
    if pruned:
        print(f"[layout] removed {pruned} dangling tracks or vias")
    errs = [x for x in drc if x["sev"] == "error"]
    unrouted = [x for x in drc if x["type"] == "unconnected"]
    lays = pcb.net_layers(d)  # nets.<name>.layers: a track on another layer fails the layout
    off_layer = sorted({f"{t['net']} on {t['layer']}" for t in tracks if t["net"] in lays and t["layer"] not in lays[t["net"]]})
    iso = isolation_distances(d, board, drc)
    # mains copper (tracks, vias, pads) near a conductive keep-out: the router keeps it out, this makes sure
    conductive = postlayout.conductive_clearances(d, pcb.read_board(board))
    for x in conductive:
        print(f"[layout] conductive isolation: {x['net']} {x['item']} {x['mm']:.2f} mm from {x['keepout']} "
              f"(need {x['need']:g} mm)")
    bud = budgets.measure(d, postlayout.Extraction(d, board)) if budgets.budgets(d) else []
    from . import diagnose
    failing = diagnose.failing_parts(drc, pcb.read_board(board))  # the parts each error touches (layout diagnosis)
    return {"failing": failing, "ok": not errs and not viol and not off_layer and not conductive
            and all(r["ok"] for r in iso) and all(r["ok"] for r in bud),
            "isolation": iso, "off_layer": off_layer, "conductive_isolation": conductive,
            "budget_widths": bud_w, "budgets": bud,
            "board_mm": [d.board["width_mm"], d.board["height_mm"]],
            "area_search": area_log, "placement_cost": round(cost, 1), "placement_violations": viol,
            "corridors": corridors, "router": summary, "segments": len(tracks), "vias": len(vias), "drc_errors": len(errs),
            "drc_warnings": len(drc) - len(errs), "unrouted": len(unrouted), "drc": drc[:40], "repair": rep,
            "route_cache": key if hit else None, "fixed_parts_only": repair.fixed_part_errors(drc, parts),
            "seconds": round(time.time() - t0, 1)}


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
    seed0 = 1 + k * seeds
    use_saved = c["reuse"] and k == 0 and (out / "placement.json").exists()
    if use_saved:
        shutil.copy(out / "placement.json", w / "placement.json")
    try:
        r = stage_layout(d, libs, reuse=use_saved, seeds=seeds, iters=c["iters"], passes=c["passes"], seed0=seed0,
                         workdir=w, deadline=c.get("deadline"))
    except Exception as e:  # noqa: BLE001 - a router or DRC crash ends this attempt, not the search
        return {"k": k + 1, "error": f"attempt {k + 1}: {type(e).__name__}: {str(e)[:120]}"}
    r["post_warnings"] = _post_warnings(d, w)  # tie-break of layout_auto: post-layout layout checks (no SPICE)
    how = "saved placement" if use_saved else f"placer seeds {seed0}-{seed0 + seeds - 1}"
    line = (f"attempt {k + 1} ({how}): board {r['board_mm'][0]}x{r['board_mm'][1]} mm, {r['unrouted']} unrouted, "
            f"DRC {r['drc_errors']} errors, {len(r['placement_violations'])} placement violations, "
            f"{r['vias']} vias" + (f", {r['repair']['text']}" if r.get("repair") else "")
            + (", route cache" if r.get("route_cache") else "")
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
    from . import diagnose
    out = _out(d)
    if libs is None or not (out / f"{d.name}.kicad_pcb").exists():
        return {}  # no board to read (an attempt that crashed): no diagnosis
    board = pcb.read_board(out / f"{d.name}.kicad_pcb")
    parts = placer.build_parts(d, libs)
    for ref, fp in board["footprints"].items():
        if ref in parts:
            parts[ref].x, parts[ref].y, parts[ref].r = fp["x"], fp["y"], fp["r"]
    return diagnose.diagnose(d, parts, [{"failing": a.get("failing") or {}} for a in attempts], board, out, libs)


def _layout_blocked(d: Design, a) -> dict | None:
    """The layout stage result when the layout diagnosis blocks this build (see diagnose.blocked), else None."""
    from . import diagnose
    e = diagnose.blocked(d, _out(d))
    if e is None or getattr(a, "retry_layout", False):
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
    from . import diagnose
    g = r.get("diagnosis") or {}
    diagnose.record(_out(d), {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "structure": diagnose.structure_key(d),
                              "ok": bool(r.get("ok")), "unrouted": r.get("unrouted"), "drc_errors": r.get("drc_errors"),
                              "persistent": g.get("persistent") or [], "blocked": bool(g.get("blocked")),
                              "override": override, "diagnosis": g})


def _layout_budget() -> float:
    """Wall time in seconds `layout --auto` may use for its attempts (HWFLOW_LAYOUT_BUDGET_S, default 3000: inside the
    3600 s an agent's `timeout` gives the call, with time left for the diagnosis, status.json and REPORT.md)."""
    return float(os.environ.get("HWFLOW_LAYOUT_BUDGET_S", 3000))


def _hopeless_limit() -> int:
    """Errors + unrouted of attempt 1 above which `--auto` starts no other attempt (HWFLOW_AUTO_MAX_ERRORS, default the
    repair limit: a board that repair skips is not one that a new seed fixes)."""
    from . import repair
    return int(os.environ.get("HWFLOW_AUTO_MAX_ERRORS", repair.MAX_ERRORS))


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


# ------------------------------------------------------------------ report

def _sim_verdict(c: dict) -> str:
    """Name what failed: the failing measures, or the analyses whose ngspice run reported an error."""
    n = c["total"] - c["passed"]
    if n:
        names = ", ".join(f"{f['analysis']}/{f['name']}" for f in c["fails"][:3])
        return f"{n} sim measures fail ({names}{', ...' if n > 3 else ''})"
    ans = sorted({e.split(": ", 1)[0] for e in c["errors"]})
    first = c["errors"][0].split(": ", 1)[-1][:120] if c["errors"] else ""
    return f"ngspice errors in {', '.join(ans[:4])} ({first})"


def write_report(d: Design, history: bool = False) -> str:
    """Rewrite build/REPORT.md. Results of an older design stay in the report, marked (stale), and make the
    verdict FAIL; trace and score use fresh results only. With `history`, the report is also kept in
    build/history/ and summarized in build/history.jsonl."""
    t = trace(d)
    if t["total"]:
        _save_status(d, "trace", {k: v for k, v in t.items() if k != "rows"})
    s = _status(d)
    cur = _design_hash(d)
    stale = _stale_stages(s, cur)
    crashed = {k: s.pop(k)["exception"] for k in ("check", "sch", "sim", "layout", "post", "fab")
               if isinstance(s.get(k), dict) and "exception" in s[k]}
    L = [f"# {d.name}: build report", f"Updated {s.get('updated', '?')}. Source: design.yaml.", ""]
    verdict = []
    st = lambda k: " (stale)" if k in stale else ""  # noqa: E731

    def fail(k, msg):  # a stale stage's failure is old news; the verdict says `stale` for it instead
        if k not in stale:
            verdict.append(msg)
    if stale:
        L.append(f"- stale (older design.yaml; last results kept below and marked (stale), not evidence for this "
                 f"design; rerun to refresh): {', '.join(stale)}")
    for k, e in crashed.items():
        L.append(f"- {k}{st(k)}: FAIL, the stage did not run ({e[:200]})")
        fail(k, f"{k} did not run")
    if "check" in s:
        c = s["check"]
        L.append(f"- check{st('check')}: {'OK' if c['ok'] else 'FAIL'} ({c['errors']} errors, {c['warnings']} warnings)")
        for x in c["findings"][:12]:
            L.append(f"  - {x['sev']}: {x['msg']}")
        if not c["ok"]:
            fail("check", "design check errors")
    if "sch" in s:
        c = s["sch"]
        L.append(f"- ERC{st('sch')}: {'OK' if c['ok'] else 'FAIL'} ({c['errors']} errors, {c['warnings']} warnings)")
        for x in c["violations"][:10]:
            L.append(f"  - {x['sev']} {x['type']}: {x['msg']} {x['items'][:2]}")
        if not c["ok"]:
            fail("sch", "ERC errors")
    if "sim" in s:
        c = s["sim"]
        L.append(f"- SPICE{st('sim')}: {c['passed']}/{c['total']} measures pass")
        for f in c["fails"]:
            rng = f"{_fmt(f['min'])}..{_fmt(f['max'])}"
            why = f"; n/a: {f['na_reason']}" if f["value"] is None and f.get("na_reason") else ""
            L.append(f"  - FAIL {f['analysis']}/{f['name']} = {_fmt(f['value'])} {f['unit']} (limit {rng}, req {f.get('req')}{why})")
        for x in c.get("runs") or []:
            L.append(f"  - analysis {x['name']}: {x['status']} after {x['seconds']} s"
                     + (f"; {x['reason']}" if x.get("reason") else "") + ("; flaky: crashed once, retried" if x.get("flaky") else ""))
        for e in c["errors"][:5]:
            L.append(f"  - ngspice: {e}")
        if c.get("debug"):
            L.append(f"  - debug probes (not counted): " + ", ".join(
                f"{r['analysis']}/{r['name']}={_fmt(r['value'])}" for r in c["debug"][:12])
                + (f" and {len(c['debug']) - 12} more" if len(c["debug"]) > 12 else ""))
        if c.get("warnings"):
            L.append(f"  - ngspice warnings on passing analyses ({len(c['warnings'])}, not a failure): "
                     + "; ".join(c["warnings"][:3]))
        if c["unmodeled"]:
            L.append(f"  - not simulated: {', '.join(c['unmodeled'])}")
        if c.get("generic_models"):
            L.append(f"  - approximate models (built-in, datasheet fit, behavioral or no `spice.source`): "
                     f"{', '.join(c['generic_models'])}; justify each in notes/models.md or use a vendor model")
        hot = sorted(c["derating"], key=lambda r: -r.get("p_w", 0))[:3]
        L.append("- derating: " + ("OK" if not c["derating_issues"] else f"{len(c['derating_issues'])} issues") +
                 "; top dissipation " + ", ".join(f"{r['ref']} {r['p_w'] * 1e3:.1f} mW" for r in hot))
        for i in c["derating_issues"][:8]:
            L.append(f"  - {i}")
        for m in c.get("monte_carlo") or []:
            spread = "; ".join(f"{k} {_fmt(v['min'])}..{_fmt(v['max'])}" for k, v in m["stats"].items())
            L.append(f"- Monte Carlo {m['analysis']}: yield {m['yield'] * 100:.0f}% over {m['runs']} runs ({spread})")
            if m.get("failures"):
                L.append(f"  - failing runs (seed, values and log in build/mc_failures/; "
                         f"{m.get('excluded_from_stats', 0)} unfinished runs left out of the spread):")
                for x in m["failures"][:5]:
                    L.append(f"    - run {x['run']} {x['status']}: {x['reason'][:120]} -> build/{x['file']}")
        if not c["ok"]:
            fail("sim", _sim_verdict(c))
    if "layout" in s:
        c = s["layout"]
        if c.get("area_search"):
            L.append(f"- area search: {'; '.join(c['area_search'])}")
        if c.get("auto"):
            L.append(f"- layout --auto: kept attempt {c['auto']['kept']} of {len(c['auto']['attempts'])}"
                     + (f"; {c['auto']['stopped']}" if c["auto"].get("stopped") else ""))
            for x in c["auto"]["attempts"]:
                L.append(f"  - {x}")
        L.append(f"- layout{st('layout')}: {'OK' if c['ok'] else 'FAIL'}; board {c.get('board_mm')} mm; router {c['router']}; {c['segments']} segments, "
                 f"{c['vias']} vias; DRC {c['drc_errors']} errors / {c['drc_warnings']} warnings; "
                 f"unrouted {c['unrouted']}")
        if c.get("repair"):
            L.append(f"  - {c['repair']['text']}")
        for x in c.get("conductive_isolation") or []:
            L.append(f"  - conductive isolation: {x['net']} {x['item']} at {x['at']} is {x['mm']:.2f} mm from "
                     f"{x['keepout']} (region {x['region']} needs {x['need']:g} mm) FAIL")
        for r in c.get("isolation") or []:
            creep = (f"; creepage {r['creepage_mm']} mm by DRC: {r['drc_creepage']} violations"
                     if r.get("creepage_mm") is not None else "")
            L.append(f"  - isolation {r['rule']}{' (' + r['req'] + ')' if r.get('req') else ''}: copper "
                     f"{'between its member nets' if r.get('members_only') else 'to other nets'} "
                     f"{r['mm']:.2f} mm in plan (limit {r['limit']} mm; DRC clearance {r['drc_clearance']} violations"
                     f"{creep}; closest {r['between']}){'' if r['ok'] else ' FAIL'}")
        for r in c.get("budget_widths") or []:
            L.append(f"  - budget {r['name']} width: {r['msg']}")
        for r in c.get("budgets") or []:
            L.append(f"  - {r['msg']}")
        for x in c.get("corridors") or []:
            L.append(f"  - corridor {x['msg']}")
        for v in c["placement_violations"][:5]:
            L.append(f"  - placement: {v}")
        for x in [x for x in c["drc"] if x["sev"] == "error"][:10]:
            L.append(f"  - DRC {x['type']}: {x['msg']} {x['items'][:2]}")
        from . import diagnose
        if c.get("blocked"):
            L.append(f"  - {c['blocked']}")
        if diagnose.summary(c.get("diagnosis")):
            L.append(f"  - layout diagnosis: {diagnose.summary(c['diagnosis'])}")
        if not c["ok"]:
            g = c.get("diagnosis") or {}
            fail("layout", f"layout blocked, go back to {g['stage']}" if g.get("blocked") else "layout/DRC errors")
    if "post" in s:
        c = s["post"]
        m = c["metrics"]
        L.append(f"- post-layout{st('post')}: {'OK' if c['ok'] else 'FAIL'}; board {m['area_mm2']} mm2, {m['layers']} layers, "
                 f"{m['track_len_mm']} mm copper, {m['vias']} vias")
        for x in c["checks"]:
            if x["sev"] != "info":
                L.append(f"  - {x['sev']} {x['code']}: {x['msg']}")
        dist = c.get("distances") or {}
        for r in [] if (s.get("layout") or {}).get("isolation") else dist.get("clearance", []):
            L.append(f"  - clearance {r['pattern']} to other nets: {r['mm']:.2f} mm (limit {r['limit']} mm, "
                     f"{r['rule']}; closest {r['between']}){'' if r['ok'] else ' FAIL'}")
        if dist.get("decap"):
            worst = sorted(dist["decap"], key=lambda r: r["limit"] - r["mm"])[:4]
            L.append("  - decap path, tightest: " + "; ".join(
                f"{r['cap']}->{r['ic']} {r['net']} {r['mm']:.1f} mm (limit {r['limit']})" for r in worst))
        for r in c["ir_drop"]:
            if r.get("islands"):
                L.append(f"  - IR drop {r['net']}: not measured, the net is split in {r['islands']} copper islands")
            else:
                L.append(f"  - IR drop {r['net']}: {r['drop_mv']:.2f} mV" + (
                    f" at {r['current_a']:g} A over {r['path']} ({r['mohm']:.2f} mOhm, worst pad pair)"
                    if r.get("current_a") else f" at the operating point (worst at {r['worst_pin']})"))
        if c.get("scope"):
            L.append(f"  - scope: {c['scope']}")
        lc = c.get("lump_check")
        if lc:
            L.append(f"  - lumping check on {lc['analysis']}: " + (lc["error"] if "error" in lc else
                     f"max difference {lc['max_rel'] * 100:.2f}% against the full R and C network "
                     f"({lc['seconds_lumped']} s lumped, {lc['seconds_full']} s full, full run {lc['status']})"))
        big = [x for x in c["deltas"] if x["rel"] is not None and abs(x["rel"]) > 0.01]
        L.append(f"  - post-layout SPICE: {sum(x['pass_post'] for x in c['deltas'])}/{len(c['deltas'])} pass; "
                 f"{len(big)} measures moved more than 1%")
        for x in c.get("runs") or []:
            na = sum(1 for y in c["deltas"] if y["analysis"] == x["name"] and y["post"] is None)
            L.append(f"    - analysis {x['name']}: {x['status']} after {x['seconds']} s, {na} measures n/a"
                     + (f"; {x['reason']}" if x.get("reason") else "") + ("; flaky: crashed once, retried" if x.get("flaky") else ""))
        for x in big[:8] + c["regressions"]:
            why = f" (n/a: {x['na_reason']})" if x["post"] is None and x.get("na_reason") else ""
            L.append(f"    - {x['name']}: {_fmt(x['pre'])} -> {_fmt(x['post'])} {x['unit']}{why}")
        if not c["ok"]:
            fail("post", "post-layout issues")
    if "fab" in s:
        L.append(f"- fab outputs{st('fab')}: {'OK' if s['fab']['ok'] else 'FAIL'} (build/fab)")
        if s["fab"].get("bottom"):
            L.append(f"  - bottom side (positions.csv, cpl.csv): {', '.join(s['fab']['bottom'])}")
        if s["fab"].get("tht"):
            L.append(f"  - through-hole, not placed by SMT (THT or hand soldering): {', '.join(s['fab']['tht'])}")
    if t["total"]:
        L.append(f"- requirements: {t['covered']}/{t['total']} verified, {t['failing']} failing, "
                 f"{t['unverified']} unverified")
        for r in t["rows"]:
            if r["status"] in ("FAIL", "UNVERIFIED"):
                L.append(f"  - {r['id']} {r['status']}: {r['text'][:90]}")
        for r in t["rows"]:
            if r.get("evidence_missing"):
                L.append(f"  - {r['id']} evidence file missing or empty: {', '.join(r['evidence_missing'])}")
        if t["failing"] or t["unverified"]:
            verdict.append("requirements not all verified")
    if stale:
        verdict.append(f"stale: {', '.join(stale)}")
    cost, missing = d.cost()
    L.append(f"- BOM cost: ${cost:.2f} per board" + (f" (no price for {', '.join(missing)})" if missing else ""))
    L.insert(2, f"Verdict: {'PASS' if not verdict else 'FAIL: ' + '; '.join(verdict)}")
    L.insert(3, "")
    text = "\n".join(L) + "\n"
    (_out(d) / "REPORT.md").write_text(text)
    sc = score(d)
    _save_status(d, "score", sc)
    if history:
        _append_history(d, text, verdict, stale, sc)
    return text


HISTORY_KEEP = 50


def _append_history(d: Design, text: str, verdict: list[str], stale: list[str], sc: dict):
    """One line per build in build/history.jsonl and the full report in build/history/<timestamp>.md (backlog 18)."""
    out = _out(d)
    hd = out / "history"
    hd.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    name, n = f"{ts}.md", 1
    while (hd / name).exists():
        n += 1
        name = f"{ts}-{n}.md"
    (hd / name).write_text(text)
    keys = ("check_errors", "erc_errors", "sim_pass_ratio", "sim_measures", "mc_min_yield", "drc_errors", "unrouted",
            "post_pass_ratio", "area_mm2", "vias", "cost_usd", "req_total", "req_unverified", "req_failing")
    line = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "design_hash": _design_hash(d),
            "verdict": "PASS" if not verdict else "FAIL: " + "; ".join(verdict), "stale": stale,
            **{k: sc.get(k) for k in keys}, "report": f"history/{name}"}
    with open(out / "history.jsonl", "a") as fh:
        fh.write(json.dumps(line, default=str) + "\n")
    for old in sorted(hd.glob("*.md"), key=lambda p: (p.stat().st_mtime, p.name))[:-HISTORY_KEEP]:
        old.unlink()


# a stage result is also stale when a stage it reads from ran after it (post and fab read the routed board)
STAGE_DEPS = {"post": ("layout",), "fab": ("layout", "sch")}


def _stale_stages(s: dict, cur: str) -> list[str]:
    out = []
    for k in ("check", "sch", "sim", "layout", "post", "fab"):
        v = s.get(k)
        if not isinstance(v, dict):
            continue
        if v.get("design_hash") != cur or any(
                isinstance(s.get(dep), dict) and s[dep].get("at", 0) > v.get("at", 0) for dep in STAGE_DEPS.get(k, ())):
            out.append(k)
    return out


def _fresh_status(d) -> dict:
    s = _status(d)
    cur = _design_hash(d)
    stale = set(_stale_stages(s, cur))
    return {k: v for k, v in s.items() if k not in stale
            and not (isinstance(v, dict) and "design_hash" in v and v["design_hash"] != cur)}


def score(d: Design) -> dict:
    """Deterministic scorecard used by evals."""
    s = _fresh_status(d)
    cost, missing = d.cost()
    g = lambda k, f, dflt=None: f(s[k]) if k in s and "exception" not in s[k] else dflt  # noqa: E731
    return {
        "check_errors": g("check", lambda c: c["errors"]),
        "erc_errors": g("sch", lambda c: c["errors"]),
        "sim_pass_ratio": g("sim", lambda c: c["passed"] / max(c["total"], 1)),
        "sim_measures": g("sim", lambda c: c["total"]),
        "derating_issues": g("sim", lambda c: len(c["derating_issues"])),
        "generic_models": g("sim", lambda c: len(c.get("generic_models", []))),
        "mc_min_yield": g("sim", lambda c: min((m["yield"] for m in c.get("monte_carlo") or []), default=None)),
        "drc_errors": g("layout", lambda c: c["drc_errors"]),
        "unrouted": g("layout", lambda c: c["unrouted"]),
        "post_pass_ratio": g("post", lambda c: sum(x["pass_post"] for x in c["deltas"]) / max(len(c["deltas"]), 1)),
        "post_warnings": g("post", lambda c: sum(1 for x in c["checks"] if x["sev"] == "warn")),
        "area_mm2": g("post", lambda c: c["metrics"]["area_mm2"]),
        "vias": g("post", lambda c: c["metrics"]["vias"]),
        "cost_usd": round(cost, 4), "unpriced_lines": len(missing),
        "parts": len([c for c in d.components.values() if not c.get("virtual")]),
        "req_total": g("trace", lambda c: c["total"]), "req_unverified": g("trace", lambda c: c["unverified"]),
        "req_failing": g("trace", lambda c: c["failing"]),
        "req_evidence_missing": g("trace", lambda c: len(c.get("evidence_missing", []))),
    }


def trace(d: Design) -> dict:
    """Requirement -> verification matrix from requirements.yaml and measure `req` tags."""
    import yaml
    rp = d.dir / "requirements.yaml"
    reqs = (yaml.safe_load(rp.read_text()) or {}).get("requirements", []) if rp.exists() else []
    s = _fresh_status(d)
    results: dict[str, list] = {}
    for a in (s.get("sim") or {}).get("analyses", []):
        for r in a["rows"]:
            if r.get("debug"):
                continue
            for rid in str(r.get("req") or "").replace(" ", "").split(","):
                if rid:
                    results.setdefault(rid, []).append((r["name"], r["pass"]))
    for r in (s.get("layout") or {}).get("isolation") or []:   # board.isolation with req: (backlog 38)
        for rid in str(r.get("req") or "").replace(" ", "").split(","):
            if rid:
                results.setdefault(rid, []).append((f"isolation {r['rule']} {r['mm']:.2f}/{r['limit']} mm", r["ok"]))
    for r in (s.get("layout") or {}).get("budgets") or []:   # budgets: with req: (copper on the routed board)
        for rid in str(r.get("req") or "").replace(" ", "").split(","):
            if rid:
                results.setdefault(rid, []).append((f"budget {r['name']} {r['value']}/{r['limit']:g} {r['unit']}", r["ok"]))
    post = {x["name"]: x["pass_post"] for x in (s.get("post") or {}).get("deltas", [])}
    rows = []
    for q in reqs:
        ms = results.get(q["id"], [])
        method = q.get("verification", "simulation")
        if ms:
            ok = all(p for _, p in ms) and all(post.get(n, True) for n, _ in ms)
            status = "pass" if ok else "FAIL"
        elif method in ("inspection", "datasheet", "layout", "analysis", "test") and q.get("evidence"):
            status = method
        else:
            status = "UNVERIFIED"
        row = {"id": q["id"], "text": q.get("text", ""), "priority": q.get("priority", "must"),
               "status": status, "measures": [f"{n}:{'ok' if p else 'FAIL'}" for n, p in ms]}
        miss = missing_evidence(d.dir, q.get("evidence"))
        if miss:
            row["evidence_missing"] = miss
        rows.append(row)
    return {"rows": rows, "total": len(rows), "covered": sum(r["status"] not in ("UNVERIFIED",) for r in rows),
            "failing": sum(r["status"] == "FAIL" for r in rows),
            "unverified": sum(r["status"] == "UNVERIFIED" for r in rows),
            "evidence_missing": sorted((r["id"] for r in rows if r.get("evidence_missing")), key=_natural)}


def _natural(s: str):
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


_EVIDENCE_PATH = None


def missing_evidence(root: Path, evidence) -> list[str]:
    """File paths cited in a free-text evidence field (notes/x.md, SUMMARY.md, calc.py, ...) that do not exist
    under the project or are empty (backlog 54). URLs and plain words are ignored."""
    import re
    global _EVIDENCE_PATH
    if _EVIDENCE_PATH is None:
        ext = r"md|py|csv|txt|pdf|ya?ml|json|cir|lib|sp|ipynb|png|svg|kicad_\w+"
        _EVIDENCE_PATH = re.compile(r"(?<![\w/.:-])((?:notes|build|models|lib|docs|datasheets)/[\w./-]*[\w-]"
                                    rf"|(?:[\w.-]+/)*[\w-][\w.-]*\.(?:{ext}))(?![\w/-])")
    text = " ".join(str(x) for x in evidence) if isinstance(evidence, list) else str(evidence or "")
    text = re.sub(r"\w+://\S+", " ", text)
    out = []
    for m in _EVIDENCE_PATH.finditer(text):
        rel = m.group(1).rstrip(".")
        f = root / rel
        ok = f.is_dir() and any(f.iterdir()) if f.is_dir() else (f.is_file() and f.stat().st_size > 0)
        if not ok and rel not in out:
            out.append(rel)
    return out


# ------------------------------------------------------------------ em (optional, prototype)

OPENEMS_PYTHON = Path.home() / "opt" / "openEMS" / "venv" / "bin" / "python"


def cmd_em(a, argv) -> int:
    """openEMS lives in its own venv (built from source). Re-execute there when it is not importable here."""
    import os
    from . import em
    if not em.available():
        py = Path(os.environ.get("HWFLOW_OPENEMS_PYTHON", OPENEMS_PYTHON))
        if not py.exists() or os.environ.get("HWFLOW_EM_REEXEC"):
            print("em: openEMS Python bindings not found (see docs/em-openems-spike.md); skipped")
            return 0
        env = dict(os.environ, HWFLOW_EM_REEXEC="1",
                   PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parents[1]), os.environ.get("PYTHONPATH", "")]))
        import subprocess
        return subprocess.call([str(py), "-m", "hwflow.cli"] + list(argv if argv is not None else sys.argv[1:]), env=env)
    d = _load(a.project)
    if not em.em_nets(d) and not a.net:
        print("em: no net has `em:` set in design.yaml; nothing to do")
        return 0
    res = em.run_project(d, a.net, a.fmax_ghz, a.cell_mm)
    for r in res["nets"]:
        s = {x["f_ghz"]: x for x in r["s_at"]}
        s1 = s.get(1.0) or r["s_at"][-1]
        print(f"em {r['net']} {r['ports'][0]}->{r['ports'][1]} {r['length_mm']} mm: Z_tdr {_fmt(r['z_tdr_plateau'])} ohm "
              f"(closed form microstrip {r['z0_closed_form']}, CPWG {_fmt(r['z0_cpwg_closed_form_lenavg'])}), "
              f"@{s1['f_ghz']} GHz S11 {s1['s11_db']} dB S21 {s1['s21_db']} dB; "
              f"{r['runtime_s']:.0f} s, {r['cells']} cells, {r['peak_rss_mb']:.0f} MB"
              + ("" if r["ok"] else f"  FAIL vs target {r['z_target']} ohm"))
    _save_status(d, "em", res)
    return 0 if res["ok"] else 1


# ------------------------------------------------------------------ main

def cmd_block(a) -> int:
    """`hwflow block list|show|check|extract`: the layout block library (src/hwflow/blocks.py)."""
    import tempfile

    from . import blocks
    d = _load(a.project) if a.project else None
    if a.what == "list":
        for n in blocks.names(d):
            b = blocks.load(n, d)
            print(f"{n:<28} {str(b.get('description', '')).strip().splitlines()[0][:90]}")
        return 0
    if not a.arg:
        print(f"hwflow block {a.what}: name the {'board' if a.what == 'extract' else 'block'}")
        return 2
    if a.what == "show":
        print(blocks.describe(a.arg, d))
        return 0
    if a.what == "check":
        w = Path(a.out) if a.out else Path(tempfile.mkdtemp(prefix=f"block_{a.arg}_"))
        r = blocks.verify(a.arg, w, d)
        for x in r["drc"]:
            if x["sev"] == "error":
                print(f"  {x['type']}: {x['msg']} {x['items']}")
        for n, k in r["split"].items():
            print(f"  net {n} is split in {k} copper islands for post-layout extraction")
        print(f"block {a.arg}: {'OK' if r['ok'] else 'FAIL'}, DRC {r['errors']} errors, {r['warnings']} warnings on the "
              f"block alone; nets joined by its copper: {', '.join(r['joined']) or '-'} ({r['board']})")
        return 0 if r["ok"] else 1
    refs = [r.strip() for r in (a.refs or "").split(",") if r.strip()]
    if not refs or not a.name:
        print("hwflow block extract <board.kicad_pcb> --refs U2,L1 --name <name> --source <text>")
        return 2
    roles = dict(x.split("=", 1) for x in a.roles.split(",")) if a.roles else None
    b = blocks.extract(Path(a.arg), refs, a.name, a.source or "", roles, d, Libs(design=d) if d else Libs())
    f = blocks.write(b, Path(a.out) if a.out else None)
    print(f"wrote {f}: {len(b['roles'])} parts, ports {', '.join(b['ports'])}, {len(b['copper']['tracks'])} tracks, "
          f"{len(b['copper']['vias'])} vias")
    return 0


def _brief(old: str, new: str) -> str:
    """Only what changed against the previous REPORT.md, plus the verdict (backlog 8)."""
    skip = ("Updated ", "# ")
    o = [ln for ln in old.splitlines() if ln.strip() and not ln.startswith(skip)]
    n = [ln for ln in new.splitlines() if ln.strip() and not ln.startswith(skip)]
    verdict = next((ln for ln in n if ln.startswith("Verdict")), "Verdict: ?")
    os_, ns_ = set(o), set(n)
    added = [ln for ln in n if ln not in os_ and not ln.startswith("Verdict")]
    gone = [ln for ln in o if ln not in ns_ and not ln.startswith("Verdict")]
    if not old:
        return new
    L = [verdict]
    if added or gone:
        L.append("changed since the last report (+ new, - gone):")
        L += [f"+ {ln.strip()}" for ln in added[:40]] + ([f"+ ... {len(added) - 40} more"] if len(added) > 40 else [])
        L += [f"- {ln.strip()}" for ln in gone[:20]] + ([f"- ... {len(gone) - 20} more"] if len(gone) > 20 else [])
    else:
        L.append("no change since the last report")
    L.append(f"({len(n) - len(added)} lines unchanged; full report in build/REPORT.md)")
    return "\n".join(L) + "\n"


def _can_skip(d: Design, st: str, prev, inputs: str, a) -> bool:
    """A stage is skipped when it passed last time with the same inputs and its outputs are still there."""
    if not isinstance(prev, dict) or not prev.get("ok") or "exception" in prev or prev.get("inputs_hash") != inputs:
        return False
    if st in _stale_stages(_status(d), _design_hash(d)):  # e.g. post older than the board it was run on
        return False
    out = _out(d)
    need = {"sch": out / f"{d.name}.kicad_sch", "layout": out / f"{d.name}.kicad_pcb", "fab": out / "fab"}.get(st)
    if need is not None and not need.exists():
        return False
    if st == "sim" and getattr(a, "mc", 0) and prev.get("mc_runs") != a.mc:
        return False
    if st == "check" and prev.get("approval") != approval_findings(d):  # approved.json, conflicts.md, inbox
        return False
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hwflow", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    brief = "print only the report lines that changed since the last report, and the verdict"
    p = sub.add_parser("approve", help="record layers, board size and BOM cost as accepted by the product person")
    p.add_argument("project"); p.add_argument("--note", default="", help="who accepted it and where (e.g. round 3 q1)")
    for name in ("check", "sch", "post", "fab", "report", "status"):
        p = sub.add_parser(name); p.add_argument("project")
        if name not in ("report", "status"):
            p.add_argument("--brief", action="store_true", help=brief)
    p = sub.add_parser("sim"); p.add_argument("project"); p.add_argument("--mc", type=int, default=0)
    p.add_argument("--brief", action="store_true", help=brief)
    auto = ("placement, routing with net-order retries, area search and DRC in one call: up to --attempts "
            "placements in parallel (--workers, default cpu count - 1), stop at the first clean board or when the errors "
            "are on fixed parts; prints only the final numbers")
    p = sub.add_parser("layout"); p.add_argument("project"); p.add_argument("--reuse", action="store_true")
    p.add_argument("--brief", action="store_true", help=brief)
    p.add_argument("--auto", action="store_true", help=auto); p.add_argument("--attempts", type=int, default=4)
    p.add_argument("--seeds", type=int, default=4); p.add_argument("--iters", type=int, default=30000)
    p.add_argument("--passes", type=int, default=30); p.add_argument("--workers", type=int, default=None)
    retry = ("run a layout the layout diagnosis blocked (same parts, nets and board as a build it sent back to an "
             "earlier stage); recorded in the layout history")
    p.add_argument("--retry-layout", action="store_true", help=retry)
    p = sub.add_parser("build"); p.add_argument("project"); p.add_argument("--mc", type=int, default=0)
    p.add_argument("--until", choices=["check", "sch", "sim", "layout", "post", "fab"], default="fab")
    p.add_argument("--reuse", action="store_true"); p.add_argument("--force", action="store_true",
                                                                   help="continue past failing stages")
    p.add_argument("--rebuild", action="store_true", help="run every stage even when its inputs are unchanged")
    p.add_argument("--auto", action="store_true", help="layout stage as in `layout --auto`")
    p.add_argument("--attempts", type=int, default=4)
    p.add_argument("--retry-layout", action="store_true", help=retry)
    p.add_argument("--brief", action="store_true", help=brief)
    p = sub.add_parser("optimize"); p.add_argument("project"); p.add_argument("--budget", type=int)
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("lib"); p.add_argument("what", choices=["sym", "fp", "show", "pads"]); p.add_argument("query", nargs="+")
    p = sub.add_parser("block", help="layout blocks: list, show <name>, check <name>, extract <board> --refs ...")
    p.add_argument("what", choices=["list", "show", "check", "extract"]); p.add_argument("arg", nargs="?")
    p.add_argument("--refs", help="extract: parts of the block, comma separated"); p.add_argument("--name")
    p.add_argument("--source", help="extract: where the block comes from (board, run, datasheet)")
    p.add_argument("--roles", help="extract: role=ref pairs, comma separated (default: role = ref)")
    p.add_argument("--project", help="design whose blocks/, symbols and values to use")
    p.add_argument("--out", help="extract: library folder (default: the repository blocks/); check: work folder")
    p = sub.add_parser("parts"); p.add_argument("category"); p.add_argument("filters", nargs="*",
                                                                           help="key=value server filters")
    p.add_argument("--text"); p.add_argument("--limit", type=int, default=15)
    sub.add_parser("trace").add_argument("project")
    p = sub.add_parser("floorplan", help="early layout feasibility of the floorplan: field (area, depth, regions, quick "
                                         "placement and congestion); no simulation or routing")
    p.add_argument("project"); p.add_argument("--iters", type=int, default=20000); p.add_argument("--seeds", type=int, default=2)
    p = sub.add_parser("netlist"); p.add_argument("project"); p.add_argument("analysis", nargs="?")
    p = sub.add_parser("em", help="optional openEMS field-solver run on nets with `em:` set (prototype)")
    p.add_argument("project"); p.add_argument("--net", action="append")
    p.add_argument("--fmax-ghz", type=float); p.add_argument("--cell-mm", type=float)
    a = ap.parse_args(argv)

    if a.cmd == "em":
        return cmd_em(a, argv)

    if a.cmd == "lib":
        libs = Libs()
        q = " ".join(a.query)
        if a.what == "sym":
            for lid, desc in libs.search_symbols(q):
                print(f"{lid}  {desc}")
        elif a.what == "fp":
            print("\n".join(libs.search_footprints(q)))
        elif a.what == "show":
            s = libs.symbol(q)
            print(f"{q} units={s['units']} footprint={s['props'].get('Footprint', '')}")
            print(f"  {s['props'].get('Description', '')}")
            def natural(num):
                return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.findall(r"\d+|\D+", num)]
            for p in sorted(s["pins"], key=lambda p: (p["unit"], natural(p["number"]))):
                print(f"  pin {p['number']:>4} {p['name']:<12} {p['type']:<14} unit {p['unit']}{' hidden' if p['hidden'] else ''}")
        else:
            fp = libs.footprint(q)
            print(f"{q} bbox={tuple(round(v, 2) for v in fp['bbox'])}")
            for p in fp["pads"]:
                print(f"  pad {p['number']:>4} {p['kind']:<10} {p['shape']:<9} at ({p['x']:.2f},{p['y']:.2f}) {p['w']}x{p['h']}")
        return 0
    if a.cmd == "block":
        return cmd_block(a)
    if a.cmd == "parts":
        from . import parts
        filters = dict(f.split("=", 1) for f in a.filters)
        for r in parts.search(a.category, filters, a.text, a.limit):
            print(f"{r['lcsc']:>10} {r['mpn']:<28} {r['package']:<14} stock={r['stock']:<9} ${_fmt(r['price_usd'])}"
                  f"{' basic' if r['basic'] else ''}  {r['desc']}")
        return 0

    d = _load(a.project)
    if a.cmd == "netlist":
        sim = d.raw.get("sim") or {}
        an = next((x for x in sim.get("analyses", []) if x["name"] == a.analysis), {"name": "op", "cmd": "op"})
        print(spice.build_netlist(d, an)[0])
        return 0
    if a.cmd == "trace":
        t = trace(d)
        for r in t["rows"]:
            print(f"{r['id']:<5} {r['status']:<10} {r['priority']:<6} {r['text'][:70]}"
                  + (f"  [{', '.join(r['measures'])}]" if r["measures"] else ""))
        for r in t["rows"]:
            if r.get("evidence_missing"):
                print(f"{r['id']:<5} evidence file missing or empty: {', '.join(r['evidence_missing'])}")
        print(f"coverage {t['covered']}/{t['total']} verified, {t['failing']} failing, {t['unverified']} unverified"
              + (f"; evidence files missing for {len(t['evidence_missing'])} ({', '.join(t['evidence_missing'])})"
                 if t["evidence_missing"] else ""))
        _save_status(d, "trace", {k: v for k, v in t.items() if k != "rows"})
        return 0
    if a.cmd == "approve":
        x = approve(d, a.note)
        print(f"approved: {x['layers']} layers, {x['width_mm']} x {x['height_mm']} mm, BOM ${x['cost_usd']:.2f} "
              f"(build/approved.json)")
        return 0
    if a.cmd == "status":
        print(json.dumps(_status(d).get("score", {}), indent=2))
        return 0
    if a.cmd == "report":
        print(write_report(d))
        return 0
    if a.cmd == "optimize":
        from . import optimize
        res = optimize.run(d, _out(d), a.budget)
        print(json.dumps({k: res[k] for k in ("evals", "score", "fails", "start", "best")}, indent=1))
        print("measures:", {k: _fmt(v) for k, v in res["measures"].items()})
        if a.apply:
            optimize.apply(d, res)
            print("applied to design.yaml")
        _save_status(d, "optimize", {k: res[k] for k in ("evals", "score", "fails", "start", "best", "measures")})
        return 0

    libs = Libs(design=d)
    if a.cmd == "floorplan":
        from . import floorplan
        text, data = floorplan.run(d, libs, _out(d), a.iters, a.seeds)
        _save_status(d, "floorplan", {**data, "design_hash": _design_hash(d), "at": time.time()})
        print(text)
        return 0 if data["ok"] else 1
    order = ["check", "sch", "sim", "layout", "post", "fab"]
    if a.cmd == "build":
        stages = order[: order.index(a.until) + 1]
    else:
        stages = [a.cmd]
    rc = 0
    prev_report = (_out(d) / "REPORT.md").read_text() if (_out(d) / "REPORT.md").exists() else ""
    inputs = _inputs_hash(d)
    ran = False
    for st in stages:
        t0 = time.time()
        # once a stage runs, every later stage runs too: its inputs include the earlier stage's outputs
        if a.cmd == "build" and not a.rebuild and not ran and _can_skip(d, st, _status(d).get(st), inputs, a):
            print(f"[{st}] skipped: passed last time with the same inputs ({inputs}); --rebuild to run it")
            continue
        ran = True
        try:
            if st == "check":
                r = stage_check(d, libs)
            elif st == "sch":
                r = stage_sch(d, libs)
            elif st == "sim":
                r = stage_sim(d, getattr(a, "mc", 0))
            elif st == "layout" and (blk := _layout_blocked(d, a)):
                r = blk
            elif st == "layout":
                kw = {"seeds": a.seeds, "iters": a.iters, "passes": a.passes} if a.cmd == "layout" else {}
                if getattr(a, "auto", False):
                    r = layout_auto(d, libs, reuse=getattr(a, "reuse", False), attempts=a.attempts,
                                    workers=getattr(a, "workers", None), **kw)
                else:
                    r = stage_layout(d, libs, reuse=getattr(a, "reuse", False), **kw)
                    if not r["ok"]:
                        r["diagnosis"] = layout_diagnosis(d, libs, [r])
                _record_layout(d, r, getattr(a, "retry_layout", False))
            elif st == "post":
                r = stage_post(d)
            else:
                r = stage_fab(d)
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "exception": f"{type(e).__name__}: {e}"}
        r["inputs_hash"] = inputs
        _save_status(d, st, r)
        print(f"[{st}] {'OK' if r.get('ok') else 'FAIL'} ({time.time() - t0:.1f}s)" +
              (f" {r['exception']}" if "exception" in r else ""))
        if r.get("auto"):
            print(_auto_summary(r))
        if not r.get("ok"):
            rc = 1
            hard = st in ("check", "sch") or "exception" in r or bool(r.get("blocked"))
            if a.cmd == "build" and hard and not a.force:
                break
    text = write_report(d, history=True)
    brief_out = getattr(a, "brief", False) or (a.cmd == "layout" and getattr(a, "auto", False))
    print(_brief(prev_report, text) if brief_out else text)
    return rc


if __name__ == "__main__":
    sys.exit(main())
