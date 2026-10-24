"""build/status.json: several processes saving at once lose no update and never leave a corrupt file."""
import json
import multiprocessing as mp
from pathlib import Path
from types import SimpleNamespace

from hwflow.pipeline.status import _save_status, _status, _status_path

PROCS, KEYS = 6, 25


def _writer(proj: str, w: int):
    d = SimpleNamespace(dir=Path(proj), name="t", path=Path(proj) / "design.yaml", raw={})
    for i in range(KEYS):
        _save_status(d, f"k_{w}_{i}", {"w": w, "i": i, "pad": "x" * 2000})


def _reader(proj: str, stop, bad):
    p = Path(proj) / "build" / "status.json"
    while not stop.is_set():
        if p.exists():
            try:
                json.loads(p.read_text())
            except ValueError:
                bad.value += 1


def test_concurrent_saves_lose_nothing(tmp_path):
    (tmp_path / "design.yaml").write_text("project: t\n")
    d = SimpleNamespace(dir=tmp_path, name="t", path=tmp_path / "design.yaml", raw={})
    ctx = mp.get_context("fork")
    stop, bad = ctx.Event(), ctx.Value("i", 0)
    reader = ctx.Process(target=_reader, args=(str(tmp_path), stop, bad))
    reader.start()
    ws = [ctx.Process(target=_writer, args=(str(tmp_path), w)) for w in range(PROCS)]
    for p in ws:
        p.start()
    for p in ws:
        p.join(120)
        assert p.exitcode == 0
    stop.set()
    reader.join(30)
    assert bad.value == 0, "a reader saw a half-written status.json"
    s = _status(d)
    missing = [f"k_{w}_{i}" for w in range(PROCS) for i in range(KEYS) if f"k_{w}_{i}" not in s]
    assert not missing, f"lost updates: {missing[:5]} ({len(missing)})"
    assert not list((tmp_path / "build").glob("*.tmp")), "temporary files left behind"
    assert _status_path(d).exists()
