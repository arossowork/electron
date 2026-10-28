"""Placement is reproducible: the same design and seed give the same placement bit for bit, in any process (any
PYTHONHASHSEED, so no step may depend on the iteration order of a set of strings) and under any CPU load (every
normal stop is a count; a wall-clock limit is only a safety abort, reported in placer.WORK)."""
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

from hwflow.layout.place import placer

from test_placer import channel

SEEDS, ITERS = 2, 2000


def _hash(parts) -> str:
    h = hashlib.sha256()
    for r in sorted(parts):
        p = parts[r]
        h.update(f"{r}:{float(p.x).hex()}:{float(p.y).hex()}:{float(p.r).hex()};".encode())
    return h.hexdigest()


_CHILD = """
import sys
from pathlib import Path
sys.path.insert(0, {tests!r})
from hwflow.layout.place import placer
from test_place_determinism import SEEDS, ITERS, _hash
from test_placer import channel
d, libs = channel(Path({tmp!r}))
parts, cost, viol = placer.place(d, libs, seeds=SEEDS, iters=ITERS)
print("HASH", _hash(parts))
"""


def _child(tmp: Path, hashseed: str) -> str:
    tmp.mkdir()
    env = dict(os.environ, PYTHONHASHSEED=hashseed)
    p = subprocess.run([sys.executable, "-c", _CHILD.format(tests=str(Path(__file__).parent), tmp=str(tmp))],
                       env=env, capture_output=True, text=True, timeout=600)
    assert p.returncode == 0, p.stderr[-2000:]
    return next(line.split()[1] for line in p.stdout.splitlines() if line.startswith("HASH"))


def test_placement_is_the_same_in_every_process(tmp_path):
    """The channel design has through-hole headers (their hole boxes are density blockages of the global placement):
    with the pads taken in set order, a new hash seed gave another placement."""
    hashes = {_child(tmp_path / f"h{s}", s) for s in ("1", "2", "3")}
    assert len(hashes) == 1, hashes


def test_placement_does_not_depend_on_the_clock(tmp_path, monkeypatch):
    """A slow machine (a clock that runs 5 s per call) gives the same placement as a fast one, with no safety abort."""
    d, libs = channel(tmp_path)
    placer.WORK.update(pack_evals=0, aborts=[])
    fast = _hash(placer.place(d, libs, seeds=SEEDS, iters=ITERS)[0])
    real = time.time
    t = [real()]

    def slow():
        t[0] += 5.0
        return t[0]
    monkeypatch.setattr(time, "time", slow)
    slow_hash = _hash(placer.place(d, libs, seeds=SEEDS, iters=ITERS)[0])
    monkeypatch.setattr(time, "time", real)
    assert slow_hash == fast
    assert placer.WORK["aborts"] == []


def test_pack_search_stops_on_its_try_count_and_reports_a_safety_abort(tmp_path, monkeypatch):
    """_pack_search ends after PACK_EVALS tries; only past PACK_ABORT_S of wall time does it stop early, and then it
    says so in placer.WORK["aborts"]."""
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, d.board["width_mm"], d.board["height_mm"], seed=1)
    mv = pl._movable()
    for p in mv:  # every resistor on one spot: overlaps the settle search cannot all clear in one try
        p.x, p.y = 18.0, 15.0
        pl._refs(p)
    jam = pl._state()
    pl.cost()
    v = pl.violations()
    assert v
    monkeypatch.setattr(placer, "PACK_EVALS", 3)
    calls = []
    real = pl._settle

    def counted(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(pl, "_settle", counted)
    placer.WORK.update(pack_evals=0, aborts=[])
    pl._pack_search(v)
    assert len(calls) <= 3 and placer.WORK["pack_evals"] == len(calls) and placer.WORK["aborts"] == []
    pl._restore(jam)
    pl.cost()
    monkeypatch.setattr(placer, "PACK_ABORT_S", -1.0)  # the safety abort fires at once and is reported
    calls.clear()
    pl._pack_search(v)
    assert not calls and placer.WORK["aborts"] and "packing search" in placer.WORK["aborts"][0]

