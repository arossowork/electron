"""`hwflow evidence <proj>`: the sign-off evidence plan, and the writes that make it mechanical.

Every requirement falls in one bucket:

- verified: `hwflow trace` already counts it;
- failing: a linked check fails (a fix, not evidence);
- computed: a manual-method requirement (test, datasheet, analysis, inspection) whose hwflow results (tagged
  simulation measures with their Monte Carlo yield, layout checks, firmware host-test checks) all pass. `--accept`
  names them as its evidence (`evidence: {auto: [sim, fw_test]}`); trace then re-evaluates them on every run, so the
  record never goes stale by itself;
- needs a check: a `simulation` or `layout` requirement that no current check names, or an `auto:` source with no
  current result;
- needs a record: a manual-method requirement with nothing computed behind it. `--stubs` writes an artifact template
  per requirement, `--set R8=pass` writes the record with the current evidence hash once the artifact is filled in.

`--refresh` first brings the computed results up to date: the firmware host tests (when fw/test exists and the stored
run is not current) and a `--until sim --mc N` build when Monte Carlo did not run on the current design.
"""

from __future__ import annotations

import io
import re
import time
from pathlib import Path

from ..core.design import Design
from .status import _design_hash, _fresh_status, _save_status
from .trace import (MANUAL_METHODS, MC_MIN_YIELD, STUB_MARK, _evidence_hash, _manual_evidence,
                    _natural, trace)

MC_RUNS = 100
STUB_DIR = "notes/evidence"


# ------------------------------------------------------------------------------------------------ computed results

def run_fw_test(d: Design) -> tuple[int, str, dict]:
    """`hwflow fw test`: build and run the firmware host tests, store the result (with the per-requirement checks)."""
    from ..core.libs import Libs
    from ..fw.hosttest import host_test
    from .status import _out
    rc, text, summary = host_test(d, Libs(design=d), _out(d))
    _save_status(d, "fw_test", {**summary, "design_hash": _design_hash(d), "at": time.time()})
    return rc, text, summary


def fw_state(d: Design, s: dict) -> dict:
    """The stored host-test run: `none` (no fw/test), `missing`, `stale` (design or firmware changed) or `current`."""
    from ..fw.hosttest import source_hash
    has_tests = any((d.dir / "fw").glob("**/test/*.c")) if (d.dir / "fw").is_dir() else False
    ft = s.get("fw_test")
    if not has_tests:
        return {"state": "none"}
    if not ft:
        return {"state": "missing"}
    if ft.get("src_hash") != source_hash(d) or "reqs" not in ft:
        return {"state": "stale"}
    return {"state": "current", "ok": ft.get("rc") == 0 and ft.get("ran"), "checks": ft.get("checks"),
            "failed": ft.get("failed"), "reqs": sorted(ft.get("reqs") or {}, key=_natural)}


def mc_state(d: Design, s: dict) -> dict:
    """Monte Carlo on the current design: `none` (no analysis takes part), `missing`, or `current` with the yields."""
    sim = (d.raw.get("sim") or {})
    if not any(a.get("monte_carlo", True) and a.get("measures") for a in sim.get("analyses") or []):
        return {"state": "none"}
    runs = (s.get("sim") or {}).get("monte_carlo") or []
    if not runs:
        return {"state": "missing"}
    lim = float(sim.get("mc_min_yield", MC_MIN_YIELD))
    low = [{"analysis": r["analysis"], "yield": r["yield"],
            "unfinished": r.get("excluded_from_stats", 0)} for r in runs if r.get("yield", 1) < lim - 1e-9]
    return {"state": "current", "runs": (s.get("sim") or {}).get("mc_runs") or runs[0].get("runs"),
            "min_yield": min(r.get("yield", 1) for r in runs), "limit": lim, "below": low,
            "seconds": ((s.get("sim") or {}).get("timing") or {}).get("mc_s")}


def refresh(d: Design, mc: int = MC_RUNS, log=print) -> Design:
    """Bring the computed evidence up to date: the firmware host tests when they are not current, and a
    `--until sim --mc <mc>` build when the current design has no Monte Carlo result of at least `mc` runs.
    Never runs a layout."""
    s = _fresh_status(d)
    if fw_state(d, s)["state"] in ("missing", "stale"):
        rc, _, summ = run_fw_test(d)
        log(f"[evidence] firmware host tests: {'PASS' if rc == 0 else 'FAIL'} "
            f"({summ.get('checks', '?')} checks, {summ.get('failed', '?')} failed)")
    m = mc_state(d, s)
    if mc and (m["state"] == "missing" or (m["state"] == "current" and (m.get("runs") or 0) < mc)):
        from .runner import BuildOptions, run_build
        t0 = time.time()
        res = run_build(d, BuildOptions(until="sim", mc=mc))
        log(f"[evidence] build --until sim --mc {mc}: rc {res['rc']} in {time.time() - t0:.0f} s")
        d = Design.load(d.dir)
    return d


