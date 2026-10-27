"""Compact, stable JSON views of build/status.json for agents (`--json` on check, trace, nets, build, summary).

Each view carries a `schema` name with a version (`hwflow.<view>/1`). A field is added in a backward-compatible way
(new key); a rename or a change of meaning bumps the version. Lists that can grow without bound are cut to a few items
with a count next to them, so an agent reads one short object instead of the text report or the 100+ KB status file.
The full data stays in build/status.json.
"""

from __future__ import annotations

import re
from collections import Counter

from ..core.design import Design
from .status import _design_hash, _fresh_status, _out, _stale_stages, _status

TOP = 8  # list items kept per section; the count of all of them is next to the list


def _finding(x: dict) -> dict:
    return {"sev": x.get("sev"), "code": x.get("code"), "msg": x.get("msg")}


def check_view(c: dict | None) -> dict:
    if not c:
        return {"ran": False}
    f = c.get("findings") or []
    return {"ok": bool(c.get("ok")), "errors": c.get("errors", 0), "warnings": c.get("warnings", 0),
            "findings": [_finding(x) for x in f if x.get("sev") in ("error", "warn")]}


def erc_view(c: dict | None) -> dict:
    if not c:
        return {"ran": False}
    errs = [v for v in c.get("violations") or [] if v.get("sev") == "error"]
    return {"ok": bool(c.get("ok")), "errors": c.get("errors", 0), "warnings": c.get("warnings", 0),
            "top": [{"type": v.get("type"), "msg": v.get("msg"), "items": (v.get("items") or [])[:2]}
                    for v in errs[:TOP]]}


def sim_view(c: dict | None) -> dict:
    if not c:
        return {"ran": False}
    fails = [{"analysis": f.get("analysis"), "name": f.get("name"), "value": f.get("value"), "min": f.get("min"),
              "max": f.get("max"), "unit": f.get("unit"), "req": f.get("req"),
              **({"na": f["na_reason"]} if f.get("value") is None and f.get("na_reason") else {})}
             for f in c.get("fails") or []]
    out = {"ok": bool(c.get("ok")), "passed": c.get("passed"), "total": c.get("total"), "fails": fails[:2 * TOP],
           "n_fails": len(fails), "derating_issues": (c.get("derating_issues") or [])[:TOP],
           "generic_models": len(c.get("generic_models") or [])}
    bad = [{"name": r.get("name"), "status": r.get("status"), "reason": r.get("reason")}
           for r in c.get("runs") or [] if r.get("status") not in (None, "ok")]
    if bad:
        out["runs_not_ok"] = bad[:TOP]
    mc = c.get("monte_carlo") or []
    if mc:
        out["mc_yield"] = {m.get("analysis"): m.get("yield") for m in mc}
    return out


def layout_view(c: dict | None) -> dict:
    if not c:
        return {"ran": False}
    from ..layout import diagnose
    errs = [x for x in c.get("drc") or [] if x.get("sev") == "error"]
    out = {"ok": bool(c.get("ok")), "board_mm": c.get("board_mm"), "unrouted": c.get("unrouted"),
           "drc_errors": c.get("drc_errors"), "drc_warnings": c.get("drc_warnings"), "vias": c.get("vias"),
           "drc_by_type": dict(Counter(x.get("type") for x in errs).most_common()),
           "drc_top": [{"type": x.get("type"), "msg": x.get("msg"), "items": (x.get("items") or [])[:2],
                        "at": (x.get("at") or [None])[0]} for x in errs[:TOP]],
           "placement_violations": (c.get("placement_violations") or [])[:TOP],
           "seconds": c.get("seconds")}
    diag = diagnose.summary(c.get("diagnosis"))
    if diag:
        g = c.get("diagnosis") or {}
        out["diagnosis"] = {"text": diag, "blocked": bool(g.get("blocked")), "stage": g.get("stage")}
    if c.get("blocked"):
        out["blocked"] = c["blocked"]
    if c.get("repair"):
        out["repair"] = c["repair"].get("text")
    for key, ok in (("budgets", "ok"), ("isolation", "ok"), ("corridors", "ok")):
        bad = [x.get("msg") or x.get("rule") or x.get("name") for x in c.get(key) or [] if not x.get(ok, True)]
        if bad:
            out[f"{key}_failing"] = bad[:TOP]
    for key in ("off_layer", "too_narrow", "conductive_isolation"):
        if c.get(key):
            out[key] = c[key][:TOP]
    if c.get("auto"):
        out["auto"] = {"kept": c["auto"].get("kept"), "attempts": len(c["auto"].get("attempts") or []),
                       "stopped": c["auto"].get("stopped")}
    return out


