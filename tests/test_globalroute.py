"""Global router (PathFinder on g-cells) and pin access on small synthetic boards."""
from test_barrier import ALL, Libs, _design, _pad, _zone

from hwflow import globalroute as G


def _slit_wall(gap, yc=10.5, thick=2.0):
    """Two through-hole pads that wall the board at x = 15 with a slit of `gap` mm (copper to copper) at y = yc."""
    lo, hi = yc - gap / 2, yc + gap / 2
    return [_pad("1", 15, (lo - 1) / 2, thick, lo + 1, ALL, "thru_hole"),
            _pad("2", 15, (hi + 21) / 2, thick, 21 - hi, ALL, "thru_hole")]


def _crossing(tmp_path, gap, widths=None, nets=("S1", "S2"), one_layer=True):
    """Two nets across the wall; `one_layer`: both kept on F.Cu (nets.<n>.layers), so only the F.Cu slit counts."""
    widths = {n: dict((widths or {}).get(n, {}), **({"layers": ["F.Cu"]} if one_layer else {})) for n in nets}
    fps = {"A": ([_pad("1", 4, 7.5, 1, 1), _pad("2", 4, 13.5, 1, 1)], ""),
           "B": ([_pad("1", 26, 7.5, 1, 1), _pad("2", 26, 13.5, 1, 1)], ""),
           "W": (_slit_wall(gap), "")}
    pins = {"1": nets[0], "2": nets[1]} if len(nets) == 2 else {"1": nets[0]}
    comps = {"U1": ("A", pins, True), "U2": ("B", pins, True), "W1": ("W", {"1": "X1", "2": "X2"}, True)}
    return _design(tmp_path, comps, widths), Libs(fps)


def test_two_nets_through_a_one_track_slit_overflow_and_a_two_track_slit_fits(tmp_path):
    # free run = gap - 2 x 0.2 clearance; tracks of 0.2 at 0.2 spacing: 0.9 mm -> 1 track, 1.3 mm -> 2 tracks
    d, libs = _crossing(tmp_path, 0.9)
    r = G.global_route(d, libs, pitch=1.0)
    assert r.total_overflow > 0 and set(r.failing()) == {"S1", "S2"}
    assert all(e[1] in ("F.Cu", "B.Cu") for e in r.overflow_edges)
    d, libs = _crossing(tmp_path, 1.3)
    r = G.global_route(d, libs, pitch=1.0)
    assert r.total_overflow == 0 and r.failing() == []
    assert all(r.nets[n]["ok"] and r.nets[n]["layers"] == ["F.Cu"] for n in ("S1", "S2"))
    d, libs = _crossing(tmp_path, 0.9, one_layer=False)  # both layers: one net per layer's slit
    r = G.global_route(d, libs, pitch=1.0)
    assert r.failing() == [] and r.nets["S1"]["vias"] + r.nets["S2"]["vias"] >= 2


def test_a_wide_net_uses_more_capacity(tmp_path):
    # 1.3 mm slit holds two 0.2 mm tracks; a 0.6 mm net needs (0.6 + 0.2) / 0.4 = 2 of them
    d, libs = _crossing(tmp_path, 1.3, {"S1": {"width_mm": 0.6}})
    r = G.global_route(d, libs, pitch=1.0)
    assert r.total_overflow > 0 and "S1" in r.failing()


def test_a_keepout_across_the_board_leaves_no_path(tmp_path):
    fps = {"A": ([_pad("1", 4, 10, 1, 1)], ""), "B": ([_pad("1", 26, 10, 1, 1)], ""),
           "KO": ([], _zone(14, -1, 16, 21))}
    d = _design(tmp_path, {"U1": ("A", {"1": "SIG"}, True), "U2": ("B", {"1": "SIG"}, True), "K": ("KO", {}, True)})
    r = G.global_route(d, Libs(fps), pitch=1.0)
    assert not r.nets["SIG"]["ok"] and r.failing() == ["SIG"]
    assert "NO PATH" in G.report(r)
    fps["KO"] = ([], _zone(14, -1, 16, 21, layers='"F.Cu"'))  # one layer only: vias go around
    r = G.global_route(d, Libs(fps), pitch=1.0)
    assert r.nets["SIG"]["ok"] and r.nets["SIG"]["vias"] >= 2 and r.failing() == []


