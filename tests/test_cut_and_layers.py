"""Floorplan cut regions (the board outline minus the cuts) and per-net router layers (nets.<name>.layers)."""
import shutil
from pathlib import Path

import pytest
import yaml
from shapely.geometry import LineString, box

from hwflow.layout import diagnose
from hwflow import floorplan, placer, postlayout
from hwflow.layout.board import pcb
from hwflow.core import sexpr
from hwflow.core.design import Design, check
from hwflow.core.libs import Libs

EX = "examples/sensor_preamp"
needs_router = pytest.mark.skipif(not (pcb.krt_dir() / "py_router" / "route.py").exists(), reason="KiCadRoutingTools not installed")


def make(tmp_path, regions=None, board=None, nets=None):
    raw = {"project": "c", "board": {"width_mm": 40, "height_mm": 30, "layers": 2, **(board or {})}}
    if regions is not None:
        raw["floorplan"] = {"regions": regions}
    if nets:
        raw["nets"] = nets
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    return Design.load(tmp_path)


def preamp(tmp_path, nets=None, regions=None, **board):
    """examples/sensor_preamp with extra net fields, cut regions and board fields."""
    proj = tmp_path / "p"
    shutil.copytree(EX, proj)
    raw = yaml.safe_load((proj / "design.yaml").read_text())
    raw["board"].pop("optimize_area", None)
    raw["board"].update(board)
    for n, extra in (nets or {}).items():
        raw["nets"][n] = {**(raw["nets"].get(n) or {}), **extra}
    if regions:
        raw["floorplan"] = {"regions": regions}
    (proj / "design.yaml").write_text(yaml.safe_dump(raw))
    return Design.load(proj)


def _dsn(path):
    return sexpr.loads(path.read_text().replace('(string_quote ")', ""))


def _items(design, head):
    return [x for x in pcb.edge_cuts(design) if x[0] == head]


# ------------------------------------------------------------------ cut regions

def test_cut_region_errors(tmp_path):
    bad = [{"name": "a", "cut": True, "kind": "mains", "rect": [0, 0, 5, 5]},
           {"name": "b", "cut": True, "side": "front", "rect": [0, 0, 5, 5]},
           {"name": "c", "cut": True, "circle": [100, 100, 3]},
           {"name": "d", "cut": True, "rect": [0, 0, 5, 5], "items": ["U1"]}]
    text = " | ".join(floorplan.validate(make(tmp_path, bad)))
    for want in ("a: a cut region is a keep-out", "b: a cut goes through the board", "c: the cut lies outside",
                 "d: a cut region holds no items"):
        assert want in text, want
    split = [{"name": "s", "cut": True, "rect": [18, -1, 22, 31]}]
    assert any("split board" in e for e in floorplan.validate(make(tmp_path, split)))
    whole = [{"name": "w", "cut": True, "rect": [-1, -1, 41, 31]}]
    assert any("remove the whole board" in e for e in floorplan.validate(make(tmp_path, whole)))
    ok = [{"name": "n", "cut": True, "circle": [0, 0, 6]}, {"name": "h", "cut": True, "rect": [15, 10, 20, 14]}]
    d = make(tmp_path, ok)
    assert floorplan.validate(d) == []
    r = {x["name"]: x for x in floorplan.parse(d)["regions"]}
    assert r["n"]["kind"] == "keepout" and r["n"]["sides"] == ("front", "back") and r["n"]["circle"] == (0, 0, 6)


def test_no_cut_keeps_the_rectangle(tmp_path):
    d = make(tmp_path, [{"name": "k", "kind": "keepout", "rect": [1, 1, 5, 5]}])
    assert [x[0] for x in pcb.edge_cuts(d)] == ["gr_rect"]
    assert floorplan.cuts(d) == [] and floorplan.outline(d).area == 40 * 30


