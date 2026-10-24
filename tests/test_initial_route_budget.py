"""Initial routing must respect the search budget without losing accepted copper."""
import json
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from hwflow import cli, kicad, pcb, placer, regionroute
from hwflow.design import Design
from hwflow.libs import Libs


def project(tmp_path):
    resistor = lambda x: {"value": "1k", "symbol": "Device:R", "footprint": "Resistor_SMD:R_0603_1608Metric",
                         "pins": {"1": "A", "2": "B"}, "placement": {"x": x, "y": 10, "fixed": True}}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump({"project": "budget", "board": {
        "width_mm": 30, "height_mm": 20, "layers": 2, "gnd_pour": False, "repair_rounds": 0},
        "components": {"R1": resistor(10), "R2": resistor(20)}}))
    d = Design.load(tmp_path)
    return d, Libs(design=d)


def route_setup(tmp_path, monkeypatch):
    d, libs = project(tmp_path)
    parts = placer.build_parts(d, libs)
    router = tmp_path / "router" / "py_router"
    router.mkdir(parents=True)
    (router / "route.py").write_text("# subprocess is stubbed\n")
    monkeypatch.setattr(pcb, "KRT_DIR", router.parent)
    monkeypatch.setattr(pcb, "router_version", lambda d: "test-router")
    monkeypatch.setattr(pcb, "_kicad_cli", lambda: "kicad-cli")
    monkeypatch.setenv("HWFLOW_NECKDOWN", "0")
    monkeypatch.setenv("HWFLOW_FLOW", "classic")
    track = {"net": "A", "layer": "F.Cu", "w": .25, "x1": 10, "y1": 10, "x2": 20, "y2": 10}
    calls = []

    def run(args, **kwargs):
        calls.append(kwargs["timeout"])
        if len(calls) % 2 == 0:
            raise subprocess.TimeoutExpired(args, kwargs["timeout"], output=b"unfinished retry output")
        pcb.write_board(Path(args[3]), pcb.build_board(d, libs, parts, [track], []))
        return subprocess.CompletedProcess(args, 0, 'JSON_SUMMARY_MIN: {"failed": 1, "failed_single": ["B"]}\n', '')

    monkeypatch.setattr(pcb.subprocess, "run", run)
    return d, libs, parts, calls


def test_initial_retry_timeout_keeps_completed_copper_and_skips_cache(tmp_path, monkeypatch):
    d, libs, parts, calls = route_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(kicad, "drc", lambda *a, **k: [{"sev": "error", "type": "unconnected", "msg": "B open",
                                                     "items": ["Pad 2 [B] of R1", "Pad 2 [B] of R2"]}])
    deadline = time.time() + 60
    result = cli.stage_layout(d, libs, seeds=1, iters=1, deadline=deadline)
    assert calls and max(calls) <= 60, "the first attempt must pass its remaining budget to KRT"
    assert result["budget_exhausted"] and not result["ok"]
    assert result["segments"] == 1 and result["unrouted"] == 1
    kept = pcb.read_board(tmp_path / "build" / "budget.kicad_pcb")
    assert [t["net"] for t in kept["segments"]] == ["A"]
    assert "budget" in result["router"] and "unfinished retry output" in (tmp_path / "build" / "krt.log").read_text()
    assert not list((tmp_path / "build" / "routecache").glob("*.json")), "partial routing must not poison the board cache"
    assert (tmp_path / "build" / "placement.json").exists()


def test_repair_route_still_rejects_timeout(tmp_path, monkeypatch):
    d, libs, parts, calls = route_setup(tmp_path, monkeypatch)
    out = tmp_path / "out"
    out.mkdir()
    src = out / "in.kicad_pcb"
    pcb.write_board(src, pcb.build_board(d, libs, parts))
    with pytest.raises(subprocess.TimeoutExpired):
        pcb.krt_route(d, src, out, "repair", libs=libs, deadline=time.time() + 60)
    assert len(calls) == 2