def test_order_puts_the_most_critical_net_first_and_variants_keep_the_count(tmp_path):
    d, libs = _crossing(tmp_path, 1.3, {"S1": {"width_mm": 0.6}})
    r = G.global_route(d, libs, pitch=1.0)
    o = G.order(r, ["S2", "S1", "OTHER"])
    assert r.nets[o[0]]["crit"] >= r.nets[o[1]]["crit"] and set(o[:2]) == {"S1", "S2"} and o[-1] == "OTHER"
    assert min(r.nets[n]["crit"] for n in r.failing()) >= 100  # overflow dominates the criticality
    base = [[], ["--ordering", "inside_out"], ["--mps-reverse-rounds"], ["--direction", "backward"]]
    v = G.variants(base)
    assert v[0] == G.ORIGINAL and len(v) == len(base) and v[1:] == base[:3]


def test_layer_hint_only_for_a_net_on_one_layer_without_vias(tmp_path):
    d, libs = _crossing(tmp_path, 1.3)
    r = G.global_route(d, libs, pitch=1.0)
    for n in ("S1", "S2"):
        h = G.layer_hint(r, n)
        assert h is None or (len(h) == 1 and r.nets[n]["vias"] == 0)
    r.nets["S1"]["vias"] = 2
    assert G.layer_hint(r, "S1") is None


def test_pin_access_counts_directions_and_finds_a_walled_in_pad(tmp_path):
    # P1 alone: every direction is open. P2 in a ring of foreign pads 0.3 mm away: no track of any width leaves.
    ring = [_pad(str(k + 2), 20 + dx, 10 + dy, 0.8, 0.8) for k, (dx, dy) in
            enumerate([(-1.1, -1.1), (0, -1.1), (1.1, -1.1), (-1.1, 0), (1.1, 0), (-1.1, 1.1), (0, 1.1), (1.1, 1.1)])]
    fps = {"A": ([_pad("1", 5, 10, 0.8, 0.8)], ""), "R": ([_pad("1", 20, 10, 0.8, 0.8)] + ring, "")}
    pins = {"1": "SIG"} | {str(k + 2): f"N{k}" for k in range(8)}
    d = _design(tmp_path, {"U1": ("A", {"1": "SIG"}, True), "U2": ("R", pins, True)})
    m = G._model(d, Libs(fps))
    acc = G.pin_access(d, m)
    assert acc[("U1", "1")]["n"] == G.ACCESS_DIRS and not acc[("U1", "1")]["no_access"]
    assert acc[("U2", "1")]["n"] == 0 and acc[("U2", "1")]["floor"] == 0 and acc[("U2", "1")]["no_access"]
    assert G.access_summary(acc, 1)[0]["ref"] == "U2"
    # the global router starts U1.1 from an access point and reports the walled-in pad
    r = G.route(G.build(d, Libs(fps), model=m, access=True, pitch=1.0))
    assert ("U2", "1") in r.no_access and "pads without access: U2.1" in G.report(r)


def test_the_gcell_follows_the_board_rules(tmp_path, monkeypatch):
    monkeypatch.delenv("HWFLOW_GROUTE_PITCH", raising=False)
    fps = {"A": ([_pad("1", 4, 10, 1, 1)], ""), "B": ([_pad("1", 26, 10, 1, 1)], "")}
    comps = {"U1": ("A", {"1": "SIG"}, True), "U2": ("B", {"1": "SIG"}, True)}
    d = _design(tmp_path, comps)                                       # 0.2 + 0.2 mm: 3 tracks = 1.2 mm
    assert abs(G.pitch(d) - 1.2) < 1e-9
    assert G.build(d, Libs(fps)).pitch == G.pitch(d) and G.build(d, Libs(fps)).nx == 25
    d = _design(tmp_path, comps, board={"track_mm": 0.6, "clearance_mm": 0.4})  # clamped to PITCH_MAX_MM
    assert G.pitch(d) == G.PITCH_MAX_MM
    monkeypatch.setenv("HWFLOW_GROUTE_PITCH", "2.0")
    assert G.pitch(d) == 2.0
