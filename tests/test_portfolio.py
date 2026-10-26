"""Layout portfolio (pipeline/portfolio.py, off by default, HWFLOW_PORTFOLIO=1): the strategies of attempt 1 run at once, the best converged one is kept by a
fixed score, the slow ones are stopped after the grace time, and without a converged one the sequential attempts
go on with the best strategy's knobs. The attempts are stubs, so these run in seconds."""
import concurrent.futures as cf
import json
import os
import time


from hwflow.core.design import Design
from hwflow.core.libs import Libs
from hwflow.pipeline import portfolio
from hwflow.pipeline import stages as cli
from hwflow.pipeline.portfolio import Strategy

YAML = """project: pf
board: {width_mm: 30, height_mm: 20, layers: 2}
nets:
  GND: {class: ground}
  A: {class: signal}
components:
  R1: {value: 1k, symbol: Device:R, footprint: Resistor_SMD:R_0603_1608Metric, pins: {"1": A, "2": GND}, placement: {x: 10, y: 10, rot: 0, fixed: true}}
  R2: {value: 1k, symbol: Device:R, footprint: Resistor_SMD:R_0603_1608Metric, pins: {"1": A, "2": GND}}
"""


def _r(errors=0, vias=10, track=5.0, route_s=1.0, viol=()):
    return {"ok": errors == 0 and not viol, "unrouted": 0, "drc_errors": errors, "placement_violations": list(viol),
            "vias": vias, "segments": 1, "router": "", "drc": [], "board_mm": [30, 20], "drc_warnings": 0,
            "nets": {"A": {"length_mm": track}}, "timing": {"route_s": route_s}}


def test_score_order():
    s = portfolio.score
    assert s(_r(0, 20), 4) < s(_r(1, 1), 0)              # converged first
    assert s(_r(0, 9), 4) < s(_r(0, 10), 0)              # then fewer vias
    assert s(_r(0, 9, 4.0), 4) < s(_r(0, 9, 5.0), 0)     # then shorter track
    assert s(_r(0, 9, 4.0, 2.0), 1) < s(_r(0, 9, 4.0, 3.0), 0)
    assert s(_r(0, 9, 4.0, 2.0), 0) < s(_r(0, 9, 4.0, 2.0), 1)  # then the strategy order
    assert not portfolio.converged(_r(0, viol=["courtyard overlap A/B"]))
    assert s(_r(2), 0) < s(_r(0, viol=["x", "y", "z"]) | {"drc_errors": 3}, 0)


def test_strategies_leave_out_what_cannot_differ(tmp_path):
    (tmp_path / "design.yaml").write_text(YAML)
    d = Design.load(tmp_path)
    assert [s.name for s in portfolio.strategies(d)] == ["base", "order_inside_out", "order_mps_reverse"]
    portfolio.SELECT.append(lambda d, libs, ss: [s for s in ss if s.name != "order_mps_reverse"])
    try:
        assert [s.name for s in portfolio.strategies(d)] == ["base", "order_inside_out"]
    finally:
        portfolio.SELECT.clear()


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("HWFLOW_PORTFOLIO", raising=False)
    assert not portfolio.on()
    monkeypatch.setenv("HWFLOW_PORTFOLIO", "1")
    assert portfolio.on()


def _stub(plan):
    """stages._auto_attempt stub: strategy env T -> (seconds, result); writes a board into its work directory."""
    def attempt(k, workdir=None, env=None, placement=None):
        sec, r = plan[(env or {}).get("T", "fallback")]
        time.sleep(sec)
        w = workdir or cli._AUTO["out"] / "auto" / f"a{k + 1}"
        w.mkdir(parents=True, exist_ok=True)
        (w / "pf.kicad_pcb").write_text(f"board {(env or {}).get('T', 'fallback')} {k + 1}")
        (w / "placed.txt").write_text(placement.read_text() if placement else "own")
        return {"k": k + 1, "r": r, "line": f"attempt {k + 1}"}
    return attempt


def _lanes(*names):
    return [Strategy(n, {"T": n}) for n in names]


def _place(k, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"shared {k}")
    return True


def test_shared_placement_once(tmp_path, monkeypatch):
    plan = {"a": (0.1, _r(0, 3)), "b": (0.1, _r(0, 2)), "r": (0.1, _r(0, 1))}
    monkeypatch.setattr(cli, "_auto_attempt", _stub(plan))
    monkeypatch.setattr(portfolio, "POLL_S", 0.05)
    monkeypatch.setenv("HWFLOW_PORTFOLIO_GRACE", "inf")
    cli._AUTO.update(out=tmp_path)
    lanes = _lanes("a", "b") + [Strategy("r", {"T": "r", "HWFLOW_ROADS": "1"})]
    res = portfolio.run(tmp_path, 0, workers=4, lanes=lanes, log=lambda m: None, place=_place)
    placed = {n: (tmp_path / "auto" / f"p_{n}" / "placed.txt").read_text() for n in "abr"}
    assert placed == {"a": "shared 0", "b": "shared 0", "r": "own"}  # roads places for itself
    assert res["place_s"] is not None and res["strategy"] == "r"