def test_region_timeout_keeps_completed_group_without_caching_partial(tmp_path, monkeypatch):
    d, libs = project(tmp_path)
    parts = placer.build_parts(d, libs)
    out = tmp_path / "out"
    out.mkdir()
    track = {"net": "A", "layer": "F.Cu", "w": .25, "x1": 10, "y1": 10, "x2": 20, "y2": 10}
    monkeypatch.setattr(regionroute, "partition", lambda *a: [{"name": n, "kind": "region", "nets": [n]} for n in ["A", "B"]])
    monkeypatch.setattr(pcb, "router_version", lambda d: "test-router")
    seen = []
    deadline = time.time() + 60

    def route(design, src, workdir, stem, **kwargs):
        seen.append(kwargs)
        target = workdir / (stem + ".kicad_pcb")
        pcb.write_board(target, pcb.build_board(d, libs, parts, [track], []))
        if kwargs["nets"] == {"A"}:
            return target, {"A": {"failed": 0}}, [], 1
        return target, {"budget": {"budget_exhausted": True, "failed": 1, "failed_single": ["B"]}}, ["layout budget exhausted"], 0

    monkeypatch.setattr(pcb, "krt_route", route)
    tracks, vias, summary, opened, rows = regionroute.route(d, libs, parts, out, tmp_path / "cache", deadline=deadline)
    assert all(s["deadline"] == deadline and s["keep_partial"] for s in seen)
    assert [t["net"] for t in tracks] == ["A"] and opened == 1
    assert d.route_budget_exhausted and "budget" in summary
    saved = list((tmp_path / "cache" / "groups").glob("*.json"))
    assert len(saved) == 1
    assert [t["net"] for t in json.loads(saved[0].read_text())["tracks"]] == ["A"]


def test_auto_keeps_and_diagnoses_first_attempt_at_budget(tmp_path, monkeypatch):
    d, libs, parts, calls = route_setup(tmp_path, monkeypatch)
    monkeypatch.setenv("HWFLOW_LAYOUT_BUDGET_S", "60")
    monkeypatch.setenv("HWFLOW_AUTO_MAX_ERRORS", "0")
    monkeypatch.setattr(kicad, "drc", lambda *a, **k: [{"sev": "error", "type": "unconnected", "msg": "B open",
                                                     "items": ["Pad 2 [B] of R1", "Pad 2 [B] of R2"]}])
    monkeypatch.setattr(cli, "layout_diagnosis", lambda d, libs, attempts: {"retained": len(attempts)})
    result = cli.layout_auto(d, libs, attempts=1, seeds=1, iters=1)
    assert result["segments"] == 1 and result["budget_exhausted"]
    assert result["auto"]["kept"] == 1 and "wall-time budget" in result["auto"]["stopped"]
    assert result["diagnosis"] == {"retained": 1}
    assert "structural change" not in result["auto"]["stopped"]
    assert (tmp_path / "build" / "budget.kicad_pcb").exists()


def test_expired_budget_starts_no_initial_router(tmp_path, monkeypatch):
    d, libs, parts, calls = route_setup(tmp_path, monkeypatch)
    tracks, vias, summary, opened = cli._route_once(d, libs, parts, 1, tmp_path, deadline=time.time() - 1)
    assert not calls and not tracks and not vias
    assert d.route_budget_exhausted and opened == 2 and "before routing" in summary


@pytest.mark.parametrize("with_deadline", [False, True])
def test_freerouting_timeout_never_imports_unfinished_session(tmp_path, monkeypatch, with_deadline):
    d, libs = project(tmp_path)
    d.raw["board"]["router"] = "freerouting"
    parts = placer.build_parts(d, libs)
    monkeypatch.setattr(pcb, "export_dsn", lambda *a: None)

    def timed_out(dsn, ses, **kwargs):
        ses.write_text("unfinished session from killed process")
        return "TIMEOUT after 1s\nAuto-routing stage completed (0 unrouted and 0 violations)"

    monkeypatch.setattr(pcb, "run_freerouting", timed_out)
    monkeypatch.setattr(pcb, "import_ses", lambda *a: pytest.fail("must not parse the killed process's session"))
    result = cli._route_once(d, libs, parts, 1, tmp_path,
                             deadline=time.time() + 60 if with_deadline else None)
    assert result[:2] == ([], []) and result[3] == 2
    assert d.route_budget_exhausted and "no completed Freerouting candidate" in result[2]


