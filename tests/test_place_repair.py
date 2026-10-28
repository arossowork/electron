"""The placer's last legality repairs and the verdict when it fails (thermo-e2e-2: one movable 0603 left on the coil pad
of a fixed back-side relay, reported as an architecture blockage although the design allowed a legal placement):

- legalize pushes a courtyard off a through-hole pad of a part on the other side (violations counts that overlap);
- settle repacks a fragmented neighbourhood when the violating part alone finds no free spot;
- the placement verdict names the constraints of a part that has no legal spot even alone (back to architecture), and
  calls anything else a placer failure: an hwflow defect, never a design blockage, however many attempts failed.
"""

from hwflow.core.libs import Libs
from hwflow.layout import diagnose
from hwflow.layout.place import legality, placer

from test_floorplan import RS, make, region_design
from test_placer import H, W, channel


def test_legalize_pushes_a_courtyard_off_a_through_hole_pad_of_the_other_side(tmp_path):
    d, libs = channel(tmp_path)
    parts = placer.build_parts(d, libs)
    j1 = parts["J1"]
    j1.sides = {"back": j1.sides["front"]}  # J1 as a back-side through-hole part: its pads reach the front
    for r, p in parts.items():  # every other resistor out of the way, on the far side
        if r.startswith("R"):
            p.x, p.y, p.r = 25.0, 3.0 + 2.4 * int(r[1:]), 0.0
    r0 = parts["R0"]
    px, py = j1.pad("3")
    r0.x, r0.y = px + 1.0, py  # its front courtyard over J1 pad 3
    pl = placer.Placer(d, parts, W, H, seed=1)
    assert placer._tht_pair(j1, r0) is not None
    assert "courtyard overlap J1/R0" in pl.violations()
    pl.legalize()  # the placer's own passes: as before (their trajectories stay)
    assert placer._tht_pair(j1, r0) is not None
    pl.legalize(tht=True)
    assert placer._tht_pair(j1, r0) is None
    assert not [v for v in pl.violations() if "J1/R0" in v]


def _row(tmp_path):
    """Three 0603 members of a one-row region (rot 0 only fits; 1.9 mm of slack in all) and five free resistors."""
    comps = {f"R{i}": {"value": "1k", "symbol": "Device:R", "footprint": RS, "pins": {"1": f"N{i}", "2": f"N{i + 1}"}}
             for i in range(8)}
    fp = {"construction": "single", "regions": [{"name": "row", "kind": "other", "rect": [1, 1, 13, 2.9],
                                                 "items": ["g"]}],
          "items": [{"name": "g", "refs": ["R0", "R1", "R2"]}]}
    d = make(tmp_path, fp, board={"width_mm": 50, "height_mm": 30, "layers": 2}, comps=comps,
             nets={f"N{i}": {"class": "signal"} for i in range(9)})
    return d, Libs(design=d)


def test_settle_repacks_a_fragmented_region_when_the_part_alone_finds_no_spot(tmp_path):
    d, libs = _row(tmp_path)
    parts = placer.build_parts(d, libs)
    for i in range(3, 8):
        parts[f"R{i}"].x, parts[f"R{i}"].y = 20.0 + 4 * (i - 3), 20.0
    y = 1.0 + parts["R0"].h / 2
    parts["R0"].x, parts["R1"].x, parts["R2"].x = 2.3 + 1.73, 7.6 + 1.73, 4.0  # gaps 1.3, 1.8, 2.2 mm: none fits
    for r in ("R0", "R1", "R2"):
        parts[r].y, parts[r].r = y, 0.0
    pl = placer.Placer(d, parts, 50, 30, seed=1)
    v = pl.violations()
    assert any("R0/R2" in x or "R2/R0" in x for x in v)
    s = pl._state()
    assert pl._settle(pl._violators(v)) == 0  # R2 alone: no free window between the members left in place
    pl._restore(s)
    assert pl.infeasible(v) == []  # alone on the board it fits: a placer problem, not a design one
    assert pl.settle() == []
    assert all(parts[r].box()[2] - 0.25 <= 13 + 1e-6 for r in ("R0", "R1", "R2"))
    assert all((parts[f"R{i}"].x, parts[f"R{i}"].y) == (20.0 + 4 * (i - 3), 20.0) for i in range(3, 8))


def test_verdict_names_the_constraints_of_a_part_that_cannot_fit_alone(tmp_path):
    d, libs = region_design(tmp_path)
    d.raw["floorplan"]["regions"][1]["rect"] = [30, 0, 50, 1.2]  # region lv: room by area, thinner than an 0603
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, 50, 30, seed=1)
    pl.run(1500)
    v = pl.violations()
    assert any(x.startswith(("R6 ", "R7 ")) or "R6/" in x or "R7/" in x for x in v)
    stuck = pl.infeasible(v)
    assert stuck and {x["ref"] for x in stuck} <= {"R6", "R7"} and stuck[0]["regions"] == ["lv"]
    pv = legality.placement_verdict(d, parts, 50, 30, v)
    assert pv["back_to"] == "architecture" and "has no legal spot inside floorplan region lv" in pv["msg"]
    assert "no placer can place it" in pv["msg"] and "should exist" not in pv["msg"]
    g = diagnose.placement_diagnosis(d, pv, 4)
    assert g["blocked"] and not g["placer_failure"] and g["stage"] == diagnose.STAGES["architecture"]


