"""Rip-up rounds of krt.krt_route: an isolation member left open because the copper of a net routed in an earlier pass
(with its isolation keep-off) closes its way gets that copper ripped up; it routes first, then the ripped net again.

The board: A (F.Cu only, 0.25 mm) from x = 5 to x = 35 along y = 10; B (0.5 mm, both layers, so an earlier pass)
from (20, 2) to (20, 18) across it. Each net is an isolation group of its own (3 mm to every other net). B routes
first, straight on F.Cu, and walls A in; the per-pass retry ("open nets first") cannot help, as B is in another pass.
Ripped up, A takes the straight line and B crosses under it on B.Cu, its vias 3 mm from A."""

from types import SimpleNamespace

import pytest
import yaml

from hwflow.core.design import Design
from hwflow.layout.board import pcb, routing
from hwflow.layout.route import krt, seedroute

needs_router = pytest.mark.skipif(not (krt.krt_dir() / "py_router" / "route.py").exists(),
                                  reason="KiCadRoutingTools not installed")
PADS = {"A1": ("A", 5, 10), "A2": ("A", 35, 10), "B1": ("B", 20, 2), "B2": ("B", 20, 18)}


class _Libs:
    """One 1 x 1 mm SMD pad on F.Cu per footprint, at the part origin."""

    def footprint(self, fid):
        return {"pads": [{"number": "1", "kind": "smd", "shape": "rect", "x": 0.0, "y": 0.0, "rot": 0.0, "w": 1.0,
                          "h": 1.0, "layers": ["F.Cu"], "drill": None, "rratio": 0.25, "hull": None}],
                "bbox": (-0.5, -0.5, 0.5, 0.5), "sides": {}}

    def footprint_tree(self, fid):
        from hwflow.core import sexpr
        return sexpr.loads(f'(footprint "{fid}" (layer "F.Cu") (property "Reference" "REF" (at 0 0 0) (layer "F.SilkS")) '
                           '(property "Value" "V" (at 0 0 0) (layer "F.Fab")) (pad "1" smd rect (at 0 0) (size 1 1) '
                           '(layers "F.Cu")))')


def _board(tmp_path):
    raw = {"project": "rip", "board": {"layers": 2, "width_mm": 40, "height_mm": 20, "track_mm": 0.25,
                                       "clearance_mm": 0.2, "via_dia_mm": 0.6, "via_drill_mm": 0.3, "gnd_pour": False,
                                       "router_retries": 0,
                                       "isolation": [{"name": "ga", "nets": ["A"], "clearance_mm": 3.0},
                                                     {"name": "gb", "nets": ["B"], "clearance_mm": 3.0}]},
           "nets": {"A": {"layers": ["F.Cu"]}, "B": {"width_mm": 0.5}}, "components": {}}
    for ref, (net, x, y) in PADS.items():
        raw["components"][ref] = {"footprint": "P", "pins": {"1": net}, "placement": {"x": x, "y": y, "rot": 0, "fixed": True}}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    d = Design.load(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    libs = _Libs()
    parts = {r: SimpleNamespace(x=float(x), y=float(y), r=0.0, fixed=True) for r, (_, x, y) in PADS.items()}
    src = out / "rip_unrouted.kicad_pcb"
    pcb.write_board(src, pcb.build_board(d, libs, parts))
    pcb.write_project(out, d.name, d)
    return d, libs, parts, src, out


def _with_b_wall(d, libs, parts, out):
    """The board with B routed straight on F.Cu (what the router does in its earlier pass)."""
    wall = [{"net": "B", "layer": "F.Cu", "w": 0.5, "x1": 20, "y1": 2, "x2": 20, "y2": 18}]
    path = out / "rip_wall.kicad_pcb"
    pcb.write_board(path, pcb.build_board(d, libs, parts, wall, []))
    return path


def test_ripup_options_name_the_copper_on_the_way(tmp_path):
    d, libs, parts, src, out = _board(tmp_path)
    wall = _with_b_wall(d, libs, parts, out)
    assert seedroute.ripup_options(d, libs, wall, "A", {"B"}) == [["B"]]
    assert seedroute.ripup_options(d, libs, wall, "A", set()) == []  # B's copper may not move: not a rip-up case
    assert seedroute.ripup_options(d, libs, src, "A", {"B"}) == []  # nothing routed: nothing blocks


def test_closed_summary_has_nothing_open():
    s = {"failed": 1, "multipoint_deficit": 1, "pad_pairs_open": {"count": 1, "nets": ["A"]}, "failed_single": []}
    assert krt.krt_unrouted(krt.closed(s, ["A", "B"])) == 0 and krt.krt_open_nets(krt.closed(s, ["A"])) == []
    s2 = {"failed": 2, "failed_single": ["A", "C"]}
    assert krt.krt_open_nets(krt.closed(s2, ["A"])) == ["C"] and krt.krt_unrouted(krt.closed(s2, ["A"])) == 1


def test_strip_nets_keeps_pads_and_other_copper(tmp_path):
    d, libs, parts, src, out = _board(tmp_path)
    tracks = [{"net": "B", "layer": "F.Cu", "w": 0.5, "x1": 20, "y1": 2, "x2": 20, "y2": 18},
              {"net": "A", "layer": "F.Cu", "w": 0.25, "x1": 5, "y1": 10, "x2": 8, "y2": 10}]
    path = out / "both.kicad_pcb"
    pcb.write_board(path, pcb.build_board(d, libs, parts, tracks, [{"net": "B", "x": 20, "y": 5}]))
    b = pcb.read_board(routing.strip_nets(path, out / "stripped.kicad_pcb", ["B"]))
    assert [s["net"] for s in b["segments"]] == ["A"] and b["vias"] == []
    assert sorted(p["net"] for fp in b["footprints"].values() for p in fp["pads"]) == ["A", "A", "B", "B"]


@needs_router
def test_ripup_routes_the_walled_in_member(tmp_path, monkeypatch):
    d, libs, parts, src, out = _board(tmp_path)
    monkeypatch.setenv("HWFLOW_RIPUP", "0")
    _, last, notes, _ = krt.krt_route(d, src, out, "off_", libs=libs)
    assert {n for v in last.values() for n in krt.krt_open_nets(v)} == {"A"}, notes  # the failure: B walls A in
    monkeypatch.delenv("HWFLOW_RIPUP")
    cur, last, notes, _ = krt.krt_route(d, src, out, "on_", libs=libs)
    assert sum(krt.krt_unrouted(v) for v in last.values()) == 0, notes
    assert "rip-up 1: A blocked by B: ripped up, A routed first, then B again" in notes, notes
    b = pcb.read_board(cur)
    assert {s["layer"] for s in b["segments"] if s["net"] == "A"} == {"F.Cu"}
    assert any(s["layer"] == "B.Cu" for s in b["segments"] if s["net"] == "B")  # B crosses under A
