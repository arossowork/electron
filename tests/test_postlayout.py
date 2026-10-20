"""Post-layout parasitics: the transient netlist is reduced to the pads with exact DC resistances."""
from types import SimpleNamespace

import pytest

from hwflow.postlayout import UF, PostLayoutNodeMap


def _stub(elements, pads):
    uf = UF()
    for e in elements:
        uf.find(e["a"]), uf.find(e["b"])
    names = {}

    def node_name(n):
        r = uf.find(n)
        return f"x_{r[1]}_{r[2]}".lower() if r[0] == "pad" else names.setdefault(r, f"x_n{len(names)}")

    ex = SimpleNamespace(elements=elements, uf=uf, pad_nodes={(p[1], p[2]): p for p in pads}, node_name=node_name)
    return SimpleNamespace(ex=ex, _is_ideal=lambda net: False)


def test_lumped_star_becomes_exact_delta():
    a, b, c, mid = ("pad", "U1", "1"), ("pad", "C1", "1"), ("pad", "R1", "2"), ("p", 1.0, 1.0, "F.Cu")
    elements = [{"net": "N", "a": a, "b": mid, "R": 1.0, "C": 1e-12},
                {"net": "N", "a": mid, "b": b, "R": 2.0, "C": 2e-12},
                {"net": "N", "a": mid, "b": c, "R": 3.0, "C": 3e-12}]
    lines = PostLayoutNodeMap._lumped(_stub(elements, [a, b, c]))
    rs = {frozenset(l.split()[1:3]): float(l.split()[3]) for l in lines if l.startswith("Rpx")}
    assert rs[frozenset(("x_u1_1", "x_c1_1"))] == pytest.approx(11 / 3)
    assert rs[frozenset(("x_u1_1", "x_r1_2"))] == pytest.approx(11 / 2)
    assert rs[frozenset(("x_c1_1", "x_r1_2"))] == pytest.approx(11 / 1)
    assert not [l for l in lines if l.startswith("C")]   # trace C is left out of transients
    assert "x_n0" not in " ".join(lines)          # no internal trace nodes are left


def test_lumped_skips_islands_without_pads():
    a, p1, p2 = ("pad", "J1", "1"), ("p", 0.0, 0.0, "F.Cu"), ("p", 1.0, 0.0, "F.Cu")
    elements = [{"net": "N", "a": p1, "b": p2, "R": 0.01, "C": 1e-13}]
    lines = PostLayoutNodeMap._lumped(_stub(elements, [a]))
    assert lines == [lines[0]]


def test_group_clearance_reports_the_closest_copper():
    """Backlog 39: the clearance between a net group and every other net is reported as a number with its limit."""
    from hwflow.postlayout import _seg_dist, group_clearances
    import numpy as np
    assert _seg_dist(np.array([[0, 0, 1, 1.0]]), np.array([[0, 1, 1, 0.0]]))[0, 0] == 0          # crossing
    assert _seg_dist(np.array([[0, 0, 1, 0.0]]), np.array([[2, 3, 2, 5.0]]))[0, 0] == pytest.approx(10 ** 0.5)
    rule = ("(rule \"mains\" (constraint clearance (min 4.0mm)) "
            "(condition \"A.NetName == 'AC_*' && B.NetName != 'AC_*'\"))")
    board = {"layers": 2, "via_dia_mm": 0.6, "drc_rules": [rule]}
    d = SimpleNamespace(board=board)
    segs = [{"net": "AC_L", "layer": "F.Cu", "x1": 0, "y1": 0, "x2": 10, "y2": 0, "w": 1.0},
            {"net": "AC_N", "layer": "F.Cu", "x1": 0, "y1": 2, "x2": 10, "y2": 2, "w": 1.0},   # same group: ignored
            {"net": "VCC", "layer": "F.Cu", "x1": 0, "y1": 5, "x2": 10, "y2": 5, "w": 0.5},
            {"net": "GND", "layer": "B.Cu", "x1": 0, "y1": 0.5, "x2": 10, "y2": 0.5, "w": 0.5}]  # other layer
    pad = {"number": "1", "net": "SIG", "x": 20, "y": 0, "w": 2, "h": 2, "layers": ["F.Cu"]}
    ex = SimpleNamespace(d=d, path=None, board={"segments": segs, "vias": [], "footprints": {"U1": {"pads": [pad]}}})
    (r,) = group_clearances(d, ex)
    assert r["mm"] == pytest.approx(2.25) and r["limit"] == 4.0 and not r["ok"]
    assert r["between"] == "AC_N / VCC on F.Cu"


