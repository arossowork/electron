"""The product-person protocol: approved baseline, conflicts.md and the inbox (questions and answers)."""

from __future__ import annotations

import json
import re
import time

from hwflow.core.design import Design
from hwflow.pipeline.status import _design_hash, _out


APPROVAL_DEFAULTS = {"cost_pct": 5.0, "size_mm": 0.5}


def _approval_state(d: Design) -> dict:
    """What `hwflow approve` records: layers, board size and BOM cost from design.yaml (backlog 44)."""
    import yaml
    b = dict(d.board, **((yaml.safe_load(d.path.read_text()) or {}).get("board") or {}))  # size as written
    cost, missing = d.cost()
    return {"layers": b["layers"], "width_mm": b["width_mm"], "height_mm": b["height_mm"],
            "cost_usd": round(cost, 4), "unpriced_lines": len(missing)}


def _approved(d: Design) -> dict:
    p = _out(d) / "approved.json"
    return json.loads(p.read_text()) if p.exists() else {}


def approve(d: Design, note: str = "") -> dict:
    """`hwflow approve <proj>`: the product person accepted the current layers, board size and cost.
    Written to build/approved.json; `check` compares later designs with it. The requirements baseline is kept."""
    a = dict(_approval_state(d), at=time.strftime("%Y-%m-%d %H:%M:%S"), design_hash=_design_hash(d), note=note)
    old = _approved(d)
    if "requirements" in old:
        a["requirements"] = old["requirements"]
    (_out(d) / "approved.json").write_text(json.dumps(a, indent=2))
    return a


# ------------------------------------------------------------------ requirements baseline (acceptance diff)

NUM_RE = re.compile(r"(?<![A-Za-z_\d.])[-+]?\d+(?:\.\d+)?(?![\d])")
PM_CITE = re.compile(r"PM round (\d+),?\s*(q\d+)", re.I)


def _norm(s) -> str:
    return " ".join(str(s or "").split()).lower()


def _numbers(s) -> list[str]:
    """The numbers of a requirement's text (spec values), not the ones of identifiers (R7, C23, q2) or round names."""
    s = re.sub(r"\b(round|rounds|q|C|R|A)\s?\d+", " ", str(s or ""))
    return sorted(NUM_RE.findall(s))


def _requirements(d: Design) -> list[dict]:
    import yaml
    p = d.dir / "requirements.yaml"
    if not p.exists():
        return []
    return [r for r in (yaml.safe_load(p.read_text()) or {}).get("requirements") or [] if isinstance(r, dict)]


def _req_state(d: Design) -> dict:
    """What `hwflow approve --requirements` records per requirement: priority, acceptance, verification method and
    the numbers of its text (the spec), with the source it had then."""
    import hashlib
    items = {str(r.get("id")): {"priority": str(r.get("priority", "")).lower(), "acceptance": _norm(r.get("acceptance")),
                                "verification": _norm(r.get("verification")), "numbers": _numbers(r.get("text")),
                                "source": str(r.get("source") or "")}
             for r in _requirements(d)}
    h = hashlib.sha1(json.dumps({k: {x: v[x] for x in ("priority", "acceptance", "verification", "numbers")}
                                 for k, v in items.items()}, sort_keys=True).encode()).hexdigest()[:12]
    return {"hash": h, "items": items}


def approve_requirements(d: Design, note: str = "") -> dict:
    """`hwflow approve <proj> --requirements`: the product person accepted the requirements as they stand (end of
    stage 1, and after each answered question that changes one). `check` then flags any change of a requirement's
    priority, acceptance, verification method or text numbers that no product decision records."""
    a = _approved(d)
    a["requirements"] = dict(_req_state(d), at=time.strftime("%Y-%m-%d %H:%M:%S"), note=note)
    (_out(d) / "approved.json").write_text(json.dumps(a, indent=2))
    return a["requirements"]


def _answered(d: Design) -> dict[int, str]:
    """Round number -> the text of that round's section in inbox/answers.md."""
    p = d.dir / "inbox" / "answers.md"
    if not p.exists():
        return {}
    t = p.read_text()
    return {int(m.group(1)): m.group(2) for m in re.finditer(r"^## Round (\d+)\b(.*?)(?=^## |\Z)", t, re.M | re.S)}


def _req_decision(d: Design, rid: str, old_src: str, new_src: str, text: str,
                  numbers_only: bool = False) -> tuple[str, str] | None:
    """How a requirement change is recorded: (severity, why) or None (not recorded: a product decision taken alone).
    - an answered PM question: a question in inbox/questions*.json with "change": "<id>" or "reqs": [..] that
      inbox/answers.md answers, or a "PM round N, qM" citation new in the source whose answer exists -> warn until
      `hwflow approve --requirements`;
    - pending: `change: <id>` in conflicts.md, or such a question not answered yet -> warn;
    - a derived limit re-allocated in a notes/ file that exists (SKILL.md: no question needed) -> warn, when only its
      numbers changed (`numbers_only`): new words in an acceptance (a firmware restriction, a new condition) are a
      spec change, not a re-allocation."""
    ans = _answered(d)
    inbox = d.dir / "inbox"
    for qf in sorted(inbox.glob("questions*.json")) if inbox.is_dir() else []:
        try:
            qs = json.loads(qf.read_text())
        except ValueError:
            continue
        rnd = qs.get("round")
        for q in qs.get("questions") or []:
            ids = [str(q.get("change") or "")] + [str(x) for x in q.get("reqs") or []]
            if rid not in ids:
                continue
            sec = ans.get(int(rnd)) if isinstance(rnd, int) or str(rnd).isdigit() else None
            if sec and re.search(rf"^{re.escape(str(q.get('id')))}:\s*\S", sec, re.M):
                return "warn", f"answered in round {rnd} {q.get('id')}; run `hwflow approve <proj> --requirements`"
            return "warn", f"asked in {qf.name} {q.get('id')}, not answered yet"
    for rnd, q in set(PM_CITE.findall(new_src)) - set(PM_CITE.findall(old_src)):
        sec = ans.get(int(rnd))
        if sec and re.search(rf"^{re.escape(q)}:\s*\S", sec, re.M | re.I):
            return "warn", f"source cites PM round {rnd}, {q} (answered); run `hwflow approve <proj> --requirements`"
    cf = d.dir / "conflicts.md"
    if cf.exists() and re.search(rf"change:\s*{re.escape(rid)}\b", cf.read_text()):
        return "warn", "recorded in conflicts.md (change: <id>), waiting for the product person"
    if numbers_only and re.search(r"\bderiv(ed|ation)\b", f"{text} {new_src}", re.I):
        for f in re.findall(r"notes/[\w.\-/]+\.md", new_src):
            if (d.dir / f).exists():
                return "warn", f"derived limit re-allocated in {f} (product number unchanged: check it still holds)"
    return None


