"""hwflow command line. Every command prints a short, agent-friendly summary."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path

from . import kicad, pcb, placer, postlayout, spice
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


def _save_status(d, key, val):
    s = _status(d)
    if isinstance(val, dict) and key in ("check", "sch", "sim", "layout", "post", "fab", "optimize"):
        val["design_hash"] = _design_hash(d)
    s[key] = val
    s["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _status_path(d).write_text(json.dumps(s, indent=2, default=str))


def _fmt(v):
    if v is None:
        return "n/a"
    return f"{v:.4g}" if isinstance(v, float) else str(v)


# ------------------------------------------------------------------ stages

def stage_check(d: Design, libs: Libs) -> dict:
    f = check(d, libs)
    errs = [x for x in f if x["sev"] == "error"]
    warns = [x for x in f if x["sev"] == "warn"]
    return {"ok": not errs, "errors": len(errs), "warnings": len(warns), "findings": f}


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
    res = spice.run_all(d, w)
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
    if mc:
        out["monte_carlo"] = spice.monte_carlo(d, w, runs=mc)
    else:  # keep Monte Carlo results of this exact design (a --reuse or plain rebuild must not erase them)
        prev = _status(d).get("sim") or {}
        if prev.get("monte_carlo") and prev.get("design_hash") == _design_hash(d):
            out["monte_carlo"] = prev["monte_carlo"]
    out["ok"] = out["passed"] == out["total"] and not out["errors"]
    return out


def _run_summary(res: list[dict]) -> list[dict]:
    """Analyses that did not finish cleanly (timeout, crash, aborted, error) or needed a retry, with run time."""
    return [{k: a[k] for k in ("name", "status", "seconds", "reason", "flaky") if k in a}
            for a in res if a.get("status", "ok") != "ok" or a.get("flaky")]


def _route_once(d: Design, libs: Libs, parts, passes: int) -> tuple[list, list, str, int]:
    out = _out(d)
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


def _minimize_area(d: Design, libs: Libs, seeds: int, iters: int, passes: int, weights):
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
        parts, cost, viol = placer.place(d, libs, seeds=seeds, iters=iters, weights=weights)
        if viol:
            tried.append(f"{W}x{H}: placement fails")
            break
        tracks, vias, summary, unrouted = _route_once(d, libs, parts, passes)
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


def stage_layout(d: Design, libs: Libs, reuse: bool = False, seeds: int = 4, iters: int = 30000,
                 passes: int = 30) -> dict:
    out = _out(d)
    pj = out / "placement.json"
    t0 = time.time()
    weights = (d.raw.get("placement") or {}).get("weights")
    area_log = None
    pre = None
    if d.board.get("optimize_area") and not reuse:
        pre, area_log = _minimize_area(d, libs, seeds, iters, passes, weights)
    if pre:
        _, _, parts, cost, viol, tracks, vias, summary = pre
    elif reuse and pj.exists():
        _apply_layout_size(d)
        parts = placer.build_parts(d, libs)
        saved = json.loads(pj.read_text())
        for r, p in parts.items():
            if r in saved:
                p.x, p.y, p.r = saved[r]
        pl = placer.Placer(d, parts, d.board["width_mm"], d.board["height_mm"])
        pl.legalize()
        viol, cost = pl.violations(), pl.cost()
    else:
        parts, cost, viol = placer.place(d, libs, seeds=seeds, iters=iters, weights=weights)
    pj.write_text(json.dumps({r: [p.x, p.y, p.r] for r, p in parts.items()}, indent=1))
    name = d.name
    board = out / f"{name}.kicad_pcb"
    if not pre:
        tracks, vias, summary, _ = _route_once(d, libs, parts, passes)
    pcb.write_board(board, pcb.build_board(d, libs, parts, tracks, vias))
    drc = kicad.drc(board, refill=True, parity=True)
    errs = [x for x in drc if x["sev"] == "error"]
    unrouted = [x for x in drc if x["type"] == "unconnected"]
    return {"ok": not errs and not viol, "board_mm": [d.board["width_mm"], d.board["height_mm"]],
            "area_search": area_log, "placement_cost": round(cost, 1), "placement_violations": viol,
            "router": summary, "segments": len(tracks), "vias": len(vias), "drc_errors": len(errs),
            "drc_warnings": len(drc) - len(errs), "unrouted": len(unrouted), "drc": drc[:40],
            "seconds": round(time.time() - t0, 1)}


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
        pre = spice.run_all(d, out / "sim")
    post = spice.run_all(d, w, nm, tag="_post")
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
            "runs": _run_summary(post)}


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
    with open(fab / "bom.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Comment", "Designator", "Footprint", "MPN", "LCSC", "Qty", "UnitPriceUSD"])
        for r in d.bom():
            w.writerow([r["value"], r["refs"], (r["footprint"] or "").split(":")[-1], r["mpn"] or "",
                        r["lcsc"] or "", r["qty"], r["unit_price_usd"] or ""])
    return {"ok": all(m.endswith("ok") for m in msgs), "steps": msgs}


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
    stale = [k for k in ("check", "sch", "sim", "layout", "post", "fab")
             if k in s and s[k].get("design_hash") != cur]
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
        L.append(f"- layout{st('layout')}: {'OK' if c['ok'] else 'FAIL'}; board {c.get('board_mm')} mm; router {c['router']}; {c['segments']} segments, "
                 f"{c['vias']} vias; DRC {c['drc_errors']} errors / {c['drc_warnings']} warnings; "
                 f"unrouted {c['unrouted']}")
        for v in c["placement_violations"][:5]:
            L.append(f"  - placement: {v}")
        for x in [x for x in c["drc"] if x["sev"] == "error"][:10]:
            L.append(f"  - DRC {x['type']}: {x['msg']} {x['items'][:2]}")
        if not c["ok"]:
            fail("layout", "layout/DRC errors")
    if "post" in s:
        c = s["post"]
        m = c["metrics"]
        L.append(f"- post-layout{st('post')}: {'OK' if c['ok'] else 'FAIL'}; board {m['area_mm2']} mm2, {m['layers']} layers, "
                 f"{m['track_len_mm']} mm copper, {m['vias']} vias")
        for x in c["checks"]:
            if x["sev"] != "info":
                L.append(f"  - {x['sev']} {x['code']}: {x['msg']}")
        for r in c["ir_drop"]:
            L.append(f"  - IR drop {r['net']}: {r['drop_mv']:.2f} mV (worst at {r['worst_pin']})")
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
    if t["total"]:
        L.append(f"- requirements: {t['covered']}/{t['total']} verified, {t['failing']} failing, "
                 f"{t['unverified']} unverified")
        for r in t["rows"]:
            if r["status"] in ("FAIL", "UNVERIFIED"):
                L.append(f"  - {r['id']} {r['status']}: {r['text'][:90]}")
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


def _fresh_status(d) -> dict:
    s = _status(d)
    cur = _design_hash(d)
    return {k: v for k, v in s.items()
            if not (isinstance(v, dict) and "design_hash" in v and v["design_hash"] != cur)}


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
        rows.append({"id": q["id"], "text": q.get("text", ""), "priority": q.get("priority", "must"),
                     "status": status, "measures": [f"{n}:{'ok' if p else 'FAIL'}" for n, p in ms]})
    return {"rows": rows, "total": len(rows), "covered": sum(r["status"] not in ("UNVERIFIED",) for r in rows),
            "failing": sum(r["status"] == "FAIL" for r in rows),
            "unverified": sum(r["status"] == "UNVERIFIED" for r in rows)}


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

def main(argv=None):
    ap = argparse.ArgumentParser(prog="hwflow", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "sch", "post", "fab", "report", "status"):
        sub.add_parser(name).add_argument("project")
    p = sub.add_parser("sim"); p.add_argument("project"); p.add_argument("--mc", type=int, default=0)
    p = sub.add_parser("layout"); p.add_argument("project"); p.add_argument("--reuse", action="store_true")
    p.add_argument("--seeds", type=int, default=4); p.add_argument("--iters", type=int, default=30000)
    p.add_argument("--passes", type=int, default=30)
    p = sub.add_parser("build"); p.add_argument("project"); p.add_argument("--mc", type=int, default=0)
    p.add_argument("--until", choices=["check", "sch", "sim", "layout", "post", "fab"], default="fab")
    p.add_argument("--reuse", action="store_true"); p.add_argument("--force", action="store_true",
                                                                   help="continue past failing stages")
    p = sub.add_parser("optimize"); p.add_argument("project"); p.add_argument("--budget", type=int)
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("lib"); p.add_argument("what", choices=["sym", "fp", "show", "pads"]); p.add_argument("query", nargs="+")
    p = sub.add_parser("parts"); p.add_argument("category"); p.add_argument("filters", nargs="*",
                                                                           help="key=value server filters")
    p.add_argument("--text"); p.add_argument("--limit", type=int, default=15)
    sub.add_parser("trace").add_argument("project")
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
            for p in sorted(s["pins"], key=lambda p: (p["unit"], [int(t) if t.isdigit() else t for t in p["number"]])):
                print(f"  pin {p['number']:>4} {p['name']:<12} {p['type']:<14} unit {p['unit']}{' hidden' if p['hidden'] else ''}")
        else:
            fp = libs.footprint(q)
            print(f"{q} bbox={tuple(round(v, 2) for v in fp['bbox'])}")
            for p in fp["pads"]:
                print(f"  pad {p['number']:>4} {p['kind']:<10} {p['shape']:<9} at ({p['x']:.2f},{p['y']:.2f}) {p['w']}x{p['h']}")
        return 0
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
        print(f"coverage {t['covered']}/{t['total']} verified, {t['failing']} failing, {t['unverified']} unverified")
        _save_status(d, "trace", {k: v for k, v in t.items() if k != "rows"})
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
    order = ["check", "sch", "sim", "layout", "post", "fab"]
    if a.cmd == "build":
        stages = order[: order.index(a.until) + 1]
    else:
        stages = [a.cmd]
    rc = 0
    for st in stages:
        t0 = time.time()
        try:
            if st == "check":
                r = stage_check(d, libs)
            elif st == "sch":
                r = stage_sch(d, libs)
            elif st == "sim":
                r = stage_sim(d, getattr(a, "mc", 0))
            elif st == "layout":
                r = stage_layout(d, libs, reuse=getattr(a, "reuse", False), **(
                    {"seeds": a.seeds, "iters": a.iters, "passes": a.passes} if a.cmd == "layout" else {}))
            elif st == "post":
                r = stage_post(d)
            else:
                r = stage_fab(d)
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "exception": f"{type(e).__name__}: {e}"}
        _save_status(d, st, r)
        print(f"[{st}] {'OK' if r.get('ok') else 'FAIL'} ({time.time() - t0:.1f}s)" +
              (f" {r['exception']}" if "exception" in r else ""))
        if not r.get("ok"):
            rc = 1
            hard = st in ("check", "sch") or "exception" in r
            if a.cmd == "build" and hard and not a.force:
                break
    print(write_report(d, history=True))
    return rc


if __name__ == "__main__":
    sys.exit(main())
