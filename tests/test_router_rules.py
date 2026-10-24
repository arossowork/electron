import shutil
import subprocess
from pathlib import Path

import pytest

from hwflow import pcb, placer
from hwflow.core.design import Design
from hwflow.core.libs import Libs

EX = "examples/sensor_preamp"
needs_router = pytest.mark.skipif(not (pcb.krt_dir() / "py_router" / "route.py").exists(), reason="KiCadRoutingTools not installed")


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
@pytest.mark.slow
def test_large_class_clearance_has_no_drc_clearance_errors(tmp_path):
    from hwflow.pipeline import stage_layout
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
@pytest.mark.slow
def test_router_keeps_the_creepage_between_isolation_sides(tmp_path):
    from hwflow.pipeline import stage_layout
    d = _iso_project(tmp_path, 1.0)
    d.raw["board"].update(width_mm=60, height_mm=45)
    res = stage_layout(d, Libs(), seeds=1, iters=3000)
    assert res["unrouted"] == 0
    segs = pcb.read_board(d.dir / "build" / f"{d.name}.kicad_pcb")["segments"]
    assert any(s["net"] == "AMP_OUT" for s in segs)
    assert _min_gap(segs, {"AMP_OUT"}, None) >= 1.0 - 0.02


@needs_router
@pytest.mark.slow
def test_router_keeps_tracks_out_of_a_tracks_not_allowed_area(tmp_path, monkeypatch):
    """Rule areas are read by the router from the board itself, so no `disallow` rule is needed for them."""
    from shapely.geometry import LineString, Polygon
    from hwflow.core import sexpr
    from hwflow.pipeline import stage_layout
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


# ---- board.isolation between_mm: functional insulation between the member nets of one group

def _between_project(tmp_path, creepage=0.3, between=0.6):
    proj = tmp_path / "p"
    shutil.copytree(EX, proj)
    f = proj / "design.yaml"
    f.write_text(f.read_text().replace("board: {", "board: {isolation: [{name: sig, nets: [SENS_IN, AMP_OUT], "
                                       "creepage_mm: %s, between_mm: %s}], " % (creepage, between)))
    return Design.load(proj)


