"""Footprint keep-out check that does not depend on `fp-lib-table`.

KiCad 10.0.7 drops the `footprints not_allowed` test (DRC type `items_not_allowed`, item "Footprint X") when the
footprint that carries the rule area has a library link that resolves through `fp-lib-table`, whatever the library
file contains. hwflow always runs DRC where the table exists, so those errors never showed. This module repeats that
one test in Python: the courtyard of every footprint (F.CrtYd or B.CrtYd, in board coordinates) against every rule
area that forbids footprints, on the layers the footprint sits on. Pads over areas that forbid pads are checked too (KiCad drops
those the same way). Tracks and vias are not repeated: KiCad reports them in both modes (tested for tracks).

It is on by default (user decision, 2026-10-20). `kicad.drc(..., keepout_strict=False)` or `HWFLOW_KEEPOUT_STRICT=0`
turns it off.
"""

from __future__ import annotations

import os
from pathlib import Path

from shapely.geometry import MultiPoint, Polygon

from . import sexpr
from .placer import rot


def enabled(flag: bool | None = None) -> bool:
    """The explicit flag wins; None reads HWFLOW_KEEPOUT_STRICT (0, false, no, off turn it off; default on)."""
    if flag is not None:
        return bool(flag)
    return os.environ.get("HWFLOW_KEEPOUT_STRICT", "").strip().lower() not in ("0", "false", "no", "off")


def _pts(node) -> list[tuple[float, float]]:
    return [(float(p[1]), float(p[2])) for p in sexpr.find_all(sexpr.find(node, "pts") or [], "xy")]


