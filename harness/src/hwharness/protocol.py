"""The product-person protocol: approved baseline, conflicts.md and the inbox (questions and answers)."""

from __future__ import annotations

import json
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


# what approval_findings reads besides design.yaml (the check stage's declared inputs, hwflow.pipeline.hooks)
APPROVAL_INPUTS = ("build/approved.json", "conflicts.md", "inbox/")


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
