from types import SimpleNamespace

from hwflow.layout.board import pcb


def _design(nets):
    board = {"track_mm": 0.25, "clearance_mm": 0.2, "copper_oz": 1}
    raw = {"nets": nets}
    return SimpleNamespace(board=board, raw=raw, nets=lambda: list(nets),
                           net_info=lambda n: nets.get(n) or {})


def test_min_width_only_for_nets_that_set_it():
    d = _design({"AC": {"class": "mains", "width_mm": 1.0, "min_width_mm": 1.0}, "SIG": {}})
    assert pcb.net_min_widths(d) == {"AC": 1.0}
    cl = pcb.net_classes(d)
    assert cl["AC"]["min_width"] == 1.0 and "min_width" not in cl["SIG"]


def test_net_stats_widths_narrow_length_layers_vias():
    from hwflow.pipeline import net_stats
    tracks = [{"net": "AC", "w": 1.0, "x1": 0, "y1": 0, "x2": 3, "y2": 4, "layer": "F.Cu"},
              {"net": "AC", "w": 0.15, "x1": 3, "y1": 4, "x2": 3, "y2": 6, "layer": "F.Cu"}]
    vias = [{"net": "AC", "x": 0, "y": 0}, {"net": "GND", "x": 1, "y": 1}]
    st = net_stats(tracks, vias, {"AC": {"width": 1.0}})
    assert st["AC"]["min_w"] == 0.15 and st["AC"]["max_w"] == 1.0
    assert st["AC"]["length_mm"] == 7.0 and st["AC"]["narrow_mm"] == 2.0
    assert st["AC"]["layers"] == ["F.Cu"] and st["AC"]["vias"] == 1
    assert st["GND"]["vias"] == 1 and st["GND"]["min_w"] is None


def _traced(tmp_path, nets_status, reqs):
    import json
    import yaml
    from hwflow.pipeline import _design_hash, trace
    from hwflow.core.design import Design
    raw = {"project": "mw", "board": {"layers": 2, "gnd_pour": False},
           "nets": {"AC": {"class": "mains", "width_mm": 1.0, "min_width_mm": 1.0, "layers": ["F.Cu"], "req": "R13"}},
           "components": {"J1": {"pins": {"1": "AC", "2": "GND"}}, "K1": {"pins": {"1": "AC", "2": "GND"}}}}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    d = Design.load(tmp_path)
    (tmp_path / "requirements.yaml").write_text(yaml.safe_dump({"requirements": reqs}))
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "status.json").write_text(json.dumps({"layout": {
        "ok": True, "nets": nets_status, "design_hash": _design_hash(d)}}))
    return trace(d)


def test_net_req_checks_the_routed_width_and_layers(tmp_path):
    t = _traced(tmp_path, {"AC": {"min_w": 0.15, "layers": ["F.Cu"]}},
                [{"id": "R13", "text": "230 V at 1 mm", "verification": "layout", "evidence": "IPC"}])
    (r,) = t["rows"]
    assert r["status"] == "FAIL"
    assert r["measures"] == ["net AC min width 0.15/1 mm:FAIL", "net AC layers F.Cu:ok"]


def test_layout_requirement_with_evidence_only_is_flagged(tmp_path):
    t = _traced(tmp_path, {"AC": {"min_w": 1.0, "layers": ["F.Cu"]}},
                [{"id": "R13", "text": "230 V at 1 mm", "verification": "layout", "evidence": "IPC"},
                 {"id": "R1", "text": "fits the box", "verification": "layout", "evidence": "notes/x.md"}])
    st = {r["id"]: r["status"] for r in t["rows"]}
    assert st == {"R13": "pass", "R1": "UNVERIFIED"} and t["evidence_only"] == ["R1"]


def test_shared_library_is_searched_after_project_libraries():
    from hwflow.core.libs import Libs, shared_lib, shared_lib_dirs
    SHARED_LIB = shared_lib()
    if not (SHARED_LIB / "footprints" / "hw.pretty").is_dir():
        return
    libs = Libs()
    assert SHARED_LIB / "footprints" in shared_lib_dirs() and SHARED_LIB / "footprints" in libs.extra
    assert libs.footprint("hw:Relay_Panasonic_ADW1_1coil")["pads"]
    assert "hw:Relay_Panasonic_ADW1_1coil" in libs.search_footprints("ADW1")


def test_class_width_is_never_under_min_width():
    """A net that sets only min_width_mm routes at it (timed-bathroom-vent: L, class power, 0.4 mm against 1.0 mm)."""
    d = _design({"L": {"class": "power", "min_width_mm": 1.0}, "S": {"min_width_mm": 0.3}, "W": {"width_mm": 0.8,
                                                                                               "min_width_mm": 0.5}})
    cl = pcb.net_classes(d)
    assert cl["L"]["width"] == 1.0 and cl["S"]["width"] == 0.3 and cl["W"]["width"] == 0.8


