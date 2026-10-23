"""Local web UI for the /hw-design agent.

Runs on 127.0.0.1 by default (--host 0.0.0.0 in a container). Each chat message starts `claude -p ... --resume <session>` with
stream-json output, so the agent uses the logged-in Claude subscription. Events stream to the
browser over Server-Sent Events.

  uv run python ui/server.py [--port 8765] [--host 127.0.0.1]
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import queue
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))
from harness import (LIMIT_RE, _limit_wait_seconds, _usage, designer_args, headless_env,  # noqa: E402
                     model_usage_delta)
PROJECTS = ROOT / "projects"
STATIC = Path(__file__).parent / "static"
HISTORY_TAIL = 400
PAGE = 300
USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
MODEL_USAGE_KEYS = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
FILES = ["build/REPORT.md", "SUMMARY.md", "requirements.yaml", "conflicts.md", "architecture.md", "state.md",
         "notes/parts.md", "design.yaml", "build/fab/pcb.svg", "build/fab/pcb_3d_top.png", "build/fab/schematic.pdf"]


def _loads(x: str) -> dict:
    try:
        return json.loads(x)
    except ValueError:
        return {"kind": "status", "text": "(unreadable history line)"}


def normalize_questions(raw) -> dict | None:
    """The questions the agent wrote, as {round, questions:[{id,text,why,options}], count}. Tolerates a bare list,
    a missing id (q1, q2, ...), duplicate ids, the keys question/prompt for text, and options that are objects
    (item 36: the count shown to the user is the number of questions in the file, nothing else)."""
    if isinstance(raw, list):
        raw = {"questions": raw}
    if not isinstance(raw, dict):
        return None
    items, seen = [], set()
    for k, x in enumerate(raw.get("questions") or [], 1):
        if isinstance(x, str):
            x = {"text": x}
        if not isinstance(x, dict):
            continue
        qid = str(x.get("id") or f"q{k}")
        while qid in seen:
            qid += "_"
        seen.add(qid)
        opts = [o if isinstance(o, str) else str(o.get("label") or o.get("text") or o) if isinstance(o, dict)
                else str(o) for o in x.get("options") or []]
        items.append({"id": qid, "text": str(x.get("text") or x.get("question") or x.get("prompt") or qid),
                      "why": x.get("why") or "", "options": opts})
    if not items:
        return None
    return {"round": raw.get("round"), "questions": items, "count": len(items)}


class Session:
    """One project: the running claude process, its event history and subscribers."""

    def __init__(self, name: str):
        self.name = name
        self.dir = PROJECTS / name
        self.proc: subprocess.Popen | None = None
        self.history: list[dict] = []
        self.subs: list[queue.Queue] = []
        self.lock = threading.Lock()
        self.queued: list[str] = []  # messages sent while the agent works (item 34)
        self.interrupted = False
        self.last_model = "opus"
        self.live: dict = {}  # live usage per agent, this process (item 35)
        self._reset_run()
        self.n_events = 0  # events in the history file; event i is line i
        hist = self.dir / ".ui_history.jsonl"
        if hist.exists():
            lines = [x for x in hist.read_text().splitlines() if x.strip()]
            self.n_events = len(lines)
            first = self.n_events - HISTORY_TAIL
            self.history = [{**_loads(x), "i": max(first, 0) + k} for k, x in enumerate(lines[-HISTORY_TAIL:])]

    @property
    def session_id(self) -> str | None:
        p = self.dir / ".session"
        return p.read_text().strip() if p.exists() else None

    def emit(self, ev: dict, persist: bool = True):
        ev["t"] = time.strftime("%H:%M:%S")
        with self.lock:
            if persist:
                ev["i"] = self.n_events
                self.n_events += 1
                self.history.append(ev)
                self.history = self.history[-2 * HISTORY_TAIL:]
                with open(self.dir / ".ui_history.jsonl", "a") as fh:
                    fh.write(json.dumps(ev) + "\n")
            for q in list(self.subs):
                q.put(ev)

    def history_page(self, before: int | None = None, limit: int = PAGE) -> dict:
        """Events with index < before (the newest ones when before is None), at most limit, oldest first."""
        hist = self.dir / ".ui_history.jsonl"
        lines = [x for x in hist.read_text().splitlines() if x.strip()] if hist.exists() else []
        end = len(lines) if before is None else max(0, min(before, len(lines)))
        start = max(0, end - max(1, min(limit, 1000)))
        return {"events": [{**_loads(x), "i": start + k} for k, x in enumerate(lines[start:end])],
                "start": start, "total": len(lines)}

    def busy(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def send(self, text: str, model: str, interrupt: bool = False):
        """Start the agent, or queue the message when it is working (item 34). The queue is delivered, joined,
        when the process exits. interrupt=True stops the running turn first and sends at its exit."""
        with self.lock:
            if self.busy():
                self.queued.append(text)
                n = len(self.queued)
                self.last_model = model
                kill = interrupt
                if kill:
                    self.interrupted = True
            else:
                kill = None
        if kill is not None:
            self.emit({"kind": "user", "text": text, "queued": True})
            self.emit({"kind": "status", "text": ("interrupting; the message is sent when the agent has stopped"
                                                  if kill else f"message queued ({n}); it is sent when the agent "
                                                               "finishes this turn")})
            if kill:
                self.proc.terminate()
            return
        self.emit({"kind": "user", "text": text})
        self._start(text, model)

    def _start(self, text: str, model: str):
        self.last_model = model
        sid = self.session_id
        prompt = text if sid else f"/hw-design new {self.name} . {text}"
        cmd = ["claude", "-p", prompt, *designer_args(model), "--output-format", "stream-json", "--verbose"]
        if sid:
            cmd += ["--resume", sid]
        env = headless_env()
        self.proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     env=env)
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _reset_run(self):
        self.results: list[dict] = []
        self.launches: dict[str, str] = {}  # Agent tool_use id -> subagent type
        self.agents: dict[str, dict] = {}  # label -> {turns, first, last, tokens per model}
        self.seen_msgs: set[str] = set()
        self.done_agents: set[str] = set()
        self.last_live = 0.0

    def _pump(self, proc: subprocess.Popen):
        self._reset_run()
        t0 = time.time()
        self.emit({"kind": "status", "text": "agent started", "busy": True})
        for line in proc.stdout:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.handle(d)
        proc.wait()
        self.finish(proc.returncode, time.time() - t0)

    def handle(self, d: dict):
        """One stream-json event. A `result` event is a turn result, not the end of the process (item 51)."""
        if d.get("session_id"):
            (self.dir / ".session").write_text(d["session_id"])
        if d.get("type") == "assistant":
            self._track_usage(d)
            for c in d["message"].get("content", []):
                if c.get("type") == "text" and c["text"].strip():
                    self.emit({"kind": "agent", "text": c["text"]})
                elif c.get("type") == "tool_use":
                    i = c.get("input", {})
                    if c.get("name") in ("Agent", "Task"):
                        self.launches[c.get("id", "")] = str(i.get("subagent_type") or "subagent")
                    desc = i.get("description") or i.get("command") or i.get("file_path") or i.get("prompt", "")
                    self.emit({"kind": "tool", "text": f"{c['name']}: {str(desc)[:160]}"})
        elif d.get("type") == "user":
            for c in (d.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in self.launches:
                    self._agent_done(c["tool_use_id"])
        elif d.get("type") == "result":
            models = self._turn_models(d)
            self._log_usage(d, models)
            self.results.append({"d": d, "models": models})

    def finish(self, code: int | None, elapsed: float):
        """The process has exited: say how it ended, deliver queued messages, report files and questions."""
        text = ""
        limit_wait = None
        if self.results:
            d, models = self.results[-1]["d"], self.results[-1]["models"]
            text = str(d.get("result", ""))
            if d.get("is_error") and LIMIT_RE.search(text):
                limit_wait = _limit_wait_seconds(text)
                self.emit({"kind": "status", "text": f"usage limit reached; the agent continues automatically "
                                                     f"in {limit_wait // 60} min, when the window refreshes",
                           "busy": False})
                threading.Timer(limit_wait, self._retry_after_limit).start()
            else:
                n = sum(r["d"].get("num_turns") or 0 for r in self.results)
                secs = round(sum((r["d"].get("duration_ms") or 0) for r in self.results) / 1000)
                self.emit({"kind": "status", "busy": False,
                           "text": f"agent finished ({n} turns, {secs} s, {self._tokens_text(d, models)})"})
        else:
            self.emit({"kind": "status", "busy": False,
                       "text": f"agent exited without a result (code {code}, {round(elapsed)} s)"})
        self._flush_agents(final=True)
        self.emit({"kind": "files", "files": self.files()})
        q = self.questions()
        if q:
            self.emit({"kind": "questions", "questions": q})
        self._deliver_queue()

    def _deliver_queue(self):
        with self.lock:
            msgs, self.queued, self.interrupted = self.queued, [], False
        if msgs and not self.busy():
            self._start("\n\n".join(msgs), self.last_model)

    # --- live usage per agent (item 35) ---
    def _track_usage(self, d: dict):
        m = d.get("message") or {}
        u = m.get("usage")
        mid = m.get("id")
        if not u or (mid and mid in self.seen_msgs):
            return
        if mid:
            self.seen_msgs.add(mid)
        parent = d.get("parent_tool_use_id")
        label = self.launches.get(parent, "subagent") if parent else "orchestrator"
        a = self.agents.setdefault(label, {"turns": 0, "first": time.time(), "tokens": {}, "launches": set()})
        if parent:
            a["launches"].add(parent)
        a["turns"] += 1
        a["last"] = time.time()
        t = a["tokens"].setdefault(m.get("model", "?"), dict.fromkeys(USAGE_FIELDS, 0))
        for f in USAGE_FIELDS:
            t[f] += u.get(f, 0) or 0
        if time.time() - self.last_live > 3:
            self._flush_agents()

    def snapshot(self) -> dict:
        out = {}
        for k, a in self.agents.items():
            out[k] = {"turns": a["turns"], "seconds": round(a.get("last", a["first"]) - a["first"]),
                      "launches": len(a["launches"]), "tokens": a["tokens"]}
        return out

    def _flush_agents(self, final: bool = False):
        self.last_live = time.time()
        self.live = self.snapshot()
        self.emit({"kind": "usage", "agents": self.live}, persist=False)
        if final:
            self._agent_done(None)

    def _agent_done(self, launch_id: str | None):
        """Append one line per finished subagent type to .usage_agents.jsonl, as soon as its Agent call returns."""
        labels = [self.launches[launch_id]] if launch_id else list(self.agents)
        for label in labels:
            a = self.agents.get(label)
            key = launch_id or label
            if not a or key in self.done_agents:
                continue
            self.done_agents.add(key)
            rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "agent": label, **self.snapshot()[label]}
            with open(self.dir / ".usage_agents.jsonl", "a") as fh:
                fh.write(json.dumps(rec) + "\n")
        if launch_id:
            self._flush_agents()

    def _turn_models(self, d: dict) -> dict:
        """This turn's tokens per model. modelUsage of a resumed session is cumulative, so subtract the total of
        the earlier turns of the same session id (kept in .usage_total.json)."""
        tp = self.dir / ".usage_total.json"
        try:
            seen = json.loads(tp.read_text())
        except (OSError, ValueError):
            seen = {}
        prev = seen.get(d.get("session_id") or "")
        cur = model_usage_delta(prev, d.get("modelUsage") or {})
        total = {m: {k: (prev or {}).get(m, {}).get(k, 0) + v[k] for k in MODEL_USAGE_KEYS} for m, v in cur.items()}
        seen[d.get("session_id") or ""] = {**(prev or {}), **total}
        tp.write_text(json.dumps(seen))
        return cur

    def _log_usage(self, d: dict, models: dict):
        """One line per agent turn in .usage.jsonl: tokens per model, main session and subagents together."""
        rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "model": self.last_model, "turns": d.get("num_turns"),
               "seconds": round((d.get("duration_ms") or 0) / 1000), "usage": _usage(d),
               "by_model": models}
        with open(self.dir / ".usage.jsonl", "a") as fh:
            fh.write(json.dumps(rec) + "\n")

    def _tokens_text(self, d: dict, models: dict) -> str:
        u = _usage(d)
        per_model = ", ".join(f"{m.removeprefix('claude-')} {round(sum(v.values()) / 1e3)}k"
                              for m, v in models.items() if sum(v.values()))
        return (f"tokens: {round(u['cache_read_input_tokens'] / 1e3)}k cache read, "
                f"{round((u['cache_creation_input_tokens'] + u['input_tokens']) / 1e3)}k new in, "
                f"{round(u['output_tokens'] / 1e3)}k out; {per_model}")

    def _retry_after_limit(self):
        if not self.busy():
            self.send("Continue the /hw-design flow from state.md. The usage limit interrupted the last turn.",
                      self.last_model)

    def stop(self):
        if self.busy():
            with self.lock:
                self.queued.clear()
            self.proc.terminate()
            self.emit({"kind": "status", "text": "stopped by user"})

    def questions(self):
        p = self.dir / "inbox" / "questions.json"
        if not p.exists():
            return None
        try:
            return normalize_questions(json.loads(p.read_text()))
        except ValueError:
            return None  # being written

    def answer(self, answers: dict, model: str):
        q = self.questions() or {"round": 0, "questions": []}
        q["round"] = q.get("round") or 0
        lines = [f"\n## Round {q.get('round', '?')}"]
        for item in q.get("questions", []):
            a = answers.get(item["id"], "").strip() or "No preference, use your judgment."
            lines.append(f"{item['id']}: {a}")
        (self.dir / "inbox").mkdir(exist_ok=True)
        with open(self.dir / "inbox" / "answers.md", "a") as fh:
            fh.write("\n".join(lines) + "\n")
        dest = self.dir / "inbox" / f"questions_round{q.get('round', 0)}.json"
        k = 1
        while dest.exists():
            k += 1
            dest = self.dir / "inbox" / f"questions_round{q.get('round', 0)}_{k}.json"
        (self.dir / "inbox" / "questions.json").rename(dest)
        self.send("The product person answered. The answers are in inbox/answers.md. Continue the flow.", model)

    def files(self):
        return [f for f in FILES if (self.dir / f).exists()]

    def state(self) -> str:
        p = self.dir / "state.md"
        return p.read_text()[:400] if p.exists() else "new"


SESSIONS: dict[str, Session] = {}


def session(name: str) -> Session:
    if not NAME_RE.match(name):
        raise ValueError("project names use lowercase letters, digits, '-' and '_'")
    if name not in SESSIONS:
        SESSIONS[name] = Session(name)
    return SESSIONS[name]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _file(self, path: Path):
        if not path.exists() or not path.is_file():
            return self._json({"error": "not found"}, 404)
        data = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "text/plain; charset=utf-8"
        if path.suffix in (".md", ".yaml", ".yml"):
            ctype = "text/plain; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        parts = [unquote(p) for p in u.path.strip("/").split("/") if p]
        try:
            if not parts:
                return self._file(STATIC / "index.html")
            if parts[0] in ("manifest.webmanifest", "sw.js", "icon.svg", "app.js", "app.css", "md.js"):
                return self._file(STATIC / parts[0])
            if parts == ["api", "projects"]:
                PROJECTS.mkdir(exist_ok=True)
                out = []
                for d in sorted(PROJECTS.iterdir()):
                    if d.is_dir() and NAME_RE.match(d.name):
                        s = session(d.name)
                        sc = {}
                        st = d / "build" / "status.json"
                        if st.exists():
                            sc = json.loads(st.read_text()).get("score", {})
                        out.append({"name": d.name, "state": s.state(), "busy": s.busy(), "score": sc,
                                    "waiting": len((s.questions() or {}).get("questions", []))})
                return self._json(out)
            if parts[:2] == ["api", "projects"] and len(parts) >= 4:
                s = session(parts[2])
                if parts[3] == "events":
                    return self._sse(s)
                if parts[3] == "info":
                    pg = s.history_page()
                    return self._json({"files": s.files(), "busy": s.busy(), "questions": s.questions(),
                                       "history": pg["events"], "start": pg["start"], "queued": len(s.queued),
                                       "usage": s.live, "state": s.state()})
                if parts[3] == "history":
                    qs = parse_qs(u.query)
                    b = qs.get("before", [None])[0]
                    return self._json(s.history_page(int(b) if b is not None else None,
                                                     int(qs.get("limit", [PAGE])[0])))
                if parts[3] == "file":
                    rel = "/".join(parts[4:])
                    if rel not in FILES and not rel.startswith("build/fab/"):
                        return self._json({"error": "not allowed"}, 403)
                    p = (s.dir / rel).resolve()
                    if not str(p).startswith(str(s.dir.resolve())):
                        return self._json({"error": "not allowed"}, 403)
                    return self._file(p)
            return self._json({"error": "not found"}, 404)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)

    def _sse(self, s: Session):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        q: queue.Queue = queue.Queue()
        s.subs.append(q)
        try:
            while True:
                try:
                    ev = q.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            s.subs.remove(q)

    def do_POST(self):
        u = urlparse(self.path)
        parts = [unquote(p) for p in u.path.strip("/").split("/") if p]
        try:
            body = self._body()
            model = body.get("model") or "opus"
            if parts == ["api", "projects"]:
                name = body["name"].strip().lower()
                s = session(name)
                s.dir.mkdir(parents=True, exist_ok=True)
                (s.dir / "brief.md").write_text(body.get("brief", ""))
                s.send(f"The product person's brief is in brief.md:\n\n{body.get('brief', '')}", model)
                return self._json({"ok": True, "name": name})
            if parts[:2] == ["api", "projects"] and len(parts) == 4:
                s = session(parts[2])
                if parts[3] == "message":
                    s.send(body["text"], model, bool(body.get("interrupt")))
                elif parts[3] == "answer":
                    s.answer(body.get("answers", {}), model)
                elif parts[3] == "stop":
                    s.stop()
                elif parts[3] == "build":
                    def run():
                        s.emit({"kind": "status", "text": "hwflow build started (no LLM)"})
                        p = subprocess.run([sys.executable, "-m", "hwflow.cli", "build", str(s.dir)], cwd=ROOT,
                                           capture_output=True, text=True)
                        s.emit({"kind": "tool", "text": "\n".join(p.stdout.splitlines()[:8])})
                        s.emit({"kind": "files", "files": s.files()})
                    threading.Thread(target=run, daemon=True).start()
                else:
                    return self._json({"error": "unknown action"}, 404)
                return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)
        except (ValueError, KeyError, RuntimeError) as e:
            return self._json({"error": str(e)}, 400)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 inside a container; the UI has no login, so publish "
                    "the port on the host's loopback only")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"hwflow UI on http://{a.host}:{a.port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
