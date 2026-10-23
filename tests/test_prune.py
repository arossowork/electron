"""Removal of dangling router leftovers (prune.py)."""
from hwflow import prune


def _t(x1, y1, x2, y2, net="SW", layer="F.Cu"):
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "w": 0.3, "layer": layer, "net": net}


def test_dangling_via_and_track_are_removed_and_nothing_else():
    tracks = [_t(0, 0, 1, 0), _t(1, 0, 1.05, 0), _t(1, 0, 1.05, 0, layer="B.Cu"), _t(5, 5, 6, 5, net="GND")]
    vias = [{"x": 3.0, "y": 3.0, "net": "SW"}, {"x": 3.0, "y": 3.0, "net": "GND"}, {"x": 1.0, "y": 0.0, "net": "SW"}]
    drc = [{"sev": "warning", "type": "via_dangling", "items": ["Via [SW] on F.Cu - B.Cu"], "at": [[3.0, 3.0]]},
           {"sev": "warning", "type": "track_dangling", "items": ["Track [SW] on F.Cu, length 0.05 mm"],
            "at": [[1.05, 0.0]]}]
    tr, vi, n = prune.dangling(tracks, vias, drc)
    assert n == 2
    assert vi == vias[1:]                     # the GND via at the same point stays
    assert tr == [tracks[0], tracks[2], tracks[3]]  # the B.Cu piece is another layer


def test_a_removal_that_adds_errors_is_undone():
    tracks, vias = [_t(0, 0, 1, 0)], []
    drc = [{"sev": "warning", "type": "track_dangling", "items": ["Track [SW] on F.Cu"], "at": [[1.0, 0.0]]}]
    calls = []

    def check(tr, vi):
        calls.append(len(tr))
        return [{"sev": "error", "type": "unconnected", "items": [], "at": []}] if not tr else []

    tr, vi, d, n = prune.prune(tracks, vias, drc, check)
    assert (tr, n, d) == (tracks, 0, drc) and calls == [0, 1]   # the kept board is written back
