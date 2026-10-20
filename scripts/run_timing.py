"""Where the wall time of one headless /hw-design run went, from the Claude Code transcripts.

  uv run python scripts/run_timing.py <project>                 # session id from projects/<project>/.session
  uv run python scripts/run_timing.py --session <transcript.jsonl without the suffix>
  uv run python scripts/run_timing.py <project> --json out.json

Per agent type: wall time (first to last transcript line), time inside tool calls, and model time (from a tool
result to the next tool call). Per command kind: count and time. Layout builds are every `hwflow build` that
reaches the layout stage plus every `hwflow layout`. Nested Agent calls are not counted in the parent, so the
subagent's own transcript carries their time.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from usage_tally import ROOT, transcript_dir


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def kind(cmd: str) -> str:
    if "hwflow build" in cmd:
        m = re.search(r"--until\s+(\w+)", cmd)
        return "build" + (f" --until {m.group(1)}" if m else " (full)") + (" --reuse" if "--reuse" in cmd else "")
    for k in ("hwflow layout", "hwflow post", "hwflow optimize", "hwflow check", "hwflow trace", "hwflow lib",
              "hwflow parts", "pytest", "kicad-cli", "ngspice"):
        if k in cmd:
            return k
    return "python adhoc" if "python" in cmd else "other bash"


def is_layout_build(k: str) -> bool:
    return k.startswith("hwflow layout") or (k.startswith("build") and "--until sim" not in k)


def analyse(base: Path) -> dict:
    files = [base.with_suffix(".jsonl")] + sorted((base / "subagents").glob("*.jsonl"))
    agents: dict = defaultdict(lambda: {"wall_s": 0.0, "tool_s": 0.0, "model_s": 0.0, "kinds": defaultdict(lambda: [0, 0.0])})
    layout_builds = 0
    for f in files:
        meta = f.with_suffix(".meta.json")
        at = json.loads(meta.read_text()).get("agentType", "subagent") if meta.exists() else "orchestrator"
        a = agents[at]
        pending, last_result, first, last = {}, None, None, None
        for line in f.read_text(errors="replace").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not e.get("timestamp"):
                continue
            t = _ts(e["timestamp"])
            first, last = first or t, t
            content = (e.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if c.get("type") == "tool_use":
                    if last_result is not None:
                        a["model_s"] += t - last_result
                        last_result = None
                    k = kind(c.get("input", {}).get("command", "")) if c["name"] == "Bash" else c["name"]
                    pending[c["id"]] = (t, k)
                elif c.get("type") == "tool_result" and c.get("tool_use_id") in pending:
                    t0, k = pending.pop(c["tool_use_id"])
                    last_result = t
                    if k == "Agent":
                        continue
                    a["tool_s"] += t - t0
                    a["kinds"][k][0] += 1
                    a["kinds"][k][1] += t - t0
                    layout_builds += is_layout_build(k)
        if first:
            a["wall_s"] += last - first
    return {"agents": {k: dict(v, kinds={x: {"n": n, "s": round(s, 1)} for x, (n, s) in v["kinds"].items()})
                       for k, v in agents.items()},
            "layout_builds": layout_builds,
            "build_calls": sum(x["n"] for v in agents.values() for k, x in
                               ((k, {"n": n}) for k, (n, _) in v["kinds"].items()) if k.startswith(("build", "hwflow layout")))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", nargs="?")
    ap.add_argument("--session")
    ap.add_argument("--json")
    a = ap.parse_args()
    if a.session:
        base = Path(a.session)
    else:
        sid = (ROOT / "projects" / a.project / ".session").read_text().strip()
        base = transcript_dir() / sid
    r = analyse(base)
    for at, v in sorted(r["agents"].items(), key=lambda kv: -kv[1]["wall_s"]):
        print(f"{at}: wall {v['wall_s'] / 60:.0f} min, tools {v['tool_s'] / 60:.0f} min, model {v['model_s'] / 60:.0f} min")
        for k, x in sorted(v["kinds"].items(), key=lambda kv: -kv[1]["s"])[:8]:
            print(f"   {k:30s} n={x['n']:4d}  {x['s'] / 60:6.1f} min")
    print(f"build calls: {r['build_calls']}, of which reach layout: {r['layout_builds']}")
    if a.json:
        Path(a.json).write_text(json.dumps(r, indent=1))


if __name__ == "__main__":
    main()
