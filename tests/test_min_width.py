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
