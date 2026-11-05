"""The real-board benchmark glue (scripts/pcbench.py, scripts/layout_bench.py --pcbench): HPWL and the route and
place summaries, on hand-made rows (no KiCad, no router)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import layout_bench  # noqa: E402
import pcbench  # noqa: E402


def test_board_hpwl_counts_nets_with_two_or_more_pads():
    b = {"footprints": {"R1": {"pads": [{"net": "A", "x": 0, "y": 0}, {"net": "B", "x": 1, "y": 1}]},
                        "R2": {"pads": [{"net": "A", "x": 3, "y": 4}, {"net": "", "x": 9, "y": 9}]}}}
    assert pcbench.board_hpwl(b) == 7.0  # A: 3 + 4; B has one pad; the unconnected pad is no net


def run(**k):
    return {"runs": [{"wall_s": 5.0, "phase_s": {"router": 1.0, "placer": 2.0}, **k}]}


def test_route_summary_follows_pcbworld_rules():
    doc = {"designs": {"pcb:a": run(routability=1.0, drv_after=0, track_mm=100, vias_board=2, fixed_errors=1),
                       "pcb:b": run(routability=0.5, drv_after=3, track_mm=50, vias_board=9, fixed_errors=0),
                       "pcbp:a": run(routability=1.0, drv_after=0), "other": run(routability=0.0)}}
    s = layout_bench.pcbench_summary(doc)
    assert s["boards"] == 2 and s["routability"] == 0.75 and s["clean_pass"] == 0.5
    assert (s["drv"], s["wl_mm"], s["vias"]) == (0, 100, 2)  # means over the fully routed boards only
    assert s["failed"] == ["pcb:b"] and "KiCadRoutingTools" in s["reference"] and s["set"] == "D3-A"


def test_place_summary_compares_with_the_human_board():
    human = {"hpwl_mm": 200.0, "track_mm": 100.0, "vias": 4}
    doc = {"designs": {"pcbp:a": {**run(routability=1.0, drv_after=0, hpwl_mm=180.0, track_mm=90.0, vias_board=6), "human": human},
                       "pcbp:b": {**run(routability=0.0, blocked="not routed: overlap"), "human": human},
                       "pcbp:c": {**run(routability=0.9, drv_after=0, hpwl_mm=100.0), "human": human},
                       # routed on an illegal placement: the placer's violation fails it, one between fixed parts does not
                       "pcbp:d": {**run(routability=1.0, drv_after=0, placement_violations=["R1 pad 1 copper closer"],
                                        violations_placer=1), "human": human},
                       "pcbp:e": {**run(routability=1.0, drv_after=0, hpwl_mm=180.0, track_mm=90.0, vias_board=6,
                                        placement_violations=["courtyard overlap J1/J2"], violations_placer=0), "human": human}}}
    s = layout_bench.pcbench_place_summary(doc)
    assert s["boards"] == 5 and s["legal"] == 0.6 and s["clean_pass"] == 0.4
    assert (s["hpwl_ratio"], s["wl_ratio"], s["vias_delta"]) == (0.9, 0.9, 2)  # clean boards only
    assert s["blocked"] == ["pcbp:b", "pcbp:d"] and s["failed"] == ["pcbp:c"] and s["inherited"] == ["pcbp:e"]
    assert "HPWL x0.90" in layout_bench.pcbench_place_table(s)


def test_d3_sets_keep_d3a_paths_and_separate_the_others(tmp_path):
    assert pcbench.set_dirs(tmp_path)["designs"] == tmp_path / "designs"  # D3-A unchanged
    assert pcbench.set_dirs(tmp_path, "d3b")["designs"] == tmp_path / "designs_d3b"
    assert pcbench.set_dirs(tmp_path, "d3c")["manifest"] == tmp_path / "manifest_d3c.json"
    assert [pcbench.SETS[k]["key"] for k in ("d3a", "d3b", "d3c")] == ["easy", "medium", "hard"]
    doc = {"pcbench_set": "d3b", "designs": {"pcb:a": run(routability=1.0, drv_after=0),
                                             "pcb:b": {"runs": [{"timeout": True, "wall_s": 9.0}]}}}
    s = layout_bench.pcbench_summary(doc)
    assert s["set"] == "D3-B" and s["reference"]["Freerouting"]["clean_pass"] == 0.78
    assert s["timeouts"] == ["pcb:b"] and s["routability"] == 0.5  # a timeout counts 0
    assert "timeouts (1" in layout_bench.pcbench_table(s)


def test_replay_fixes_every_part_at_the_saved_placement():
    raw = {"components": {"J1": {"placement": {"x": 1, "y": 2, "rot": 0, "fixed": True}},
                          "R1": {"value": "1k"}, "U1": {"placement": {"side": "back"}}}}
    saved = {"__board_mm__": [10, 10], "J1": [1, 2, 0], "R1": [3.123456, 4, 90], "U1": [5, 6, 180]}
    assert layout_bench.fix_at(raw, saved) == ["R1", "U1"]
    assert raw["components"]["R1"]["placement"] == {"x": 3.1235, "y": 4.0, "rot": 90.0, "fixed": True}
    assert raw["components"]["U1"]["placement"]["side"] == "back"  # other placement keys stay
    assert raw["components"]["J1"]["placement"]["fixed"] is True
