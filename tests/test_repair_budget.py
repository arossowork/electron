import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from hwflow import repair


def test_router_call_stops_at_the_repair_budget(monkeypatch, tmp_path):
    """A re-route that would run past the repair budget is stopped inside the round, not after it."""
    seen = {}

    def slow_reroute(d, libs, parts, workdir, tracks, vias, nets, attempt, island, deadline=None):
        seen["deadline"] = deadline
        raise subprocess.TimeoutExpired("route.py", 0)
    monkeypatch.setattr(repair, "reroute", slow_reroute)
    monkeypatch.setenv("HWFLOW_REPAIR_BUDGET_S", "0.05")
    monkeypatch.setattr(repair, "plan", lambda *a, **k: {"rip": ["N1"], "islands": [], "drop_vias": []})
    monkeypatch.setattr(repair, "fixed_error", lambda x, parts: None)
    monkeypatch.setattr(time, "time", lambda t=[1000.0]: t.__setitem__(0, t[0] + 0.03) or t[0])
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)")
    drc = [{"sev": "error", "type": "clearance", "desc": "x", "items": []}]
    d = SimpleNamespace(name="b", board={})
    r = repair.repair(d, None, {}, [], [], drc, Path(board), lambda *a: drc, rounds=3)
    assert seen["deadline"] is not None
    assert any("stopped at the repair budget" in x for x in r["report"]["log"])
    assert r["report"]["rounds"] == 1
