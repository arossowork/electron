"""Failed local repair may free nearby copper at non-pour open-net endpoints."""
from types import SimpleNamespace

import pytest

from hwflow.layout.route import repair


def design():
    return SimpleNamespace(name="neighbors", board={"gnd_pour": True}, ground_nets=lambda: {"GND"})


def track(net, x, y):
    return {"net": net, "layer": "F.Cu", "w": 0.2, "x1": x, "y1": y, "x2": x + 1, "y2": y}


def opened(net="OPEN", first="Pad 1 [{net}] of U1 on F.Cu", at=None):
    return {"sev": "error", "type": "unconnected", "msg": "Missing connection between items",
            "items": [first.format(net=net), f"Pad 2 [{net}] of U2 on F.Cu"],
            "at": [[10, 10], [30, 10]] if at is None else at}


@pytest.mark.parametrize("endpoint", ["Pad 1 [{net}] of U1 on F.Cu", "Track [{net}] on F.Cu", "Via [{net}] on F.Cu"])
def test_open_endpoint_neighbors_are_added_only_with_retry_radius(endpoint):
    tracks = [track("TRACK_NEIGHBOR", 10, 10.5), track("FAR", 50, 50), track("GND", 10, 10.1)]
    vias = [{"net": "VIA_NEIGHBOR", "x": 29, "y": 11}, {"net": "GND", "x": 30, "y": 10.1}]
    drc = [opened(first=endpoint)]

    assert repair.plan(design(), tracks, vias, drc, radius=0)["rip"] == ["OPEN"]
    assert repair.plan(design(), tracks, vias, drc, radius=2)["rip"] == ["OPEN", "TRACK_NEIGHBOR", "VIA_NEIGHBOR"]


def test_unlocated_open_endpoints_do_not_invent_neighbors():
    drc = [opened(at=[None, None])]
    result = repair.plan(design(), [track("NEAR_ORIGIN", 0, 0)], [], drc, radius=2)
    assert result["rip"] == ["OPEN"]


def test_split_ground_pour_does_not_expand_to_signal_neighbors():
    result = repair.plan(design(), [track("SIGNAL", 10, 10.1)], [],
                         [opened(net="GND", first="Zone [{net}] on F.Cu")], radius=2)
    assert result == {"rip": [], "islands": ["GND"], "drop_vias": [], "unfixable": 0}


def test_endpoint_expansion_keeps_named_nets_first_and_respects_ripup_cap():
    tracks = [track(f"NEIGHBOR_{i}", 10, 10 + i / 100) for i in range(20)]
    result = repair.plan(design(), tracks, [], [opened("OPEN_A"), opened("OPEN_B")], radius=2)
    assert len(result["rip"]) == repair.MAX_RIPUP == 12
    assert result["rip"][:2] == ["OPEN_A", "OPEN_B"]
    assert result["rip"][2:] == [f"NEIGHBOR_{i}" for i in range(10)]


@pytest.mark.parametrize("retry_improves", [True, False])
def test_failed_round_frees_endpoint_neighbor_then_keeps_only_improvement(tmp_path, monkeypatch, retry_improves):
    d = design()
    tracks = [track("OPEN", 12, 10), track("BLOCKER", 10, 10.5), track("FAR", 50, 50)]
    drc = [opened()]
    board = tmp_path / "neighbors.kicad_pcb"
    board.write_text("accepted initial board")
    report = board.with_suffix(".drc.json")
    report.write_text("accepted initial DRC")
    calls = []
    monkeypatch.setenv("HWFLOW_REPAIR_MAX_STALL", "2")

    def reroute(d, libs, parts, workdir, kept_tracks, kept_vias, nets, attempt, island, deadline=None):
        # Both rejected candidates must restore the last accepted board before another round.
        assert board.read_text() == "accepted initial board"
        assert report.read_text() == "accepted initial DRC"
        calls.append((list(nets), [t["net"] for t in kept_tracks]))
        new_tracks = list(kept_tracks) + [track(net, 20, 20 + i) for i, net in enumerate(nets)]
        return new_tracks, kept_vias

    checks = []
    def check(tracks, vias, island):
        checks.append(1)
        board.write_text(f"candidate {len(checks)}")
        report.write_text(f"candidate DRC {len(checks)}")
        return [] if retry_improves and len(checks) == 2 else drc

    monkeypatch.setattr(repair, "reroute", reroute)
    result = repair.repair(d, None, {}, tracks, [], drc, board, check, rounds=2)

    assert calls == [(["OPEN"], ["BLOCKER", "FAR"]), (["OPEN", "BLOCKER"], ["FAR"])]
    assert result["report"]["rounds"] == 2
    assert any("rejected" in line for line in result["report"]["log"])
    if retry_improves:
        assert result["drc"] == [] and board.read_text() == "candidate 2"
        assert report.read_text() == "candidate DRC 2"
    else:
        assert result["tracks"] == tracks and result["drc"] == drc
        assert board.read_text() == "accepted initial board"
        assert report.read_text() == "accepted initial DRC"
