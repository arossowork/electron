import shutil
import subprocess
from pathlib import Path

import pytest

from hwflow import pcb, placer
from hwflow.design import Design
from hwflow.libs import Libs

EX = "examples/sensor_preamp"
needs_router = pytest.mark.skipif(not (pcb.KRT_DIR / "py_router" / "route.py").exists(), reason="KiCadRoutingTools not installed")


def _project(tmp_path, clearance=2.5):
    proj = tmp_path / "p"
    shutil.copytree(EX, proj)
    f = proj / "design.yaml"
    f.write_text(f.read_text().replace("AMP_OUT: {class: signal}", f"AMP_OUT: {{class: signal, clearance_mm: {clearance}}}")
                 .replace("board: {", "board: {isolation: [{name: amp, nets: AMP_OUT, clearance_mm: %s}], " % clearance))
    return Design.load(proj)


@needs_router
def test_router_pass_sees_the_drc_rules(tmp_path, monkeypatch):
    d = _project(tmp_path)
    libs = Libs()
    parts = placer.build_parts(d, libs)
    (tmp_path / "out").mkdir()
    seen = []

    def fake_run(args, **kw):
        board = next(a for a in args if str(a).endswith(".kicad_pcb"))
        sib = {s: (board.rsplit(".", 1)[0] + s) for s in (".kicad_pro", ".kicad_dru")}
        from pathlib import Path
        seen.append({s: Path(p).read_text() for s, p in sib.items() if Path(p).exists()})
        return subprocess.CompletedProcess(args, 1, "", "")

    monkeypatch.setattr(pcb.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError):
        pcb.run_krt(d, libs, parts, tmp_path / "out")
    assert seen, "router was not called"
    for files in seen:
        assert '"clearance": 2.5' in files[".kicad_pro"]
        assert "amp clearance" in files[".kicad_dru"]


@needs_router
def test_large_class_clearance_has_no_drc_clearance_errors(tmp_path):
    from hwflow.cli import stage_layout
    d = _project(tmp_path, 1.2)
    d.raw["board"].update(width_mm=60, height_mm=45)
    res = stage_layout(d, Libs(), seeds=1, iters=3000)
    # copper the router drew against the 1.2 mm class; pad-to-pad distances are the placer's, other errors are not this test's
    bad = [x for x in res["drc"] if x["sev"] == "error" and "amp " in x["msg"]
           and any(i.startswith(("Track", "Via")) for i in x["items"])]
    assert bad == [], "\n".join(str(x) for x in bad)


def test_board_writes_quoted_pad_numbers_and_layers(tmp_path):
    """Older KiCad libraries write `(pad 1 smd ..)` unquoted; the router's parser then drops those pads (ICs), so nets
    came out half-connected while the router reported 0 unrouted. The written board must quote them."""
    import re
    d = Design.load(EX)
    libs = Libs()
    parts = placer.build_parts(d, libs)
    path = tmp_path / "b.kicad_pcb"
    pcb.write_board(path, pcb.build_board(d, libs, parts))
    text = path.read_text()
    pads = re.findall(r"\(pad\s+(\S+)", text)
    assert pads and all(p.startswith('"') for p in pads)
    assert not re.search(r"\(layers?\s+[^\"()\s]", text)


# ---- L11: creepage and keep-out areas while routing

def _iso_project(tmp_path, creepage=3.0):
    proj = tmp_path / "p"
    shutil.copytree(EX, proj)
    f = proj / "design.yaml"
    f.write_text(f.read_text().replace("board: {", "board: {isolation: [{name: amp, nets: AMP_OUT, creepage_mm: %s}], " % creepage))
    return Design.load(proj)


def test_dru_has_the_router_creepage_rule_next_to_the_drc_one(tmp_path):
    d = _iso_project(tmp_path)
    pcb.write_project(tmp_path, d.name, d)
    dru = (tmp_path / f"{d.name}.kicad_dru").read_text()
    assert "(constraint creepage (min 3mm))" in dru  # the real rule stays for KiCad DRC
    assert ("A.Type == 'track' && B.Type == 'track' && A.NetClass == 'iso_amp' && B.NetClass != 'iso_amp'" in dru
            and "(constraint clearance (min 3mm))" in dru)
    import json
    pro = json.loads((tmp_path / f"{d.name}.kicad_pro").read_text())["net_settings"]
    assert {"netclass": "iso_amp", "pattern": "AMP_OUT"} in pro["netclass_patterns"]
    assert "iso_amp" in [c["name"] for c in pro["classes"]]