# ---------------------------------------------------------------------------------------------------------- plan

def plan(d: Design) -> dict:
    """The sign-off evidence plan (see the module docstring)."""
    import yaml
    s = _fresh_status(d)
    t = trace(d)
    rp = d.dir / "requirements.yaml"
    reqs = {q["id"]: q for q in ((yaml.safe_load(rp.read_text()) or {}).get("requirements") or [])} \
        if rp.exists() else {}
    out = {"verified": [], "failing": [], "computed": [], "needs_check": [], "needs_record": []}
    fws = fw_state(d, s)
    fw_msgs = {k: v.get("msgs") or [] for k, v in ((s.get("fw_test") or {}).get("reqs") or {}).items()} \
        if fws["state"] == "current" else {}
    for r in t["rows"]:
        q = reqs.get(r["id"], {})
        item = {"id": r["id"], "priority": r["priority"], "method": r["method"], "text": r["text"],
                "measures": r["measures"]}
        if r["id"] in fw_msgs:
            item["fw_checks"] = fw_msgs[r["id"]]
        if r["status"] == "FAIL":
            out["failing"].append(item)
        elif r["status"] != "UNVERIFIED":
            out["verified"].append({**item, "by": r.get("auto") or r["status"]})
        elif r.get("auto") or r["method"] not in MANUAL_METHODS:
            out["needs_check"].append({**item, "reason": r.get("evidence_reason", "")})
        elif r["measures"] and all(m.endswith(":ok") for m in r["measures"]):
            item["sources"] = r["sources"]
            out["computed"].append(item)
        else:
            ev = q.get("evidence")
            item["reason"] = r.get("evidence_reason", "")
            if isinstance(ev, dict) and ev.get("artifact"):
                item["artifact"] = ev["artifact"]
            out["needs_record"].append(item)
    return {"evidence_hash": t["evidence_hash"], "design_hash": t["design_hash"], "mc": mc_state(d, s),
            "fw_test": fws, **out,
            "counts": {"total": t["total"], "verified": t["covered"], "failing": t["failing"],
                       "unverified": t["unverified"], "unverified_must": t["unverified_must"]}}


def render(p: dict, proj: str) -> str:
    L = [f"evidence design hash: {p['evidence_hash']}"]
    m = p["mc"]
    if m["state"] == "missing":
        L.append(f"Monte Carlo: not run on the current design: `hwflow evidence {proj} --refresh` "
                 f"(or `hwflow build {proj} --until sim --mc {MC_RUNS}`)")
    elif m["state"] == "current":
        L.append(f"Monte Carlo: {m['runs']} runs, lowest yield {m['min_yield'] * 100:.0f}% (limit "
                 f"{m['limit'] * 100:.0f}%)" + (f", {m['seconds']:g} s" if m.get("seconds") else "")
                 + "".join(f"; {b['analysis']} {b['yield'] * 100:.0f}%"
                           + (f" ({b['unfinished']} runs did not finish)" if b["unfinished"] else "")
                           for b in m["below"]))
    f = p["fw_test"]
    if f["state"] in ("missing", "stale"):
        L.append(f"firmware host tests: {f['state']}: `hwflow evidence {proj} --refresh` (or `hwflow fw test {proj}`)")
    elif f["state"] == "current":
        L.append(f"firmware host tests: {'PASS' if f['ok'] else 'FAIL'}, {f['checks']} checks, {f['failed']} failed; "
                 f"checks name {', '.join(f['reqs']) or 'no requirement (put the id in the CHECK message)'}")
    c = p["counts"]
    L.append(f"requirements: {c['verified']}/{c['total']} verified, {c['failing']} failing, {c['unverified']} unverified "
             f"({c['unverified_must']} must)")
    if p["verified"]:
        L.append("verified: " + ", ".join(f"{r['id']}" + (f" (auto {'+'.join(r['by'])})" if isinstance(r["by"], list)
                                                          else "") for r in p["verified"]))
    if p["failing"]:
        L.append("\nfailing (fix the design or the check; evidence cannot help):")
        for r in p["failing"]:
            L.append(f"  {r['id']:<5} {r['priority']:<6} {', '.join(x for x in r['measures'] if x.endswith(':FAIL'))}")
    if p["computed"]:
        L.append(f"\ncomputed: hwflow results name these and all pass; if they prove the requirement, "
                 f"`hwflow evidence {proj} --accept {','.join(r['id'] for r in p['computed'])}` "
                 f"(writes `evidence: {{auto: [...]}}`):")
        for r in p["computed"]:
            note = "; the bench test goes to the first-article list" if r["method"] == "test" else ""
            L.append(f"  {r['id']:<5} {r['priority']:<6} {r['method']:<10} auto {'+'.join(r['sources'])}: "
                     f"{', '.join(r['measures'][:4])}{' ...' if len(r['measures']) > 4 else ''}{note}")
            if r.get("fw_checks"):
                L.append("        fw checks: " + "; ".join(f'"{x}"' for x in r["fw_checks"]))
    if p["needs_check"]:
        L.append("\nneeds a check (tag a measure or layout check with `req:`, or run the result named):")
        for r in p["needs_check"]:
            L.append(f"  {r['id']:<5} {r['priority']:<6} {r['method']:<10} {r['reason']}")
    if p["needs_record"]:
        L.append(f"\nneeds a record ({len(p['needs_record'])}; write the artifact, then `hwflow evidence {proj} "
                 f"--set <ids>=pass[:artifact]`; `--stubs` writes {STUB_DIR}/<id>.md templates):")
        for r in p["needs_record"]:
            L.append(f"  {r['id']:<5} {r['priority']:<6} {r['method']:<10} {r['text'][:70]}")
            if r.get("reason") and "no evidence record yet" not in r["reason"]:
                L.append(f"        {r['reason']}")
        r0 = p["needs_record"][0]
        L.append("record template (requirements.yaml; `--set` writes it):")
        L.append(f"    evidence: {{status: pass, artifact: {STUB_DIR}/{r0['id']}.md, design_hash: {p['evidence_hash']}}}")
    return "\n".join(L)


