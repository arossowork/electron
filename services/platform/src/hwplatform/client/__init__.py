"""`hwflow build --remote` (ADR 0001, 4.9): the `remote` executor of the entry-point group
`hwflow.executors`.

It uploads the project folder as a design version (a tar.gz bundle without build/), starts a build run
with the CLI's options, follows the run's SSE stream (stage lines as they finish), and checks REPORT.md,
status.json and the stage outputs of the run out into the project's build/. `hwflow run status|wait
--remote` read the run.

Configuration (environment): HWPLATFORM_API_URL (default http://127.0.0.1:8080), HWPLATFORM_TOKEN (a
bearer access token of the platform account). The platform project of a folder is kept in
build/remote.json (created on the first remote build, named after the folder).
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import time
from pathlib import Path

import httpx

V1 = "/api/v1"
EXCLUDE = ("build", ".git", "__pycache__", "scratch", ".venv", "node_modules")
TERMINAL = ("succeeded", "failed", "cancelled")
EXTRA_OPTIONS = ("until", "mc", "reuse", "force", "rebuild", "auto", "attempts", "retry_layout")


def bundle(project_dir: Path) -> bytes:
    """tar.gz of the project files (what the inline build reads), without build/, VCS or cache folders."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in sorted(project_dir.rglob("*")):
            rel = p.relative_to(project_dir)
            if any(part in EXCLUDE for part in rel.parts) or not p.is_file():
                continue
            tf.add(p, arcname=rel.as_posix(), recursive=False)
    return buf.getvalue()


def sse_events(resp: httpx.Response):
    """Parsed SSE frames {"id", "event", "data"} of a streaming response."""
    cur: dict = {}
    for line in resp.iter_lines():
        if line == "":
            if cur:
                yield cur
                cur = {}
            continue
        if line.startswith(":"):
            continue
        k, _, v = line.partition(": ")
        cur[k] = json.loads(v) if k == "data" else v


def _stage_line(ev: dict) -> str | None:
    t, d = ev.get("event"), (ev.get("data") or {}).get("data") or ev.get("data") or {}
    key = d.get("key")
    if t == "stage.completed":
        s = d.get("summary") or {}
        if key and key.startswith("layout_attempt") and s.get("line"):
            return f"[layout --auto] {s['line']}"
        name = "layout" if key == "layout_select" else key
        return f"[{name}] {(d.get('verdict') or '').upper()}{' (cached)' if d.get('cached') else ''} " + (
            f"({d['seconds']:.1f}s)" if d.get("seconds") else ""
        )
    if t == "stage.failed":
        return f"[{key}] error: {(d.get('error') or {}).get('message')}"
    if t == "stage.skipped":
        return f"[{key}] skipped ({d.get('reason')})"
    return None