def test_router_keeps_min_width(tmp_path):
    """The real router: a net with only min_width_mm gets no track under it."""
    import pytest
    import yaml

    from hwflow.core.design import Design
    from hwflow.layout.route import krt
    if not (krt.krt_dir() / "py_router" / "route.py").exists():
        pytest.skip("KiCadRoutingTools not installed")
    from test_ripup import _Libs
    raw = {"project": "mw", "board": {"layers": 2, "width_mm": 30, "height_mm": 15, "track_mm": 0.25,
                                      "clearance_mm": 0.2, "via_dia_mm": 0.6, "via_drill_mm": 0.3, "gnd_pour": False,
                                      "router_retries": 0},
           "nets": {"M": {"min_width_mm": 0.6}}, "components": {}}
    pads = {"A1": (5, 7), "A2": (25, 9)}
    for ref, (x, y) in pads.items():
        raw["components"][ref] = {"footprint": "P", "pins": {"1": "M"}, "placement": {"x": x, "y": y, "rot": 0, "fixed": True}}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    d = Design.load(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    parts = {r: SimpleNamespace(x=float(x), y=float(y), r=0.0, fixed=True) for r, (x, y) in pads.items()}
    src = out / "mw_unrouted.kicad_pcb"
    pcb.write_board(src, pcb.build_board(d, _Libs(), parts))
    pcb.write_project(out, d.name, d)
    cur, last, notes, _ = krt.krt_route(d, src, out, "mw_", libs=_Libs())
    assert sum(krt.krt_unrouted(v) for v in last.values()) == 0, notes
    widths = {s["w"] for s in pcb.read_board(cur)["segments"] if s["net"] == "M"}
    assert widths and min(widths) >= 0.6 - 1e-6, widths


def _slit_board(tmp_path, nets, tracks=()):
    """M from (5, 10) to (35, 10), F.Cu only; a wall of net W across x = 20 with a 0.8 mm slit at y = 10: room for a
    0.15 mm neck, not for a 0.6 mm track (0.6 + 2 x 0.2 mm clearance)."""
    import yaml

    from hwflow.core.design import Design
    from test_ripup import _Libs
    raw = {"project": "mw", "board": {"layers": 2, "width_mm": 40, "height_mm": 20, "track_mm": 0.25,
                                      "clearance_mm": 0.2, "via_dia_mm": 0.6, "via_drill_mm": 0.3, "gnd_pour": False,
                                      "router_retries": 0},
           "nets": nets, "components": {}}
    pads = {"A1": ("M", 5, 10), "A2": ("M", 35, 10), "W1": ("W", 20, 0.5), "W2": ("W", 20, 19.5)}
    for ref, (net, x, y) in pads.items():
        raw["components"][ref] = {"footprint": "P", "pins": {"1": net}, "placement": {"x": x, "y": y, "rot": 0, "fixed": True}}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    d = Design.load(tmp_path)
    parts = {r: SimpleNamespace(x=float(x), y=float(y), r=0.0, fixed=True) for r, (_, x, y) in pads.items()}
    wall = [{"net": "W", "layer": L, "w": 0.5, "x1": 20, "y1": y1, "x2": 20, "y2": y2}
            for L in ("F.Cu", "B.Cu") for y1, y2 in ((0.5, 9.35), (10.65, 19.5))]
    src = tmp_path / "mw.kicad_pcb"
    pcb.write_board(src, pcb.build_board(d, _Libs(), parts, wall + list(tracks), []))
    return d, _Libs(), src


def test_seed_route_never_necks_under_min_width(tmp_path):
    """seedroute.route_net necked to the board floor whatever the net's min_width_mm (the bathroom vent's N at
    0.65 mm against 1.0 mm). With min_width_mm the slit is no way: the net stays open (a reported blockage), never
    narrow copper. Without it, the same slit takes a neck."""
    from hwflow.layout.route import seedroute
    (tmp_path / "a").mkdir()
    d, libs, src = _slit_board(tmp_path / "a", {"M": {"layers": ["F.Cu"], "width_mm": 0.6}})
    r = seedroute.seed(d, libs, src, tmp_path / "a" / "out.kicad_pcb", ["M"])["M"]
    assert r["ok"] and r["min_width"] < 0.6, r  # control: a neck through the slit
    (tmp_path / "b").mkdir()
    d, libs, src = _slit_board(tmp_path / "b", {"M": {"layers": ["F.Cu"], "width_mm": 0.6, "min_width_mm": 0.6}})
    r = seedroute.seed(d, libs, src, tmp_path / "b" / "out.kicad_pcb", ["M"])["M"]
    assert not r["ok"] or r["min_width"] >= 0.6 - 1e-6, r


def test_min_width_fix_widens_or_removes_router_connectors(tmp_path):
    """The router's cleanup connectors are drawn after the route at their own width (a via-to-pad bridge at the via
    diameter: the bathroom vent's FAN and L_F 0.6 mm against 1.0 mm). routing.min_width_fix widens such a track to
    the net's min_width_mm where it fits, else removes it (the net then routes again); a track at the minimum is left
    alone, and a net without min_width_mm keeps its narrow copper."""
    from shapely.geometry import box

    from hwflow.layout.board import routing
    tracks = [{"net": "M", "layer": "F.Cu", "w": 1.0, "x1": 5, "y1": 10, "x2": 10, "y2": 10},   # at the minimum
              {"net": "M", "layer": "F.Cu", "w": 0.6, "x1": 10, "y1": 10, "x2": 15, "y2": 10},  # open space: widened
              {"net": "M", "layer": "F.Cu", "w": 0.6, "x1": 18, "y1": 10, "x2": 22, "y2": 10},  # through the slit: removed
              {"net": "W", "layer": "F.Cu", "w": 0.3, "x1": 30, "y1": 2, "x2": 30, "y2": 5}]    # no min width: kept
    d, libs, path = _slit_board(tmp_path, {"M": {"layers": ["F.Cu"], "min_width_mm": 1.0}}, tracks)
    clr = lambda a, b: 0.2  # noqa: E731
    wide, lost = routing.min_width_fix(path, {"M": 1.0}, clr, ["F.Cu", "B.Cu"], 0.6, box(0, 0, 40, 20), {})
    assert (wide, lost) == (["M"], ["M"])
    m = sorted((s["x1"], s["w"]) for s in pcb.read_board(path)["segments"] if s["net"] == "M")
    assert m == [(5.0, 1.0), (10.0, 1.0)], m
    assert any(s["net"] == "W" and abs(s["w"] - 0.3) < 1e-9 for s in pcb.read_board(path)["segments"])
    assert routing.min_width_fix(path, {"M": 1.0}, clr, ["F.Cu", "B.Cu"], 0.6, box(0, 0, 40, 20), {}) == ([], [])


def test_min_width_inner_is_the_floor_on_inner_layers_only(tmp_path, tmp_path_factory):
    """nets.<name>.min_width_inner_mm (timed-bathroom-vent N: 0.55 mm outer, 1.10 mm on 0.5 oz inner copper): the
    routing width is at least the inner floor, an inner track under it is widened or removed, an outer track is
    judged by min_width_mm alone."""
    from shapely.geometry import box

    from hwflow.layout.board import routing
    d = _design({"N": {"min_width_mm": 0.55, "min_width_inner_mm": 1.1}, "S": {"min_width_mm": 0.3}})
    assert pcb.net_inner_min_widths(d) == {"N": 1.1}
    assert pcb.net_min_widths(d) == {"N": 0.55, "S": 0.3}
    cl = pcb.net_classes(d)
    assert cl["N"]["width"] == 1.1 and cl["N"]["min_width"] == 0.55 and cl["N"]["min_width_inner"] == 1.1
    assert "min_width_inner" not in cl["S"]
    tracks = [{"net": "M", "layer": "F.Cu", "w": 0.6, "x1": 10, "y1": 10, "x2": 15, "y2": 10}]
    d2, libs, path = _slit_board(tmp_path, {"M": {"layers": ["F.Cu"], "min_width_mm": 0.6}}, tracks)
    clr = lambda a, b: 0.2  # noqa: E731
    # an outer track at its outer floor is left alone by a larger inner floor
    assert routing.min_width_fix(path, {"M": 0.6}, clr, ["F.Cu", "B.Cu"], 0.6, box(0, 0, 40, 20), {},
                                 {"M": 1.0}) == ([], [])
    tracks = [{"net": "M", "layer": "In1.Cu", "w": 0.6, "x1": 10, "y1": 10, "x2": 15, "y2": 10}]
    d3, libs, path3 = _slit_board(tmp_path_factory.mktemp("inner"), {"M": {"layers": ["F.Cu"], "min_width_mm": 0.6}}, tracks)
    wide, lost = routing.min_width_fix(path3, {"M": 0.6}, clr, ["F.Cu", "B.Cu", "In1.Cu"], 0.6, box(0, 0, 40, 20), {},
                                       {"M": 1.0})
    assert (wide, lost) == (["M"], [])
    assert [s["w"] for s in pcb.read_board(path3)["segments"] if s["net"] == "M"] == [1.0]
