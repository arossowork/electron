"""Build status: build/status.json (atomic, locked), the design and input hashes, staleness."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from ..design import Design


def _out(d: Design) -> Path:
    o = d.dir / "build"
    o.mkdir(exist_ok=True)
    return o


def _status_path(d):
    return _out(d) / "status.json"


def _status(d) -> dict:
    p = _status_path(d)
    return json.loads(p.read_text()) if p.exists() else {}


PROSE_KEYS = ("text", "evidence", "notes", "note", "source", "rationale", "comment")


def _design_hash(d) -> str:
    """Hash of the parsed design, so comments, whitespace and key order do not make results stale (backlog 58).

    design.yaml is parsed fresh from the file: `d.raw` is mutated in memory (layout board size). From
    requirements.yaml only the electrical content counts; prose fields (text, evidence, notes, ...) do not."""
    import hashlib

    import yaml
    raw = yaml.safe_load(d.path.read_text()) or {}
    h = hashlib.sha1(json.dumps(raw, sort_keys=True, default=str).encode())
    rq = d.dir / "requirements.yaml"
    if rq.exists():
        reqs = yaml.safe_load(rq.read_text()) or {}

        def strip(x):
            if isinstance(x, dict):
                return {k: strip(v) for k, v in x.items() if k not in PROSE_KEYS}
            if isinstance(x, list):
                return [strip(v) for v in x]
            return x
        h.update(json.dumps(strip(reqs), sort_keys=True, default=str).encode())
    return h.hexdigest()[:12]


def _inputs_hash(d) -> str:
    """Everything a build stage reads besides design.yaml: model files, project libraries and the hwflow code.
    Two builds with the same inputs give the same results, so `build` skips a stage whose inputs did not change
    (backlog 27)."""
    import hashlib

    from ..libs import project_lib_dirs, shared_lib_files
    h = hashlib.sha1(_design_hash(d).encode())
    files = [d.dir / f for f in (d.raw.get("sim") or {}).get("includes") or []]
    files += [d.dir / c.get("spice")["lib"] for c in d.components.values()
              if isinstance(c.get("spice"), dict) and c.get("spice").get("lib")]
    files += _block_files(d)
    files += shared_lib_files(d)  # library/: only the shared libraries this design uses
    for f in sorted(set(files)):
        h.update(str(f).encode() + (f.read_bytes() if f.is_file() else b"-"))
    for root in project_lib_dirs(d):
        for f in sorted(root.rglob("*")):
            if f.is_file():
                st = f.stat()
                h.update(f"{f.relative_to(root)}:{st.st_size}:{st.st_mtime_ns}".encode())
    for f in sorted(Path(__file__).parents[1].rglob("*")):
        if f.suffix in (".py", ".lib") and f.is_file():
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def _block_files(d) -> list[Path]:
    """The block.yaml files of the layout blocks the design uses."""
    from .. import blocks
    return [f for e in (d.raw.get("blocks") or {}).values() if (f := blocks.find(str((e or {}).get("use")), d))]


@contextmanager
def _status_lock(d):
    """Exclusive lock (flock on a sidecar file) around a read-modify-write of status.json."""
    with open(_out(d) / ".status.lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _write_atomic(p: Path, text: str):
    """Write to a temporary file in the same folder, then rename: a reader never sees a half-written file."""
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _save_status(d, key, val):
    if isinstance(val, dict) and key in ("check", "sch", "sim", "layout", "post", "fab", "optimize"):
        val["design_hash"] = _design_hash(d)
        val["at"] = time.time()
    with _status_lock(d):
        s = _status(d)
        s[key] = val
        s["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write_atomic(_status_path(d), json.dumps(s, indent=2, default=str))


# a stage result is also stale when a stage it reads from ran after it (post and fab read the routed board)
STAGE_DEPS = {"post": ("layout",), "fab": ("layout", "sch")}


def _stale_stages(s: dict, cur: str) -> list[str]:
    out = []
    for k in ("check", "sch", "sim", "layout", "post", "fab"):
        v = s.get(k)
        if not isinstance(v, dict):
            continue
        if v.get("design_hash") != cur or any(
                isinstance(s.get(dep), dict) and s[dep].get("at", 0) > v.get("at", 0) for dep in STAGE_DEPS.get(k, ())):
            out.append(k)
    return out


def _fresh_status(d) -> dict:
    s = _status(d)
    cur = _design_hash(d)
    stale = set(_stale_stages(s, cur))
    return {k: v for k, v in s.items() if k not in stale
            and not (isinstance(v, dict) and "design_hash" in v and v["design_hash"] != cur)}
