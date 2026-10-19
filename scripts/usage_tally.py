"""Token and turn tally of one headless /hw-design run, from the Claude Code transcripts.

  uv run python scripts/usage_tally.py <project>                  # print a table
  uv run python scripts/usage_tally.py <project> --json out.json  # also save it
  uv run python scripts/usage_tally.py <project> --compare evals/baselines/<name>/metrics.json

The run is the session id in projects/<project>/.session plus its subagents. Transcripts live in
~/.claude/projects/<repo path with / replaced by ->/. Each message is counted once (the transcript
repeats a message while it streams). Haiku summaries of WebFetch results are not in the transcripts,
so Haiku is an undercount.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def transcript_dir() -> Path:
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(ROOT))


def _messages(path: Path):
    seen = set()
    for line in path.read_text(errors="replace").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = d.get("message") or {}
        if d.get("type") != "assistant" or not m.get("usage"):
            continue
        mid = m.get("id")
        if mid in seen:
            continue
        seen.add(mid)
        yield d.get("timestamp"), m


def tally(project: str) -> dict:
    sid = (ROOT / "projects" / project / ".session").read_text().strip()
    base = transcript_dir()
    files = [("orchestrator", base / f"{sid}.jsonl")]
    for p in sorted((base / sid / "subagents").glob("agent-*.jsonl")):
        meta = p.with_suffix(".meta.json")
        kind = json.loads(meta.read_text()).get("agentType", "subagent") if meta.exists() else "subagent"
        files.append((kind, p))
    by_agent: dict = defaultdict(lambda: {"launches": 0, "turns": 0, "tokens": Counter()})
    by_model: Counter = Counter()
    first = last = None
    for kind, p in files:
        if not p.exists():
            continue
        row = by_agent[kind]
        row["launches"] += 0 if kind == "orchestrator" else 1
        for ts, m in _messages(p):
            u = m["usage"]
            n = sum(u.get(f, 0) or 0 for f in FIELDS)
            row["turns"] += 1
            for f in FIELDS:
                row["tokens"][f] += u.get(f, 0) or 0
            by_model[m.get("model", "?")] += n
            if ts:
                first, last = min(first or ts, ts), max(last or ts, ts)
    total = sum(by_model.values())
    cache = sum(r["tokens"]["cache_read_input_tokens"] for r in by_agent.values())
    return {
        "project": project,
        "session": sid,
        "first_message": first,
        "last_message": last,
        "total_tokens": total,
        "cache_read_share": round(cache / total, 3) if total else 0,
        "by_model": dict(by_model),
        "by_agent": {k: {"launches": v["launches"], "turns": v["turns"], "tokens": dict(v["tokens"]),
                         "total": sum(v["tokens"].values())} for k, v in by_agent.items()},
    }


def show(t: dict, base: dict | None = None):
    print(f"{t['project']}: {t['total_tokens'] / 1e6:.1f} M tokens, cache read {t['cache_read_share']:.1%}, "
          f"{t['first_message']} to {t['last_message']}")
    print("\nby model (M tokens)")
    for k, v in sorted(t["by_model"].items(), key=lambda x: -x[1]):
        b = f"  baseline {base['by_model'].get(k, 0) / 1e6:.1f}" if base else ""
        print(f"  {k:32} {v / 1e6:8.1f}{b}")
    print("\nby agent                launches   turns   M tokens")
    for k, v in sorted(t["by_agent"].items(), key=lambda x: -x[1]["total"]):
        b = ""
        if base and k in base["by_agent"]:
            o = base["by_agent"][k]
            b = f"   baseline {o['launches']} / {o['turns']} / {o['total'] / 1e6:.1f}"
        print(f"  {k:22} {v['launches']:8} {v['turns']:7} {v['total'] / 1e6:9.1f}{b}")
    if base:
        d = t["total_tokens"] - base["total_tokens"]
        print(f"\ntotal vs baseline: {d / 1e6:+.1f} M ({d / base['total_tokens']:+.1%})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--json")
    ap.add_argument("--compare", help="metrics.json of a baseline")
    a = ap.parse_args()
    t = tally(a.project)
    base = json.loads(Path(a.compare).read_text()).get("tokens") if a.compare else None
    show(t, base)
    if a.json:
        Path(a.json).write_text(json.dumps(t, indent=1))


if __name__ == "__main__":
    main()
