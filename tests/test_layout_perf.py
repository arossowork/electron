"""L3 (reproducible layout: UUID-free board text, route cache) and L5 (parallel --auto attempts) with stubs for the
router, KiCad and the attempts, so they run in seconds."""
import concurrent.futures as cf
import json
import time

import pytest

from hwflow.layout.board import kicad, pcb
from hwflow.layout.route import repair
from hwflow.pipeline import stages as pl_stages
from hwflow.core.design import Design
from hwflow.core.libs import Libs

YAML = """project: perf
description: two fixed parts
board: {width_mm: 30, height_mm: 20, layers: 2}
nets:
  GND: {class: ground}
  A: {class: signal}
components:
  R1: {value: 1k, symbol: Device:R, footprint: Resistor_SMD:R_0603_1608Metric, pins: {"1": A, "2": GND}, placement: {x: 10, y: 10, rot: 0, fixed: true}}
  R2: {value: 1k, symbol: Device:R, footprint: Resistor_SMD:R_0603_1608Metric, pins: {"1": A, "2": GND}, placement: {x: 20, y: 10, rot: 0, fixed: false}}
"""


def _design(tmp_path):
    (tmp_path / "design.yaml").write_text(YAML)
    d = Design.load(tmp_path)
    return d, Libs(design=d)


def test_board_text_is_reproducible(tmp_path):
    from hwflow import placer
    d, libs = _design(tmp_path)
    parts = placer.build_parts(d, libs)
    a, b = (pcb.board_text(pcb.build_board(d, libs, parts)) for _ in range(2))
    assert a == b


def test_route_cache_skips_router(tmp_path, monkeypatch):
    d, libs = _design(tmp_path)
    calls = {"route": 0, "drc": 0}

    def route(d, libs, parts, passes, out=None):
        calls["route"] += 1
        return [{"net": "A", "layer": "F.Cu", "w": 0.25, "x1": 10, "y1": 10, "x2": 20, "y2": 10}], [], "0 unrouted", 0

    def drc(board, **kw):
        calls["drc"] += 1
        return []

    monkeypatch.setattr(pl_stages, "_route_once", route)
    monkeypatch.setattr(kicad, "drc", drc)
    r1 = pl_stages.stage_layout(d, libs, seeds=1, iters=200)
    assert calls == {"route": 1, "drc": 1} and not r1["route_cache"]
    b1 = (tmp_path / "build" / "perf.kicad_pcb").read_text()
    r2 = pl_stages.stage_layout(d, libs, reuse=True)
    assert calls == {"route": 1, "drc": 2}, "a cache hit must not call the router, the full DRC runs again"
    assert r2["route_cache"] and r2["segments"] == r1["segments"]
    assert (tmp_path / "build" / "perf.kicad_pcb").read_text() == b1
    d.raw["board"]["clearance_mm"] = 0.3  # other rules: another key
    pl_stages.stage_layout(d, libs, reuse=True)
    assert calls["route"] == 2
    monkeypatch.setenv("KICAD_RIP_PREEXISTING", "1")  # another router environment: another key
    pl_stages.stage_layout(d, libs, reuse=True)
    assert calls["route"] == 3


def test_fixed_part_errors():
    class P:
        def __init__(self, fixed):
            self.fixed = fixed

    ko = {"sev": "error", "type": "items_not_allowed", "msg": "", "items": ["Footprint BT1"]}
    pad = {"sev": "error", "type": "clearance", "msg": "", "items": ["PTH pad 1 [A] of U2", "SMD pad 2 [B] of C1"]}
    trk = {"sev": "error", "type": "clearance", "msg": "", "items": ["Track [A] on F.Cu, length 3", "Footprint BT1"]}
    parts = {"BT1": P(True), "U2": P(True), "C1": P(True), "R9": P(False)}
    assert repair.fixed_part_errors([ko, pad], parts) == ["BT1", "C1", "U2"]
    assert repair.fixed_part_errors([ko, trk], parts) is None  # routing can cure a track error
    assert repair.fixed_part_errors([dict(ko, items=["Footprint R9"])], parts) is None  # R9 can be moved
    assert repair.fixed_part_errors([{"sev": "error", "type": "unconnected", "items": ["Pad 1 [A] of BT1"]}], parts) is None
    assert repair.fixed_part_errors([{"sev": "warning", "type": "silk_overlap", "items": []}], parts) is None


