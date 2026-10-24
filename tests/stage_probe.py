"""What a real stage run reads: files it opens or probes, folders it lists, programs it starts and the paths they get,
the hwflow code it executes and the settings groups it reads. tests/test_stage_specs.py compares this with the
stage's StageSpec (the guard against under-declared inputs, ADR 0001 2.3: a too-narrow key gives stale results
silently)."""

from __future__ import annotations

import os
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

_ACTIVE: list[Probe] = []


def _audit(event: str, args: tuple):
    if not _ACTIVE:
        return
    p = _ACTIVE[-1]
    if p.busy:
        return
    p.busy = True
    try:
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            mode = args[1] if len(args) > 1 and isinstance(args[1], str) else "r"
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            path = os.fsdecode(args[0])
            writing = any(c in (mode or "") for c in "wax+") or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))
            if writing:
                p.written.add(os.path.abspath(path))
            elif os.path.abspath(path) not in p.written:
                p.opened.add(os.path.abspath(path))
        elif event in ("os.listdir", "os.scandir") and args:
            a = args[0]
            if isinstance(a, (str, bytes, os.PathLike)):
                p.listed.add(os.path.abspath(os.fsdecode(a)))
        elif event == "os.mkdir" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            p.written.add(os.path.abspath(os.fsdecode(args[0])))
        elif event == "os.rename" and len(args) > 1 and isinstance(args[1], (str, bytes, os.PathLike)):
            p.written.add(os.path.abspath(os.fsdecode(args[1])))
        elif event == "subprocess.Popen":
            argv = args[1] if len(args) > 1 else ()
            p.commands.append([os.fsdecode(x) if isinstance(x, (str, bytes, os.PathLike)) else str(x)
                               for x in (argv if isinstance(argv, (list, tuple)) else [argv])])
    finally:
        p.busy = False


sys.addaudithook(_audit)  # audit hooks cannot be removed: this one is inert unless a Probe is active


@dataclass
class Probe:
    opened: set[str] = field(default_factory=set)
    written: set[str] = field(default_factory=set)
    stated: set[str] = field(default_factory=set)
    listed: set[str] = field(default_factory=set)
    commands: list[list[str]] = field(default_factory=list)
    code: set[str] = field(default_factory=set)
    settings: set[str] = field(default_factory=set)
    busy: bool = False

    def __enter__(self):
        self._stat, self._lstat = os.stat, os.lstat
        probe = self

        def stat(path, *a, **kw):
            if not probe.busy and isinstance(path, (str, bytes, os.PathLike)):
                probe.stated.add(os.path.abspath(os.fsdecode(path)))
            return probe._stat(path, *a, **kw)
        os.stat = stat

        def prof(frame, event, arg):
            if event == "call":
                probe.code.add(frame.f_code.co_filename)
        sys.setprofile(prof)
        threading.setprofile(prof)
        _ACTIVE.append(self)
        return self

    def __exit__(self, *exc):
        _ACTIVE.remove(self)
        sys.setprofile(None)
        threading.setprofile(None)
        os.stat = self._stat
        return False

    def subprocess_paths(self) -> set[str]:
        """Absolute paths that exist and are named in the arguments of the programs the stage started."""
        out = set()
        for argv in self.commands:
            for a in argv[1:]:
                for tok in re.split(r"[=,]", a):
                    if tok.startswith("/") and os.path.exists(tok):
                        out.add(os.path.abspath(tok))
        return out


def referenced_paths(root: Path, pre: set[str], limit: int = 2_000_000) -> set[str]:
    """Absolute paths named inside the text files under `root` that the stage wrote (not the materialized ones in
    `pre`): netlist .include lines, library tables; the programs the stage runs read them."""
    out = set()
    for f in root.rglob("*"):
        if os.path.abspath(f) in pre or not f.is_file() or f.is_symlink() or f.stat().st_size > limit:
            continue
        try:
            text = f.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for m in re.finditer(r"(/[\w.+\-@/]+)", text):
            s = m.group(1).rstrip(".")
            if len(s) > 4 and os.path.exists(s):
                out.add(os.path.abspath(s))
    return out


class RecordingGroup:
    def __init__(self, inner, name: str, sink: set[str]):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_sink", sink)

    def __getattr__(self, k):
        self._sink.add(f"{self._name}.{k}")
        return getattr(self._inner, k)


class RecordingSettings:
    """A Settings stand-in that records the group and field of every read (`get_settings().layout.seed0`)."""

    def __init__(self, inner, sink: set[str]):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_sink", sink)

    def __getattr__(self, k):
        v = getattr(self._inner, k)
        if k in ("layout", "repair", "router", "tools", "paths"):
            return RecordingGroup(v, k, self._sink)
        if k == "key_dump":
            self._sink.update({"layout.*", "repair.*", "router.*"})
        elif k in ("snapshot", "model_dump"):
            self._sink.update({"layout.*", "repair.*", "router.*", "tools.*", "paths.*"})
        else:
            self._sink.add(k)
        return v