def test_edge_cuts_follow_the_cuts(tmp_path):
    d = make(tmp_path, [{"name": "notch", "cut": True, "circle": [0, 0, 6]},
                        {"name": "hole", "cut": True, "circle": [30, 15, 2]},
                        {"name": "slot", "cut": True, "rect": [15, 10, 20, 14]}])
    arcs, circles, lines = _items(d, "gr_arc"), _items(d, "gr_circle"), _items(d, "gr_line")
    assert len(arcs) == 1 and len(circles) == 1 and len(lines) == 4 + 4
    a = {k[0]: (float(k[1]), float(k[2])) for k in arcs[0][1:4]}
    assert {a["start"], a["end"]} == {(6.0, 0.0), (0.0, 6.0)}
    assert abs((a["mid"][0] ** 2 + a["mid"][1] ** 2) ** 0.5 - 6) < 1e-3
    c = circles[0]
    assert [float(v) for v in sexpr.find(c, "center")[1:]] == [30, 15] and [float(v) for v in sexpr.find(c, "end")[1:]] == [32, 15]
    ends = [(float(x[1][1]), float(x[1][2])) for x in lines] + [(float(x[2][1]), float(x[2][2])) for x in lines]
    assert (0.0, 6.0) in ends and (6.0, 0.0) in ends  # the arc meets the edge lines exactly
    # the routers read every Edge.Cuts shape on the layer, KiCadRoutingTools by text patterns
    text = sexpr.dumps(["kicad_pcb"] + pcb.edge_cuts(d))
    assert '(layer "Edge.Cuts")' in text and "(gr_arc" in text and "(mid " in text
    assert abs(floorplan.outline(d).area - (1200 - 3.14159 * 36 / 4 - 3.14159 * 4 - 20)) < 0.1


def test_cut_is_a_keepout_for_the_placer_and_the_area(tmp_path):
    d = make(tmp_path, [{"name": "slot", "cut": True, "rect": [10, 10, 20, 14]}], board={"margin_mm": 1.0})
    rules, _ = floorplan.placer_regions(d, {})
    assert len(rules) == 1 and rules[0]["excl"] and rules[0]["sides"] == ("front", "back")
    assert rules[0]["rect"] == (9, 9, 21, 15)  # grown by the board margin, as the board edge keeps it
    res = floorplan.check(d, Libs(design=d), place=False)
    front = next(s for s in res["sides"] if s["side"] == "front")
    assert front["usable_mm2"] == pytest.approx(38 * 28 - 40)
    # a part placed in the cut is a violation, and the placer moves parts out of it
    from hwflow.core.design import Component
    for i in range(4):
        d.components[f"R{i}"] = Component(f"R{i}", {"footprint": "Resistor_SMD:R_0603_1608Metric",
                                                    "pins": {"1": f"N{i}", "2": f"N{i + 1}"}})
    parts = placer.build_parts(d, Libs(design=d))
    pl = placer.Placer(d, parts, 40, 30, seed=1)
    parts["R0"].x, parts["R0"].y = 15, 12
    assert any(v.startswith("R0 inside floorplan region 'slot'") for v in pl.violations())
    pl.run(3000)
    assert not any("slot" in v for v in pl.violations())


def test_cut_in_dsn_and_route_key(tmp_path):
    d = preamp(tmp_path, regions=[{"name": "notch", "cut": True, "circle": [40, 30, 5]},
                                  {"name": "slot", "cut": True, "rect": [18, 2, 22, 6]}])
    libs = Libs(design=d)
    parts = placer.build_parts(d, libs)
    pcb.export_dsn(d, libs, parts, tmp_path / "x.dsn")
    t = _dsn(tmp_path / "x.dsn")
    st = sexpr.find(t, "structure")
    ring = sexpr.find(sexpr.find(st, "boundary"), "path")[3:]
    pts = [(float(ring[i]) / 1000, -float(ring[i + 1]) / 1000) for i in range(0, len(ring), 2)]
    assert len(pts) > 5 and (40.0, 30.0) not in pts and (35.0, 30.0) in pts  # the notch is in the boundary
    ko = sexpr.find(st, "keepout")
    assert ko is not None and str(sexpr.find(ko, "polygon")[1]) == "signal"  # the slot is a keep-out
    text = pcb.board_text(pcb.build_board(d, libs, parts))
    assert "(gr_arc" in text and "gr_rect" not in text.split("(footprint")[0]  # the route cache key sees the outline


