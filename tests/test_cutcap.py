"""Cut capacity: more nets must cross a cut than its free length can hold, so no router can route the board."""
import yaml
from shapely.geometry import LineString

from hwflow.layout.place import cutcap, placer
from hwflow.core.design import Design
from hwflow.core.libs import Libs

R0402 = "Resistor_SMD:R_0402_1005Metric"


def _load(tmp_path, comps, board, nets=None, frame=None):
    raw = {"project": "cut", "board": dict({"track_mm": 0.25, "clearance_mm": 0.2}, **board),
           "nets": nets or {}, "components": comps}
    if frame:
        pretty = tmp_path / "lib" / "footprints" / "t.pretty"
        pretty.mkdir(parents=True)
        pts = " ".join(f"(xy {x} {y})" for x, y in frame)
        (pretty / "Frame.kicad_mod").write_text(
            '(footprint "Frame" (version 20221018) (generator test) (layer "F.Cu")\n'
            '  (pad "1" smd rect (at 0 0) (size 0.2 0.2) (layers "Dwgs.User"))\n'
            '  (zone (net 0) (net_name "") (layers "F.Cu" "B.Cu") (name "FRAME") (hatch edge 0.5)\n'
            '    (keepout (tracks not_allowed) (vias not_allowed) (pads allowed) (copperpour not_allowed) '
            '(footprints allowed))\n'
            f'    (polygon (pts {pts}))))\n')
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    d = Design.load(tmp_path)
    return d, Libs(design=d)


def _strip(n=10, H=2.0, layers=2, fix_right=True, net="N"):
    """Two rows of 0402 resistors on a 30 x H board, one at each end: n nets cross the middle (x = 15)."""
    comps = {}
    for i in range(n):
        pl = {"x": 1.0 + 1.2 * i, "y": H / 2, "rot": 90, "fixed": True}
        comps[f"R{i + 1}"] = {"footprint": R0402, "value": "1k", "pins": {"1": f"{net}{i}", "2": f"L{i}"},
                              "placement": pl}
        pr = {"x": 29.0 - 1.2 * i, "y": H / 2, "rot": 90, "fixed": True}
        comps[f"R{i + 101}"] = {"footprint": R0402, "value": "1k", "pins": {"1": f"{net}{i}", "2": f"M{i}"},
                                "placement": pr if fix_right else {}}
    return comps, {"width_mm": 30, "height_mm": H, "layers": layers}


def _x15(d, libs, parts=None, **kw):
    lay = cutcap.Layout(d, libs, parts)
    return lay.evaluate(LineString([(15, 0), (15, lay.H)]), every=True, **kw)


def test_chord_over_capacity_fires(tmp_path):
    # x = 15: 1.4 mm of copper area per layer (2 mm less 2 x 0.3 mm edge clearance), so (1.4 + 0.2) x 2 = 3.2 mm;
    # 10 nets x (0.15 mm neck + 0.2 mm clearance) = 3.5 mm
    d, libs = _load(tmp_path, *_strip())
    t = _x15(d, libs)[0]
    assert len(t["nets"]) == 10 and round(t["need"], 3) == 3.5 and round(t["have"], 3) == 3.2
    f = cutcap.findings(d, libs)
    assert f and f[0]["check"] == "cut" and f[0]["back_to"] == "architecture" and f[0]["need_mm"] > f[0]["have_mm"]
    assert set(f[0]) >= {"check", "back_to", "refs", "nets", "need_mm", "have_mm", "msg"}
    assert cutcap.findings(d, libs, placer.build_parts(d, libs))


def test_chord_with_room_is_silent(tmp_path):
    d, libs = _load(tmp_path, *_strip(layers=4))  # x = 15: 6.4 mm of room for 3.5 mm
    assert round(_x15(d, libs)[0]["have"], 3) == 6.4
    assert cutcap.findings(d, libs) == []
    assert cutcap.findings(d, libs, placer.build_parts(d, libs)) == []


def test_ground_with_pour_needs_no_crossing(tmp_path):
    comps, board = _strip()
    for r in ("R1", "R101"):
        comps[r]["pins"]["1"] = "GND"
    d, libs = _load(tmp_path, comps, board)
    assert len(_x15(d, libs)[0]["nets"]) == 9  # GND is poured: a pour may connect it across
    (tmp_path / "np").mkdir()
    d, libs = _load(tmp_path / "np", comps, dict(board, gnd_pour=False))
    assert "GND" in _x15(d, libs)[0]["nets"]


def test_pad_on_the_cut_crosses_for_free(tmp_path):
    comps, board = _strip()
    comps["R1"]["placement"]["x"] = 15.0  # N0 has a pad on x = 15: it crosses through that pad
    d, libs = _load(tmp_path, comps, board)
    t = _x15(d, libs)[0]
    assert "N0" not in t["nets"] and t["per_layer"]["F.Cu"] < t["per_layer"]["B.Cu"]  # the pad blocks F.Cu


