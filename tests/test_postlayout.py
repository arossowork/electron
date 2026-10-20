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


def test_isolation_rules_come_from_design_data(tmp_path):
    """Backlog 38: board.isolation (net group, clearance, creepage, req) becomes KiCad DRC rules in the .kicad_dru,
    the measured distance of the group is reported next to its limit, and the requirement is verified by trace."""
    import json

    import yaml

    from hwflow.cli import _design_hash, isolation_distances, trace
    from hwflow.design import Design
    from hwflow.pcb import isolation_groups, write_project
    raw = {"project": "iso", "board": {"layers": 2, "isolation": [
        {"name": "mains", "class": "mains", "nets": "HV_*", "clearance_mm": 4.0, "creepage_mm": 6.0, "req": "R8"}]},
        "nets": {"AC_L": {"class": "mains"}, "AC_N": {"class": "mains"}},
        "components": {"J1": {"pins": {"1": "AC_L", "2": "AC_N", "3": "HV_X"}}, "U1": {"pins": {"1": "VCC"}}}}
    (tmp_path / "design.yaml").write_text(yaml.safe_dump(raw))
    d = Design.load(tmp_path)
    (g,) = isolation_groups(d)
    assert g["members"] == ["AC_L", "AC_N", "HV_X"]
    cond = "(A.NetName == 'AC_L' || A.NetName == 'AC_N' || A.NetName == 'HV_X') && B.NetName != 'AC_L' && " \
           "B.NetName != 'AC_N' && B.NetName != 'HV_X'"
    assert g["kicad"] == [f'(rule "mains clearance" (constraint clearance (min 4mm)) (condition "{cond}"))',
                          f'(rule "mains creepage" (constraint creepage (min 6mm)) (condition "{cond}"))']
    write_project(tmp_path, "iso", d)
    dru = (tmp_path / "iso.kicad_dru").read_text()
    assert dru.startswith("(version 1)\n" + "\n".join(g["kicad"]) + "\n")
    assert dru.count("(constraint clearance (min 6mm))") == 1  # L11: creepage as a track clearance for the router
    # measured distance: AC_N track 2.25 mm from VCC (limit 4.0), one DRC creepage error on the group
    segs = [{"net": "AC_L", "layer": "F.Cu", "x1": 0, "y1": 0, "x2": 10, "y2": 0, "w": 1.0},
            {"net": "AC_N", "layer": "F.Cu", "x1": 0, "y1": 2, "x2": 10, "y2": 2, "w": 1.0},
            {"net": "VCC", "layer": "F.Cu", "x1": 0, "y1": 5, "x2": 10, "y2": 5, "w": 0.5}]
    ex = SimpleNamespace(d=d, path=None, board={"segments": segs, "vias": [], "footprints": {}})
    import hwflow.postlayout as pl
    orig, pl.Extraction = pl.Extraction, lambda design, board: ex
    try:
        drc = [{"sev": "error", "type": "creepage", "msg": "Creepage violation", "items": ["Track [AC_N] on F.Cu"]},
               {"sev": "error", "type": "clearance", "msg": "x", "items": ["Track [VCC] on F.Cu", "Pad [SIG]"]}]
        (r,) = isolation_distances(d, tmp_path / "iso.kicad_pcb", drc)
    finally:
        pl.Extraction = orig
    assert r["mm"] == pytest.approx(2.25) and r["limit"] == 4.0 and r["creepage_mm"] == 6.0 and r["req"] == "R8"
    assert (r["drc_clearance"], r["drc_creepage"], r["ok"]) == (0, 1, False)
    (tmp_path / "requirements.yaml").write_text(yaml.safe_dump({"requirements": [
        {"id": "R8", "text": "mains isolation", "verification": "layout"}]}))
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "status.json").write_text(json.dumps({"layout": {
        "ok": False, "isolation": [r], "design_hash": _design_hash(d)}}))
    (row,) = trace(d)["rows"]
    assert row["status"] == "FAIL" and row["measures"] == ["isolation mains 2.25/4.0 mm:FAIL"]
    # a design without board.isolation writes the same .kicad_dru as before (only board.drc_rules)
    d.raw["board"] = {"drc_rules": ["(rule \"x\" (constraint clearance (min 1mm)))"]}
    write_project(tmp_path, "iso", d)
    assert (tmp_path / "iso.kicad_dru").read_text() == "(version 1)\n(rule \"x\" (constraint clearance (min 1mm)))\n"


