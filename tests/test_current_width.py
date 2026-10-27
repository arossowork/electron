"""A net's `current_a` is a floor for its copper (thermo-e2e-2: VBAT at 0.96 A left with a 4.3 mm stretch at 0.25 mm,
the 0.25 mm track of a layout block): one IPC-2221 width (core.current) for the router's necks, widening, seed routes,
block copper, the layout stage's track check and the post-layout track-current check."""

from types import SimpleNamespace

from shapely.geometry import box

from hwflow.core import blocks
from hwflow.core.current import NECK_MAX_MM, current_width_mm, ipc2221_width_mm, piece_floors
from hwflow.layout.board import pcb, routing
from hwflow.layout.place import placer
from hwflow.verify import postlayout

from test_blocks import BOOST, _design as _boost_design


def _design(nets):
    return SimpleNamespace(board={"track_mm": 0.25, "clearance_mm": 0.2, "copper_oz": 1}, raw={"nets": nets},
                           nets=lambda: list(nets), net_info=lambda n: nets.get(n) or {})


def test_ipc_width_of_current_rounds_up_and_uses_the_rise():
    d = _design({"VBAT": {"class": "power", "current_a": 0.96}, "HOT": {"current_a": 0.96, "max_rise_c": 20}, "SIG": {}})
    ipc = ipc2221_width_mm(0.96, 1.0, 10)
    assert 0.28 < ipc < 0.29 and current_width_mm(d, "VBAT") == 0.29  # rounded up: never under the IPC width
    assert current_width_mm(d, "HOT") < current_width_mm(d, "VBAT")  # the net's max_rise_c, as post-layout uses
    assert current_width_mm(d, "SIG") == 0.0
    assert pcb.ipc2221_width_mm is ipc2221_width_mm and routing.NECK_MAX_MM == NECK_MAX_MM


def test_piece_floors_widen_long_narrow_stretches_and_keep_pad_entry_necks():
    d = _design({"VBAT": {"current_a": 0.96}, "AC": {"min_width_mm": 1.0}, "SIG": {}})
    tracks = [_seg("VBAT", 0.25, 0, 0, 2, 0), _seg("VBAT", 0.25, 2, 0, 2, 2.5),  # one 4.5 mm stretch
              _seg("VBAT", 0.25, 10, 0, 11, 0),                                    # a 1 mm pad-entry neck
              _seg("VBAT", 0.25, 2, 2.5, 4, 2.5, "B.Cu"),                          # 2 mm on the other layer
              _seg("AC", 0.5, 0, 5, 0.5, 5), _seg("SIG", 0.15, 0, 9, 9, 9)]
    assert piece_floors(d, tracks) == [0.29, 0.29, 0.0, 0.0, 1.0, 0.0]


def _seg(net, w, x1, y1, x2, y2, layer="F.Cu"):
    return {"net": net, "w": w, "x1": x1, "y1": y1, "x2": x2, "y2": y2, "layer": layer}


def test_post_layout_and_the_layout_check_share_one_rule():
    d = _design({"VBAT": {"current_a": 0.96}, "SIG": {}})
    long_neck = [_seg("VBAT", 0.4, 0, 0, 5, 0), _seg("VBAT", 0.25, 5, 0, 5, 1.25), _seg("VBAT", 0.25, 5, 1.25, 8, 1.25)]
    (x,) = postlayout.current_shortfalls(d, long_neck)
    assert x["sev"] == "error" and x["need"] == 0.29 and abs(x["neck"] - 4.25) < 1e-6
    short = [_seg("VBAT", 0.4, 0, 0, 5, 0), _seg("VBAT", 0.25, 5, 0, 6, 0)]
    assert [y["sev"] for y in postlayout.current_shortfalls(d, short)] == ["warn"]  # a pad-entry neck
    at_floor = [_seg("VBAT", 0.29, 0, 0, 9, 0), _seg("SIG", 0.15, 0, 1, 9, 1)]
    assert postlayout.current_shortfalls(d, at_floor) == []


def test_widen_never_narrows_a_net_under_its_floor(tmp_path):
    # VBAT routed at 0.4 next to a SIG track 0.45 mm away (centre to centre): 0.4 does not fit the 0.2 mm clearance
    board = {"footprints": {}, "vias": [],
             "segments": [_seg("VBAT", 0.4, 0, 0, 4, 0), _seg("SIG", 0.15, 0, 0.45, 4, 0.45)]}
    text = ('(kicad_pcb (net 0 "") (net 1 "VBAT") (net 2 "SIG") '
            '(segment (start 0 0) (end 4 0) (width 0.4) (layer "F.Cu") (net 1)) '
            '(segment (start 0 0.45) (end 4 0.45) (width 0.15) (layer "F.Cu") (net 2)))')

    def run(floors):
        f = tmp_path / "b.kicad_pcb"
        f.write_text(text)
        routing.widen(f, board, {"VBAT": 0.4}, lambda a, b: 0.2, ["F.Cu"], 0.6, box(-5, -5, 10, 10), {}, 0.15,
                      floors=floors)
        return sorted({float(s["w"]) for s in pcb.read_board(f)["segments"] if s["net"] == "VBAT"})
    assert run(None) == [0.15]  # without a floor: the neck floor
    assert run({"VBAT": 0.29}) == [0.29]  # the floor: the widest that still fits is never under it


def test_block_copper_is_widened_to_the_floor_of_its_net(tmp_path):
    d, libs = _boost_design(tmp_path, entry={"use": "tps61299_boost", "parts": dict(BOOST), "at": {"x": 15, "y": 11}})
    parts = placer.build_parts(d, libs)
    bt0, _ = blocks.copper(d, parts)
    assert [t for t in bt0 if t["net"] == "VBAT" and t["w"] < 0.29]  # the block's EN tie to VIN: 4.2 mm at 0.25 mm
    d.raw["nets"]["VBAT"]["current_a"] = 0.96
    d.raw["nets"]["SW"] = {"current_a": 1.5}  # the switch node: a 0.85 mm 0.2 mm neck at the U pin, then 0.6 mm
    bt, _ = blocks.copper(d, parts)
    assert min(t["w"] for t in bt if t["net"] == "VBAT") == 0.29
    sw0 = sorted(t["w"] for t in bt0 if t["net"] == "SW")
    assert sw0[0] == 0.2 and current_width_mm(d, "SW") > 0.5
    assert sorted(t["w"] for t in bt if t["net"] == "SW") == sw0  # the pad-entry neck stays (fine pitch)
    assert [t["w"] for t in bt if t["net"] == "GND"] == [t["w"] for t in bt0 if t["net"] == "GND"]
    assert postlayout.current_shortfalls(d, [t for t in bt if t["net"] == "VBAT"]) == []
    warn = [f["msg"] for f in blocks.findings(d, libs) if f["sev"] == "warn" and "floor" in f["msg"]]
    assert len(warn) == 1 and "0.25 mm copper on VBAT" in warn[0] and "widens it to 0.29 mm" in warn[0]
