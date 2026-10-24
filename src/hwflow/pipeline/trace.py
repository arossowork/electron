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
    rows = []
    evidence_hash = _evidence_hash(d)
    manual_methods = ("inspection", "datasheet", "analysis", "test")
    for q in reqs:
        sim_ms, layout_ms = sim_results.get(q["id"], []), results.get(q["id"], [])
        ms = sim_ms + layout_ms
        method = q.get("verification", "simulation")
        eligible = {"simulation": sim_ms, "layout": layout_ms}.get(method, [])
        ev_status, reason = _manual_evidence(d, q.get("evidence"), evidence_hash)
        if any(not p or not post.get(n, True) for n, p in ms) or ev_status == "FAIL":
            status = "FAIL"
        elif eligible:
            status = "pass"
        elif method in manual_methods and ev_status == "pass":
            status = method
        else:
            status = "UNVERIFIED"
            if method in ("simulation", "layout"):
                reason = f"no current {method} check names this requirement"
        row = {"id": q["id"], "text": q.get("text", ""), "priority": q.get("priority", "must"),
               "status": status, "measures": [f"{n}:{'ok' if p else 'FAIL'}" for n, p in ms]}
        if status == "UNVERIFIED":
            row["evidence_reason"] = reason
        if method == "layout" and q.get("evidence") and not eligible:
            row["evidence_only"] = True
        miss = missing_evidence(d.dir, q.get("evidence"))
        if miss:
            row["evidence_missing"] = miss
        rows.append(row)
    return {"rows": rows, "total": len(rows),
            "covered": sum(r["status"] not in ("UNVERIFIED", "FAIL") for r in rows),
            "failing": sum(r["status"] == "FAIL" for r in rows),
            "unverified": sum(r["status"] == "UNVERIFIED" for r in rows),
            "design_hash": _design_hash(d), "evidence_hash": evidence_hash,
            "evidence_missing": sorted((r["id"] for r in rows if r.get("evidence_missing")), key=_natural),
            "evidence_only": [r["id"] for r in rows if r.get("evidence_only")]}



def _manual_evidence(d: Design, evidence, current_hash: str) -> tuple[str, str]:
    """A manual result is an explicit attestation with an artifact for this design.

    Existence/freshness checks do not independently verify the artifact's contents.
    Legacy prose is retained for readers but cannot stand in for a completed check.
    """
    if not isinstance(evidence, dict):
        return "UNVERIFIED", "no explicit evidence record (status, artifact, design_hash)"
    state = evidence.get("status")
    if state not in ("pass", "fail"):
        return "UNVERIFIED", "evidence status is not pass or fail"
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
    except (OSError, ValueError, RuntimeError):
        return "UNVERIFIED", "evidence artifact cannot be read"
    return ("pass" if state == "pass" else "FAIL"), ""



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