def acceptance_findings(d: Design) -> list[dict]:
    """A requirement whose priority, acceptance, verification method or text numbers changed against the approved
    requirements baseline (build/approved.json `requirements`), added or removed, is a check error unless a product
    decision records it (_req_decision)."""
    base = _approved(d).get("requirements")
    if not base:
        if _requirements(d):
            return [{"sev": "info", "code": "acceptance", "msg": "no approved requirements baseline; run `hwflow approve "
                     "<proj> --requirements` once the product person has answered the stage-1 questions"}]
        return []
    old, new = base.get("items") or {}, _req_state(d)["items"]
    reqs = {str(r.get("id")): r for r in _requirements(d)}
    out = []
    for rid in sorted(set(old) | set(new), key=lambda x: (len(x), x)):
        o, n = old.get(rid), new.get(rid)
        if o is not None and n is not None:
            what = [k for k in ("priority", "acceptance", "verification", "numbers") if o.get(k) != n.get(k)]
            if not what:
                continue
            desc = ", ".join({"numbers": "text numbers"}.get(k, k) for k in what)
            if "priority" in what:
                desc += f" ({o['priority']} -> {n['priority']})"
            if "numbers" in what:
                gone = sorted(set(o["numbers"]) - set(n["numbers"]))
                added = sorted(set(n["numbers"]) - set(o["numbers"]))
                desc += f" ({', '.join(gone) or '-'} -> {', '.join(added) or '-'})"
            msg = f"{rid}: {desc} changed since the approved requirements of {base.get('at')}"
            must = "must" in (o["priority"], n["priority"])
        elif n is not None:
            msg, must = f"{rid}: new {n['priority']} requirement since the approved requirements of {base.get('at')}", \
                n["priority"] == "must"
        else:
            msg, must = f"{rid}: removed since the approved requirements of {base.get('at')}", o["priority"] == "must"
        r = reqs.get(rid) or {}
        nums_only = bool(o and n and o["priority"] == n["priority"] and o["verification"] == n["verification"]
                         and NUM_RE.sub("#", o["acceptance"]) == NUM_RE.sub("#", n["acceptance"]))
        dec = _req_decision(d, rid, (o or {}).get("source", ""), str(r.get("source") or ""), str(r.get("text") or ""),
                            nums_only)
        if dec:
            out.append({"sev": dec[0], "code": "acceptance", "msg": f"{msg}: {dec[1]}"})
        else:
            out.append({"sev": "error" if must else "warn", "code": "acceptance",
                        "msg": f"{msg}, and no product decision records it. Ask the product person (inbox question "
                               f"with \"change\": \"{rid}\" or \"reqs\": [...]) or restore the approved text; a "
                               f"derived limit is re-allocated in notes/ with the arithmetic"})
    return out


def pending_cost_filter(d: Design, findings: list[dict]) -> list[dict]:
    """Check filter: a cost-limit or cost-doc error while the cost is a recorded, open product question (`change:
    cost` in conflicts.md or an inbox question not answered yet) is a warning. Once answered, the error is back: the
    design meets the limit, or the requirement changes with the answer."""
    rec = _recorded_changes(d).get("cost")
    if not rec or rec.startswith("answered"):
        return findings
    out = []
    for x in findings:
        if x.get("sev") == "error" and x.get("code") in ("cost-limit", "cost-doc"):
            x = dict(x, sev="warn", msg=f"{x['msg']} [cost decision {rec}]")
        out.append(x)
    return out


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


# what approval_findings reads besides design.yaml (the check stage's declared inputs, hwflow.pipeline.hooks)
APPROVAL_INPUTS = ("build/approved.json", "conflicts.md", "inbox/")


def acceptance_facts(d: Design) -> dict:
    """What acceptance_findings reads from requirement prose (the check stage's key strips it): per requirement the
    numbers of its text, its source, and whether the notes/ files the source names exist."""
    out = {}
    for r in _requirements(d):
        src = str(r.get("source") or "")
        notes = {f: (d.dir / f).exists() for f in re.findall(r"notes/[\w.\-/]+\.md", src)}
        out[str(r.get("id"))] = [_numbers(r.get("text")), src, notes,
                                 bool(re.search(r"\bderiv(ed|ation)\b", str(r.get("text") or ""), re.I))]
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
    if "layers" not in old:  # only a requirements baseline so far
        return [{"sev": "info", "code": "approval", "msg": "no approved layers, board size and cost; run `hwflow "
                 "approve <proj>` after the product person accepts them"}]
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