def _result(errors=0, vias=10, fixed=None):
    return {"ok": not errors, "board_mm": [30, 20], "unrouted": 0, "drc_errors": errors, "placement_violations": [],
            "vias": vias, "repair": None, "fixed_parts_only": fixed, "route_cache": None, "drc": [], "drc_warnings": 0,
            "segments": 1, "isolation": [], "router": "x", "seconds": 0}


def _run_auto(tmp_path, monkeypatch, plan, workers=4):
    """plan: {k: (seconds, result)}; the stub attempts finish in the order of their sleep times."""
    d, libs = _design(tmp_path)
    started = []

    def attempt(k):
        started.append(k)
        sec, r = plan[k]
        time.sleep(sec)
        w = pl_stages._AUTO["out"] / "auto" / f"a{k + 1}"
        w.mkdir(parents=True, exist_ok=True)
        (w / "perf.kicad_pcb").write_text(f"board {k + 1}")
        return {"k": k + 1, "r": r, "line": f"attempt {k + 1}"}

    monkeypatch.setattr(pl_stages, "_auto_attempt", attempt)
    monkeypatch.setattr(pl_stages, "_auto_pool", lambda n: cf.ThreadPoolExecutor(n))
    r = pl_stages.layout_auto(d, libs, attempts=4, workers=workers)
    return r, started, (tmp_path / "build" / "perf.kicad_pcb").read_text()


def test_pick_ignores_completion_order(tmp_path, monkeypatch):
    # attempts 2 and 3 tie with 1 on the rank key and finish first; the lowest index wins
    plan = {0: (0.3, _result(2, 10)), 1: (0.0, _result(2, 10)), 2: (0.1, _result(2, 10)), 3: (0.2, _result(3, 5))}
    r, started, board = _run_auto(tmp_path, monkeypatch, plan)
    assert r["auto"]["kept"] == 1 and board == "board 1"
    plan = {0: (0.3, _result(2, 10)), 1: (0.0, _result(1, 99)), 2: (0.1, _result(1, 12)), 3: (0.2, _result(3, 5))}
    r, _, board = _run_auto(tmp_path, monkeypatch, plan)
    assert r["auto"]["kept"] == 3 and board == "board 3"  # fewer errors, then fewer vias
    plan = {0: (0.3, _result(2, 10)), 1: (0.0, _result(0, 7)), 2: (0.1, _result(0, 3)), 3: (0.2, _result(0, 1))}
    r, _, board = _run_auto(tmp_path, monkeypatch, plan)
    assert r["auto"]["kept"] == 2 and r["ok"], "the first clean attempt wins, as when they ran in turn"


def test_first_attempt_final_starts_no_pool(tmp_path, monkeypatch):
    def no_pool(n):
        raise AssertionError("no pool when attempt 1 is final")

    monkeypatch.setattr(pl_stages, "_auto_pool", no_pool)
    d, libs = _design(tmp_path)
    monkeypatch.setattr(pl_stages, "_auto_attempt", lambda k: {"k": k + 1, "r": _result(0), "line": f"attempt {k + 1}"})
    r = pl_stages.layout_auto(d, libs, attempts=4, workers=4)
    assert r["ok"] and r["auto"]["kept"] == 1 and len(r["auto"]["attempts"]) == 1


def test_all_fixed_parts_run_one_attempt(tmp_path, monkeypatch):
    d, libs = _design(tmp_path)
    d.raw["components"]["R2"]["placement"]["fixed"] = True
    monkeypatch.setattr(pl_stages, "_auto_pool", lambda n: (_ for _ in ()).throw(AssertionError("no pool")))
    started = []
    monkeypatch.setattr(pl_stages, "_auto_attempt", lambda k: started.append(k) or {"k": k + 1, "r": _result(2), "line": "a"})
    d2 = Design.load(tmp_path)
    d2.raw["components"]["R2"]["placement"]["fixed"] = True
    r = pl_stages.layout_auto(d2, Libs(design=d2), attempts=4)
    assert started == [0] and r["auto"]["stopped"] == "all parts fixed: one attempt"