class RemoteExecutor:
    def __init__(self, base_url: str | None = None, token: str | None = None, *, transport=None):
        self.base_url = (base_url or os.environ.get("HWPLATFORM_API_URL") or "http://127.0.0.1:8080").rstrip(
            "/"
        )
        self.token = token if token is not None else os.environ.get("HWPLATFORM_TOKEN", "")
        self.transport = transport

    def _client(self, timeout: float = 60.0) -> httpx.Client:
        h = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        return httpx.Client(
            base_url=self.base_url,
            headers=h,
            timeout=httpx.Timeout(timeout, connect=10.0),
            transport=self.transport,
            follow_redirects=True,
        )

    @staticmethod
    def _check(r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            raise SystemExit(
                f"platform: {r.request.method} {r.request.url.path}: {r.status_code} {r.text[:500]}"
            )
        return r

    # -- project mapping ------------------------------------------------------------------------------------
    def project_id(self, c: httpx.Client, project_dir: Path) -> str:
        cfg = project_dir / "build" / "remote.json"
        try:
            doc = json.loads(cfg.read_text())
            if doc.get("base_url") == self.base_url and doc.get("project_id"):
                return doc["project_id"]
        except (OSError, ValueError):
            pass
        pid = self._check(c.post(f"{V1}/projects", json={"name": project_dir.name})).json()["id"]
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"base_url": self.base_url, "project_id": pid}, indent=1))
        return pid

    # -- the executor interface (hwflow.commands.run) ---------------------------------------------------
    def start(self, project_dir: Path, options: dict, settings_overrides: dict | None = None) -> dict:
        """Upload a design version and start the run; returns the run (RunOut JSON)."""
        project_dir = Path(project_dir).resolve()
        if project_dir.is_file():
            project_dir = project_dir.parent
        with self._client() as c:
            pid = self.project_id(c, project_dir)
            dv = self._check(
                c.post(
                    f"{V1}/projects/{pid}/design-versions",
                    files=[("bundle", ("project.tar.gz", bundle(project_dir), "application/gzip"))],
                    data={"message": "hwflow build --remote"},
                )
            ).json()
            body = {
                "design_version_id": dv["id"],
                "options": options,
                "settings_overrides": settings_overrides or {},
            }
            run = self._check(c.post(f"{V1}/projects/{pid}/runs", json=body)).json()
        (project_dir / "build" / "runs").mkdir(parents=True, exist_ok=True)
        (project_dir / "build" / "runs" / "REMOTE_LATEST").write_text(run["id"])
        return run

    def build(self, project_dir: Path, options: dict, *, detach: bool = False) -> dict:
        opts = {k: v for k, v in options.items() if k in EXTRA_OPTIONS}
        run = self.start(project_dir, opts)
        rid = run["id"]
        if detach:
            return {
                "rc": 0,
                "run_id": rid,
                "text": f"run {rid} started on {self.base_url}\n"
                f"wait for it: uv run hwflow run wait {project_dir} {rid} --remote",
            }
        rc = self.wait(project_dir, rid, timeout=None)
        return {"rc": rc, "run_id": rid, "text": ""}

    def _latest(self, project_dir: Path, run_id: str | None) -> str:
        if run_id:
            return run_id
        p = Path(project_dir) / "build" / "runs" / "REMOTE_LATEST"
        try:
            return p.read_text().strip()
        except OSError as e:
            raise SystemExit(f"no remote run of {project_dir} (no {p})") from e

    def status(self, project_dir: Path, run_id: str | None) -> str:
        rid = self._latest(project_dir, run_id)
        with self._client() as c:
            run = self._check(c.get(f"{V1}/runs/{rid}")).json()
        lines = [
            f"run {rid}: {run['status']}" + (f", verdict {run['verdict']}" if run.get("verdict") else "")
        ]
        for s in sorted(run.get("stages") or [], key=lambda s: s.get("seq") or 0):
            lines.append(f"  {s['key']:<18} {s['status']}" + (f" {s['verdict']}" if s.get("verdict") else ""))
        return "\n".join(lines)

    def wait(self, project_dir: Path, run_id: str | None, timeout: float | None) -> int:
        """Follow the run events until it ends (`timeout` s: exit 2), then check its files out into build/."""
        rid = self._latest(project_dir, run_id)
        t_end = None if timeout is None else time.time() + timeout
        last = None
        with self._client(timeout=120.0) as c:
            while True:
                run = self._check(c.get(f"{V1}/runs/{rid}")).json()
                if run["status"] in TERMINAL:
                    break
                if t_end is not None and time.time() >= t_end:
                    print(f"run {rid}: still {run['status']} after {timeout:g} s: call wait again")
                    return 2
                headers = {"Last-Event-ID": last} if last else {}
                try:
                    with c.stream(
                        "GET",
                        f"{V1}/runs/{rid}/events",
                        headers=headers,
                        timeout=httpx.Timeout(30.0, connect=10.0),
                    ) as resp:
                        self._check(resp)
                        for ev in sse_events(resp):
                            last = ev.get("id") or last
                            line = _stage_line(ev)
                            if line:
                                print(line, flush=True)
                            if ev.get("event") in ("run.completed", "run.failed", "run.cancelled"):
                                break
                            if t_end is not None and time.time() >= t_end:
                                break
                except httpx.ReadTimeout:
                    pass  # no event for a while: poll the run and reconnect
            self.checkout(c, Path(project_dir), run)
        if run["status"] != "succeeded":
            print(f"run {rid}: {run['status']}: {(run.get('error') or {}).get('message', '')}")
            return 1
        return 0 if run.get("verdict") == "PASS" else 1

    def checkout(self, c: httpx.Client, project_dir: Path, run: dict) -> None:
        """REPORT.md, status.json and the run's stage outputs into build/ (tree outputs as their archives)."""
        if project_dir.is_file():
            project_dir = project_dir.parent
        out = project_dir / "build"
        out.mkdir(parents=True, exist_ok=True)
        rid = run["id"]
        arts = self._check(c.get(f"{V1}/runs/{rid}/artifacts")).json()
        names = [a["name"] for a in arts] if isinstance(arts, list) else list(arts)
        for name in ("REPORT.md", "status.json"):
            if name in names:
                r = self._check(c.get(f"{V1}/runs/{rid}/artifacts/{name}"))
                (out / name).write_bytes(r.content)
        if (out / "REPORT.md").is_file():
            print((out / "REPORT.md").read_text())
