"""The local CLI's door to the platform's evaluations (issue #19). Stdlib only.

  HWPLATFORM_API_URL=https://hw.example.com HWPLATFORM_TOKEN=<access token> \\
  uv run python evals/harness.py platform run --suite nightly --fixtures usb_preamp,led_driver \\
      --models sonnet --trials 2 [--baseline evals/baselines/home-thermostat-2026-10-19/baseline.json] [--wait]
  uv run python evals/harness.py platform get <eval_id>
  uv run python evals/harness.py platform wait <eval_id>
  uv run python evals/harness.py platform report <eval_id>          # REPORT.md
  uv run python evals/harness.py platform promote <eval_id> [--name "release 1.2"]

The fixtures are the files of evals/fixtures/ (a `requirements_file` is inlined); the API runs them as design
workflows with a simulated product person and scores them from the runs' manifests and events. The same score
card as `harness.py score` on a local run: both use hwharness.evals.cards.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "evals" / "fixtures"
TERMINAL = {"succeeded", "failed", "cancelled"}


class ApiError(RuntimeError):
    pass


def _conf() -> tuple[str, str]:
    url, tok = os.environ.get("HWPLATFORM_API_URL", ""), os.environ.get("HWPLATFORM_TOKEN", "")
    if not url or not tok:
        raise ApiError("set HWPLATFORM_API_URL and HWPLATFORM_TOKEN (an owner or admin access token)")
    return url.rstrip("/"), tok


def call(method: str, path: str, body: dict | None = None, *, raw: bool = False):
    url, tok = _conf()
    req = urllib.request.Request(
        f"{url}/api/v1{path}",
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310  (the operator's own URL)
            data = r.read()
    except urllib.error.HTTPError as e:
        raise ApiError(f"{method} {path}: {e.code} {e.read().decode(errors='replace')[:500]}") from e
    return data.decode() if raw else json.loads(data)


def fixture_doc(fid: str) -> dict:
    """A fixture file as the API takes it: `requirements_file` becomes the inline `requirements` text."""
    p = FIX / f"{fid}.yaml"
    if not p.exists():
        raise ApiError(f"no fixture {fid} in {FIX}")
    doc = yaml.safe_load(p.read_text())
    doc.setdefault("id", fid)
    rf = doc.pop("requirements_file", None)
    if rf and not doc.get("requirements"):
        doc["requirements"] = (FIX / rf).read_text()
    return doc


def start(args) -> dict:
    ids = sorted(p.stem for p in FIX.glob("*.yaml")) if args.fixtures == "all" else args.fixtures.split(",")
    body: dict = {
        "suite": args.suite,
        "fixtures": [fixture_doc(f) for f in ids],
        "models": [m for m in args.models.split(",") if m],
        "repetitions": args.trials,
    }
    if args.parallel:
        body["max_parallel"] = args.parallel
    if args.budget:
        body["budget_usd"] = args.budget
    if args.baseline:
        body["baseline"] = json.loads(Path(args.baseline).read_text())
    return call("POST", "/evals", body)


def wait(eval_id: str, *, every: float = 20.0, out=print) -> dict:
    last = None
    while True:
        e = call("GET", f"/evals/{eval_id}")
        line = f"{e['status']}: {sum(1 for c in e['children'] if c['status'] in TERMINAL)}/{len(e['children'])} design runs finished"
        if line != last:
            out(line)
            last = line
        if e["status"] in TERMINAL:
            return e
        time.sleep(every)


def command(a) -> int:
    """`harness.py platform <cmd>`; the exit code is 1 when the evaluation's verdict is FAIL."""
    try:
        if a.pcmd == "run":
            e = start(a)
            print(json.dumps({"id": e["id"], "status": e["status"]}))
            if not a.wait:
                return 0
            e = wait(e["id"])
        elif a.pcmd == "get":
            e = call("GET", f"/evals/{a.eval_id}")
            print(json.dumps(e, indent=1))
            return 0
        elif a.pcmd == "wait":
            e = wait(a.eval_id)
        elif a.pcmd == "report":
            print(call("GET", f"/evals/{a.eval_id}/report", raw=True))
            return 0
        elif a.pcmd == "promote":
            print(json.dumps(call("POST", f"/evals/{a.eval_id}/promote-baseline", {"name": a.name}), indent=1))
            return 0
        else:
            raise ApiError(f"unknown platform command {a.pcmd}")
    except ApiError as err:
        print(f"error: {err}")
        return 2
    print(call("GET", f"/evals/{e['id']}/report", raw=True) if e["status"] == "succeeded" else json.dumps(e, indent=1))
    return 0 if e.get("verdict") == "PASS" else 1


def add_parser(sub) -> None:
    p = sub.add_parser("platform", help="run and read evaluations on the hosted platform")
    ps = p.add_subparsers(dest="pcmd", required=True)
    r = ps.add_parser("run")
    r.add_argument("--suite", default="local")
    r.add_argument("--fixtures", default="all")
    r.add_argument("--models", default="")
    r.add_argument("--trials", type=int, default=1)
    r.add_argument("--parallel", type=int, default=0)
    r.add_argument("--budget", type=float, default=0.0)
    r.add_argument("--baseline", default="", help="eval.json / baseline.json file; default: the suite's promoted one")
    r.add_argument("--wait", action="store_true")
    for name in ("get", "wait", "report", "promote"):
        q = ps.add_parser(name)
        q.add_argument("eval_id")
        if name == "promote":
            q.add_argument("--name", default="")
