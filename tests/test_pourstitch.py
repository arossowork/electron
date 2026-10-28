"""Pour stitching (layout.route.pourstitch): a GND pad walled in by a signal track on F.Cu, on a board whose B.Cu
GND pour is joined to the F.Cu pour by one via."""

import pytest

from hwflow.core.design import Design
from hwflow.core.settings import get_settings
from hwflow.layout.board import kicad, pcb
from hwflow.layout.route import pourstitch

YAML = """project: stitchfix
description: one GND pad walled in by a signal track
board: {width_mm: 20, height_mm: 20, layers: 2}
nets:
  GND: {class: ground}
  SIG: {class: signal}
  BIG: {class: signal}
components: {}
"""


def _ring(cx, cy, half):
    pts = [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half), (cx - half, cy + half)]
    return [{"net": "SIG", "layer": "F.Cu", "w": 0.25, "x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]}
            for a, b in zip(pts, pts[1:] + pts[:1])]


def _board(path, tracks, vias, pad=1.0, paste=True):
    """20 x 20 mm, GND pour on both layers, the GND pad TP1 (pad x pad mm, F.Cu) at (10, 10) and TP2 (1 x 1 mm) at
    (16, 4), on the main pour."""
    from hwflow.core.sexpr import Q, dumps
    uid = iter(f"00000000-0000-0000-0000-{i:012d}" for i in range(1, 10000))

    def zone(layer):
        return ["zone", ["net", 1], ["net_name", Q("GND")], ["layer", Q(layer)], ["uuid", Q(next(uid))],
                ["hatch", "edge", 0.5], ["connect_pads", "yes", ["clearance", 0.2]], ["min_thickness", 0.25],
                ["filled_areas_thickness", "no"], ["fill", "yes", ["thermal_gap", 0.3], ["thermal_bridge_width", 0.4],
                                                   ["island_removal_mode", 0]],
                ["polygon", ["pts", ["xy", 0.3, 0.3], ["xy", 19.7, 0.3], ["xy", 19.7, 19.7], ["xy", 0.3, 19.7]]]]

    nets = {"GND": 1, "SIG": 2, "BIG": 3}
    tree = ["kicad_pcb", ["version", "20241229"], ["generator", Q("hwflow")], ["general", ["thickness", 1.6]],
            ["paper", Q("A4")], pcb._layers(2), ["setup", ["pad_to_mask_clearance", 0]], ["net", 0, Q("")],
            ["net", 1, Q("GND")], ["net", 2, Q("SIG")], ["net", 3, Q("BIG")],
            ["gr_rect", ["start", 0, 0], ["end", 20, 20], ["stroke", ["width", 0.1], ["type", "default"]],
             ["fill", "no"], ["layer", Q("Edge.Cuts")], ["uuid", Q(next(uid))]],
            ["footprint", Q("X"), ["layer", Q("F.Cu")], ["uuid", Q(next(uid))], ["at", 10, 10],
             ["property", Q("Reference"), Q("TP1"), ["at", 0, 0], ["layer", Q("F.Fab")], ["uuid", Q(next(uid))]],
             ["pad", Q("1"), "smd", "rect", ["at", 0, 0], ["size", pad, pad],
              ["layers", Q("F.Cu"), Q("F.Mask"), *([Q("F.Paste")] if paste else [])],
              ["net", 1, Q("GND")], ["uuid", Q(next(uid))]]],
            ["footprint", Q("X"), ["layer", Q("F.Cu")], ["uuid", Q(next(uid))], ["at", 16, 4],
             ["property", Q("Reference"), Q("TP2"), ["at", 0, 0], ["layer", Q("F.Fab")], ["uuid", Q(next(uid))]],
             ["pad", Q("1"), "smd", "rect", ["at", 0, 0], ["size", 1, 1], ["layers", Q("F.Cu"), Q("F.Mask")],
              ["net", 1, Q("GND")], ["uuid", Q(next(uid))]]]]
    for t in tracks:
        tree.append(["segment", ["start", t["x1"], t["y1"]], ["end", t["x2"], t["y2"]], ["width", t["w"]],
                     ["layer", Q(t["layer"])], ["net", nets[t["net"]]], ["uuid", Q(next(uid))]])
    for v in vias:
        tree.append(["via", ["at", v["x"], v["y"]], ["size", 0.6], ["drill", 0.3], ["layers", Q("F.Cu"), Q("B.Cu")],
                     ["net", nets[v["net"]]], ["uuid", Q(next(uid))]])
    tree += [zone("F.Cu"), zone("B.Cu")]
    path.write_text(dumps(tree) + "\n")


@pytest.fixture
def design(tmp_path):
    try:
        kicad.cli()
    except FileNotFoundError:
        pytest.skip("kicad-cli not installed")
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "design.yaml").write_text(YAML)
    return Design.load(proj)