def test_si_screen_flags_long_lines_plane_gaps_and_decap_loops():
    """Backlog 48: for nets with edge_ns, the delay of the longest copper path against t_r/6, vias and branch points,
    trace length without a reference plane, and the decap loop inductance at 0.35/t_r against si_pdn_z_ohm."""
    from pathlib import Path

    import numpy as np

    from hwflow.design import Component, Design
    from hwflow.postlayout import si_screen
    raw = {"board": {"layers": 4}, "nets": {"CLK": {"edge_ns": 0.5}, "GND": {"class": "ground"}},
           "components": {"U1": {"pins": {"1": "CLK", "2": "VCC", "3": "GND"}}, "R1": {"pins": {"1": "CLK"}},
                          "J1": {"pins": {"1": "CLK"}}, "C1": {"pins": {"1": "VCC", "2": "GND"}, "decouples": "U1"}}}
    d = Design(path=Path("x/design.yaml"), raw=raw)
    d.components = {r: Component(r, c) for r, c in raw["components"].items()}
    uf, elements, segs = UF(), [], []
    td, z0 = 6.7e-12, 50.0                                       # s/mm and ohm of a microstrip on FR-4

    def trace(net, a, b, x1, y1, x2, y2, l_h=None):
        n = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        L = l_h if l_h is not None else td * n * z0
        elements.append({"net": net, "a": a, "b": b, "R": 0.01, "L": L, "C": (td * n) ** 2 / L, "len": n})
        segs.append({"net": net, "layer": "F.Cu", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "w": 0.2, "len": n})
    u1, r1, j1, t = ("pad", "U1", "1"), ("pad", "R1", "1"), ("pad", "J1", "1"), ("p", 50.0, 0.0, "F.Cu")
    trace("CLK", u1, t, 0, 0, 50, 0)
    trace("CLK", t, r1, 50, 0, 100, 0)
    trace("CLK", t, j1, 50, 0, 50, 5)                            # a 5 mm stub at the T
    c1, u2 = ("pad", "C1", "1"), ("pad", "U1", "2")
    trace("VCC", c1, u2, 0, 10, 10, 10, l_h=10e-9)               # 10 mm, 10 nH (the C5 case)
    adj = {}
    for e in elements:
        ra, rb = uf.find(e["a"]), uf.find(e["b"])
        adj.setdefault(ra, []).append((rb, e)), adj.setdefault(rb, []).append((ra, e))
    pads = {(p[1], p[2]): p for p in (u1, r1, j1, c1, u2)}

    def path_len(a, b):
        return 10.0 if {a, b} == {c1, u2} else None
    ex = SimpleNamespace(elements=elements, segs=segs, uf=uf, adj=adj, pad_nodes=pads, grounds={"GND"},
                         board={"vias": [{"net": "CLK", "x": 70, "y": 0}]}, path=None, path_len=path_len)
    plane = [{"net": "GND", "layer": "In1.Cu", "pts": np.array([[-1, 60, 60, -1], [-10, -10, 20, 20.0]])}]
    rows = {r["check"]: r for r in si_screen(d, ex, fills=plane)}
    ln = rows["length"]
    assert ln["sev"] == "warn" and ln["mm"] == pytest.approx(100) and ln["delay_ns"] == pytest.approx(0.67)
    assert (ln["vias"], ln["stubs"]) == (1, 1)
    assert rows["plane-gap"]["mm"] == pytest.approx(40 - 1.4, abs=0.3)  # own via antipad is no gap
    assert rows["plane-gap"]["layer"] == "F.Cu"
    dl = rows["decap-loop"]
    assert dl["sev"] == "warn" and dl["nH"] == pytest.approx(10) and dl["z_ohm"] == pytest.approx(44.0, rel=0.01)
    # a slow edge (100 ns) makes the same copper short and the loop fine
    raw["nets"]["CLK"]["edge_ns"] = 100
    rows = {r["check"]: r for r in si_screen(d, ex, fills=plane)}
    assert rows["length"]["sev"] == "info" and rows["decap-loop"]["sev"] == "info"
    raw["nets"].pop("CLK")
    assert si_screen(d, ex, fills=plane) == []                   # nothing declared, nothing screened


def test_split_tjunctions_is_bounded():
    """Overlapping same-net tracks (a router leaves them where it fails) put many endpoints inside other segments;
    the extraction used to restart its scan after every split (minutes for 1400 segments on a real board)."""
    import time
    from hwflow.postlayout import _split_tjunctions
    segs = [{"net": "N", "layer": "F.Cu", "w": 0.25, "x1": i * 0.4, "y1": 0.0, "x2": i * 0.4 + 30.0, "y2": 0.0}
            for i in range(150)]
    segs += [{"net": "M", "layer": "F.Cu", "w": 0.25, "x1": 5.0, "y1": 1.0, "x2": 25.0, "y2": 1.0}]
    t0 = time.time()
    out = _split_tjunctions(segs)
    assert time.time() - t0 < 2.0
    xs = sorted({round(p, 3) for s in segs[:150] for p in (s["x1"], s["x2"])})
    n = [s for s in out if s["net"] == "N"]
    assert {round(p, 3) for s in n for p in (s["x1"], s["x2"])} == set(xs)  # every endpoint is a vertex
    assert sum(1 for s in out if s["net"] == "M") == 1
