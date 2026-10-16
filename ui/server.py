"""Local web UI for the /hw-design agent.

Runs on 127.0.0.1 only. Each chat message starts `claude -p ... --resume <session>` with
stream-json output, so the agent uses the logged-in Claude subscription. Events stream to the
browser over Server-Sent Events.

  uv run python ui/server.py [--port 8765]
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))
from harness import LIMIT_RE, _limit_wait_seconds  # noqa: E402
PROJECTS = ROOT / "projects"
STATIC = Path(__file__).parent / "static"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
FILES = ["build/REPORT.md", "SUMMARY.md", "requirements.yaml", "conflicts.md", "architecture.md", "state.md",
         "notes/parts.md", "design.yaml", "build/fab/pcb.svg", "build/fab/pcb_3d_top.png", "build/fab/schematic.pdf"]


class Session:
    """One project: the running claude process, its event history and subscribers."""

    def __init__(self, name: str):
        self.name = name
        self.dir = PROJECTS / name
        self.proc: subprocess.Popen | None = None
        self.history: list[dict] = []
        self.subs: list[queue.Queue] = []
        self.lock = threading.Lock()
        hist = self.dir / ".ui_history.jsonl"
        if hist.exists():
            self.history = [json.loads(x) for x in hist.read_text().splitlines()[-400:] if x.strip()]

    @property
    def session_id(self) -> str | None:
        p = self.dir / ".session"
        return p.read_text().strip() if p.exists() else None

    def emit(self, ev: dict):
        ev["t"] = time.strftime("%H:%M:%S")
        with self.lock:
            self.history.append(ev)
            with open(self.dir / ".ui_history.jsonl", "a") as fh:
                fh.write(json.dumps(ev) + "\n")
            for q in list(self.subs):
                q.put(ev)

    def busy(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def send(self, text: str, model: str):
        if self.busy():
            raise RuntimeError("the agent is still working; stop it first or wait")
        self.emit({"kind": "user", "text": text})
        self.last_model = model
        sid = self.session_id
        prompt = text if sid else f"/hw-design new {self.name} . {text}"
        cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose",
               "--permission-mode", "acceptEdits"]
        if sid:
            cmd += ["--resume", sid]
        env = dict(os.environ, HWFLOW_HEADLESS="1")
        env.pop("ANTHROPIC_API_KEY", None)
        self.proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     env=env)
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc: subprocess.Popen):
        self.emit({"kind": "status", "text": "agent started"})
        for line in proc.stdout:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("session_id"):
                (self.dir / ".session").write_text(d["session_id"])
            if d.get("type") == "assistant":
                for c in d["message"].get("content", []):
                    if c.get("type") == "text" and c["text"].strip():
                        self.emit({"kind": "agent", "text": c["text"]})
                    elif c.get("type") == "tool_use":
                        i = c.get("input", {})
                        desc = i.get("description") or i.get("command") or i.get("file_path") or i.get("prompt", "")
                        self.emit({"kind": "tool", "text": f"{c['name']}: {str(desc)[:160]}"})
            elif d.get("type") == "result":
                text = str(d.get("result", ""))
                if d.get("is_error") and LIMIT_RE.search(text):
                    wait = _limit_wait_seconds(text)
                    self.emit({"kind": "status", "text": f"usage limit reached; the agent continues automatically "
                                                         f"in {wait // 60} min, when the window refreshes"})
                    threading.Timer(wait, self._retry_after_limit).start()
                else:
                    self.emit({"kind": "status", "text": f"agent finished ({d.get('num_turns')} turns, "
                                                         f"{round((d.get('duration_ms') or 0) / 1000)} s)"})
        proc.wait()
        self.emit({"kind": "files", "files": self.files()})
        q = self.questions()
        if q:
            self.emit({"kind": "questions", "questions": q})

    def _retry_after_limit(self):
        if not self.busy():
            self.send("Continue the /hw-design flow from state.md. The usage limit interrupted the last turn.",
                      self.last_model)

    def stop(self):
        if self.busy():
            self.proc.terminate()
            self.emit({"kind": "status", "text": "stopped by user"})

    def questions(self):
        p = self.dir / "inbox" / "questions.json"
        return json.loads(p.read_text()) if p.exists() else None

    def answer(self, answers: dict, model: str):
        q = self.questions() or {"round": 0, "questions": []}
        lines = [f"\n## Round {q.get('round', '?')}"]
        for item in q.get("questions", []):
            a = answers.get(item["id"], "").strip() or "No preference, use your judgment."
            lines.append(f"{item['id']}: {a}")
        (self.dir / "inbox").mkdir(exist_ok=True)
        with open(self.dir / "inbox" / "answers.md", "a") as fh:
            fh.write("\n".join(lines) + "\n")
        (self.dir / "inbox" / "questions.json").rename(self.dir / "inbox" / f"questions_round{q.get('round', 0)}.json")
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
            if parts[0] in ("manifest.webmanifest", "sw.js", "icon.svg", "app.js", "app.css"):
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
                                    "waiting": bool(s.questions())})
                return self._json(out)
            if parts[:2] == ["api", "projects"] and len(parts) >= 4:
                s = session(parts[2])
                if parts[3] == "events":
                    return self._sse(s)
                if parts[3] == "info":
                    return self._json({"files": s.files(), "busy": s.busy(), "questions": s.questions(),
                                       "history": s.history[-300:], "state": s.state()})
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
                    s.send(body["text"], model)
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
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"hwflow UI on http://127.0.0.1:{a.port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
