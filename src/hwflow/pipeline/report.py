"""build/REPORT.md writer and the --brief diff."""

from __future__ import annotations

import json
import time

from ..sim import simrobust
from ..core.design import Design
from .hooks import registry
from .status import _design_hash, _out, _save_status, _stale_stages, _status
from .trace import trace


def _fmt(v):
    if v is None:
        return "n/a"
    return f"{v:.4g}" if isinstance(v, float) else str(v)


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
    verdict FAIL; trace and the report extras use fresh results only. With `history`, the report is also kept in
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
        L.append(f"- check{st('check')}: {'OK' if c['ok'] else 'FAIL'} ({c['errors']} errors, {c['warnings']} warnings"
                 + (f", {c['decisions_pending']} product decision(s) pending" if c.get("decisions_pending") else "") + ")")
        rank = {"error": 0, "warn": 1}  # errors first: a long list of warnings never hides the error that fails the check
        shown = sorted(c["findings"], key=lambda x: rank.get(x["sev"], 2))[:12]
        for x in shown:  # a pending product decision (cost over a must) is not a design error: say which it is
            L.append(f"  - {'decision pending' if x.get('decision') and x['sev'] == 'error' else x['sev']}: {x['msg']}")
        if len(c["findings"]) > len(shown):
            L.append(f"  - and {len(c['findings']) - len(shown)} more (status.json check.findings)")
        if not c["ok"]:
            fail("check", "design check errors")
        if c.get("decisions_pending"):
            L.append(f"  - {c['decisions_pending']} product decision(s) pending: later stages run, the verdict stays FAIL "
                     f"until the requirement is met or the product person changes it")
            fail("check", "product decision pending")
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
        L += simrobust.report_lines(c)
        if c.get("debug"):
            L.append("  - debug probes (not counted): " + ", ".join(
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
        L.append("- derating: " + ("FAILED" if c.get("derating_status") == "failed" else
                                 "OK" if not c["derating_issues"] else f"{len(c['derating_issues'])} issues") +
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
        if c.get("portfolio"):
            from . import portfolio
            L += portfolio.report_lines(c["portfolio"])
        if c.get("auto"):
            L.append(f"- layout --auto: kept attempt {c['auto']['kept']} of {len(c['auto']['attempts'])}"
                     + (f"; {c['auto']['stopped']}" if c["auto"].get("stopped") else ""))
            for x in c["auto"]["attempts"]:
                L.append(f"  - {x}")
        L.append(f"- layout{st('layout')}: {'OK' if c['ok'] else 'FAIL'}; board {c.get('board_mm')} mm; router {c['router']}; {c['segments']} segments, "
                 f"{c['vias']} vias; DRC {c['drc_errors']} errors / {c['drc_warnings']} warnings; "
                 f"unrouted {c['unrouted']}")
        if c.get("route_groups"):
            from ..layout.route import regionroute
            L.append(f"  - route groups: {regionroute.text(c['route_groups'])}")
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
        if c.get("routability"):
            ra = c["routability"]
            L.append(f"  - routability prediction ({ra['seconds']} s, no routing): {ra['message']}")
            if ra.get("outcome"):
                L.append(f"  - prediction against the router: {ra['outcome']}")
        for x in c.get("corridors") or []:
            L.append(f"  - corridor {x['msg']}")
        for v in c["placement_violations"][:5]:
            L.append(f"  - placement: {v}")
        for x in [x for x in c["drc"] if x["sev"] == "error"][:10]:
            L.append(f"  - DRC {x['type']}: {x['msg']} {x['items'][:2]}")
        from ..layout import diagnose
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
                if r.get("evidence_reason"):
                    L.append(f"    - {r['evidence_reason']}")
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
    extras = []
    for h in registry().report_extras:
        data = h.fn(d)
        _save_status(d, h.name, data)
        extras.append((h, data))
    if history:
        _append_history(d, text, verdict, stale, extras)
    return text


HISTORY_KEEP = 50


def _append_history(d: Design, text: str, verdict: list[str], stale: list[str], extras: list):
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
    line = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "design_hash": _design_hash(d),
            "verdict": "PASS" if not verdict else "FAIL: " + "; ".join(verdict), "stale": stale,
            **{k: data.get(k) for h, data in extras for k in h.history_keys}, "report": f"history/{name}"}
    tm = _status(d).get("timing")
    if isinstance(tm, dict):  # where this build's time went (`hwflow time`): stage spans and step seconds
        line["timing"] = {"run_id": tm.get("run_id"), "wall_s": tm.get("wall_s"),
                          "stages": {k: v.get("s") if not v.get("cached") else 0.0
                                     for k, v in (tm.get("stages") or {}).items()},
                          "steps": tm.get("steps") or {}}
    with open(out / "history.jsonl", "a") as fh:
        fh.write(json.dumps(line, default=str) + "\n")
    for old in sorted(hd.glob("*.md"), key=lambda p: (p.stat().st_mtime, p.name))[:-HISTORY_KEEP]:
        old.unlink()


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
