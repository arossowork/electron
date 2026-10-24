"""Requirement verification matrix."""

from __future__ import annotations

from pathlib import Path

from .. import pcb
from ..design import Design
from .status import _fresh_status


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
    for q in reqs:
        ms = results.get(q["id"], [])
        method = q.get("verification", "simulation")
        if ms:
            ok = all(p for _, p in ms) and all(post.get(n, True) for n, _ in ms)
            status = "pass" if ok else "FAIL"
        elif method == "layout" and q.get("evidence"):
            status = "layout?"  # evidence text only: no check on the routed board names it (evidence_only)
        elif method in ("inspection", "datasheet", "analysis", "test") and q.get("evidence"):
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
            "evidence_missing": sorted((r["id"] for r in rows if r.get("evidence_missing")), key=_natural),
            "evidence_only": [r["id"] for r in rows if r["status"] == "layout?"]}


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
