"""Seed route (seedroute.py): a net the corridor proof connects but the router leaves open gets copper through the
proof's free space, with exact spacings and necks of at most routing.NECK_MAX_MM."""

from types import SimpleNamespace

import pytest
import yaml
from shapely.geometry import LineString, Point, box

from hwflow import corridor, pcb, routing, seedroute
from hwflow.core import sexpr
from hwflow.core.design import Design

needs_router = pytest.mark.skipif(not (pcb.krt_dir() / "py_router" / "route.py").exists(), reason="KiCadRoutingTools not installed")
F = ("F.Cu",)
BETWEEN = 1.0
GAP = 2 * BETWEEN + routing.NECK_FLOOR + 0.06  # the proof's corridor fits a 0.21 mm track; with ISO_SLACK the router's 0.01 mm


class _Libs:
    """footprint id -> [(number, x0, y0, x1, y1)]: rect SMD pads on F.Cu in board coordinates (parts at (0, 0))."""

    def __init__(self, fps):
        self.fps = fps

    def _pads(self, fid):
        return [(n, (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0) for n, x0, y0, x1, y1 in self.fps[fid]]

    def footprint(self, fid):
        return {"pads": [{"number": n, "kind": "smd", "shape": "rect", "x": x, "y": y, "rot": 0.0, "w": w, "h": h,
                          "layers": list(F), "drill": None, "rratio": 0.25, "hull": None}
                         for n, x, y, w, h in self._pads(fid)], "bbox": (-1, -1, 1, 1), "sides": {}}

    def footprint_tree(self, fid):
        pads = " ".join(f'(pad "{n}" smd rect (at {x} {y}) (size {w} {h}) (layers "F.Cu"))' for n, x, y, w, h in self._pads(fid))
        return sexpr.loads(f'(footprint "{fid}" (layer "F.Cu") (property "Reference" "REF" (at 0 0 0) (layer "F.SilkS")) '
                           f'(property "Value" "V" (at 0 0 0) (layer "F.Fab")) {pads})')


def _board(tmp_path, walls, a=(5, 10), b=(25, 10)):
    """AC_ROOM (0.6 mm, F.Cu only, a mains member with between_mm 1.0) from pad `a` to pad `b`; `walls` the pads
    (x0, y0, x1, y1) of the other members AC_L and AC_N."""
    libs = _Libs({"A": [("1", a[0] - 0.5, a[1] - 0.5, a[0] + 0.5, a[1] + 0.5)],
                  "B": [("1", b[0] - 0.5, b[1] - 0.5, b[0] + 0.5, b[1] + 0.5)],
                  "W": [(str(k + 1), *r) for k, r in enumerate(walls)]})
    raw = {"project": "seed", "board": {"layers": 2, "width_mm": 30, "height_mm": 20, "track_mm": 0.6,
                                        "clearance_mm": 0.2, "via_dia_mm": 0.6, "gnd_pour": False,
                                        "isolation": [{"name": "mains", "nets": ["AC_*"], "clearance_mm": 6.0,
                                                       "between_mm": BETWEEN}]},
           "nets": {"AC_ROOM": {"layers": ["F.Cu"]}}, "components": {}}
    wall = {str(k + 1): ("AC_L" if k == 0 else "AC_N") for k in range(len(walls))}
    for ref, fid, pins in (("U1", "A", {"1": "AC_ROOM"}), ("U2", "B", {"1": "AC_ROOM"}), ("W1", "W", wall)):
        raw["components"][ref] = {"footprint": fid, "pins": pins, "placement": {"x": 0, "y": 0, "rot": 0, "fixed": True}}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    d = Design.load(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    src = out / "seed_unrouted.kicad_pcb"
    parts = {r: SimpleNamespace(x=0.0, y=0.0, r=0.0, fixed=True) for r in raw["components"]}
    pcb.write_board(src, pcb.build_board(d, libs, parts))
    pcb.write_project(out, d.name, d)
    return d, libs, src, out


def _slit(length):
    """Two AC_L/AC_N pads leaving a slit GAP wide and `length` long at x = 15, y = 10."""
    lo, hi = 10 - GAP / 2, 10 + GAP / 2
    return [(15 - length / 2, 0, 15 + length / 2, lo), (15 - length / 2, hi, 15 + length / 2, 20)]


def _check_copper(d, path, walls, pads):
    """The AC_ROOM copper of board `path`: on F.Cu, ends in both pads, keeps between_mm from every wall exactly."""
    b = pcb.read_board(path)
    segs = [s for s in b["segments"] if s["net"] == "AC_ROOM"]
    assert segs and {s["layer"] for s in segs} == {"F.Cu"}
    cu = [LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])]).buffer(s["w"] / 2, quad_segs=64) for s in segs]
    gap = min(g.distance(box(*w)) for g in cu for w in walls)
    assert gap >= BETWEEN
    ends = [Point(s["x1"], s["y1"]) for s in segs] + [Point(s["x2"], s["y2"]) for s in segs]
    for p in pads:
        assert any(box(p[0] - 0.5, p[1] - 0.5, p[0] + 0.5, p[1] + 0.5).contains(e) for e in ends)
    return segs, gap


