"""Evaluation harness for the /hw-design agent.

  uv run python evals/harness.py run --models sonnet,opus --trials 3 [--fixtures usb_preamp,led_driver]
  uv run python evals/harness.py score <run_dir>                 # re-score saved runs, no agent calls
  uv run python evals/harness.py compare <run_dir> [--baseline evals/baseline.json]
  uv run python evals/harness.py baseline <run_dir>              # save run aggregate as the new baseline

The agent runs through `claude -p` (the logged-in subscription). The product person is a second
`claude -p` session that answers only from the fixture's hidden spec. Scoring is deterministic:
the scorer rebuilds the agent's design with hwflow and reads files; it never asks an LLM.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "evals" / "fixtures"
RUNS = ROOT / "evals" / "runs"
PM_MODEL = os.environ.get("HWFLOW_PM_MODEL", "haiku")

PM_PROMPT = """You are the product manager in a hardware project. You are not an engineer.
Answer the engineer's questions using ONLY the facts below. Keep each answer to one or two sentences.
If a question is not covered by the facts, answer "No preference, use your judgment."
If the engineer shows numbers that prove two wishes conflict, follow the preference rules in the facts.
Never invent new requirements. Answer every question by its id, as "q1: ...".

FACTS:
{spec}
"""


def claude(prompt: str, model: str, cwd: Path, resume: str | None = None, system: str | None = None,
           headless: bool = True, timeout: int = 3600, log: Path | None = None) -> dict:
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json"]
    if resume:
        cmd += ["--resume", resume]
    if system:
        cmd += ["--system-prompt", system, "--tools", ""]
    else:
        cmd += ["--permission-mode", "acceptEdits"]
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # always use the subscription login
    if headless:
        env["HWFLOW_HEADLESS"] = "1"
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        out = {"is_error": True, "result": p.stdout[-2000:] + p.stderr[-2000:]}
    out["_seconds"] = round(time.time() - t0, 1)
    if log:
        with open(log, "a") as fh:
            fh.write(json.dumps({"prompt": prompt[:500], "model": model, "result": str(out.get("result"))[:4000],
                                 "cost": out.get("total_cost_usd"), "turns": out.get("num_turns"),
                                 "seconds": out["_seconds"]}) + "\n")
    return out


# ------------------------------------------------------------------ one trial

def run_trial(fx: dict, model: str, trial: int, run_dir: Path) -> dict:
    tdir = run_dir / f"{fx['id']}__{model}__t{trial}"
    proj = tdir / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "brief.md").write_text(fx["brief"])
    log = tdir / "transcript.jsonl"
    rel = proj.relative_to(ROOT)
    if fx.get("mode") == "from_requirements":
        shutil.copy(FIX / fx["requirements_file"], proj / "requirements.yaml")
        first = (f"/hw-design new {rel} . The brief is in brief.md and requirements.yaml is final. "
                 f"Start at stage 2 and run to sign-off without asking questions.")
    else:
        first = f"/hw-design new {rel} . The product person's brief is in brief.md."
    t0 = time.time()
    meta = {"fixture": fx["id"], "model": model, "trial": trial, "questions": 0, "rounds": 0,
            "designer_calls": 0, "cost_usd": 0.0, "errors": []}
    r = claude(first, model, ROOT, log=log)
    sid = r.get("session_id")
    meta["designer_calls"] += 1
    meta["cost_usd"] += r.get("total_cost_usd") or 0
    for _ in range(int(fx.get("max_designer_turns", 6))):
        state = (proj / "state.md").read_text().lower() if (proj / "state.md").exists() else ""
        qf = proj / "inbox" / "questions.json"
        if qf.exists():
            q = json.loads(qf.read_text())
            meta["rounds"] += 1
            meta["questions"] += len(q.get("questions", []))
            pm = claude("Questions from the engineer:\n" + json.dumps(q, indent=1), PM_MODEL, tdir,
                        system=PM_PROMPT.format(spec=fx.get("hidden_spec") or "Requirements are final."), log=log)
            meta["cost_usd"] += pm.get("total_cost_usd") or 0
            with open(proj / "inbox" / "answers.md", "a") as fh:
                fh.write(f"\n## Round {q.get('round', meta['rounds'])}\n{pm.get('result', '')}\n")
            qf.rename(proj / "inbox" / f"questions_round{meta['rounds']}.json")
            msg = "The product person answered. The answers are in inbox/answers.md. Continue the flow."
        elif re.search(r"\bdone\b", state):
            break
        else:
            msg = "Continue the /hw-design flow from state.md until sign-off or until you need product answers."
        r = claude(msg, model, ROOT, resume=sid, log=log)
        sid = r.get("session_id", sid)
        meta["designer_calls"] += 1
        meta["cost_usd"] += r.get("total_cost_usd") or 0
        if r.get("is_error"):
            meta["errors"].append(str(r.get("result"))[:300])
    meta["seconds"] = round(time.time() - t0, 1)
    (tdir / "meta.json").write_text(json.dumps(meta, indent=1))
    return score_trial(tdir, fx)


# ------------------------------------------------------------------ deterministic scoring

def _text(proj: Path, names) -> str:
    out = []
    for n in names:
        p = proj / n
        if p.exists():
            out.append(p.read_text(errors="replace"))
    return "\n".join(out).lower()


def _hit(text: str, group: list) -> bool:
    return any(str(k).lower() in text for k in group)


def score_trial(tdir: Path, fx: dict) -> dict:
    proj = tdir / "proj"
    meta = json.loads((tdir / "meta.json").read_text()) if (tdir / "meta.json").exists() else {}
    card: dict = {"fixture": fx["id"], "model": meta.get("model"), "trial": meta.get("trial")}
    has_design = (proj / "design.yaml").exists()
    card["has_design"] = has_design
    sc = {}
    if has_design:
        shutil.rmtree(proj / "build", ignore_errors=True)  # never trust the agent's own build
        subprocess.run([sys.executable, "-m", "hwflow.cli", "build", str(proj), "--force"], cwd=ROOT,
                       capture_output=True, text=True, timeout=1800)
        sp = proj / "build" / "status.json"
        sc = json.loads(sp.read_text()).get("score", {}) if sp.exists() else {}
    card.update({f"m_{k}": v for k, v in sc.items()})
    text = _text(proj, ["requirements.yaml", "conflicts.md", "architecture.md", "SUMMARY.md", "state.md"])
    conf_text = _text(proj, ["conflicts.md", "requirements.yaml", "architecture.md"])
    for q in sorted((proj / "inbox").glob("questions_round*.json")):  # conflicts raised with the PM count too
        conf_text += "\n" + q.read_text(errors="replace").lower()
    pc = fx.get("planted_conflicts") or []
    found = [c["id"] for c in pc if all(_hit(conf_text, g) for g in c["keywords"])]
    er = fx.get("expected_requirements") or []
    req_hits = sum(_hit(text, g) for g in er)
    lim = fx.get("limits") or {}

    def ok(v, f):
        return v is not None and f(v)

    pts = {
        "design_exists": 5 * has_design,
        "erc_clean": 10 * ok(sc.get("erc_errors"), lambda v: v == 0),
        "drc_clean": 10 * ok(sc.get("drc_errors"), lambda v: v == 0),
        "routed": 10 * ok(sc.get("unrouted"), lambda v: v == 0),
        "sim_pass": 15 * (sc.get("sim_pass_ratio") or 0),
        "post_pass": 10 * (sc.get("post_pass_ratio") or 0),
        "req_coverage": 10 * (req_hits / len(er) if er else 1.0),
        "req_verified": 10 * (1 - (sc.get("req_unverified") or 0) / sc["req_total"] if sc.get("req_total") else 0),
        "conflicts": 15 * (len(found) / len(pc) if pc else 1.0),
        "area_ok": 2.5 * ok(sc.get("area_mm2"), lambda v: v <= lim.get("max_area_mm2", 1e9)),
        "cost_ok": 2.5 * ok(sc.get("cost_usd"), lambda v: 0 < v <= lim.get("max_cost_usd", 1e9)),
    }
    card.update({"points": {k: round(v, 2) for k, v in pts.items()}, "score": round(sum(pts.values()), 2),
                 "conflicts_found": found, "req_hits": req_hits, **{k: meta.get(k) for k in
                                                                     ("questions", "rounds", "designer_calls",
                                                                      "cost_usd", "seconds")}})
    (tdir / "score.json").write_text(json.dumps(card, indent=1))
    return card


# ------------------------------------------------------------------ aggregation

def aggregate(cards: list[dict]) -> dict:
    groups: dict[str, list] = {}
    for c in cards:
        groups.setdefault(f"{c['fixture']}::{c['model']}", []).append(c)
    out = {}
    for k, cs in sorted(groups.items()):
        s = [c["score"] for c in cs]
        out[k] = {"n": len(s), "mean": round(statistics.mean(s), 2),
                  "std": round(statistics.stdev(s), 2) if len(s) > 1 else 0.0, "min": min(s), "max": max(s),
                  "points": {p: round(statistics.mean(c["points"][p] for c in cs), 2) for p in cs[0]["points"]},
                  "questions": round(statistics.mean(c.get("questions") or 0 for c in cs), 1),
                  "seconds": round(statistics.mean(c.get("seconds") or 0 for c in cs), 0),
                  "cost_usd_equiv": round(statistics.mean(c.get("cost_usd") or 0 for c in cs), 3)}
    models = sorted({c["model"] for c in cards})
    out["_overall"] = {m: round(statistics.mean(c["score"] for c in cards if c["model"] == m), 2) for m in models}
    return out


def load_cards(run_dir: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(run_dir.glob("*/score.json"))]


def compare(agg: dict, base: dict) -> list[str]:
    lines = []
    for k, v in agg.items():
        if k.startswith("_") or k not in base:
            continue
        b = base[k]
        delta = v["mean"] - b["mean"]
        tol = max(5.0, 2 * max(v["std"], b["std"]))
        tag = "REGRESSION" if delta < -tol else "IMPROVED" if delta > tol else "same"
        lines.append(f"{k:<34} {b['mean']:6.1f} -> {v['mean']:6.1f}  ({delta:+.1f}, tol {tol:.1f})  {tag}")
        for p, pv in v["points"].items():
            bp = b["points"].get(p, 0)
            if abs(pv - bp) >= 2:
                lines.append(f"    {p:<16} {bp:6.1f} -> {pv:6.1f}")
    return lines


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--models", default="opus")
    r.add_argument("--trials", type=int, default=1)
    r.add_argument("--fixtures", default="all")
    r.add_argument("--parallel", type=int, default=2)
    for name in ("score", "compare", "baseline"):
        p = sub.add_parser(name)
        p.add_argument("run_dir")
        p.add_argument("--baseline", default=str(ROOT / "evals" / "baseline.json"))
    a = ap.parse_args()
    fixtures = {p.stem: yaml.safe_load(p.read_text()) for p in sorted(FIX.glob("*.yaml"))}
    if a.cmd == "run":
        sel = list(fixtures) if a.fixtures == "all" else a.fixtures.split(",")
        run_dir = RUNS / time.strftime("%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True)
        jobs = [(fixtures[f], m, t) for f in sel for m in a.models.split(",") for t in range(1, a.trials + 1)]
        with ThreadPoolExecutor(max_workers=a.parallel) as ex:
            cards = list(ex.map(lambda j: run_trial(j[0], j[1], j[2], run_dir), jobs))
        agg = aggregate(cards)
        (run_dir / "aggregate.json").write_text(json.dumps(agg, indent=1))
        print(json.dumps(agg, indent=1))
        print(f"run dir: {run_dir}")
        return
    run_dir = Path(a.run_dir)
    if a.cmd == "score":
        cards = [score_trial(t, fixtures[t.name.split("__")[0]]) for t in sorted(run_dir.iterdir())
                 if t.is_dir() and (t / "proj").exists()]
        agg = aggregate(cards)
        (run_dir / "aggregate.json").write_text(json.dumps(agg, indent=1))
        print(json.dumps(agg, indent=1))
        return
    agg = json.loads((run_dir / "aggregate.json").read_text())
    if a.cmd == "baseline":
        agg["_meta"] = {"run_dir": str(run_dir.relative_to(ROOT)), "saved": time.strftime("%Y-%m-%d %H:%M")}
        Path(a.baseline).write_text(json.dumps(agg, indent=1))
        print(f"baseline saved to {a.baseline}")
        return
    base = json.loads(Path(a.baseline).read_text())
    print("\n".join(compare(agg, base)) or "no overlapping fixture/model pairs")
    print("overall:", base.get("_overall"), "->", agg.get("_overall"))


if __name__ == "__main__":
    main()