def test_area_first_timeout_retains_accepted_copper_and_dimensions(tmp_path, monkeypatch):
    d, libs, parts, calls = route_setup(tmp_path, monkeypatch)
    d.raw["board"]["optimize_area"] = True
    source = d.path.read_bytes()
    monkeypatch.setattr(placer, "place", lambda *a, **k: (parts, 0, []))
    monkeypatch.setattr(kicad, "drc", lambda *a, **k: [{"sev": "error", "type": "unconnected", "msg": "B open",
                                                     "items": ["Pad 2 [B] of R1", "Pad 2 [B] of R2"]}])
    now = [100.0]
    monkeypatch.setattr(cli.time, "time", lambda: now[0])
    run = pcb.subprocess.run

    def expire_on_retry(args, **kwargs):
        if calls:
            now[0] = 161.0
        return run(args, **kwargs)

    monkeypatch.setattr(pcb.subprocess, "run", expire_on_retry)
    result = cli.stage_layout(d, libs, seeds=1, iters=1, deadline=160.0)
    assert result["segments"] == 1 and result["unrouted"] == 1
    assert result["budget_exhausted"] and not result["ok"]
    assert result["board_mm"] == [10, 8] and len(calls) == 2
    board = pcb.read_board(tmp_path / "build" / "budget.kicad_pcb")
    assert [t["net"] for t in board["segments"]] == ["A"]
    assert not list((tmp_path / "build" / "routecache").glob("*.json"))
    assert d.path.read_bytes() == source, "area trials must not change the declared source dimensions"


def test_area_prefers_complete_candidate_before_later_timeout(tmp_path, monkeypatch):
    d, libs = project(tmp_path)
    d.raw["board"]["optimize_area"] = True
    parts = placer.build_parts(d, libs)
    monkeypatch.setattr(placer, "place", lambda *a, **k: (parts, 0, []))
    monkeypatch.setattr(kicad, "drc", lambda *a, **k: [])
    calls = []
    now = [100.0]
    monkeypatch.setattr(cli.time, "time", lambda: now[0])

    def route(design, *args, **kwargs):
        calls.append([design.board["width_mm"], design.board["height_mm"]])
        partial = len(calls) > 1
        if partial:
            now[0] = 161.0
        design.route_budget_exhausted = partial
        track = {"net": "B" if partial else "A", "layer": "F.Cu", "w": .25,
                 "x1": 10, "y1": 10, "x2": 20, "y2": 10}
        # A truncated summary must not overwrite the complete candidate even if it reports zero open nets.
        return [track], [], "partial" if partial else "complete", 0

    monkeypatch.setattr(cli, "_route_once", route)
    result = cli.stage_layout(d, libs, seeds=1, iters=1, deadline=160.0)
    assert len(calls) == 2 and calls[0] != calls[1]
    assert result["board_mm"] == calls[0] and result["router"] == "complete"
    assert not result["budget_exhausted"]
    board = pcb.read_board(tmp_path / "build" / "budget.kicad_pcb")
    assert [t["net"] for t in board["segments"]] == ["A"]
    saved = list((tmp_path / "build" / "routecache").glob("*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text())["tracks"][0]["net"] == "A"


@pytest.mark.parametrize("accept_first_variant", [False, True])
def test_interrupted_width_pass_is_not_counted_as_completed(tmp_path, monkeypatch, accept_first_variant):
    d, libs, parts, calls = route_setup(tmp_path, monkeypatch)
    if not accept_first_variant:
        calls.append(0)  # the first subprocess will time out, with no completed candidate
    src = tmp_path / "input.kicad_pcb"
    pcb.write_board(src, pcb.build_board(d, libs, parts))
    kept, summaries, notes, completed = pcb.krt_route(d, src, tmp_path, "partial", libs=libs,
                                                     deadline=time.time() + 60, keep_partial=True)
    assert completed == 0, "an interrupted width pass may retain copper but is not a fully completed pass"
    assert summaries["budget"]["budget_exhausted"]
    assert len(pcb.read_board(kept)["segments"]) == int(accept_first_variant)
