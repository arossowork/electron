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
