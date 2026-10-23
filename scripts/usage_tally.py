"""Token and turn tally of one headless /hw-design run, from the Claude Code transcripts.

  uv run python scripts/usage_tally.py <project>                  # print a table
  uv run python scripts/usage_tally.py <project> --json out.json  # also save it
  uv run python scripts/usage_tally.py <project> --compare evals/baselines/<name>/metrics.json
  uv run python scripts/usage_tally.py --history <ui_history.jsonl[.gz]>      # wall time per stage (item 12)
  uv run python scripts/usage_tally.py --cache-ttl <metrics.json> [--usage usage.jsonl] [--session-id <id>
        --transcripts <dir>]                                                  # 1 h cache check (item 14)

The run is the session id in projects/<project>/.session plus its subagents. Transcripts live in
~/.claude/projects/<repo path with / replaced by ->/. Each message is counted once (the transcript
repeats a message while it streams). Haiku summaries of WebFetch results are not in the transcripts,
so Haiku is an undercount.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import time
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


def _hms(t: str) -> int:
    h, m, sec = (int(x) for x in t.split(":"))
    return h * 3600 + m * 60 + sec


def stage_times(history: Path) -> list[dict]:
    """Wall time per Agent (subagent) launch, from the UI history (item 12). History times are HH:MM:SS, so a
    clock that goes backwards is a new day. A launch lasts until the next launch, status, user message or
    question event; launches within 10 s of each other (parallel) share the end of the group, so their times
    overlap and must not be added up. The last tool event before the end is the best available end marker."""
    opener = gzip.open if str(history).endswith(".gz") else open
    with opener(history, "rt") as fh:
        evs = [json.loads(x) for x in fh if x.strip()]
    day, prev, abs_t = 0, None, []
    for e in evs:
        t = _hms(e["t"]) if e.get("t") else (prev or 0)
        if prev is not None and t < prev - 60:
            day += 86400
        prev = t
        abs_t.append(t + day)
    stages = []
    for k, e in enumerate(evs):
        if e["kind"] == "tool" and e["text"].startswith(("Agent:", "Task:")):
            end = abs_t[-1]
            for j in range(k + 1, len(evs)):
                f = evs[j]
                if f["kind"] in ("status", "user", "questions") or (
                        f["kind"] == "tool" and f["text"].startswith(("Agent:", "Task:")) and abs_t[j] - abs_t[k] > 10):
                    end = abs_t[j]
                    break
            stages.append({"stage": e["text"].split(":", 1)[1].strip(), "start": e["t"], "seconds": end - abs_t[k]})
    turns, start = [], None
    for k, e in enumerate(evs):
        if e["kind"] == "status" and e["text"] == "agent started":
            start = k
        elif e["kind"] == "status" and start is not None and (
                e["text"].startswith("agent finished") or e["text"].startswith("agent exited")
                or e["text"] == "stopped by user"):
            turns.append({"start": evs[start]["t"], "seconds": abs_t[k] - abs_t[start]})
            start = None
    return [{"turn_seconds": sum(x["seconds"] for x in turns), "turns": len(turns)}] + stages


def show_stages(history: Path):
    rows = stage_times(history)
    print(f"{rows[0]['turns']} agent turns, {rows[0]['turn_seconds'] / 3600:.1f} h in turns; stages (launch to next event):")
    by: dict = defaultdict(lambda: [0, 0])
    for r in rows[1:]:
        mm = re.match(r"Stage (\d+)", r["stage"])
        key = f"Stage {mm.group(1)}" if mm else r["stage"]
        by[key][0] += 1
        by[key][1] += r["seconds"]
        print(f"  {r['start']}  {r['seconds'] / 60:7.1f} min  {r['stage']}")
    print("\ngrouped (Stage N labels merged):")
    for k, (n, sec) in sorted(by.items(), key=lambda x: -x[1][1]):
        print(f"  {sec / 60:7.1f} min  {n:2}x  {k}")


# relative prices in units of the base input price: 5 min write 1.25, 1 h write 2.0, read 0.1
W5, W1, RD = 1.25, 2.0, 0.1


def cache_ttl_check(metrics: Path, usage: Path | None = None, session: str | None = None,
                    transcripts: Path | None = None) -> dict:
    """Does CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL=1h pay off? (item 14)

    From metrics.json: cache writes and reads per agent. From usage.jsonl (main session turns). From the
    transcripts, when present: writes split into 5 min and 1 h, and the gaps between the messages of each
    file. A gap over 5 min means a 5 min cache would have expired and the next message would have re-written
    its whole prefix (the cache read of that message) instead of reading it."""
    out: dict = {}
    t = json.loads(metrics.read_text())["tokens"]["by_agent"]
    w = sum(v["tokens"]["cache_creation_input_tokens"] for v in t.values())
    r = sum(v["tokens"]["cache_read_input_tokens"] for v in t.values())
    out["metrics"] = {"cache_write": w, "cache_read": r, "reads_per_write": round(r / w, 1),
                      "by_agent": {k: round(v["tokens"]["cache_read_input_tokens"]
                                            / max(1, v["tokens"]["cache_creation_input_tokens"]), 1)
                                   for k, v in t.items()}}
    if usage and usage.exists():
        rows = [json.loads(x) for x in usage.read_text().splitlines() if x.strip()]
        out["usage_jsonl"] = {"turns": len(rows),
                              "main_session_write": sum(x["usage"]["cache_creation_input_tokens"] for x in rows),
                              "main_session_read": sum(x["usage"]["cache_read_input_tokens"] for x in rows)}
    if session and transcripts and (transcripts / f"{session}.jsonl").exists():
        files = [transcripts / f"{session}.jsonl", *sorted((transcripts / session / "subagents").glob("agent-*.jsonl"))]
        w5 = w1 = 0
        expired_reads = 0
        gaps = Counter()
        for p in files:
            prev = None
            for ts, m in _messages(p):
                u = m["usage"]
                cc = u.get("cache_creation") or {}
                w5 += cc.get("ephemeral_5m_input_tokens", 0) or 0
                w1 += cc.get("ephemeral_1h_input_tokens", 0) or 0
                if ts:
                    cur = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
                    if prev is not None:
                        g = cur - prev
                        gaps["<=5min" if g <= 300 else "5-60min" if g <= 3600 else ">60min"] += 1
                        if 300 < g <= 3600:
                            expired_reads += u.get("cache_read_input_tokens", 0) or 0
                    prev = cur
        cost_1h = w1 * W1 + w5 * W5 + r * RD
        # same traffic with a 5 min cache: all writes at 1.25, plus the prefixes that expired are written, not read
        cost_5m = (w1 + w5) * W5 + expired_reads * W5 + (r - expired_reads) * RD
        out["transcripts"] = {"write_5m": w5, "write_1h": w1, "gaps": dict(gaps), "reads_after_5_to_60min_gap": expired_reads,
                              "cost_1h_Minput_equiv": round(cost_1h / 1e6, 2), "cost_5m_Minput_equiv": round(cost_5m / 1e6, 2),
                              "saving_of_1h_pct": round(100 * (cost_5m - cost_1h) / cost_5m, 1)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", nargs="?")
    ap.add_argument("--json")
    ap.add_argument("--compare", help="metrics.json of a baseline")
    ap.add_argument("--history", help="UI history (jsonl or jsonl.gz): wall time per stage")
    ap.add_argument("--cache-ttl", help="metrics.json: cache write against read check")
    ap.add_argument("--usage", help="usage.jsonl of the run (with --cache-ttl)")
    ap.add_argument("--session-id")
    ap.add_argument("--transcripts", help="directory with the session transcripts (default: this repo's)")
    a = ap.parse_args()
    if a.history:
        return show_stages(Path(a.history))
    if a.cache_ttl:
        print(json.dumps(cache_ttl_check(Path(a.cache_ttl), Path(a.usage) if a.usage else None, a.session_id,
                                         Path(a.transcripts) if a.transcripts else transcript_dir()), indent=1))
        return
    if not a.project:
        ap.error("project, --history or --cache-ttl is required")
    t = tally(a.project)
    base = json.loads(Path(a.compare).read_text()).get("tokens") if a.compare else None
    show(t, base)
    if a.json:
        Path(a.json).write_text(json.dumps(t, indent=1))


if __name__ == "__main__":
    main()
