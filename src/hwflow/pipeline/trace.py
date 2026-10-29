"""Requirement verification matrix."""

from __future__ import annotations

import json
from pathlib import Path

from ..layout.board import pcb
from ..core.design import Design
from .status import _design_hash, _fresh_status


def _evidence_hash(d: Design) -> str:
    """Manual attestations bind the entire requirement, including its written target.

    The build cache deliberately ignores requirement prose; a bench-test record must
    instead go stale when a target written in `text` changes. Evidence excludes itself.
    """
    import hashlib

    import yaml
    rp = d.dir / "requirements.yaml"
    reqs = yaml.safe_load(rp.read_text()) if rp.exists() else {}

    def strip_evidence(x):
        if isinstance(x, dict):
            return {k: strip_evidence(v) for k, v in x.items() if k != "evidence"}
        if isinstance(x, list):
            return [strip_evidence(v) for v in x]
        return x

    raw = [yaml.safe_load(d.path.read_text()), strip_evidence(reqs)]
    return hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()[:16]



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
    sim_results = results
    results = {}
    for r in (s.get("layout") or {}).get("isolation") or []:   # board.isolation with req: (backlog 38)
        for rid in str(r.get("req") or "").replace(" ", "").split(","):
            if rid:
                results.setdefault(rid, []).append((f"isolation {r['rule']} {r['mm']:.2f}/{r['limit']} mm", r["ok"]))
    for r in (s.get("layout") or {}).get("budgets") or []:   # budgets: with req: (copper on the routed board)
        for rid in str(r.get("req") or "").replace(" ", "").split(","):
            if rid:
                results.setdefault(rid, []).append((f"budget {r['name']} {r['value']}/{r['limit']:g} {r['unit']}", r["ok"]))
    for r in (s.get("post") or {}).get("geometry") or []:   # layout_checks with req: (outline, distance, keep-out, side)
        for rid in str(r.get("req") or "").replace(" ", "").split(","):
            if rid:
                val = r["value"]
                val = "x".join(f"{v:g}" for v in val) if isinstance(val, list) else "n/a" if val is None else f"{val:g}"
                lim = r["limit"]
                lim = "x".join(f"{v:g}" for v in lim) if isinstance(lim, list) else "n/a" if lim is None else f"{lim:g}"
                results.setdefault(rid, []).append((f"{r['kind']} {r['name']} {val}/{lim} {r['unit']}", r["ok"]))
    nst = (s.get("layout") or {}).get("nets") or {}
    lays = pcb.net_layers(d)
    for n, mw in pcb.net_min_widths(d).items():   # nets.<name>.req: the routed copper of that net (min width, layers)
        rids = [x for x in str(d.net_info(n).get("req") or "").replace(" ", "").split(",") if x]
        if rids and s.get("layout"):
            got = (nst.get(n) or {}).get("min_w")
            for rid in rids:
                results.setdefault(rid, []).append((f"net {n} min width {got}/{mw:g} mm",
                                                    got is not None and got >= mw - 1e-6))
    for n, L in lays.items():
        rids = [x for x in str(d.net_info(n).get("req") or "").replace(" ", "").split(",") if x]
        if rids and s.get("layout") and n in nst:
            for rid in rids:
                results.setdefault(rid, []).append((f"net {n} layers {'+'.join(nst[n]['layers'])}",
                                                    set(nst[n]["layers"]) <= set(L)))
    post = {x["name"]: x["pass_post"] for x in (s.get("post") or {}).get("deltas", [])}
    mc = mc_measures(d, s)
    fw = fw_measures(d, s)
    rows = []
    evidence_hash = _evidence_hash(d)
    for q in reqs:
        rid = q["id"]
        computed = {"sim": sim_results.get(rid, []) + mc.get(rid, []), "layout": results.get(rid, []),
                    "fw_test": fw.get(rid, [])}
        ev = q.get("evidence")
        auto = auto_sources(ev)
        if "cost" in auto:
            computed["cost"] = cost_measures(d, ev)
        ms = [m for src in ("sim", "layout", "fw_test", "cost") for m in computed.get(src, [])]
        method = q.get("verification", "simulation")
        if auto:
            eligible = [m for src in auto for m in computed.get(src, [])]
            absent = [src for src in auto if not computed.get(src)]
        else:
            eligible = {"simulation": computed["sim"], "layout": computed["layout"]}.get(method, [])
            absent = []
        ev_status, reason = _manual_evidence(d, ev, evidence_hash)
        # `auto:` next to a record (status pass/fail or an artifact) needs both: the hwflow results and the record
        with_record = bool(auto) and isinstance(ev, dict) and (ev.get("status") in ("pass", "fail")
                                                                 or bool(ev.get("artifact")))
        if any(not p or not post.get(n, True) for n, p in ms) or ev_status == "FAIL":
            status = "FAIL"
        elif eligible and not absent and (not with_record or ev_status == "pass"):
            status = "pass"
        elif method in MANUAL_METHODS and ev_status == "pass" and not auto:
            status = method
        else:
            status = "UNVERIFIED"
            if with_record and not absent and eligible:
                reason = f"auto results pass, but the record next to them does not: {reason}"
            elif absent:
                reason = "auto: no current " + ", ".join(f"{src} result ({AUTO_HOW.get(src, 'unknown source: use ' + ', '.join(AUTO_SOURCES))})" for src in absent)
            elif method in ("simulation", "layout"):
                reason = f"no current {method} check names this requirement"
            elif not isinstance(ev, dict) or ev.get("status") in (None, "not_checked"):
                have = [src for src in ("sim", "layout", "fw_test") if computed[src]]
                reason = (f"{method}: no evidence record yet; hwflow results name it ({', '.join(have)}, all ok): "
                          f"`evidence: {{auto: [{', '.join(have)}]}}` if they prove it, else a record "
                          f"(`hwflow evidence <proj>`)" if have else
                          f"{method}: no evidence record yet (`hwflow evidence <proj>` prints the template)")
        row = {"id": rid, "text": q.get("text", ""), "priority": q.get("priority", "must"),
               "status": status, "measures": [f"{n}:{'ok' if p else 'FAIL'}" for n, p in ms],
               "method": method}
        row["sources"] = [src for src in AUTO_SOURCES if computed.get(src)]
        if auto:
            row["auto"] = auto
        if status == "UNVERIFIED":
            row["evidence_reason"] = reason
        if method == "layout" and ev and not auto and not eligible:
            row["evidence_only"] = True
        miss = missing_evidence(d.dir, ev)
        if miss:
            row["evidence_missing"] = miss
        rows.append(row)
    return {"rows": rows, "total": len(rows),
            "covered": sum(r["status"] not in ("UNVERIFIED", "FAIL") for r in rows),
            "failing": sum(r["status"] == "FAIL" for r in rows),
            "unverified": sum(r["status"] == "UNVERIFIED" for r in rows),
            "unverified_must": sum(r["status"] == "UNVERIFIED" and r["priority"] == "must" for r in rows),
            "design_hash": _design_hash(d), "evidence_hash": evidence_hash,
            "evidence_missing": sorted((r["id"] for r in rows if r.get("evidence_missing")), key=_natural),
            "evidence_only": [r["id"] for r in rows if r.get("evidence_only")]}