@pytest.fixture(autouse=True)
def _no_plane_repair(monkeypatch):
    """The router's plane repair only where a test asks for it (it runs KiCadRoutingTools)."""
    monkeypatch.setattr(pourstitch, "planes", lambda *a, **k: None)


def _unconnected(drc):
    return [x for x in drc if x["type"] == "unconnected"]


JOIN = [{"net": "GND", "x": 16.0, "y": 16.0}]  # joins the F.Cu pour outside the ring to B.Cu


def test_walled_in_pad_needs_a_displacing_stub(design, tmp_path):
    board = tmp_path / "b.kicad_pcb"
    ring = _ring(10, 10, 1.3)  # too tight for a via between the pad and the track
    _board(board, ring, JOIN)
    drc = kicad.drc(board)
    assert _unconnected(drc), "fixture: the pad's fill must be cut off"
    free = pourstitch.plan(design, board, "GND")
    assert free["stats"]["left"] == 1 and not free["vias"] and not free["rip"]
    rip = pourstitch.plan(design, board, "GND", rip=True)
    assert rip["rip"] == ["SIG"] and len(rip["vias"]) == 1 and len(rip["tracks"]) == 1
    s, v = rip["tracks"][0], rip["vias"][0]
    assert (s["x1"], s["y1"]) == (10, 10) and (s["x2"], s["y2"]) == (v["x"], v["y"]) and s["layer"] == "F.Cu"
    # without the displaced net, the stub and via join the pad: no unconnected item, no other error
    _board(board, rip["tracks"], JOIN + rip["vias"])
    assert not [x for x in kicad.drc(board) if x["sev"] == "error"]


def test_the_displacing_stub_crosses_the_net_with_the_fewest_pads(design, tmp_path, monkeypatch):
    """A pad walled in by a supply rail (many pads) on three sides and a two-pad signal on the fourth: the stub crosses
    the signal, whose re-route is short, not the rail, although the rail side comes first."""
    board = tmp_path / "b.kicad_pcb"
    ring = _ring(10, 10, 1.3)
    for t in ring:  # the west side (x 8.7) stays SIG, 0.25 mm of copper gap off the corners; the others the rail
        if t["x1"] == t["x2"] == 8.7:
            t["y1"], t["y2"] = 11.3 - 0.5, 8.7 + 0.5
        else:
            t["net"] = "BIG"
    _board(board, ring, JOIN)
    assert _unconnected(kicad.drc(board))
    monkeypatch.setattr(design, "nets", lambda: {"GND": [("TP1", "1"), ("TP2", "1")], "SIG": [("A", "1"), ("B", "1")],
                                                 "BIG": [(f"U{i}", "1") for i in range(20)]})
    rip = pourstitch.plan(design, board, "GND", rip=True)
    assert rip["rip"] == ["SIG"] and len(rip["vias"]) == 1
    assert rip["vias"][0]["x"] < 10


def test_exposed_pad_gets_a_via_in_pad(design, tmp_path):
    board = tmp_path / "b.kicad_pcb"
    ring = _ring(10, 10, 1.6)
    _board(board, ring, JOIN, pad=1.6, paste=False)
    assert _unconnected(kicad.drc(board))
    p = pourstitch.plan(design, board, "GND")
    assert p["stats"]["in_pad"] == 1 and p["vias"] == [{"net": "GND", "x": 10.0, "y": 10.0}] and not p["tracks"]
    _board(board, ring, JOIN + p["vias"], pad=1.6, paste=False)
    assert not [x for x in kicad.drc(board) if x["sev"] == "error"]


def test_stub_reaches_the_plane_through_a_gap(design, tmp_path):
    """A wide ring leaves room for a via next to the pad, inside the ring: a via in the fragment (no stub)."""
    board = tmp_path / "b.kicad_pcb"
    ring = _ring(10, 10, 2.5)
    _board(board, ring, JOIN)
    assert _unconnected(kicad.drc(board))
    p = pourstitch.plan(design, board, "GND")
    assert p["stats"]["inside"] == 1 and not p["rip"]
    v = p["vias"][0]
    assert abs(v["x"] - 10) < 2.2 and abs(v["y"] - 10) < 2.2
    _board(board, ring, JOIN + p["vias"])
    assert not [x for x in kicad.drc(board) if x["sev"] == "error"]