def test_cut_pad_warning_and_structure_key(tmp_path):
    d0 = preamp(tmp_path / "a")
    d = preamp(tmp_path / "b", regions=[{"name": "slot", "cut": True, "rect": [0, 0, 6, 6]}])
    d.components["R1"].data["placement"] = {"x": 6.5, "y": 3, "fixed": True}
    f = [x for x in check(d, Libs(design=d)) if x["code"] == "cut-pad"]
    assert f and all(x["sev"] == "warn" and "slot" in x["msg"] for x in f)
    from hwflow import floorplan, placer
    parts = placer.build_parts(d, Libs(design=d))
    parts["R1"].nocu = frozenset(parts["R1"].pads)            # a keep-out marker's dummy pads have no copper
    assert floorplan.cut_pad_findings(d, parts, 0.3) == []
    assert diagnose.structure_key(d0) != diagnose.structure_key(d)
    d0.raw["nets"]["AMP_OUT"]["layers"] = ["F.Cu"]
    k1 = diagnose.structure_key(d0)
    d0.raw["nets"]["AMP_OUT"]["layers"] = ["B.Cu"]
    assert k1 != diagnose.structure_key(d0)
    del d0.raw["nets"]["AMP_OUT"]["layers"]
    assert diagnose.structure_key(d0) == diagnose.structure_key(preamp(tmp_path / "c"))


# ------------------------------------------------------------------ per-net layers

@pytest.mark.slow
def test_net_layers_check(tmp_path):
    d = preamp(tmp_path / "a", nets={"AMP_OUT": {"layers": ["B.Cu"]}, "SENS_IN": {"layers": ["In*.Cu"]},
                                     "VBUS": {"layers": ["F.Cu", "Top"]}})
    f = [x["msg"] for x in check(d, Libs(design=d)) if x["code"] == "net-layers"]
    assert any("AMP_OUT is an SMD pad" in m or "(AMP_OUT) is an SMD pad on F.Cu only" in m for m in f)
    assert any("In*.Cu is not a copper layer of this 2-layer board" in m for m in f)
    assert any("Top is not a copper layer" in m for m in f)
    d4 = preamp(tmp_path / "b", nets={"SENS_IN": {"layers": ["F.Cu", "In*.Cu"]}}, layers=4)
    assert pcb.net_layers(d4) == {"SENS_IN": ["F.Cu", "In1.Cu", "In2.Cu"]}
    assert not [x for x in check(d4, Libs(design=d4)) if x["code"] == "net-layers"]


def test_dsn_class_uses_only_the_net_layers(tmp_path):
    d = preamp(tmp_path, nets={"AMP_OUT": {"layers": ["F.Cu"]}})
    libs = Libs(design=d)
    pcb.export_dsn(d, libs, placer.build_parts(d, libs), tmp_path / "x.dsn")
    net = sexpr.find(_dsn(tmp_path / "x.dsn"), "network")
    cls = [c for c in sexpr.find_all(net, "class") if "AMP_OUT" in [str(x) for x in c]]
    assert len(cls) == 1 and [str(x) for x in sexpr.find(sexpr.find(cls[0], "circuit"), "use_layer")[1:]] == ["F.Cu"]
    assert all(sexpr.find(sexpr.find(c, "circuit"), "use_layer") is None for c in sexpr.find_all(net, "class") if c is not cls[0])


def test_layer_args():
    cu = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    assert pcb.layer_args(cu, ()) == [] and pcb.layer_args(cu, tuple(cu)) == []
    assert pcb.layer_args(cu, ("In2.Cu", "B.Cu")) == ["--layers", *cu, "--layer-costs", "-1", "-1", "3.0", "3.0"]