def test_early_stop_on_fixed_parts(tmp_path, monkeypatch):
    fixed = _result(2, 10, fixed=["BT1", "U2"])
    plan = {0: (0.0, _result(2, 10)), 1: (0.05, fixed), 2: (5, _result(0, 1)), 3: (5, _result(0, 1))}
    t = time.time()
    r, started, board = _run_auto(tmp_path, monkeypatch, plan, workers=2)
    assert time.time() - t < 3, "the later attempts are not waited for"
    assert started == [0, 1, 2] and r["auto"]["kept"] == 1  # 2 is the best of 1 and 2 (same errors, same vias)
    assert r["auto"]["stopped"] == "stopped: remaining errors are on fixed parts: BT1, U2"
    assert "stopped: remaining errors are on fixed parts: BT1, U2" in pl_stages._auto_summary(r)


def test_reuse_ignores_a_placement_for_another_board_size(tmp_path, monkeypatch):
    d, libs = _design(tmp_path)
    out = tmp_path / "build"
    out.mkdir(exist_ok=True)
    pj = out / "placement.json"
    pj.write_text(json.dumps({"__design_board__": [74, 46], "R1": [1, 1, 0]}))
    assert pl_stages._saved_board(pj) == [74, 46]

    class Placed(Exception):
        pass

    monkeypatch.setattr(pl_stages.placer, "place", lambda *a, **k: (_ for _ in ()).throw(Placed()))
    with pytest.raises(Placed):  # the design declares another size: place again instead of reusing
        pl_stages.stage_layout(d, libs, reuse=True)
    pj.write_text(json.dumps({"R1": [1, 1, 0]}))
    assert pl_stages._saved_board(pj) is None  # a file from before the field: reused as before


def test_hopeless_first_attempt_starts_no_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(pl_stages, "_auto_pool", lambda n: (_ for _ in ()).throw(AssertionError("no pool")))
    monkeypatch.delenv("HWFLOW_AUTO_MAX_ERRORS", raising=False)
    d, libs = _design(tmp_path)
    started = []
    monkeypatch.setattr(pl_stages, "_auto_attempt", lambda k: started.append(k) or {"k": k + 1, "r": _result(121), "line": "a"})
    r = pl_stages.layout_auto(d, libs, attempts=4, workers=4)
    assert started == [0] and r["auto"]["stopped"].startswith("stopped: attempt 1 has 121 errors (limit 40)")
    monkeypatch.setenv("HWFLOW_AUTO_MAX_ERRORS", "200")  # under the limit: the other attempts run
    monkeypatch.setattr(pl_stages, "_auto_pool", lambda n: cf.ThreadPoolExecutor(n))
    started.clear()
    r = pl_stages.layout_auto(d, libs, attempts=4, workers=4)
    assert sorted(started) == [0, 1, 2, 3] and not r["auto"]["stopped"]


def test_attempts_have_their_own_directories(tmp_path, monkeypatch):
    d, libs = _design(tmp_path)
    out = tmp_path / "build"
    out.mkdir()
    (out / "placement.json").write_text("{}")
    (out / "fp-lib-table").write_text("(fp_lib_table)")
    dirs = []

    def stage(d, libs, reuse, seeds, iters, passes, seed0, workdir, deadline=None):
        assert workdir not in dirs
        dirs.append(workdir)
        (workdir / "perf.kicad_pcb").write_text(str(seed0))
        return _result(0)

    monkeypatch.setattr(pl_stages, "stage_layout", stage)
    pl_stages._AUTO.update(d=d, libs=libs, out=out, size0=(30, 20), seeds=4, iters=1, passes=1, reuse=True)
    for k in range(4):
        assert pl_stages._auto_attempt(k)["k"] == k + 1
    assert len(set(dirs)) == 4 and all(x.parent == out / "auto" for x in dirs)
    assert (dirs[0] / "placement.json").exists() and not (dirs[1] / "placement.json").exists()  # only attempt 1 reuses
    assert all((x / "fp-lib-table").exists() for x in dirs)
    assert [(x / "perf.kicad_pcb").read_text() for x in dirs] == ["1", "5", "9", "13"]
