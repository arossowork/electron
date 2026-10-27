"""An illegal placement is never routed: the settle legalizer (Placer.settle), the placement verdict
(layout.place.legality.placement_verdict), the layout stage that stops before the router, and the floorplan check that fails."""

from hwflow.layout import diagnose
from hwflow.layout.place import floorplan, placer
from hwflow.pipeline import stages as pl_stages

from test_floorplan import region_design
from test_placer import H, W, channel


def _pile(parts, x=18.0, y=15.0):
    for p in parts.values():
        if not p.fixed:
            p.x, p.y = x, y


def test_settle_clears_a_pile_when_the_area_allows(tmp_path):
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    _pile(parts)  # every resistor on one spot
    pl = placer.Placer(d, parts, W, H, seed=1)
    pl.legalize(rounds=2)  # a short push-apart leaves overlaps
    assert pl.violations()
    left = pl.settle()
    assert left == [] and pl.violations() == []
    m = d.board["margin_mm"]
    for p in parts.values():
        if not p.fixed:
            x0, y0, x1, y1 = p.box()
            assert x0 >= m - 1e-6 and y0 >= m - 1e-6 and x1 <= W - m + 1e-6 and y1 <= H - m + 1e-6


def test_settle_keeps_region_members_inside_and_others_out(tmp_path):
    d, libs = region_design(tmp_path)
    parts = placer.build_parts(d, libs)
    _pile(parts, 25, 15)
    pl = placer.Placer(d, parts, 50, 30, seed=1)
    assert pl.violations()
    assert pl.settle() == []
    assert all(parts[r].box()[2] <= 12.3 for r in ("R0", "R1"))  # members of the 0-12 mm mains region


def test_over_full_board_gives_an_architecture_verdict(tmp_path):
    d, libs = channel(tmp_path, board_extra={"width_mm": 11, "height_mm": 30})
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, 11, 30, seed=1)
    pl.run(2000)
    v = pl.violations()
    assert v  # two 25 mm headers and ten resistors do not fit an 11 mm wide board
    pv = diagnose.placement_verdict(d, parts, 11, 30, v)
    assert pv["back_to"] == "architecture" and "does not fit" in pv["msg"] and "board of at least" in pv["msg"]
    g = diagnose.placement_diagnosis(d, pv)
    assert g["blocked"] and g["stage"] == diagnose.STAGES["architecture"]


def test_layout_stage_never_routes_an_illegal_placement(tmp_path, monkeypatch):
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    _pile(parts)
    monkeypatch.setattr(placer, "place", lambda *a, **k: (parts, 1.0, ["courtyard overlap R1/R2"]))

    def no_router(*a, **k):
        raise AssertionError("the router must not run on an illegal placement")
    monkeypatch.setattr(pl_stages, "_route_once", no_router)
    r = pl_stages.stage_layout(d, libs)
    assert not r["ok"] and r["placement_verdict"] and "placement illegal" in r["blocked"]
    assert r["placement_verdict"]["back_to"] == "layout"  # the area allows a legal placement: a placer failure
    g = pl_stages.layout_diagnosis(d, libs, [r])
    assert g["placement"] and not g["blocked"]  # one attempt: another seed may find it
    g2 = pl_stages.layout_diagnosis(d, libs, [r, r])
    # repeated attempts do not turn a placer failure into a design blockage: it is an hwflow defect
    assert not g2["blocked"] and g2["back_to"] == "layout" and g2["placer_failure"]
    assert "hwflow defect" in " ".join(g2["reasons"]) and diagnose.summary(g2).startswith("PLACER FAILURE")


def test_floorplan_fails_on_an_illegal_quick_placement(tmp_path, monkeypatch):
    d, libs = region_design(tmp_path)
    real = floorplan._quick_place

    def bad(*a, **k):
        out = real(*a, **k)
        out["violations"] = ["courtyard overlap R0/R1"]
        return out
    monkeypatch.setattr(floorplan, "_quick_place", bad)
    res = floorplan.check(d, libs, iters=1500, seeds=1)
    c = [c for c in res["checks"] if c["name"] == "placement"]
    assert c and c[0]["ok"] is False and "illegal" in c[0]["msg"] and "region hv" in c[0]["msg"]
    assert res["verdict"] == "FAIL"