def test_between_mm_rules_for_drc_router_placer_and_checks(tmp_path):
    """between_mm: KiCad DRC rules between two different members, one router class per member (KiCadRoutingTools
    reads one class per track rule and merges a net's classes), keep-outs of the other members' copper in a pass,
    the placer pad distance, the post-layout closest pair, the design check and the structure key."""
    import json
    import re
    import sys
    from types import SimpleNamespace

    from hwflow import diagnose, postlayout, routing
    from hwflow.core.design import check
    d = _between_project(tmp_path)
    (g,) = pcb.isolation_groups(d)
    assert g["between_mm"] == 0.6 and g["members"] == ["AMP_OUT", "SENS_IN"]
    assert ('(rule "sig between AMP_OUT clearance" (constraint clearance (min 0.6mm)) '
            '(condition "A.NetName == \'AMP_OUT\' && B.NetName == \'SENS_IN\'"))') in g["kicad"]
    assert sum("between" in r for r in g["kicad"]) == 4  # clearance and creepage, per member
    rr = pcb.isolation_router_rules(d)
    assert [(x["class"], x["members"], x["mm"], x["between"]) for x in rr] == [
        ("iso_sig", ["AMP_OUT", "SENS_IN"], 0.3, False), ("isb_sig_between_AMP_OUT", ["AMP_OUT"], 0.6, True),
        ("isb_sig_between_SENS_IN", ["SENS_IN"], 0.6, True)]
    pcb.write_project(tmp_path, d.name, d)
    dru = (tmp_path / f"{d.name}.kicad_dru").read_text()
    pro = json.loads((tmp_path / f"{d.name}.kicad_pro").read_text())["net_settings"]
    assert {"netclass": "isb_sig_between_SENS_IN", "pattern": "SENS_IN"} in pro["netclass_patterns"]
    # KiCadRoutingTools' own readers: member to member 0.6 mm, member to another net the larger group rule
    if (pcb.krt_dir() / "py_router" / "kicad_dru.py").exists():
        sys.path.insert(0, str(pcb.krt_dir() / "py_router"))
        try:
            from kicad_dru import parse_dru_track_clearances, track_pair_clearance
            from list_nets import net_class_memberships
        finally:
            sys.path.pop(0)
        rules, _ = parse_dru_track_clearances(dru)
        (tmp_path / f"{d.name}.kicad_pcb").write_text("(kicad_pcb)")
        cls = net_class_memberships(str(tmp_path / f"{d.name}.kicad_pcb"), {1: "SENS_IN", 2: "AMP_OUT", 3: "VBUS"})
        assert track_pair_clearance(rules, cls[1], cls[2], 0.2)[0] == pytest.approx(0.6)
        assert track_pair_clearance(rules, cls[1], cls.get(3, set()), 0.2)[0] == pytest.approx(0.6)
        d.raw["board"]["isolation"][0]["creepage_mm"] = 2.0
        pcb.write_project(tmp_path, d.name, d)
        rules, _ = parse_dru_track_clearances((tmp_path / f"{d.name}.kicad_dru").read_text())
        assert track_pair_clearance(rules, cls[1], cls[2], 0.2)[0] == pytest.approx(0.6)
        assert track_pair_clearance(rules, cls[1], cls.get(3, set()), 0.2)[0] == pytest.approx(2.0)
        d.raw["board"]["isolation"][0]["creepage_mm"] = 0.3
    # a pass with a member: the other member's pads grown by between - clearance + slack, bound to that member
    pads = {"footprints": {"J1": {"pads": [
        {"net": "SENS_IN", "x": 0, "y": 0, "w": 1, "h": 1, "shape": "rect", "kind": "smd", "layers": ["F.Cu"]},
        {"net": "AMP_OUT", "x": 5, "y": 0, "w": 1, "h": 1, "shape": "rect", "kind": "smd", "layers": ["F.Cu"]}]}},
        "segments": [], "vias": []}
    zones, rules = routing.isolation_keepouts(pcb.isolation_needs(d), pads, ["F.Cu", "B.Cu"], 0.6, 0.2, {"SENS_IN"})
    (r,) = [x for x in rules if "_b0" in x]
    assert "A.NetName == 'SENS_IN'" in r
    from shapely.geometry import Polygon
    (z,) = [z for z in zones if z[5][1].endswith("_b0")]
    poly = Polygon([(p[1], p[2]) for p in z[-1][1][1:]])
    assert poly.bounds[0] == pytest.approx(5 - 0.5 - (0.6 - 0.2 + routing.ISO_SLACK), abs=0.01)
    # placer: two member pads need between_mm + margin, and the message names the rule
    libs = Libs()
    parts = placer.build_parts(d, libs)
    pl = placer.Placer(d, parts, 40, 30)
    assert pl._iso_short(("J1", "2"), ("J1", "3")) == 0.0          # one part: never
    ka = next(k for k in pl.pad_grp if pl.pad_net[k] == "SENS_IN" and k[0] != "J1")
    kb = next(k for k in pl.pad_grp if pl.pad_net[k] == "AMP_OUT" and k[0] not in ("J1", ka[0]))
    for k, (x, y) in ((ka, (10, 10)), (kb, (10, 10))):
        parts[k[0]].x, parts[k[0]].y, parts[k[0]].r = x, y, 0
    bx = pl._pad_box(parts[ka[0]], ka[1])
    parts[kb[0]].x += (bx[2] + 0.3) - pl._pad_box(parts[kb[0]], kb[1])[0]       # pads 0.3 mm apart in x
    parts[kb[0]].y += (bx[1] + bx[3]) / 2 - sum(pl._pad_box(parts[kb[0]], kb[1])[1::2]) / 2
    assert pl._iso_short(ka, kb) == pytest.approx(0.6 + placer.CLEAR_MARGIN - 0.3, abs=1e-6)
    assert "between_mm 0.6 mm" in pl._iso_rule(ka, kb)
    # post-layout: the closest pair of two different members, apart from the group row
    segs = [{"net": "SENS_IN", "layer": "F.Cu", "x1": 0, "y1": 0, "x2": 10, "y2": 0, "w": 0.2},
            {"net": "AMP_OUT", "layer": "F.Cu", "x1": 0, "y1": 0.6, "x2": 10, "y2": 0.6, "w": 0.2},
            {"net": "VBUS", "layer": "F.Cu", "x1": 0, "y1": 3, "x2": 10, "y2": 3, "w": 0.2}]
    ex = SimpleNamespace(d=d, path=None, board={"segments": segs, "vias": [], "footprints": {}})
    rows = {r["rule"]: r for r in postlayout.group_clearances(d, ex)}
    btw = rows["sig between members"]
    assert btw["mm"] == pytest.approx(0.4) and not btw["ok"] and btw["members_only"] and btw["req"] is None
    assert rows["sig"]["mm"] == pytest.approx(2.2) and rows["sig"]["ok"]
    assert re.match(r"(SENS_IN / AMP_OUT|AMP_OUT / SENS_IN) on F.Cu", btw["between"])
    # design check and structure key
    k0 = diagnose.structure_key(d)
    d.raw["board"]["isolation"][0]["between_mm"] = -1
    assert any("between_mm must be a positive number" in x["msg"] for x in check(d))
    d.raw["board"]["isolation"][0]["between_mm"] = 0.8
    assert diagnose.structure_key(d) != k0