@needs_router
def test_restricted_nets_route_in_their_own_pass(tmp_path, monkeypatch):
    d = preamp(tmp_path, nets={"AMP_OUT": {"layers": ["F.Cu"]}})
    libs = Libs()
    (tmp_path / "out").mkdir()
    src = tmp_path / "out" / "x.kicad_pcb"
    pcb.write_board(src, pcb.build_board(d, libs, placer.build_parts(d, libs)))
    pcb.write_project(tmp_path / "out", d.name, d)
    seen = []

    def fake(base, stem, retries, run, variants=None):
        nets = base[base.index("--nets") + 1:]
        nets = nets[:next(i for i, a in enumerate(nets) if a.startswith("--"))]
        seen.append((nets, base))
        return (Path(base[2]), {}, 0), [("default order", "0 unrouted")], [""]

    monkeypatch.setattr(pcb, "route_class", fake)
    pcb.krt_route(d, src, tmp_path / "out", "x_")
    own = [b for n, b in seen if "AMP_OUT" in n]
    assert len(own) == 1 and [n for n, b in seen if "AMP_OUT" in n] == [["AMP_OUT"]]
    b = own[0]
    assert b[b.index("--layers") + 1:b.index("--layers") + 3] == ["F.Cu", "B.Cu"]
    assert b[b.index("--layer-costs") + 1:b.index("--layer-costs") + 3] == ["1.0", "-1"]
    assert all("--layer-costs" not in b for n, b in seen if "AMP_OUT" not in n)


def test_post_layout_error_for_a_track_off_its_layers(tmp_path):
    d = preamp(tmp_path, nets={"AMP_OUT": {"layers": ["F.Cu"]}})
    libs = Libs(design=d)
    parts = placer.build_parts(d, libs)
    tr = [{"net": "AMP_OUT", "layer": "B.Cu", "w": 0.25, "x1": 5, "y1": 5, "x2": 10, "y2": 5},
          {"net": "SENS_IN", "layer": "B.Cu", "w": 0.25, "x1": 5, "y1": 8, "x2": 10, "y2": 8}]
    board = tmp_path / "b.kicad_pcb"
    pcb.write_board(board, pcb.build_board(d, libs, parts, tr, []))
    f = [x for x in postlayout.layout_checks(d, postlayout.Extraction(d, board)) if x["code"] == "net-layer"]
    assert len(f) == 1 and f[0]["sev"] == "error" and "AMP_OUT" in f[0]["msg"] and "B.Cu" in f[0]["msg"]


@needs_router
@pytest.mark.slow
def test_route_keeps_out_of_cuts_and_on_the_net_layers(tmp_path):
    from hwflow.pipeline import stage_layout
    d = preamp(tmp_path, nets={"SENS_IN": {"layers": ["F.Cu"]}, "+3V3": {"layers": ["F.Cu"]}},
               regions=[{"name": "slot", "cut": True, "rect": [18, 0, 22, 18]},
                        {"name": "notch", "cut": True, "circle": [40, 30, 5]}])
    res = stage_layout(d, Libs(), seeds=1, iters=3000)
    assert res["unrouted"] == 0 and res["off_layer"] == [] and not res["placement_violations"]
    assert not [x for x in res["drc"] if x["sev"] == "error"]
    b = pcb.read_board(d.dir / "build" / f"{d.name}.kicad_pcb")
    shape = floorplan.outline(d)
    for s in b["segments"]:
        assert shape.contains(LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])]).buffer(s["w"] / 2)), s
        assert s["net"] not in ("SENS_IN", "+3V3") or s["layer"] == "F.Cu", s
    for fp in b["footprints"].values():
        assert not box(18, 0, 22, 18).contains(box(fp["x"] - 0.1, fp["y"] - 0.1, fp["x"] + 0.1, fp["y"] + 0.1))
