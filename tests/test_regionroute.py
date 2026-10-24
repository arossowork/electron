"""Hierarchical routing (regionroute): the net partition, the per-group cache key, and a real router run that reuses
the copper of the group a change leaves alone."""
import pytest
import yaml
from shapely.geometry import box

from hwflow.layout.board import pcb
from hwflow import placer
from hwflow.layout.route import regionroute
from hwflow.core.design import Design
from hwflow.core.libs import Libs

needs_router = pytest.mark.skipif(not (pcb.krt_dir() / "py_router" / "route.py").exists(), reason="KiCadRoutingTools not installed")


def two_regions(tmp_path, r1x=4.0):
    """Two isolated regions on a 40x20 board: hv (HV_A, HV_B between R1 and R2) and lv (LV_A, LV_B between R3 and R4),
    every part fixed."""
    def res(pins, x, y, rot=0):
        return {"value": "10k", "symbol": "Device:R", "footprint": "Resistor_SMD:R_0603_1608Metric", "spice": False,
                "pins": {"1": pins[0], "2": pins[1]}, "placement": {"x": x, "y": y, "rot": rot, "fixed": True}}
    raw = {"project": "rr", "board": {"width_mm": 40, "height_mm": 20, "layers": 2, "gnd_pour": False,
                                      "isolation": [{"name": "hv-lv", "nets": "HV_*", "clearance_mm": 2.0}]},
           "floorplan": {"regions": [{"name": "hv", "kind": "mains", "rect": [0, 0, 14, 20], "nets": ["HV_*"]},
                                     {"name": "lv", "kind": "selv", "rect": [20, 0, 40, 20], "nets": ["LV_*"]}]},
           "nets": {"HV_A": {"class": "signal"}, "HV_B": {"class": "signal"}},
           "components": {"R1": res(("HV_A", "HV_B"), r1x, 5), "R2": res(("HV_A", "HV_B"), 10, 15, 90),
                          "R3": res(("LV_A", "LV_B"), 25, 5), "R4": res(("LV_A", "LV_B"), 34, 15, 90)}}
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    return Design.load(tmp_path)


def test_partition_region_rest_and_interface_nets():
    seeds = [{"name": "hv", "members": {"HV_A", "HV_C"}, "poly": box(0, 0, 10, 10)}]
    pads = {"HV_A": [(1, 1), (9, 9)], "HV_C": [(1, 1), (15, 1)], "LV_A": [(20, 1), (30, 1)], "LV_B": [(5, 5), (20, 5)],
            "SIG": [(25, 5), (26, 5)]}
    got = regionroute.partition(pads, pads, seeds)
    assert [(g["name"], g["kind"], g["nets"]) for g in got] == [
        ("hv", "region", ["HV_A"]), ("rest", "rest", ["LV_A", "SIG"]), ("interface", "interface", ["HV_C", "LV_B"])]
    # no seed: one group, routed as one job
    assert [g["name"] for g in regionroute.partition(pads, pads, [])] == ["rest"]


def test_seeds_merge_a_region_and_the_isolation_group_on_the_same_nets(tmp_path):
    raw = {"project": "s", "board": {"width_mm": 40, "height_mm": 30, "layers": 2,
                                     "isolation": [{"name": "mains-SELV", "nets": ["AC_L"], "clearance_mm": 6}]},
           "floorplan": {"regions": [{"name": "mains", "kind": "mains", "rect": [0, 0, 10, 30], "nets": ["AC_*"]}]},
           "nets": {"AC_L": {"class": "mains"}, "AC_N": {"class": "mains"}, "SIG": {"class": "signal"}}}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    (s,) = regionroute.seeds(Design.load(tmp_path))
    assert s["name"] == "mains" and s["members"] == {"AC_L", "AC_N"} and s["poly"].bounds == (0, 0, 10, 30)


def test_group_key_changes_only_for_the_group_whose_pads_move(tmp_path):
    def keys(d):
        libs = Libs()
        parts = placer.build_parts(d, libs)
        f = tmp_path / "b.kicad_pcb"
        pcb.write_board(f, pcb.build_board(d, libs, parts))
        b = pcb.read_board(f)
        nets = list(pcb.net_classes(d))
        groups = regionroute.partition(nets, regionroute.pads_by_net(b), regionroute.seeds(d))
        assert [g["nets"] for g in groups] == [["HV_A", "HV_B"], ["LV_A", "LV_B"]]
        return [regionroute.group_key(d, g["nets"], b, nets, "", parts) for g in groups]
    k0 = keys(two_regions(tmp_path / "a"))
    k1 = keys(two_regions(tmp_path / "b", r1x=5.0))
    assert k0[0] != k1[0] and k0[1] == k1[1]


@needs_router
def test_moving_a_part_in_one_region_reroutes_only_that_group(tmp_path):
    from hwflow.pipeline import stage_layout
    res = stage_layout(two_regions(tmp_path), Libs(), seeds=1, iters=500)
    assert [(g["name"], g["cache"]) for g in res["route_groups"]] == [("hv", "miss"), ("lv", "miss")]
    assert res["unrouted"] == 0 and "2 route groups" in res["router"]
    res = stage_layout(two_regions(tmp_path, r1x=5.0), Libs(), seeds=1, iters=500)  # R1 (hv) 1 mm to the right
    assert [(g["name"], g["cache"]) for g in res["route_groups"]] == [("hv", "miss"), ("lv", "hit")]
    assert res["unrouted"] == 0 and res["drc_errors"] == 0
    segs = pcb.read_board(tmp_path / "build" / "rr.kicad_pcb")["segments"]
    assert {s["net"] for s in segs} == {"HV_A", "HV_B", "LV_A", "LV_B"}