def test_unknown_positions_before_placement(tmp_path):
    d, libs = _load(tmp_path, *_strip(fix_right=False))
    assert cutcap.findings(d, libs) == []  # the right row may go anywhere: nothing is proven
    parts = placer.build_parts(d, libs)
    for i in range(10):  # the placement puts it at the right end
        p = parts[f"R{i + 101}"]
        p.x, p.y, p.r = 29.0 - 1.2 * i, 1.0, 90
    assert cutcap.findings(d, libs, parts)


def _framed(tmp_path, slit):
    """U1 (SOT-23-6, 6 nets) inside a keep-out frame that leaves a slit of `slit` mm on its top side."""
    s = slit / 2
    frame = [(-5, -5), (-s, -5), (-s, -2.4), (-2.4, -2.4), (-2.4, 2.4), (2.4, 2.4), (2.4, -2.4), (s, -2.4), (s, -5),
             (5, -5), (5, 5), (-5, 5)]
    frame = [(10 + x, 10 + y) for x, y in frame]
    comps = {"U1": {"footprint": "Package_TO_SOT_SMD:SOT-23-6", "value": "x",
                    "pins": {str(i): f"N{i}" for i in range(1, 7)}, "placement": {"x": 10, "y": 10, "fixed": True}},
             "FR1": {"footprint": "t:Frame", "value": "frame", "pins": {}, "placement": {"x": 0, "y": 0, "fixed": True}}}
    for i in range(1, 7):
        comps[f"R{i}"] = {"footprint": R0402, "value": "1k", "pins": {"1": f"N{i}", "2": "GND"},
                          "placement": {"x": 22, "y": 3 + 2 * i, "fixed": True}}
    return _load(tmp_path, comps, {"width_mm": 30, "height_mm": 20, "layers": 2}, frame=frame)


def test_ring_around_walled_part_goes_back_to_parts(tmp_path):
    # 6 nets x 0.35 = 2.1 mm through a 0.6 mm slit on 2 layers: (0.6 + 0.2) x 2 = 1.6 mm
    d, libs = _framed(tmp_path, 0.6)
    f = cutcap.findings(d, libs)
    ring = [x for x in f if x["refs"] == ["U1"]]
    assert ring and ring[0]["back_to"] == "parts" and ring[0]["need_mm"] == 2.1 and ring[0]["have_mm"] == 1.6
    (tmp_path / "w").mkdir()
    d, libs = _framed(tmp_path / "w", 2.0)  # (2.0 + 0.2) x 2 = 4.4 mm of room
    assert not [x for x in cutcap.findings(d, libs) if x["refs"] == ["U1"]]


def test_bound_quantities(tmp_path):
    comps, board = _strip(n=1)
    board["drc_rules"] = ['(rule fp (condition "A.memberOfFootprint(\'R1\')") (constraint clearance (min 0.1mm)))']
    d, libs = _load(tmp_path, comps, board, nets={"N0": {"width_mm": 0.8}})
    from hwflow.layout.board import pcb
    assert cutcap.min_width(d) == 0.15  # a neck may be anywhere: the DRC minimum
    assert cutcap.min_clearance(d, pcb.net_classes(d)) == 0.2  # a footprint-only rule applies to pads, not tracks
    d.raw["board"]["drc_rules"] = ['(rule x (condition "A.NetClass == \'a\'") (constraint clearance (min 0.1mm)))']
    assert cutcap.min_clearance(d, pcb.net_classes(d)) == 0.1


def test_isolation_band_blocks_the_other_nets(tmp_path):
    comps, board = _strip(n=6)  # 6 x 0.35 = 2.1 mm through 3.2 mm: fits
    comps["RM"] = {"footprint": R0402, "value": "1k", "pins": {"1": "AC_L", "2": "AC_N"},
                   "placement": {"x": 15, "y": 1.0, "rot": 0, "fixed": True}}
    board["isolation"] = [{"name": "mains", "nets": ["AC_*"], "clearance_mm": 6.0}]
    d, libs = _load(tmp_path, comps, board)
    f = cutcap.findings(d, libs)
    # no other net may pass within 6 mm of the group's pads on their layer (F.Cu): only B.Cu is left, 1.6 < 2.1 mm
    assert [x for x in f if "mains" in x["msg"] and x["per_layer"]["F.Cu"] == 0 and x["have_mm"] == 1.6]
    board.pop("isolation")
    (tmp_path / "n").mkdir()
    d, libs = _load(tmp_path / "n", comps, board)
    assert cutcap.findings(d, libs) == []