def test_run_keeps_the_best_converged_and_stops_the_slow(tmp_path, monkeypatch):
    plan = {"a": (0.3, _r(0, 12)), "b": (0.6, _r(0, 8)), "c": (30, _r(0, 1)), "d": (0.1, _r(3, 1))}
    monkeypatch.setattr(cli, "_auto_attempt", _stub(plan))
    monkeypatch.setattr(portfolio, "POLL_S", 0.05)
    cli._AUTO.update(out=tmp_path)
    t = time.time()
    res = portfolio.run(tmp_path, 0, workers=4, lanes=_lanes("a", "b", "c", "d"), log=lambda m: None)
    assert time.time() - t < 10, "the slow strategy is stopped after the grace time"
    rows = {r["strategy"]: r for r in res["rows"]}
    assert rows["c"]["status"].startswith("stopped")
    # a converges at about 0.3 s; b (fewer vias) finishes within 1.5 x that only if it is fast enough: either way the
    # winner is the best converged one among those that finished
    done = [n for n in "ab" if rows[n]["status"] == "converged"]
    assert res["strategy"] == ("b" if "b" in done else "a")
    assert json.loads(json.dumps(res["rows"]))  # the table goes into status.json
    assert res["peak_mb"] >= 0 and res["cpu_s"] >= 0


def test_run_waits_for_all_with_infinite_grace(tmp_path, monkeypatch):
    plan = {"a": (0.1, _r(0, 12)), "b": (0.8, _r(0, 8))}
    monkeypatch.setattr(cli, "_auto_attempt", _stub(plan))
    monkeypatch.setattr(portfolio, "POLL_S", 0.05)
    monkeypatch.setenv("HWFLOW_PORTFOLIO_GRACE", "inf")
    cli._AUTO.update(out=tmp_path)
    res = portfolio.run(tmp_path, 0, workers=2, lanes=_lanes("a", "b"), log=lambda m: None)
    assert res["strategy"] == "b"  # reproducible: every strategy finished, fewer vias wins


def test_no_convergence_keeps_the_best_partial(tmp_path, monkeypatch):
    plan = {"a": (0.1, _r(4)), "b": (0.1, _r(2)), "c": (0.1, None)}
    monkeypatch.setattr(cli, "_auto_attempt", _stub(plan))
    monkeypatch.setattr(portfolio, "POLL_S", 0.05)
    cli._AUTO.update(out=tmp_path)
    res = portfolio.run(tmp_path, 0, workers=1, lanes=_lanes("a", "b", "c"), log=lambda m: None)
    assert res["strategy"] == "b" and res["env"] == {"T": "b"}
    assert [r["status"] for r in res["rows"]] == ["partial", "partial", "error"]


def test_layout_auto_portfolio_then_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("HWFLOW_PORTFOLIO", "1")
    (tmp_path / "design.yaml").write_text(YAML)
    d = Design.load(tmp_path)
    monkeypatch.setattr(portfolio, "POLL_S", 0.05)
    monkeypatch.setattr(portfolio, "STRATEGIES", _lanes("a", "b"))
    monkeypatch.setattr(cli, "_auto_pool", lambda n: cf.ThreadPoolExecutor(n))
    monkeypatch.setattr(cli, "_auto_place", _place)
    # converged in the portfolio: no fallback attempt, the strategy's board is kept
    monkeypatch.setattr(cli, "_auto_attempt", _stub({"a": (0.1, _r(1)), "b": (0.1, _r(0)), "fallback": (0, _r(0))}))
    r = cli.layout_auto(d, Libs(design=d), attempts=3)
    assert r["ok"] and r["auto"]["kept"] == 1 and r["portfolio"]["strategy"] == "b"
    assert (tmp_path / "build" / "pf.kicad_pcb").read_text() == "board b 1"
    assert "| b (kept) | converged |" in "\n".join(portfolio.report_lines(r["portfolio"]))
    # nothing converges: the sequential attempts follow with the best strategy's knobs
    seen = []

    def attempt(k, workdir=None, env=None, placement=None):
        e = env if env is not None else cli._AUTO.get("knobs") or {}
        seen.append((k, e.get("T")))
        return _stub({"a": (0.1, _r(3)), "b": (0.1, _r(2)), "fb": (0, _r(0 if k == 2 else 1))})(
            k, workdir, {"T": "fb"} if workdir is None else env)
    monkeypatch.setattr(cli, "_auto_attempt", attempt)
    r = cli.layout_auto(d, Libs(design=d), attempts=3)
    assert sorted(x for x in seen if x[0] > 0) == [(1, "b"), (2, "b")]
    assert r["ok"] and r["auto"]["kept"] == 3 and r["portfolio"]["strategy"] is None
    assert os.environ.get("T") is None


def test_area_search_runs_base_first(tmp_path, monkeypatch):
    (tmp_path / "design.yaml").write_text(YAML.replace("layers: 2}", "layers: 2, optimize_area: true}"))
    d = Design.load(tmp_path)
    monkeypatch.setattr(portfolio, "POLL_S", 0.05)
    cli._AUTO.update(out=tmp_path)
    lanes = [Strategy("base", {"T": "base"})] + _lanes("a", "b")
    monkeypatch.setattr(cli, "_auto_attempt", _stub({"base": (0.1, _r(0, 9)), "a": (0.1, _r(0, 1)), "b": (0.1, _r(0, 1))}))
    res = portfolio.run(tmp_path, 0, workers=4, lanes=lanes, design=d, log=lambda m: None)
    assert res["strategy"] == "base" and [r["status"] for r in res["rows"]][1:] == ["not started (base converged)"] * 2
    monkeypatch.setattr(cli, "_auto_attempt", _stub({"base": (0.1, _r(2)), "a": (0.1, _r(1)), "b": (0.1, _r(0, 4))}))
    res = portfolio.run(tmp_path, 0, workers=4, lanes=lanes, design=d, log=lambda m: None)
    assert res["strategy"] == "b" and [r["kept"] for r in res["rows"]] == [False, False, True]
