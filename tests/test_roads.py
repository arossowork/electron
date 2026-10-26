"""Roads first (layout/route/roads.py, prototype): a constrained net between fixed parts is planned before placement, reserved
as a placer blockage, and its planned copper goes on the board before the router."""

from shapely.geometry import LineString, box
from shapely.ops import unary_union

from hwflow.layout.board import pcb
from hwflow.layout.place import placer
from hwflow.layout.route import roads
from test_seedroute import _board, _slit


def test_classify_and_plan(tmp_path):
    d, libs, src, out = _board(tmp_path, _slit(1.0))
    assert roads.classify(d)["AC_ROOM"] == "isolation"
    rows = {r["net"]: r for r in roads.plan(d, libs)}
    r = rows["AC_ROOM"]
    assert r["ok"] and r["fixed"] and r["segments"], r["msg"]
    assert {s[5] for s in r["segments"]} == {"F.Cu"}
    assert abs(r["spacing"] - (6.0 + placer.ISO_MARGIN)) < 1e-9  # a foreign pad keeps the isolation distance


def test_reservation_boxes_cover_the_road(tmp_path):
    d, libs, src, out = _board(tmp_path, _slit(1.0))
    road = next(r for r in roads.plan(d, libs) if r["net"] == "AC_ROOM")
    ar = roads.areas([road], d)
    assert ar and all(a["forbids"] == {"footprints"} and a["layers"] == ["F.Cu"] for a in ar)
    cover = unary_union([box(*a["box"]) for a in ar])
    for x1, y1, x2, y2, c, L in road["segments"]:  # the track itself plus a little of the spacing band
        assert cover.buffer(1e-6).contains(LineString([(x1, y1), (x2, y2)]).buffer(c / 2 + 1.0))
    assert {"U1", "U2", "W1"} <= set(ar[0]["exempt"])  # the road's own parts and the isolation group's


def test_placer_keeps_other_parts_off_and_exempts_own(tmp_path):
    d, libs, src, out = _board(tmp_path, _slit(1.0))
    road = next(r for r in roads.plan(d, libs) if r["net"] == "AC_ROOM")
    ar = roads.areas([road], d)
    parts = placer.Parts({"R1": placer.Part("R1", 1.0, 0.5, 0.0, 0.0, {"1": (-0.4, 0), "2": (0.4, 0)}, sides={
        "front": (1.0, 0.5, 0.0, 0.0)}, x=15.0, y=10.0)})
    parts.areas = ar
    pl = placer.Placer(d, parts, 30, 20)
    assert pl._ko_cost("R1") > 0  # a part of another net on the road
    assert pl._ko_skip(ar[0]["owner"], ar[0]["name"], "U1")  # a part with a pad on it is exempt


def test_preseed_puts_the_road_on_the_board(tmp_path):
    d, libs, src, out = _board(tmp_path, _slit(1.0))
    dst, left, done = roads.preseed(d, libs, src, out, "seed_t", nets={"AC_ROOM", "AC_L"})
    assert done == ["AC_ROOM"] and "AC_ROOM" not in left
    segs = [s for s in pcb.read_board(dst)["segments"] if s["net"] == "AC_ROOM"]
    assert segs
    again = roads.preseed(d, libs, dst, out, "seed_u", nets={"AC_ROOM"})  # a road with copper already is left alone
    assert again[2] == []