def test_drc_rules_creepage_with_a_glob_becomes_a_router_rule(tmp_path):
    d = _iso_project(tmp_path)
    d.raw["board"]["isolation"] = []
    d.raw["board"]["drc_rules"] = ["(rule \"R8 creepage\" (constraint creepage (min 6.0mm)) "
                                   "(condition \"A.NetName == 'AMP_*' && B.NetName != 'AMP_*'\"))"]
    r = pcb.isolation_router_rules(d)
    assert [x["members"] for x in r] == [["AMP_OUT"]] and r[0]["mm"] == 6.0
    d.raw["board"]["drc_rules"] = ["(rule \"x\" (constraint creepage (min 6mm)) (condition \"A.NetClass == 'mains'\"))"]
    assert pcb.isolation_router_rules(d) == []  # a shape the router cannot read stays with DRC and repair


def _min_gap(segs, a, b):
    from shapely.geometry import LineString
    sh = [(s["net"], s["layer"], LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])]).buffer(s["w"] / 2)) for s in segs]
    return min((p.distance(q) for n1, l1, p in sh for n2, l2, q in sh if l1 == l2 and n1 in a and n2 not in a),
               default=99.0)


@needs_router
def test_router_keeps_the_creepage_between_isolation_sides(tmp_path):
    from hwflow.cli import stage_layout
    d = _iso_project(tmp_path, 1.0)
    d.raw["board"].update(width_mm=60, height_mm=45)
    res = stage_layout(d, Libs(), seeds=1, iters=3000)
    assert res["unrouted"] == 0
    segs = pcb.read_board(d.dir / "build" / f"{d.name}.kicad_pcb")["segments"]
    assert any(s["net"] == "AMP_OUT" for s in segs)
    assert _min_gap(segs, {"AMP_OUT"}, None) >= 1.0 - 0.02


@needs_router
def test_router_keeps_tracks_out_of_a_tracks_not_allowed_area(tmp_path, monkeypatch):
    """Rule areas are read by the router from the board itself, so no `disallow` rule is needed for them."""
    from shapely.geometry import LineString, Polygon
    from hwflow import sexpr
    from hwflow.cli import stage_layout
    zone = '''(zone (net 0) (net_name "") (layers "F.Cu" "B.Cu") (uuid "5b6c1a0e-3d0a-4c7e-9a11-0a1b2c3d4e0a") (name "KO_T") (hatch edge 0.5)
      (connect_pads (clearance 0)) (min_thickness 0.25)
      (keepout (tracks not_allowed) (vias not_allowed) (pads allowed) (copperpour allowed) (footprints allowed))
      (fill (thermal_gap 0.5) (thermal_bridge_width 0.5))
      (polygon (pts (xy 27 18) (xy 33 18) (xy 33 27) (xy 27 27))))'''
    orig = pcb.build_board

    def with_area(*a, **k):
        tree = orig(*a, **k)
        root = tree[0] if tree and isinstance(tree[0], list) and tree[0] and tree[0][0] == "kicad_pcb" else tree
        root.append(sexpr.loads(zone))
        return tree

    monkeypatch.setattr(pcb, "build_board", with_area)
    d = Design.load(_copy(tmp_path))
    d.raw["board"].update(width_mm=60, height_mm=45)
    stage_layout(d, Libs(), seeds=1, iters=3000)
    segs = pcb.read_board(d.dir / "build" / f"{d.name}.kicad_pcb")["segments"]
    area = Polygon([(27, 18), (33, 18), (33, 27), (27, 27)])
    bad = [s for s in segs if area.intersects(LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])]).buffer(s["w"] / 2))]
    assert segs and bad == []


def _copy(tmp_path):
    proj = tmp_path / "q"
    shutil.copytree(EX, proj)
    return proj


@needs_router
def test_router_input_path_is_absolute(tmp_path, monkeypatch):
    """The router runs with cwd=KRT_DIR, so a relative project path (hwflow layout projects/x) must not reach it."""
    d = _project(tmp_path)
    (tmp_path / "out").mkdir()
    libs = Libs()
    pcb.write_board(tmp_path / "out" / "x.kicad_pcb", pcb.build_board(d, libs, placer.build_parts(d, libs)))
    monkeypatch.chdir(tmp_path)
    seen = []

    def stop(base, *a, **k):
        seen.append(base[2])
        raise RuntimeError("stop")

    monkeypatch.setattr(pcb, "route_class", stop)
    with pytest.raises(RuntimeError, match="stop"):
        pcb.krt_route(d, Path("out/x.kicad_pcb"), Path("out"), "x_")
    assert seen and seen[0].startswith("/")
