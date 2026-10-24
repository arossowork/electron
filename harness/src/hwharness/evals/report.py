"""Aggregate trial cards, compare them with a baseline and render the evaluation report (markdown and JSON).

Used by the local harness (`evals/harness.py`) and by the platform's EvalWorkflow, so a baseline is the same
document in both. A baseline is an aggregate (the `aggregate` field of an eval document, or the legacy
`evals/baseline.json`), keyed `<fixture>::<model>`. Stdlib only.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from .cards import USAGE_KEYS

SCHEMA = "hwflow.eval/1"
MIN_TOLERANCE = 5.0


def aggregate(cards: list[dict]) -> dict:
    """Per fixture and model: mean, spread and the mean of every point item; plus `_overall` per model."""
    groups: dict[str, list] = {}
    for c in cards:
        groups.setdefault(f"{c['fixture']}::{c['model']}", []).append(c)
    out: dict = {}
    for k, cs in sorted(groups.items()):
        s = [c["score"] for c in cs]
        out[k] = {
            "n": len(s),
            "mean": round(statistics.mean(s), 2),
            "std": round(statistics.stdev(s), 2) if len(s) > 1 else 0.0,
            "min": min(s),
            "max": max(s),
            "points": {p: round(statistics.mean(c["points"][p] for c in cs), 2) for p in cs[0]["points"]},
            "questions": round(statistics.mean(c.get("questions") or 0 for c in cs), 1),
            "seconds": round(statistics.mean(c.get("seconds") or 0 for c in cs), 0),
            "cost_usd_equiv": round(statistics.mean(c.get("cost_usd") or 0 for c in cs), 3),
            "tokens_k": {
                t: round(statistics.mean((c.get("tokens") or {}).get(t, 0) for c in cs) / 1e3) for t in USAGE_KEYS
            },
        }
    models = sorted({c["model"] for c in cards})
    out["_overall"] = {m: round(statistics.mean(c["score"] for c in cards if c["model"] == m), 2) for m in models}
    return out


def baseline_aggregate(doc: dict) -> dict:
    """The aggregate of a baseline document: an eval document or a bare aggregate (the legacy baseline.json)."""
    return doc["aggregate"] if doc.get("schema") == SCHEMA else doc


def compare(agg: dict, base: dict) -> list[dict]:
    """One row per fixture and model present in both: the means, the delta, the tolerance and the verdict
    (IMPROVED, same or REGRESSION). The tolerance is the larger of 5 points and twice the trial spread, so
    the noise of a single trial does not count as a change. `points`: items that moved by 2 or more."""
    rows = []
    for k, v in agg.items():
        if k.startswith("_") or k not in base:
            continue
        b = base[k]
        delta = v["mean"] - b["mean"]
        tol = max(MIN_TOLERANCE, 2 * max(v["std"], b["std"]))
        tag = "REGRESSION" if delta < -tol else "IMPROVED" if delta > tol else "same"
        moved = {
            p: {"baseline": b["points"].get(p, 0), "now": pv}
            for p, pv in v["points"].items()
            if abs(pv - b["points"].get(p, 0)) >= 2
        }
        rows.append(
            {"key": k, "baseline": b["mean"], "mean": v["mean"], "delta": round(delta, 2), "tolerance": tol,
             "tag": tag, "points": moved}
        )
    return rows


def compare_lines(rows: list[dict]) -> list[str]:
    """The CLI text of `compare` rows."""
    lines = []
    for r in rows:
        lines.append(
            f"{r['key']:<34} {r['baseline']:6.1f} -> {r['mean']:6.1f}  ({r['delta']:+.1f}, tol {r['tolerance']:.1f})  {r['tag']}"
        )
        for p, m in r["points"].items():
            lines.append(f"    {p:<16} {m['baseline']:6.1f} -> {m['now']:6.1f}")
    return lines


def verdict(rows: list[dict], n_scored: int) -> str:
    """FAIL when a fixture regressed or nothing was scored; else PASS."""
    return "FAIL" if n_scored == 0 or any(r["tag"] == "REGRESSION" for r in rows) else "PASS"


def eval_document(
    *,
    suite: str,
    run_id: str | None,
    cards: list[dict],
    errors: list[dict],
    baseline: dict | None,
    baseline_name: str | None = None,
    options: dict | None = None,
    created: str | None = None,
) -> dict:
    agg = aggregate(cards) if cards else {"_overall": {}}
    rows = compare(agg, baseline_aggregate(baseline)) if baseline else []
    return {
        "schema": SCHEMA,
        "suite": suite,
        "run_id": run_id,
        "created": created,
        "options": options or {},
        "trials": cards,
        "errors": errors,
        "aggregate": agg,
        "baseline": {"name": baseline_name, "overlap": len(rows)} if baseline else None,
        "comparison": rows,
        "verdict": verdict(rows, len(cards)),
    }


def render_markdown(doc: dict) -> str:
    """REPORT.md of an evaluation."""
    agg = doc["aggregate"]
    L = [f"# Evaluation {doc['suite']}", ""]
    L.append(f"Verdict: **{doc['verdict']}**" + (f" (run {doc['run_id']})" if doc.get("run_id") else ""))
    o = doc.get("options") or {}
    if o:
        L.append("")
        L.append("Options: " + ", ".join(f"{k}={o[k]}" for k in sorted(o) if o[k] not in (None, [], "")))
    ov = agg.get("_overall") or {}
    if ov:
        L += ["", "Overall mean score per model: " + ", ".join(f"{m} {s:.1f}" for m, s in sorted(ov.items()))]
    cmp_by_key = {r["key"]: r for r in doc.get("comparison") or []}
    L += ["", "## Scores", "", "| fixture::model | n | mean | std | min | max | baseline | change |",
          "|---|---|---|---|---|---|---|---|"]
    for k, v in agg.items():
        if k.startswith("_"):
            continue
        r = cmp_by_key.get(k)
        base = f"{r['baseline']:.1f}" if r else "-"
        chg = f"{r['delta']:+.1f} {r['tag']}" if r else "-"
        L.append(f"| {k} | {v['n']} | {v['mean']:.1f} | {v['std']:.1f} | {v['min']:.1f} | {v['max']:.1f} | {base} | {chg} |")
    moved = [(r["key"], p, m) for r in doc.get("comparison") or [] for p, m in r["points"].items()]
    if moved:
        L += ["", "## Items that moved by 2 points or more", "", "| fixture::model | item | baseline | now |", "|---|---|---|---|"]
        L += [f"| {k} | {p} | {m['baseline']:.1f} | {m['now']:.1f} |" for k, p, m in moved]
    if doc.get("baseline") is None:
        L += ["", "No baseline: nothing to compare with. Promote this evaluation to make it the baseline of the suite."]
    elif not doc.get("comparison"):
        L += ["", "The baseline has no fixture and model in common with this evaluation."]
    L += ["", "## Trials", "", "| fixture | model | trial | score | questions | design run |", "|---|---|---|---|---|---|"]
    for c in doc["trials"]:
        L.append(
            f"| {c['fixture']} | {c['model']} | {c['trial']} | {c['score']:.1f} | {c.get('questions') or 0} | {c.get('design_run_id') or '-'} |"
        )
    if doc["errors"]:
        L += ["", "## Trials that did not finish", "", "| fixture | model | trial | error |", "|---|---|---|---|"]
        for e in doc["errors"]:
            L.append(
                f"| {e.get('fixture')} | {e.get('model')} | {e.get('trial')} | {str(e.get('error'))[:200].replace('|', '/')} |"
            )
    return "\n".join(L) + "\n"


def load_baseline(path: Path | str) -> dict:
    """A baseline document from a file (an eval document, or the legacy aggregate `evals/baseline.json`)."""
    return json.loads(Path(path).read_text())
