"""Evaluation harness for the /hw-design agent.

  uv run python evals/harness.py run --models sonnet,opus --trials 3 [--fixtures usb_preamp,led_driver]
  uv run python evals/harness.py score <run_dir>                 # re-score saved runs, no agent calls
  uv run python evals/harness.py compare <run_dir> [--baseline evals/baseline.json]
  uv run python evals/harness.py baseline <run_dir>              # save run aggregate as the new baseline
  uv run python evals/harness.py platform run|get|wait|report|promote   # the same on the platform (evals/remote.py)

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
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from hwharness.agent import (  # noqa: F401  (re-exported for the callers of the old module)
    CALL_TIMEOUT, LIMIT_RE, MODEL_KEYS, USAGE_KEYS, _add_usage, _limit_wait_seconds, _usage, claude, designer_args,
    headless_env, model_usage_delta)
from hwharness.evals.cards import evidence_from_project, score_card  # the card is shared with the platform
from hwharness.evals.report import aggregate, baseline_aggregate, compare_lines  # noqa: F401
from hwharness.evals.report import compare as compare_rows
from hwharness.evals.simulator import PM_PROMPT  # noqa: F401  (the persona is shared with the platform)

ROOT = Path(__file__).resolve().parents[1]
DONE_RE = re.compile(r"^\s*[-*]?\s*status\s*:\s*`?(done|blocked)\b", re.I | re.M)  # explicit final status line only
FIX = ROOT / "evals" / "fixtures"
RUNS = ROOT / "evals" / "runs"
PM_MODEL = os.environ.get("HWFLOW_PM_MODEL", "sonnet")

# ------------------------------------------------------------------ one trial

def run_trial(fx: dict, model: str, trial: int, run_dir: Path, resume_sid: str | None = None) -> dict:
    try:
        return _run_trial(fx, model, trial, run_dir, resume_sid)
    except Exception as e:  # noqa: BLE001  one broken trial must not end the run
        tdir = run_dir / f"{fx['id']}__{model}__t{trial}"
        with open(tdir / "errors.log", "a") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {type(e).__name__}: {e}\n")
        return score_trial(tdir, fx)


def _save_meta(tdir: Path, meta: dict, sid: str | None):
    meta["session_id"] = sid
    (tdir / "meta.json").write_text(json.dumps(meta, indent=1))


def _run_trial(fx: dict, model: str, trial: int, run_dir: Path, resume_sid: str | None) -> dict:
    tdir = run_dir / f"{fx['id']}__{model}__t{trial}"
    proj = tdir / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    if not (proj / "brief.md").exists():
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
    mp = tdir / "meta.json"
    meta = json.loads(mp.read_text()) if mp.exists() else {
        "fixture": fx["id"], "model": model, "trial": trial, "questions": 0, "rounds": 0,
        "designer_calls": 0, "cost_usd": 0.0, "errors": [], "seconds": 0}
    prior_seconds = meta.get("seconds") or 0
    if resume_sid:
        sid = resume_sid
    else:
        r = claude(first, model, ROOT, log=log)
        sid = r.get("session_id")
        meta["designer_calls"] += 1
        meta["cost_usd"] += r.get("total_cost_usd") or 0
        _add_usage(meta, r, chain="designer")
        if r.get("is_error"):
            meta["errors"].append(str(r.get("result"))[:300])
        _save_meta(tdir, meta, sid)
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
            _add_usage(meta, pm)
            with open(proj / "inbox" / "answers.md", "a") as fh:
                fh.write(f"\n## Round {q.get('round', meta['rounds'])}\n{pm.get('result', '')}\n")
            n = q.get("round") or meta["rounds"]
            dest = proj / "inbox" / f"questions_round{n}.json"
            k = 1
            while dest.exists():  # never overwrite an archived round (resumes restart the counter)
                k += 1
                dest = proj / "inbox" / f"questions_round{n}_{k}.json"
            qf.rename(dest)
            msg = "The product person answered. The answers are in inbox/answers.md. Continue the flow."
        elif DONE_RE.search(state):
            break
        else:
            msg = ("Continue the /hw-design flow from state.md through all remaining stages without stopping, "
                   "until sign-off or until you need product answers. Keep a `status:` line at the top of "
                   "state.md (running, waiting-for-answers, blocked, or done).")
        if not sid:
            meta["errors"].append("no session id; cannot continue")
            break
        r = claude(msg, model, ROOT, resume=sid, log=log)
        sid = r.get("session_id") or sid
        meta["designer_calls"] += 1
        meta["cost_usd"] += r.get("total_cost_usd") or 0
        _add_usage(meta, r, chain="designer")
        if r.get("is_error"):
            meta["errors"].append(str(r.get("result"))[:300])
        meta["seconds"] = round(prior_seconds + time.time() - t0, 1)
        _save_meta(tdir, meta, sid)
    meta["seconds"] = round(prior_seconds + time.time() - t0, 1)
    _save_meta(tdir, meta, sid)
    return score_trial(tdir, fx)


def find_session(tdir: Path) -> str | None:
    """Session id of a trial: from meta.json, else from Claude's local session store (first prompt names the trial)."""
    mp = tdir / "meta.json"
    if mp.exists() and json.loads(mp.read_text()).get("session_id"):
        return json.loads(mp.read_text())["session_id"]
    store = Path.home() / ".claude" / "projects" / str(ROOT).replace("/", "-")
    key = str((tdir / "proj").resolve().relative_to(ROOT))
    hits = []
    for f in store.glob("*.jsonl"):
        with open(f, errors="replace") as fh:
            head = fh.read(20000)
        if key in head and "/hw-design new" in head:
            hits.append((f.stat().st_mtime, f.stem))
    return max(hits)[1] if hits else None