def test_better_keeps_other_error_types_strict():
    unc = {"sev": "error", "type": "unconnected", "items": [], "at": []}
    cl = {"sev": "error", "type": "clearance", "items": [], "at": []}
    assert pourstitch.better([unc], [unc, unc])
    assert not pourstitch.better([cl], [unc, unc])  # an open item traded for a clearance error
    assert not pourstitch.better([unc, unc], [unc, unc])


def test_flag_is_off_by_default(monkeypatch):
    monkeypatch.delenv("HWFLOW_POUR_STITCH", raising=False)
    assert get_settings().layout.pour_stitch is False
    monkeypatch.setenv("HWFLOW_POUR_STITCH", "1")
    assert get_settings().layout.pour_stitch is True


def test_avoid_keeps_a_net_from_being_displaced(design, tmp_path):
    board = tmp_path / "b.kicad_pcb"
    _board(board, _ring(10, 10, 1.3), JOIN)
    kicad.drc(board)
    p = pourstitch.plan(design, board, "GND", rip=True, avoid={"SIG"})
    assert not p["rip"] and not p["vias"] and p["stats"]["left"] == 1


class _D:
    """The part of a Design `stitch` reads."""
    board = {"gnd_pour": True}

    def ground_nets(self):
        return ["GND"]


def _unc(net):
    return {"sev": "error", "type": "unconnected", "items": [f"Zone [{net}] on F.Cu", f"Zone [{net}] on F.Cu"], "at": []}


def test_displace_then_free_round_and_retry_other_nets(tmp_path, monkeypatch):
    """A displacing round that leaves the island cut off (the stub's via lands in another pocket) is followed by free
    rounds on the re-routed board; one that does not pay off is undone and the next round displaces other nets."""
    from hwflow.layout.route import repair
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)")
    monkeypatch.setattr(repair, "zone_fill", lambda b: {("GND", "F.Cu"): []})
    calls = []

    def plan(d, b, net, rip=False, avoid=()):
        calls.append((rip, set(avoid)))
        vias = {v["tag"] for v in state["vias"]}
        out = {"tracks": [], "vias": [], "rip": [], "stats": {"groups": 1, "pads": 0, "inside": 0, "in_pad": 0,
                                                                "stub": 0, "rip": 0, "left": 0}}
        if not rip and "stub_b" in vias and "free" not in vias:  # the re-routed B opened room for a free via
            out["vias"] = [{"net": "GND", "x": 1, "y": 1, "tag": "free"}]
        elif rip and "A" not in avoid:
            out["vias"], out["rip"] = [{"net": "GND", "x": 2, "y": 2, "tag": "stub_a"}], ["A"]
        elif rip and "B" not in avoid:
            out["vias"], out["rip"] = [{"net": "GND", "x": 3, "y": 3, "tag": "stub_b"}], ["B"]
        else:
            out["stats"]["left"] = 1
        return out

    monkeypatch.setattr(pourstitch, "plan", plan)
    monkeypatch.setattr(repair, "reroute", lambda d, libs, parts, wd, t, v, nets, *a: (t, v))
    state = {"vias": []}

    def check(tracks, vias):
        state["vias"] = vias
        board.write_text(f"(kicad_pcb {len(vias)})")
        return [] if "free" in {v["tag"] for v in vias} else [_unc("GND")]

    r = pourstitch.stitch(_D(), object(), {}, board, [], [], [_unc("GND")], check)
    tags = [v["tag"] for v in r["vias"]]
    assert r["drc"] == [] and tags == ["stub_b", "free"], r["log"]
    assert any("displace 1 undone" in x for x in r["log"])
    assert (True, {"A"}) in calls  # the second displacing round avoided the net of the first
    assert r["added"] == (0, 2)


def test_displace_never_kept_when_it_does_not_pay_off(tmp_path, monkeypatch):
    from hwflow.layout.route import repair
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb before)")
    monkeypatch.setattr(repair, "zone_fill", lambda b: {("GND", "F.Cu"): []})

    def plan(d, b, net, rip=False, avoid=()):
        st = {"groups": 1, "pads": 0, "inside": 0, "in_pad": 0, "stub": 0, "rip": 0, "left": 0}
        if rip and "A" not in avoid:
            return {"tracks": [], "vias": [{"net": "GND", "x": 2, "y": 2}], "rip": ["A"], "stats": st}
        return {"tracks": [], "vias": [], "rip": [], "stats": dict(st, left=1)}

    monkeypatch.setattr(pourstitch, "plan", plan)
    monkeypatch.setattr(repair, "reroute", lambda d, libs, parts, wd, t, v, nets, *a: (t, v))

    def check(tracks, vias):
        board.write_text("(kicad_pcb after)")
        return [_unc("GND")]

    r = pourstitch.stitch(_D(), object(), {}, board, [], [], [_unc("GND")], check)
    assert r["vias"] == [] and r["added"] == (0, 0)
    assert board.read_text() == "(kicad_pcb before)"