def _courtyard(fp: list, layer: str) -> list[tuple[float, float]]:
    """Footprint-local points of the courtyard drawn on `layer` (graphic items, converted to points)."""
    pts: list[tuple[float, float]] = []
    for g in fp:
        if not (isinstance(g, list) and g and str(g[0]) in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc")):
            continue
        if str(sexpr.value(g, "layer")) != layer:
            continue
        kind = str(g[0])
        if kind == "fp_poly":
            pts += _pts(g)
        elif kind in ("fp_line", "fp_rect"):
            s, e = sexpr.find(g, "start"), sexpr.find(g, "end")
            a, b = (float(s[1]), float(s[2])), (float(e[1]), float(e[2]))
            pts += [a, b] if kind == "fp_line" else [a, (b[0], a[1]), b, (a[0], b[1])]
        elif kind == "fp_circle":
            c, e = sexpr.find(g, "center"), sexpr.find(g, "end")
            cx, cy = float(c[1]), float(c[2])
            r = ((float(e[1]) - cx) ** 2 + (float(e[2]) - cy) ** 2) ** 0.5
            pts += [(cx + dx * r, cy + dy * r) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
        else:  # arc: its three defining points; the hull is a close, slightly small bound
            for k in ("start", "mid", "end"):
                n = sexpr.find(g, k)
                if n:
                    pts.append((float(n[1]), float(n[2])))
    return pts


def _rule_areas(tree: list, kinds: tuple = ("footprints", "pads")) -> list[dict]:
    """Every keep-out zone that forbids one of `kinds` (default footprints or pads): board level and inside footprints
    (stored in board coordinates)."""
    out = []

    def scan(parent, owner):
        for z in sexpr.find_all(parent, "zone"):
            ko = sexpr.find(z, "keepout")
            if not ko:
                continue
            forbids = {k for k in kinds if (sexpr.find(ko, k) or [0, 0])[1] == "not_allowed"}
            if not forbids:
                continue
            lay = sexpr.find(z, "layers") or sexpr.find(z, "layer") or []
            layers = [str(x) for x in lay[1:]]
            poly = sexpr.find(sexpr.find(z, "polygon") or [], "pts")
            pts = _pts(sexpr.find(z, "polygon") or []) if poly else []
            if len(pts) < 3:
                continue
            out.append({"name": str(sexpr.value(z, "name", "")), "owner": owner, "forbids": forbids, "layers": layers,
                        "poly": Polygon(pts).buffer(0)})

    scan(tree, None)
    for fp in sexpr.find_all(tree, "footprint"):
        ref = next((str(p[2]) for p in sexpr.find_all(fp, "property") if str(p[1]) == "Reference"), "?")
        scan(fp, ref)
    return out


def _on(layers: list[str], side: str) -> bool:
    return side in layers or "*.Cu" in layers


def violations(pcb: Path | str) -> list[dict]:
    """`items_not_allowed` violations (same dict shape as `kicad._violations`) for footprints whose courtyard
    overlaps a rule area that forbids footprints. One violation per footprint, for the first area it overlaps, as KiCad does. A footprint without courtyard is checked by the bounding box of its pads."""
    tree = sexpr.loads(Path(pcb).read_text())
    t = tree[0] if tree and isinstance(tree[0], list) and tree[0] and tree[0][0] == "kicad_pcb" else tree
    areas = _rule_areas(t)
    if not areas:
        return []
    out = []
    for fp in sexpr.find_all(t, "footprint"):
        ref = next((str(p[2]) for p in sexpr.find_all(fp, "property") if str(p[1]) == "Reference"), "?")
        at = sexpr.find(fp, "at")
        if not at:
            continue
        fx, fy, fr = float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0
        side = str(sexpr.value(fp, "layer", "F.Cu"))
        yard = "B.CrtYd" if side == "B.Cu" else "F.CrtYd"
        local = _courtyard(fp, yard) or _courtyard(fp, "B.CrtYd" if yard == "F.CrtYd" else "F.CrtYd")
        if not local:
            for p in sexpr.find_all(fp, "pad"):
                pa, sz = sexpr.find(p, "at"), sexpr.find(p, "size")
                if pa and sz:
                    px, py, w, h = float(pa[1]), float(pa[2]), float(sz[1]), float(sz[2])
                    local += [(px - w / 2, py - h / 2), (px + w / 2, py + h / 2)]
        if len(local) < 2:
            continue
        pts = []
        for x, y in local:
            dx, dy = rot(x, y, fr)
            pts.append((fx + dx, fy + dy))
        hull = MultiPoint(pts).convex_hull
        for a in areas:
            if a["owner"] == ref or not _on(a["layers"], side):
                continue
            if "footprints" in a["forbids"] and hull.intersection(a["poly"]).area > 1e-6:
                c = hull.centroid
                who = f" of {a['owner']}" if a["owner"] else ""
                out.append({"sev": "error", "type": "items_not_allowed",
                            "msg": f"Items not allowed (keepout area '{a['name']}'{who})",
                            "items": [f"Footprint {ref}"], "at": [[round(c.x, 4), round(c.y, 4)]]})
                break
    return out + _pad_violations(t, areas)


def _pad_violations(t: list, areas: list[dict]) -> list[dict]:
    """Pads (their rotated bounding box) over a rule area that forbids pads, on a copper layer of both."""
    out = []
    for fp in sexpr.find_all(t, "footprint"):
        ref = next((str(p[2]) for p in sexpr.find_all(fp, "property") if str(p[1]) == "Reference"), "?")
        at = sexpr.find(fp, "at")
        if not at:
            continue
        fx, fy, fr = float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0
        for p in sexpr.find_all(fp, "pad"):
            lay = [str(x) for x in (sexpr.find(p, "layers") or [])[1:]]
            cu = [l for l in lay if l.endswith(".Cu")]
            pa, sz = sexpr.find(p, "at"), sexpr.find(p, "size")
            if not cu or not pa or not sz:
                continue
            w, h = float(sz[1]) / 2, float(sz[2]) / 2
            ang = float(pa[3]) if len(pa) > 3 else 0.0
            ox, oy = float(pa[1]), float(pa[2])
            corners = [rot(ox + rot(cx, cy, ang)[0], oy + rot(cx, cy, ang)[1], fr)
                       for cx, cy in ((-w, -h), (w, -h), (w, h), (-w, h))]
            box = Polygon([(fx + x, fy + y) for x, y in corners])
            for a in areas:
                if a["owner"] == ref or "pads" not in a["forbids"]:
                    continue
                hit = [l for l in cu if l in a["layers"] or "*.Cu" in a["layers"] or l == "*.Cu"]
                if hit and box.intersection(a["poly"]).area > 1e-6:
                    c = box.centroid
                    who = f" of {a['owner']}" if a["owner"] else ""
                    out.append({"sev": "error", "type": "items_not_allowed",
                                "msg": f"Items not allowed (keepout area '{a['name']}'{who})",
                                "items": [f"Pad {p[1]} [<no net>] of {ref} on {hit[0]}"],
                                "at": [[round(c.x, 4), round(c.y, 4)]]})
                    break
    return out


def _key(item: str) -> str:
    """Pad descriptions differ in net text between KiCad and here: compare on pad number and footprint."""
    import re
    m = re.match(r"Pad (\S+) .*?of (\S+)", item)
    return f"pad {m.group(1)} {m.group(2)}" if m else item


def merge(found: list[dict], extra: list[dict]) -> list[dict]:
    """`found` plus the `extra` violations whose footprint KiCad did not already report as not allowed."""
    seen = {tuple(map(_key, v["items"])) for v in found if v["type"] == "items_not_allowed"}
    return found + [v for v in extra if tuple(map(_key, v["items"])) not in seen]


def placement_areas(design, libs) -> list[dict]:
    """Rule areas of the design's footprints (the KO_* keep-out parts, stored in board coordinates) for the placer:
    `{name, owner (ref), forbids (footprints/pads/tracks), layers, box (x0, y0, x1, y1)}`. A polygon that is not a
    rectangle is replaced by its bounding box (a bound, never smaller). Board-level areas are not in the library, so
    they are not seen here."""
    out = []
    for c in design.components.values():
        if c.get("virtual"):
            continue
        for a in _rule_areas(libs.footprint_tree(c.get("footprint")), ("footprints", "pads", "tracks")):
            out.append({"name": a["name"], "owner": c.ref, "forbids": a["forbids"], "layers": a["layers"],
                        "box": a["poly"].bounds})
    return out
