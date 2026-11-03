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
    assert s["failed"] == ["pcb:b"] and "KiCadRoutingTools" in s["reference_d3a_fair95"]


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