def test_every_pad_node_of_a_net_exists_and_is_connected():
    """Backlog 46: a pad whose island holds no other pad (zone joined) keeps a node, tied to the rest of the net."""
    a, b, c, mid = ("pad", "U1", "1"), ("pad", "C1", "1"), ("pad", "BT1", "2"), ("p", 1.0, 1.0, "F.Cu")
    z = ("p", 5.0, 5.0, "F.Cu")
    elements = [{"net": "N", "a": a, "b": mid, "R": 1.0, "C": 1e-12},
                {"net": "N", "a": mid, "b": b, "R": 2.0, "C": 2e-12},
                {"net": "N", "a": c, "b": z, "R": 0.5, "C": 1e-12}]     # BT1.2 alone on its island
    st = _stub(elements, [a, b, c])
    nm = PostLayoutNodeMap.__new__(PostLayoutNodeMap)
    adj = {st.ex.uf.find(n): [1] for n in (a, b, c, mid, z)}
    nm.ex = SimpleNamespace(**vars(st.ex), adj=adj)
    nm.grounds = set()
    comps = {"U1": {"1": "N"}, "C1": {"1": "N"}, "BT1": {"2": "N"}}
    nm.d = SimpleNamespace(nets=lambda: {"N": [("U1", "1"), ("C1", "1"), ("BT1", "2")]},
                           components={r: SimpleNamespace(pins=p) for r, p in comps.items()},
                           board={"gnd_pour": True})
    lines = nm._tie_islands(PostLayoutNodeMap._lumped(nm))
    assert any(ln.startswith("Rtie") and "x_bt1_2" in ln for ln in lines)
    assert sum(ln.startswith("Rtie") for ln in lines) == 1


def test_fast_nets_keep_their_full_network_in_transients():
    """Backlog 47 (b): a net marked fast (class: fast or edge_ns) is not lumped; other nets are."""
    a, b, mid = ("pad", "U1", "1"), ("pad", "C1", "1"), ("p", 1.0, 1.0, "F.Cu")
    c, e = ("pad", "U1", "2"), ("pad", "R1", "1")
    elements = [{"net": "CLK", "a": a, "b": mid, "R": 1.0, "C": 1e-12, "L": 1e-9},
                {"net": "CLK", "a": mid, "b": b, "R": 2.0, "C": 2e-12, "L": 1e-9},
                {"net": "SLOW", "a": c, "b": e, "R": 3.0, "C": 3e-12, "L": 1e-9}]
    st = _stub(elements, [a, b, c, e])
    nm = PostLayoutNodeMap.__new__(PostLayoutNodeMap)
    nm.ex = SimpleNamespace(**vars(st.ex), adj={st.ex.uf.find(n): [1] for n in (a, b, c, e, mid)})
    nm.grounds, nm.with_l, nm.lump = set(), True, None
    pins = {"U1": {"1": "CLK", "2": "SLOW"}, "C1": {"1": "CLK"}, "R1": {"1": "SLOW"}}
    info = {"CLK": {"edge_ns": 2}}
    nm.d = SimpleNamespace(nets=lambda: {"CLK": [("U1", "1"), ("C1", "1")], "SLOW": [("U1", "2"), ("R1", "1")]},
                           components={r: SimpleNamespace(pins=p) for r, p in pins.items()},
                           board={"gnd_pour": True}, net_info=lambda n: info.get(n, {}))
    lines = nm.extra({"cmd": "tran 1n 1u"})
    assert sum(ln.startswith("Rfx") for ln in lines) == 2 and sum(ln.startswith("Cfx") for ln in lines) == 4
    assert [sorted(ln.split()[1:3]) for ln in lines if ln.startswith("Rpx")] == [["x_r1_1", "x_u1_2"]]
    info.clear()
    assert not any(ln.startswith(("Rfx", "Cfx")) for ln in nm.extra({"cmd": "tran 1n 1u"}))
