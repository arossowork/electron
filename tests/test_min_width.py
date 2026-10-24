from types import SimpleNamespace

from hwflow import pcb


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
    assert st == {"R13": "pass", "R1": "layout?"} and t["evidence_only"] == ["R1"]


def test_shared_library_is_searched_after_project_libraries():
    from hwflow.core.libs import Libs, shared_lib, shared_lib_dirs
    SHARED_LIB = shared_lib()
    if not (SHARED_LIB / "footprints" / "hw.pretty").is_dir():
        return
    libs = Libs()
    assert SHARED_LIB / "footprints" in shared_lib_dirs() and SHARED_LIB / "footprints" in libs.extra
    assert libs.footprint("hw:Relay_Panasonic_ADW1_1coil")["pads"]
    assert "hw:Relay_Panasonic_ADW1_1coil" in libs.search_footprints("ADW1")