# -------------------------------------------------------------------------------------------- requirements.yaml

def _yaml_rt(text: str):
    """A round-trip YAML loader/dumper whose indentation reproduces `text` unchanged, when one does."""
    from ruamel.yaml import YAML
    best = None
    for m, sq, off in ((2, 4, 2), (2, 2, 0), (4, 4, 2), (2, 2, 2), (4, 6, 4)):
        y = YAML()
        y.preserve_quotes = True
        y.width = 10 ** 6
        y.indent(mapping=m, sequence=sq, offset=off)
        buf = io.StringIO()
        y.dump(y.load(text), buf)
        if buf.getvalue() == text:
            return y, True
        best = best or y
    return best, False


def update_evidence(d: Design, changes: dict[str, dict]) -> list[str]:
    """Set `evidence` of the listed requirements in requirements.yaml (comments and layout kept when the file
    round-trips). Returns warnings."""
    from ruamel.yaml.comments import CommentedMap, CommentedSeq
    rp = d.dir / "requirements.yaml"
    text = rp.read_text()
    y, exact = _yaml_rt(text)
    doc = y.load(text)
    found = set()
    for q in doc.get("requirements") or []:
        if q.get("id") in changes:
            m = CommentedMap()
            old = q.get("evidence")
            keep = {k: v for k, v in old.items() if not (k == "status" and v == "not_checked")} \
                if isinstance(old, dict) else {}
            for k, v in {**keep, **changes[q["id"]]}.items():
                if isinstance(v, list):
                    v = CommentedSeq(v)
                    v.fa.set_flow_style()
                m[k] = v
            m.fa.set_flow_style()
            q["evidence"] = m
            found.add(q["id"])
    missing = sorted(set(changes) - found, key=_natural)
    if missing:
        raise SystemExit(f"evidence: no requirement {', '.join(missing)} in {rp}")
    buf = io.StringIO()
    y.dump(doc, buf)
    tmp = rp.with_name(rp.name + ".tmp")
    tmp.write_text(buf.getvalue())
    tmp.replace(rp)
    return [] if exact else [f"{rp.name}: indentation re-written (no round-trip layout matched the file)"]


def accept(d: Design, ids: list[str]) -> dict[str, list[str]]:
    """`evidence: {auto: [sources]}` for computed requirements (all of them with `all`). Only requirements whose
    hwflow results all pass are accepted; returns {id: sources}."""
    p = plan(d)
    comp = {r["id"]: r["sources"] for r in p["computed"]}
    want = list(comp) if ids == ["all"] else ids
    bad = [i for i in want if i not in comp]
    if bad:
        raise SystemExit(f"evidence --accept: {', '.join(bad)} not in the computed list (verified already, failing, "
                         f"or no hwflow result names it): see `hwflow evidence {d.dir}`")
    update_evidence(d, {i: {"auto": comp[i]} for i in want})
    return {i: comp[i] for i in want}