# ------------------------------------------------------------------ deterministic scoring

def score_trial(tdir: Path, fx: dict) -> dict:
    proj = tdir / "proj"
    meta = json.loads((tdir / "meta.json").read_text()) if (tdir / "meta.json").exists() else {}
    _fid, _model, _tr = tdir.name.split("__")  # the folder name is the source of truth
    timeout = False
    if (proj / "design.yaml").exists():
        shutil.rmtree(proj / "build", ignore_errors=True)  # never trust the agent's own build
        import signal
        proc = subprocess.Popen([sys.executable, "-m", "hwflow.cli", "build", str(proj), "--force"], cwd=ROOT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            proc.wait(timeout=int(os.environ.get("HWFLOW_SCORE_TIMEOUT", 3600)))
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)  # the build and every ngspice it started
            proc.wait()
            timeout = True  # stages not finished in time score as missing (0 points)
            subprocess.run([sys.executable, "-m", "hwflow.cli", "report", str(proj)], cwd=ROOT,
                           capture_output=True, text=True, timeout=300)
    ev = evidence_from_project(proj, meta, build_timeout=timeout)
    card = score_card(ev, fx, model=meta.get("model") or _model, trial=meta.get("trial") or int(_tr.lstrip("t")))
    (tdir / "score.json").write_text(json.dumps(card, indent=1))
    return card


# ------------------------------------------------------------------ aggregation (hwharness.evals.report)

def load_cards(run_dir: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(run_dir.glob("*/score.json"))]


def compare(agg: dict, base: dict) -> list[str]:
    return compare_lines(compare_rows(agg, baseline_aggregate(base)))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--models", default="opus")
    r.add_argument("--trials", type=int, default=1)
    r.add_argument("--fixtures", default="all")
    r.add_argument("--parallel", type=int, default=2)
    rs = sub.add_parser("resume", help="continue unfinished trials of a run")
    rs.add_argument("run_dir")
    rs.add_argument("--parallel", type=int, default=3)
    rs.add_argument("--only", default="", help="comma-separated fixture ids")
    for name in ("score", "compare", "baseline"):
        p = sub.add_parser(name)
        p.add_argument("run_dir")
        p.add_argument("--only", default="")
        p.add_argument("--baseline", default=str(ROOT / "evals" / "baseline.json"))
    import remote  # evals/remote.py: the same evaluations on the hosted platform (issue #19)

    remote.add_parser(sub)
    a = ap.parse_args()
    if a.cmd == "platform":
        sys.exit(remote.command(a))
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
    run_dir = Path(a.run_dir).resolve()
    if a.cmd == "resume":
        jobs = []
        for t in sorted(run_dir.iterdir()):
            if not t.is_dir() or not (t / "proj").exists():
                continue
            state = (t / "proj" / "state.md").read_text().lower() if (t / "proj" / "state.md").exists() else ""
            if DONE_RE.search(state):
                continue
            fid, model, tr = t.name.split("__")
            if a.only and fid not in a.only.split(","):
                continue
            sid = find_session(t)
            print(f"resume {t.name}: session {sid}")
            jobs.append((fixtures[fid], model, int(tr[1:]), sid))
        with ThreadPoolExecutor(max_workers=a.parallel) as ex:
            list(ex.map(lambda j: run_trial(j[0], j[1], j[2], run_dir, j[3]), jobs))
        cards = [score_trial(t, fixtures[t.name.split("__")[0]]) for t in sorted(run_dir.iterdir())
                 if t.is_dir() and (t / "proj").exists()]
        agg = aggregate(cards)
        (run_dir / "aggregate.json").write_text(json.dumps(agg, indent=1))
        print(json.dumps(agg, indent=1))
        return
    if a.cmd == "score":
        only = [x for x in (a.only or "").split(",") if x]
        cards = []
        for t in sorted(run_dir.iterdir()):
            if not t.is_dir() or not (t / "proj").exists():
                continue
            fid = t.name.split("__")[0]
            if only and fid not in only and (t / "score.json").exists():
                cards.append(json.loads((t / "score.json").read_text()))  # keep the existing card
            else:
                cards.append(score_trial(t, fixtures[fid]))
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