class _PadLibs:
    """footprint id -> [(number, kind, x, y, w, h, layers)], drawn in board coordinates (parts placed at (0, 0)): the
    pads for the corridor proof (footprint) and the footprint text for the board (footprint_tree)."""

    def __init__(self, fps):
        self.fps = fps

    def footprint(self, fid):
        return {"pads": [{"number": n, "kind": k, "shape": "rect", "x": x, "y": y, "rot": 0.0, "w": w, "h": h,
                          "layers": list(L), "drill": None, "rratio": 0.25, "hull": None}
                         for n, k, x, y, w, h, L in self.fps[fid]], "bbox": (-1, -1, 1, 1), "sides": {}}

    def footprint_tree(self, fid):
        from hwflow.core import sexpr
        pads = " ".join(f'(pad "{n}" {k} rect (at {x} {y}) (size {w} {h}) (layers {" ".join(chr(34) + v + chr(34) for v in L)}))'
                        for n, k, x, y, w, h, L in self.fps[fid])
        return sexpr.loads(f'(footprint "{fid}" (layer "F.Cu") (property "Reference" "REF" (at 0 0 0) (layer "F.SilkS")) '
                           f'(property "Value" "V" (at 0 0 0) (layer "F.Fab")) {pads})')


@needs_router
def test_a_net_only_a_neck_passes_routes_narrow_first_and_is_widened(tmp_path, monkeypatch):
    """AC_ROOM (a mains member at the board track width, so no power-net neck-down, kept on F.Cu) passes a 1 mm long
    slit between two other members' pads: 2.55 mm wide, so with between_mm 1.0 a 0.55 mm corridor, under its 0.6 mm
    width. Before, its pass left it open (no neck-down for a net at the track width); now the corridor proof marks it,
    it routes at the neck floor and is widened back to 0.6 mm except at the slit, keeping between_mm and its layer."""
    from types import SimpleNamespace

    import yaml
    from shapely.geometry import LineString, box

    from hwflow import corridor, routing
    F = ("F.Cu",)
    lo, hi = 10 - 2.55 / 2, 10 + 2.55 / 2
    libs = _PadLibs({"A": [("1", "smd", 5, 10, 1, 1, F)], "B": [("1", "smd", 25, 10, 1, 1, F)],
                     "W": [("1", "smd", 15, lo / 2, 1.0, lo, F), ("2", "smd", 15, (hi + 20) / 2, 1.0, 20 - hi, F)]})
    raw = {"project": "neck", "board": {"layers": 2, "width_mm": 30, "height_mm": 20, "track_mm": 0.6,
                                        "clearance_mm": 0.2, "via_dia_mm": 0.6, "gnd_pour": False,
                                        "isolation": [{"name": "mains", "nets": ["AC_*"], "clearance_mm": 6.0,
                                                       "between_mm": 1.0}]},
           "nets": {"AC_ROOM": {"layers": ["F.Cu"]}}, "components": {}}
    for ref, fid, pins in (("U1", "A", {"1": "AC_ROOM"}), ("U2", "B", {"1": "AC_ROOM"}),
                           ("W1", "W", {"1": "AC_L", "2": "AC_N"})):
        raw["components"][ref] = {"footprint": fid, "pins": pins, "placement": {"x": 0, "y": 0, "rot": 0, "fixed": True}}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    d = Design.load(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    src = out / "neck_unrouted.kicad_pcb"
    parts = {r: SimpleNamespace(x=0.0, y=0.0, r=0.0, fixed=True) for r in raw["components"]}
    pcb.write_board(src, pcb.build_board(d, libs, parts))
    pcb.write_project(out, d.name, d)
    (need,) = corridor.neck_needed(d, libs, board=pcb.read_board(src)).items()
    assert need[0] == "AC_ROOM" and need[1]["need_mm"] == 0.6 and 0.5 <= need[1]["plain_mm"] < 0.6
    # without the neck-down (as before for a net at the track width): open
    monkeypatch.setenv("HWFLOW_NECKDOWN", "0")
    d.raw["board"]["router_retries"] = 0
    _, last, _, _ = pcb.krt_route(d, src, out, "y_", nets={"AC_ROOM"}, libs=libs)
    assert sum(pcb.krt_unrouted(v) for v in last.values()) == 1
    monkeypatch.delenv("HWFLOW_NECKDOWN")
    cur, last, notes, _ = pcb.krt_route(d, src, out, "x_", nets={"AC_ROOM"}, libs=libs)
    assert sum(pcb.krt_unrouted(v) for v in last.values()) == 0, notes
    b = pcb.read_board(cur)
    segs = [s for s in b["segments"] if s["net"] == "AC_ROOM"]
    assert segs and {s["layer"] for s in segs} == {"F.Cu"} and not [v for v in b["vias"] if v["net"] == "AC_ROOM"]
    assert max(s["w"] for s in segs) == 0.6 and min(s["w"] for s in segs) == routing.NECK_FLOOR
    thin = [((s["x1"], s["y1"]), (s["x2"], s["y2"]), s["layer"]) for s in segs if s["w"] < 0.6]
    assert routing.longest_neck(thin) <= routing.NECK_MAX_MM
    wall = [box(14.5, 0, 15.5, lo), box(14.5, hi, 15.5, 20)]
    gap = min(LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])]).buffer(s["w"] / 2).distance(p)
              for s in segs for p in wall)
    assert gap >= 1.0 - 0.01
    text = " ".join(notes)
    assert "AC_ROOM routed narrow first" in text and "neck-down to 0.15 mm: AC_ROOM" in text and "longest neck" in text


@needs_router
@pytest.mark.slow
def test_router_keeps_between_mm_between_members(tmp_path):
    """A real KiCadRoutingTools run: two member nets of one group keep between_mm track to track (the isb_ class
    rules) while the group keeps its own distance to the other nets."""
    from hwflow.pipeline import stage_layout
    d = _between_project(tmp_path, 0.3, 0.6)
    d.raw["board"].update(width_mm=60, height_mm=45)
    res = stage_layout(d, Libs(), seeds=1, iters=3000)
    assert res["unrouted"] == 0
    segs = pcb.read_board(d.dir / "build" / f"{d.name}.kicad_pcb")["segments"]
    from shapely.geometry import LineString
    sh = [(s["net"], s["layer"], LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])]).buffer(s["w"] / 2)) for s in segs]
    gap = min((p.distance(q) for n1, l1, p in sh for n2, l2, q in sh
               if l1 == l2 and n1 == "SENS_IN" and n2 == "AMP_OUT"), default=99.0)
    assert gap >= 0.6 - 0.02
    (row,) = [r for r in res["isolation"] if r.get("members_only")]
    assert row["limit"] == 0.6