MANUAL_METHODS = ("inspection", "datasheet", "analysis", "test")
AUTO_SOURCES = ("sim", "layout", "fw_test", "cost")
AUTO_HOW = {"sim": "a measure with `req:`; `hwflow build <proj> --until sim`",
            "layout": "a layout check with `req:`; a current layout build",
            "fw_test": "a host-test CHECK whose message names the id; `hwflow fw test <proj>`",
            "cost": "`max_usd:` in the evidence and a price on every BOM part"}
MC_MIN_YIELD = 0.99


def auto_sources(evidence) -> list[str]:
    """The hwflow results that a requirement names as its evidence: `evidence: {auto: [sim, fw_test]}`."""
    if not isinstance(evidence, dict) or not evidence.get("auto"):
        return []
    a = evidence["auto"]
    a = [a] if isinstance(a, str) else list(a) if isinstance(a, list) else []
    return [str(x) for x in a]


def mc_measures(d: Design, s: dict) -> dict[str, list]:
    """Monte Carlo yield per requirement: one measure per analysis whose measures carry the requirement's `req`,
    from the current design's Monte Carlo run (none when Monte Carlo did not run). It passes at `sim.mc_min_yield`
    (default 0.99); a run that did not finish counts as a failing run."""
    sim = s.get("sim") or {}
    runs = sim.get("monte_carlo") or []
    if not runs:
        return {}
    lim = float((d.raw.get("sim") or {}).get("mc_min_yield", MC_MIN_YIELD))
    tags: dict[str, set] = {}
    for a in (d.raw.get("sim") or {}).get("analyses") or []:
        for m in a.get("measures") or []:
            if m.get("debug") or str(m.get("name", "")).startswith("dbg_"):
                continue
            for rid in str(m.get("req") or "").replace(" ", "").split(","):
                if rid:
                    tags.setdefault(a.get("name"), set()).add(rid)
    out: dict[str, list] = {}
    for r in runs:
        y = r.get("yield")
        if y is None:
            continue
        n = r.get("runs") or 0
        for rid in sorted(tags.get(r.get("analysis"), ())):
            out.setdefault(rid, []).append((f"mc {r['analysis']} yield {round(y * n)}/{n}", y >= lim - 1e-9))
    return out