def test_seed_route_passes_a_slit_the_router_slack_closes(tmp_path):
    """A 1 mm long slit GAP wide: with exact spacings a 0.21 mm track passes (the proof marks the net as needing a
    neck); the seed route passes it at the floor width for under 3 mm, 0.6 mm elsewhere, between_mm kept exactly."""
    walls = _slit(1.0)
    d, libs, src, out = _board(tmp_path, walls)
    need = corridor.neck_needed(d, libs, board=pcb.read_board(src))
    assert need["AC_ROOM"]["plain_mm"] < GAP - 2 * BETWEEN + 0.01
    dst = out / "seeded.kicad_pcb"
    r = seedroute.seed(d, libs, src, dst, ["AC_ROOM"])["AC_ROOM"]
    assert r["ok"], r["msg"]
    assert r["width"] == 0.6 and r["min_width"] == routing.NECK_FLOOR and 1.0 < r["neck"] <= routing.NECK_MAX_MM
    assert r["msg"].startswith("seed route AC_ROOM: ") and "longest neck" in r["msg"]
    segs, gap = _check_copper(d, dst, walls, [(5, 10), (25, 10)])
    assert gap <= BETWEEN + 0.05  # it uses the slit (no other way), at the floor width
    assert max(s["w"] for s in segs) == 0.6
    thin = [((s["x1"], s["y1"]), (s["x2"], s["y2"]), s["layer"]) for s in segs if s["w"] < 0.6]
    assert routing.longest_neck(thin) == pytest.approx(r["neck"], abs=0.01)


def test_seed_route_refuses_a_neck_over_the_limit(tmp_path):
    """A hairpin channel GAP wide around the end of a thin AC_L wall: the proof's neck bound (the distance across
    the wall, 2.9 mm) lets it through, but the only path is narrow for about 8 mm, so the seed route refuses and says
    by how much."""
    t = 0.3  # wall thickness: across it, the two wide areas are t + 2 x 1.31 mm apart
    top, bot = 10 - t / 2, 10 + t / 2
    walls = [(0.5, top, 18, bot),                              # AC_L: the wall, closed at the board edge on the left
             (16, top - GAP - 1, 21.21, top - GAP),            # AC_N: the hairpin around the wall end
             (18 + GAP, 0, 19 + GAP, 20),
             (16, bot + GAP, 21.21, bot + GAP + 1)]
    d, libs, src, out = _board(tmp_path, walls, a=(5, 5), b=(5, 15))
    assert "AC_ROOM" in corridor.neck_needed(d, libs, board=pcb.read_board(src))
    r = seedroute.seed(d, libs, src, out / "seeded.kicad_pcb", ["AC_ROOM"])["AC_ROOM"]
    assert not r["ok"] and r["neck"] > routing.NECK_MAX_MM
    assert f"longest neck {r['neck']:.2f} mm, over the 3 mm neck" in r["msg"]
    assert not (out / "seeded.kicad_pcb").exists()


def test_drop_on_drc_removes_seed_copper_an_error_touches():
    seeds = {"N": {"segments": [[0, 0, 5, 0, 0.6, "F.Cu"]], "vias": []}}
    tracks = [{"net": "N", "x1": 0, "y1": 0, "x2": 5, "y2": 0, "w": 0.6, "layer": "F.Cu"},
              {"net": "M", "x1": 0, "y1": 3, "x2": 5, "y2": 3, "w": 0.2, "layer": "F.Cu"}]
    far = [{"sev": "error", "type": "clearance", "items": ["Track [N] on F.Cu"], "at": [[20, 20]]}]
    assert seedroute.drop_on_drc(seeds, tracks, [], far)[2] == []
    near = [{"sev": "error", "type": "clearance", "items": ["Track [N] on F.Cu", "Track [M] on F.Cu"], "at": [[2, 0.3], [2, 3]]}]
    tr, vi, dropped = seedroute.drop_on_drc(seeds, tracks, [], near)
    assert dropped == ["N"] and [t["net"] for t in tr] == ["M"]


@needs_router
def test_router_leaves_the_slit_open_and_the_seed_route_closes_it(tmp_path, monkeypatch):
    """The real flow: KiCadRoutingTools (isolation keep-outs with ISO_SLACK) cannot pass the slit at any neck width;
    the seed route then connects the net, and the note says so."""
    walls = _slit(1.0)
    d, libs, src, out = _board(tmp_path, walls)
    d.raw["board"]["router_retries"] = 0
    monkeypatch.setenv("HWFLOW_SEEDROUTE", "0")
    _, last, notes, _ = pcb.krt_route(d, src, out, "y_", nets={"AC_ROOM"}, libs=libs)
    assert sum(pcb.krt_unrouted(v) for v in last.values()) == 1, notes
    monkeypatch.delenv("HWFLOW_SEEDROUTE")
    cur, last, notes, _ = pcb.krt_route(d, src, out, "x_", nets={"AC_ROOM"}, libs=libs)
    assert sum(pcb.krt_unrouted(v) for v in last.values()) == 0, notes
    assert any("seed route AC_ROOM: " in n and "longest neck" in n for n in notes), notes
    _check_copper(d, cur, walls, [(5, 10), (25, 10)])
    assert "AC_ROOM" in seedroute.load(out)