def post_view(c: dict | None) -> dict:
    if not c:
        return {"ran": False}
    checks = [x for x in c.get("checks") or [] if x.get("sev") in ("error", "warn")]
    deltas = c.get("deltas") or []
    return {"ok": bool(c.get("ok")), "metrics": c.get("metrics"),
            "by_code": dict(Counter(f"{x.get('sev')}:{x.get('code')}" for x in checks).most_common()),
            "errors": [_finding(x) for x in checks if x.get("sev") == "error"][:TOP],
            "spice_pass": sum(bool(x.get("pass_post")) for x in deltas), "spice_total": len(deltas),
            "regressions": [x.get("name") for x in c.get("regressions") or []][:TOP]}


def verdict(d: Design) -> str | None:
    """The verdict line of build/REPORT.md (`PASS` or `FAIL: ...`), None without a report."""
    p = _out(d) / "REPORT.md"
    if not p.exists():
        return None
    m = re.search(r"^Verdict: (.*)$", p.read_text(errors="replace"), re.M)
    return m.group(1) if m else None


def build_view(d: Design) -> dict:
    """The build summary: verdict, staleness, score card and one compact object per stage (fresh results only;
    a stale stage is named in `stale` and left out)."""
    s = _status(d)
    stale = _stale_stages(s, _design_hash(d))
    fresh = _fresh_status(d)
    out = {"schema": "hwflow.summary/1", "project": d.name, "verdict": verdict(d), "stale": stale,
           "updated": s.get("updated")}
    if isinstance(s.get("score"), dict):
        out["score"] = s["score"]
    views = {"check": check_view, "sch": erc_view, "sim": sim_view, "layout": layout_view, "post": post_view}
    for k, fn in views.items():
        if k in fresh:
            out["erc" if k == "sch" else k] = fn(fresh[k])
    if "fab" in fresh:
        out["fab"] = {"ok": bool(fresh["fab"].get("ok"))}
    if isinstance(s.get("trace"), dict):
        out["trace"] = {k: s["trace"].get(k) for k in ("total", "covered", "failing", "unverified")}
    return out


def check_json(d: Design) -> dict:
    s = _fresh_status(d)
    return {"schema": "hwflow.check/1", "project": d.name, **check_view(s.get("check")),
            **({"approval": [_finding(x) for x in s["check"].get("approval") or [] if x.get("sev") != "info"]}
               if s.get("check") else {})}


def trace_json(t: dict) -> dict:
    """Requirement trace: counts, the ids verified (with their method), and one row per requirement that is not
    (no requirement text: it is in requirements.yaml)."""
    rows = t.get("rows") or []
    ok = [r for r in rows if r.get("status") not in ("UNVERIFIED", "FAIL")]
    bad = [{"id": r["id"], "status": r["status"], "priority": r.get("priority"),
            **({"measures": r["measures"]} if r.get("measures") else {}),
            **({"reason": r["evidence_reason"]} if r.get("evidence_reason") else {}),
            **({"evidence_missing": r["evidence_missing"]} if r.get("evidence_missing") else {})}
           for r in rows if r.get("status") in ("UNVERIFIED", "FAIL")]
    return {"schema": "hwflow.trace/1", "evidence_hash": t.get("evidence_hash"), "total": t.get("total"),
            "verified": t.get("covered"), "failing": t.get("failing"), "unverified": t.get("unverified"),
            "verified_ids": [r["id"] for r in ok], "open": bad,
            "evidence_only": t.get("evidence_only") or []}


NET_COLS = ["class_w", "min_w", "max_w", "length_mm", "narrow_mm", "vias", "layers"]


def nets_json(nets: dict, globs: list[str] | None = None) -> dict:
    import fnmatch
    rows = {}
    for n, r in nets.items():
        if globs and not any(fnmatch.fnmatchcase(n, g) for g in globs):
            continue
        rows[n] = [r.get("width"), r.get("min_w"), r.get("max_w"), round(r.get("length_mm") or 0, 2),
                   round(r.get("narrow_mm") or 0, 2), r.get("vias"), r.get("layers") or []]
    return {"schema": "hwflow.nets/1", "cols": NET_COLS, "nets": rows}