def fw_measures(d: Design, s: dict) -> dict[str, list]:
    """Firmware host-test checks per requirement (`hwflow fw test`), when the stored run is current: same design,
    same firmware sources and interface.yaml."""
    ft = s.get("fw_test") or {}
    if not ft.get("ran") or not ft.get("reqs"):
        return {}
    from ..fw.hosttest import source_hash
    if ft.get("src_hash") != source_hash(d):
        return {}
    return {rid: [(f"fw {r['checks']} checks, {r['failed']} failed", r["failed"] == 0 and r["checks"] > 0)]
            for rid, r in ft["reqs"].items()}


def cost_measures(d: Design, evidence) -> list:
    """BOM cost per board from design.yaml prices (+ `extra_usd`, per-unit costs outside the BOM, sourced in notes)
    against `max_usd`. No measure when a part has no price or the limit is missing."""
    if not isinstance(evidence, dict) or evidence.get("max_usd") is None:
        return []
    total, missing = d.cost()
    if missing:
        return []
    extra = float(evidence.get("extra_usd") or 0)
    lim = float(evidence["max_usd"])
    got = total + extra
    label = f"cost BOM {total:.2f}" + (f" + {extra:g}" if extra else "") + f"/{lim:g} USD"
    return [(label, got <= lim + 1e-9)]



def _manual_evidence(d: Design, evidence, current_hash: str) -> tuple[str, str]:
    """A manual result is an explicit attestation with an artifact for this design.

    Existence/freshness checks do not independently verify the artifact's contents.
    Legacy prose is retained for readers but cannot stand in for a completed check.
    """
    if not isinstance(evidence, dict):
        return "UNVERIFIED", "no explicit evidence record (status, artifact, design_hash)"
    state = evidence.get("status")
    if state not in ("pass", "fail"):
        return "UNVERIFIED", f"evidence status is {state!r}, not pass or fail"
    if evidence.get("design_hash") != current_hash:
        return "UNVERIFIED", "evidence design_hash is absent or stale"
    artifact = evidence.get("artifact")
    if not isinstance(artifact, str) or not artifact.strip():
        return "UNVERIFIED", "evidence artifact is absent"
    try:
        rel = Path(artifact)
        path = (d.dir / rel).resolve()
        if rel.is_absolute() or not path.is_relative_to(d.dir.resolve()):
            return "UNVERIFIED", "evidence artifact must be a file inside the project"
        if not path.is_file() or path.stat().st_size == 0:
            return "UNVERIFIED", "evidence artifact is missing, empty, or not a file"
        if STUB_MARK in path.read_text(errors="replace"):
            return "UNVERIFIED", f"evidence artifact {artifact} is an unfilled `hwflow evidence` stub"
    except (OSError, ValueError, RuntimeError):
        return "UNVERIFIED", "evidence artifact cannot be read"
    return ("pass" if state == "pass" else "FAIL"), ""



STUB_MARK = "hwflow:evidence-stub"


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
    if isinstance(evidence, dict):
        evidence = evidence.get("artifact")
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