def set_records(d: Design, spec: str) -> list[tuple[str, str, str]]:
    """`R8,R10=pass:notes/evidence/datasheets.md`: a manual record with the current evidence hash. The artifact
    (default notes/evidence/<id>.md) must be a filled-in file inside the project that names each requirement id."""
    m = re.fullmatch(r"\s*([^=]+?)\s*=\s*(pass|fail)\s*(?::\s*(\S+))?\s*", spec)
    if not m:
        raise SystemExit(f"evidence --set {spec!r}: use <ids>=pass|fail[:artifact], e.g. R8,R10=pass:notes/x.md")
    ids = [x for x in re.split(r"[,\s]+", m.group(1)) if x]
    state, art = m.group(2), m.group(3)
    h = _evidence_hash(d)
    out, changes = [], {}
    for i in ids:
        a = art or f"{STUB_DIR}/{i}.md"
        rec = {"status": state, "artifact": a, "design_hash": h}
        st, why = _manual_evidence(d, rec, h)
        if st == "UNVERIFIED":
            raise SystemExit(f"evidence --set {i}: {why} ({a})")
        if not re.search(rf"(?<![\w-]){re.escape(i)}(?![\w-])", (d.dir / a).read_text(errors="replace")):
            raise SystemExit(f"evidence --set {i}: {a} does not name {i}; give each requirement its own section")
        changes[i] = rec
        out.append((i, state, a))
    update_evidence(d, changes)
    return out


def write_stubs(d: Design, ids: list[str] | None = None) -> list[Path]:
    """notes/evidence/<id>.md templates for the requirements that need a record (or `ids`); an existing file is
    never overwritten. The stub marker keeps an unfilled template from counting as evidence."""
    p = plan(d)
    rows = [r for r in p["needs_record"] if not ids or r["id"] in ids]
    import yaml
    rp = d.dir / "requirements.yaml"
    reqs = {q["id"]: q for q in ((yaml.safe_load(rp.read_text()) or {}).get("requirements") or [])}
    made = []
    for r in rows:
        f = d.dir / STUB_DIR / f"{r['id']}.md"
        if f.exists():
            continue
        q = reqs.get(r["id"], {})
        how = {"datasheet": "the datasheet values that meet the limit: part, document, revision, page or table, value",
               "analysis": "the calculation with its inputs and their sources (or the notes/ file that holds it)",
               "inspection": "what was inspected (drawing, BOM, 3D model, label text) and what it shows",
               "test": "the bench procedure and the measured result; before the board exists, the design-level "
                       "proof and the first-article test that will confirm it"}[r["method"]]
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            f"<!-- {STUB_MARK}: fill in the result, then delete this line -->\n"
            f"# {r['id']} evidence ({r['method']})\n\n"
            f"Requirement ({r['priority']}): {q.get('text', '').strip()}\n\n"
            + (f"Acceptance: {str(q.get('acceptance')).strip()}\n\n" if q.get("acceptance") else "")
            + (f"hwflow results that name {r['id']}: {', '.join(r['measures'])}\n\n" if r["measures"] else "")
            + f"## Result\n\nResult: pass | fail\n\nEvidence: {how}.\n")
        made.append(f)
    return made


# ---------------------------------------------------------------------------------------------------------- CLI

def cmd(d: Design, a) -> int:
    import json
    proj = str(a.project)
    if a.refresh:
        d = refresh(d, a.mc, log=(lambda *x: print(*x, file=__import__("sys").stderr)) if a.json else print)
    if a.accept:
        ids = [x for x in re.split(r"[,\s]+", a.accept) if x]
        for i, src in accept(d, ids).items():
            print(f"{i}: evidence: {{auto: [{', '.join(src)}]}}")
    for spec in a.set or []:
        for i, st, art in set_records(d, spec):
            print(f"{i}: evidence: {{status: {st}, artifact: {art}, design_hash: {_evidence_hash(d)}}}")
    if a.stubs:
        for f in write_stubs(d):
            print(f"stub {f.relative_to(d.dir)}")
    p = plan(Design.load(d.dir) if (a.accept or a.set) else d)
    if a.json:
        print(json.dumps({"schema": "hwflow.evidence/1", **p}, separators=(",", ":"), default=str))
    else:
        print(render(p, proj))
    c = p["counts"]
    return 0 if not c["failing"] and not c["unverified_must"] else 1