def test_a_feasible_illegal_placement_is_a_placer_failure_not_a_blockage(tmp_path):
    d, libs = region_design(tmp_path)
    parts = placer.build_parts(d, libs)
    for p in parts.values():
        if not p.fixed:
            p.x, p.y = 25.0, 15.0
    pl = placer.Placer(d, parts, 50, 30, seed=1)
    v = pl.violations()
    assert v and pl.infeasible(v) == []
    pv = legality.placement_verdict(d, parts, 50, 30, v)
    assert pv["back_to"] == "layout" and "placer failure" in pv["msg"] and "hwflow defect" in pv["msg"]
    g = diagnose.placement_diagnosis(d, pv, 4)
    assert not g["blocked"] and g["placer_failure"]
    assert diagnose.summary(g).startswith("PLACER FAILURE") and "BLOCKED" not in diagnose.summary(g)


def test_settle_uses_the_space_over_a_back_side_through_hole_body(tmp_path):
    """thermo-e2e-4: settle took a through-hole part's whole body as blocked on both sides, so no free spot was left in
    the region over a large back-side battery holder and L1 stayed outside `power_in` ("placer failure" in 4 attempts).
    `violations` counts only the courtyards on their side and the through-hole pads on both (_side_overlap)."""
    import yaml
    from hwflow.core.design import Design
    comps = {"K1": {"value": "relay", "symbol": "Device:R", "footprint": "Relay_THT:Relay_SPST_Panasonic_ADW11",
                    "pins": {"A1": "A", "A2": "B", "13": "C", "14": "D"},
                    "placement": {"x": 8, "y": 10, "rot": 0, "fixed": True}},
             "R0": {"value": "1k", "symbol": "Device:R", "footprint": RS, "pins": {"1": "A", "2": "B"}},
             "R1": {"value": "1k", "symbol": "Device:R", "footprint": RS, "pins": {"1": "C", "2": "D"}}}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump({"project": "tht", "board": {
        "width_mm": 40, "height_mm": 30, "layers": 2}, "components": comps}))
    d = Design.load(tmp_path)
    parts = placer.build_parts(d, Libs(design=d))
    k1 = parts["K1"]
    k1.sides = {"back": k1.sides["front"]}  # a back-side holder: its body is under the front parts
    for r in ("R0", "R1"):  # both on the front over the relay body, clear of its pins, on top of each other
        parts[r].x, parts[r].y, parts[r].r = 18.0, 13.75, 0.0
    pl = placer.Placer(d, parts, 40, 30, seed=1)
    assert pl.violations() == ["courtyard overlap R0/R1"]
    assert pl.settle() == []
    moved = [r for r in ("R0", "R1") if (parts[r].x, parts[r].y) != (18.0, 13.75)]
    assert len(moved) == 1
    p = parts[moved[0]]
    assert abs(p.x - 18.0) + abs(p.y - 13.75) < 3.0  # the nearest free spot, still over the holder's body
    kb = k1.side_box("back")
    assert kb[0] < p.x < kb[2] and kb[1] < p.y < kb[3]


def test_settle_holds_a_block_by_its_members_courtyards_in_a_region(tmp_path):
    """A block's courtyard part may be larger than its members (port stubs): `violations` tests each member's courtyard
    against the region, so settle may not ask the whole block box to fit (it found no spot in a region the members
    fit: thermo-e2e-4 boost block, L1 outside power_in)."""
    import yaml
    from hwflow.core.design import Design
    comps = {"Y1": {"value": "32k", "symbol": "Device:Crystal", "footprint": "Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm",
                    "pins": {"1": "XP", "2": "XN"}},
             "C9": {"value": "5p6", "symbol": "Device:C", "footprint": "Capacitor_SMD:C_0402_1005Metric",
                    "pins": {"1": "XP", "2": "GND"}},
             "C10": {"value": "5p6", "symbol": "Device:C", "footprint": "Capacitor_SMD:C_0402_1005Metric",
                     "pins": {"1": "XN", "2": "GND"}}}
    raw = {"project": "blk", "board": {"width_mm": 30, "height_mm": 20, "layers": 2}, "components": comps,
           "nets": {"GND": {"class": "ground"}},
           "blocks": {"xtal": {"use": "xtal32k_3215", "parts": {"Y": "Y1", "CP": "C9", "CN": "C10"}}}}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    d = Design.load(tmp_path)
    parts = placer.build_parts(d, Libs(design=d))
    pl = placer.Placer(d, parts, 30, 20, seed=1)
    u = pl._unit(pl.parts["Y1"])
    u.x, u.y, u.r = 15.0, 10.0, 0.0
    pl._refs(u)
    m = placer.COURTYARD_MARGIN
    mb = [pl.parts[r].box() for r in ("Y1", "C9", "C10")]
    rect = [min(b[0] for b in mb) + m - 0.1, min(b[1] for b in mb) + m - 0.05,
            max(b[2] for b in mb) - m + 0.1, max(b[3] for b in mb) - m + 0.05]
    yb = pl.parts[u.ref].box()
    assert yb[1] + m < rect[1] - 0.3  # the block box sticks out of the region; its members do not
    raw["floorplan"] = {"regions": [{"name": "zone", "kind": "other", "rect": [round(v, 3) for v in rect],
                                     "items": ["x"]}], "items": [{"name": "x", "refs": ["Y1", "C9", "C10"]}]}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    d = Design.load(tmp_path)
    parts = placer.build_parts(d, Libs(design=d))
    pl = placer.Placer(d, parts, 30, 20, seed=1)
    u = pl._unit(pl.parts["Y1"])
    u.x, u.y, u.r = 15.6, 10.4, 0.0  # 0.6 / 0.4 mm off: the members stick out of the region
    pl._refs(u)
    assert any("outside floorplan region 'zone'" in v for v in pl.violations())
    assert pl._settle([u]) == 1  # a free spot for the block (none when its whole box had to fit)
    assert pl.violations() == []
    assert abs(u.x - 15.0) < 0.3 and abs(u.y - 10.0) < 0.3