def test_displace_that_opens_the_displaced_net_is_rejected(tmp_path, monkeypatch):
    """Joining the island but leaving a displaced net open is no progress: the next round displaces other nets."""
    from hwflow.layout.route import repair
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)")
    monkeypatch.setattr(repair, "zone_fill", lambda b: {("GND", "F.Cu"): []})

    def plan(d, b, net, rip=False, avoid=()):
        st = {"groups": 1, "pads": 0, "inside": 0, "in_pad": 0, "stub": 0, "rip": 0, "left": 0}
        for n in ("A", "B"):
            if rip and n not in avoid:
                return {"tracks": [], "vias": [{"net": "GND", "x": 1, "y": 1, "tag": n}], "rip": [n], "stats": st}
        return {"tracks": [], "vias": [], "rip": [], "stats": dict(st, left=1)}

    monkeypatch.setattr(pourstitch, "plan", plan)
    monkeypatch.setattr(repair, "reroute", lambda d, libs, parts, wd, t, v, nets, *a: (t, v))
    sig = {"sev": "error", "type": "unconnected", "items": ["Track [A] on F.Cu", "Track [A] on F.Cu"], "at": []}

    def check(tracks, vias):
        tags = {v["tag"] for v in vias}
        return [sig] if "A" in tags else ([] if "B" in tags else [_unc("GND")])

    r = pourstitch.stitch(_D(), object(), {}, board, [], [], [_unc("GND")], check)
    assert r["drc"] == [] and [v["tag"] for v in r["vias"]] == ["B"], r["log"]
    assert any("the displaced A left open" in x for x in r["log"])


def _plane_case(tmp_path, monkeypatch, result_drc):
    """No stub joins the island; the plane repair returns a GND trace and via whose board has DRC `result_drc`."""
    from hwflow.layout.route import repair
    board = tmp_path / "b.kicad_pcb"
    board.write_text("(kicad_pcb)")
    monkeypatch.setattr(repair, "zone_fill", lambda b: {("GND", "F.Cu"): []})
    left = {"groups": 1, "pads": 0, "inside": 0, "in_pad": 0, "stub": 0, "rip": 0, "left": 1}
    monkeypatch.setattr(pourstitch, "plan", lambda *a, **k: {"tracks": [], "vias": [], "rip": [], "stats": left})
    monkeypatch.setattr(pourstitch, "fixed_nets", lambda d: set())
    tr = [{"net": "GND", "x1": 0, "y1": 0, "x2": 1, "y2": 0, "w": 0.25, "layer": "F.Cu"}]
    vi = [{"net": "GND", "x": 1, "y": 0}]
    monkeypatch.setattr(pourstitch, "planes", lambda d, libs, parts, b, nets, stop: (tr, vi))
    monkeypatch.setattr(repair, "reroute", lambda d, libs, parts, wd, t, v, nets, *a: (t, v))

    def check(tracks, vias):
        return result_drc if vias else [_unc("GND")]
    return pourstitch.stitch(_D(), object(), {}, board, [], [], [_unc("GND")], check)


def test_plane_repair_joins_a_pocket_no_stub_reaches(tmp_path, monkeypatch):
    r = _plane_case(tmp_path, monkeypatch, [])
    assert r["drc"] == [] and r["vias"] == [{"net": "GND", "x": 1, "y": 0}] and r["added"] == (1, 1)
    assert any(x.startswith("plane repair kept") for x in r["log"])


def test_plane_repair_that_leaves_a_ripped_net_open_is_rejected(tmp_path, monkeypatch):
    sig = {"sev": "error", "type": "unconnected", "items": ["Pad 1 [SIG] of U1", "Pad 2 [SIG] of U2"], "at": []}
    r = _plane_case(tmp_path, monkeypatch, [sig])
    assert r["vias"] == [] and r["drc"] == [_unc("GND")]
    assert any("plane repair rejected: moved or left open SIG" in x for x in r["log"])
