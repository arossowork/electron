"""Geometric requirement checks on the built board: `layout_checks:` in design.yaml (docs/design-yaml.md).

Deterministic, no LLM and no router: each check is measured on the `.kicad_pcb` of the build (outline, courtyards, pads,
tracks, vias, zone fills) and tagged with `req:`, so `hwflow trace` can mark a `verification: layout` requirement that
no routed-net, isolation or budget check covers (board size, antenna distance, keep-out free of copper, side of parts).
"""

from __future__ import annotations

import fnmatch
import math
from pathlib import Path

from shapely.geometry import LineString, MultiPoint, Point, Polygon, box
from shapely.ops import unary_union

from ..core import sexpr
from ..core.design import Design
from ..core.geometry import rot
from ..layout.board.pcb import read_board

KINDS = ("outline_max", "min_distance", "keepout_free", "side_only")
SELECTORS = ("parts", "role", "nets", "net_class", "rect", "circle", "region")
EPS = 1e-6


def checks_of(design: Design) -> list[dict]:
    return [c for c in design.raw.get("layout_checks") or [] if isinstance(c, dict)]


def _list(v) -> list:
    return [] if v is None else list(v) if isinstance(v, (list, tuple)) else [v]


def validate(design: Design) -> list[str]:
    """Problems of the `layout_checks:` entries (static; `hwflow check`)."""
    raw = design.raw.get("layout_checks")
    if raw is None:
        return []
    if not isinstance(raw, list):
        return ["layout_checks must be a list"]
    errs, names = [], set()
    regions = {r.get("name") for r in ((design.raw.get("floorplan") or {}).get("regions") or []) if isinstance(r, dict)}

    def num(c, k, i, positive=True):
        v = c.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (positive and v <= 0):
            errs.append(f"layout_checks[{i}].{k} must be a positive number (mm)")

    def sel(v, key, i):
        if not isinstance(v, dict) or not [k for k in v if k in SELECTORS]:
            errs.append(f"layout_checks[{i}].{key} must be a mapping with one of {', '.join(SELECTORS)}")
            return
        for k in v:
            if k not in SELECTORS + ("margin_mm", "layers", "except"):
                errs.append(f"layout_checks[{i}].{key}: unknown key {k!r}")
        if "region" in v and v["region"] not in regions:
            errs.append(f"layout_checks[{i}].{key}.region {v['region']!r} is not a floorplan region")
        if "rect" in v and not (isinstance(v["rect"], (list, tuple)) and len(v["rect"]) == 4):
            errs.append(f"layout_checks[{i}].{key}.rect must be [x0, y0, x1, y1]")
        if "circle" in v and not (isinstance(v["circle"], (list, tuple)) and len(v["circle"]) == 3):
            errs.append(f"layout_checks[{i}].{key}.circle must be [x, y, r]")
        for p in _list(v.get("parts")):
            if not any(fnmatch.fnmatchcase(r, str(p)) for r in design.components) and str(p) not in ("smd", "tht"):
                errs.append(f"layout_checks[{i}].{key}.parts: no part matches {p!r}")
        for n in _list(v.get("nets")):
            if n not in design.nets():
                errs.append(f"layout_checks[{i}].{key}.nets: unknown net {n!r}")

    for i, c in enumerate(raw):
        if not isinstance(c, dict):
            errs.append(f"layout_checks[{i}] must be a mapping")
            continue
        kind = c.get("kind")
        if kind not in KINDS:
            errs.append(f"layout_checks[{i}].kind {kind!r} is not one of {', '.join(KINDS)}")
            continue
        name = c.get("name") or f"{kind}{i}"
        if name in names:
            errs.append(f"layout_checks[{i}].name {name!r} is used twice")
        names.add(name)
        if kind == "outline_max":
            num(c, "width_mm", i), num(c, "height_mm", i)
        elif kind == "min_distance":
            num(c, "min_mm", i)
            sel(c.get("from"), "from", i), sel(c.get("to"), "to", i)
        elif kind == "keepout_free":
            sel(c.get("area"), "area", i)
        elif kind == "side_only":
            if c.get("side") not in ("front", "back"):
                errs.append(f"layout_checks[{i}].side must be front or back")
            if not _list(c.get("parts")):
                errs.append(f"layout_checks[{i}].parts must list parts (refs, globs, smd or tht)")
    return errs


# ------------------------------------------------------------------ board geometry

class _Board:
    """Geometry read from a .kicad_pcb: per part, per net, outline, copper."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.b = read_board(self.path)
        t = sexpr.loads(self.path.read_text())
        self.fp_layer, self.court = {}, {}
        for fp in sexpr.find_all(t, "footprint"):
            at = sexpr.find(fp, "at")
            fx, fy, fr = float(at[1]), float(at[2]), sexpr.at_angle(at)
            ref = next((str(p[2]) for p in sexpr.find_all(fp, "property") if str(p[1]) == "Reference"), None)
            ref = ref or next((str(p[2]) for p in sexpr.find_all(fp, "fp_text") if str(p[1]) == "reference"), None)
            if not ref:
                continue
            self.fp_layer[ref] = str(sexpr.value(fp, "layer") or "F.Cu")
            pts = []
            for k in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
                for it in sexpr.find_all(fp, k):
                    if not str(sexpr.value(it, "layer") or "").endswith("CrtYd"):
                        continue
                    for kk in ("start", "end", "center", "mid"):
                        n = sexpr.find(it, kk)
                        if n:
                            pts.append((float(n[1]), float(n[2])))
                    for xy in sexpr.find_all(sexpr.find(it, "pts") or [], "xy"):
                        pts.append((float(xy[1]), float(xy[2])))
            if pts:
                abs_pts = [(fx + a, fy + b) for a, b in (rot(x, y, fr) for x, y in pts)]
                self.court[ref] = MultiPoint(abs_pts).convex_hull
        # Edge.Cuts
        ep = []
        for k in ("gr_line", "gr_rect", "gr_arc", "gr_circle", "gr_poly"):
            for it in sexpr.find_all(t, k):
                if str(sexpr.value(it, "layer")) != "Edge.Cuts":
                    continue
                if k == "gr_circle":
                    c, e = sexpr.find(it, "center"), sexpr.find(it, "end")
                    cx, cy = float(c[1]), float(c[2])
                    r = math.hypot(float(e[1]) - cx, float(e[2]) - cy)
                    ep += [(cx - r, cy - r), (cx + r, cy + r)]
                    continue
                if k == "gr_arc":
                    ep += self._arc(it)
                    continue
                for kk in ("start", "end"):
                    n = sexpr.find(it, kk)
                    if n:
                        ep.append((float(n[1]), float(n[2])))
                for xy in sexpr.find_all(sexpr.find(it, "pts") or [], "xy"):
                    ep.append((float(xy[1]), float(xy[2])))
        self.outline = (min(p[0] for p in ep), min(p[1] for p in ep), max(p[0] for p in ep), max(p[1] for p in ep)) if ep else None
        # copper: items (net, layers, geometry)
        self.copper = []
        for s in self.b["segments"]:
            self.copper.append((s["net"], {s["layer"]}, LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])]).buffer(s["w"] / 2), None))
        for v in sexpr.find_all(t, "via"):
            at, size = sexpr.find(v, "at"), float(sexpr.value(v, "size") or 0.6)
            lays = {str(x) for x in (sexpr.find(v, "layers") or [])[1:]} or {"F.Cu", "B.Cu"}
            n = self._net(v, t)
            self.copper.append((n, {"*"}, Point(float(at[1]), float(at[2])).buffer(size / 2), None))
        for ref, fp in self.b["footprints"].items():
            for p in fp["pads"]:
                lays = {"*"} if any(x in ("*.Cu",) for x in p["layers"]) else {x for x in p["layers"] if x.endswith(".Cu")}
                if not lays or p.get("kind") == "np_thru_hole":
                    continue
                self.copper.append((p["net"], lays, box(p["x"] - p["w"] / 2, p["y"] - p["h"] / 2, p["x"] + p["w"] / 2, p["y"] + p["h"] / 2), ref))
        from .postlayout import zone_fills
        for z in zone_fills(self.path):
            g = Polygon(list(zip(*z["pts"]))).buffer(0)
            self.copper.append((z["net"], {z["layer"]}, g, None))

    @staticmethod
    def _net(node, t):
        n = sexpr.find(node, "net")
        if n is None or len(n) < 2:
            return None
        if len(n) > 2:
            return str(n[2])
        v = n[1]
        if str(v).isdigit() and not isinstance(v, sexpr.Q):
            return next((str(x[2]) for x in sexpr.find_all(t, "net") if len(x) > 2 and str(x[1]) == str(v)), None)
        return str(v)

    @staticmethod
    def _arc(it):
        s, m, e = (sexpr.find(it, k) for k in ("start", "mid", "end"))
        pts = [(float(n[1]), float(n[2])) for n in (s, m, e) if n]
        if len(pts) < 3:
            return pts
        (x1, y1), (x2, y2), (x3, y3) = pts
        dd = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
        if abs(dd) < 1e-9:
            return pts
        ux = ((x1 ** 2 + y1 ** 2) * (y2 - y3) + (x2 ** 2 + y2 ** 2) * (y3 - y1) + (x3 ** 2 + y3 ** 2) * (y1 - y2)) / dd
        uy = ((x1 ** 2 + y1 ** 2) * (x3 - x2) + (x2 ** 2 + y2 ** 2) * (x1 - x3) + (x3 ** 2 + y3 ** 2) * (x2 - x1)) / dd
        r = math.hypot(x1 - ux, y1 - uy)
        a1, am, a3 = (math.atan2(y - uy, x - ux) for y, x in ((y1, x1), (y2, x2), (y3, x3)))
        sweep = (a3 - a1) % (2 * math.pi)
        if (am - a1) % (2 * math.pi) > sweep:
            sweep -= 2 * math.pi
        return [(ux + r * math.cos(a1 + sweep * k / 32), uy + r * math.sin(a1 + sweep * k / 32)) for k in range(33)]

    # -------------------------------------------------------------- selectors
    def refs(self, design: Design, pats) -> list[str]:
        out = []
        for ref in self.b["footprints"]:
            for p in _list(pats):
                p = str(p)
                if p in ("smd", "tht"):
                    tht = any(q["kind"] == "thru_hole" for q in self.b["footprints"][ref]["pads"])
                    hit = tht if p == "tht" else not tht
                else:
                    hit = fnmatch.fnmatchcase(ref, p)
                if hit:
                    out.append(ref)
                    break
        return out

    def part_geom(self, ref: str):
        fp = self.b["footprints"][ref]
        pads = [box(p["x"] - p["w"] / 2, p["y"] - p["h"] / 2, p["x"] + p["w"] / 2, p["y"] + p["h"] / 2) for p in fp["pads"]]
        gs = ([self.court[ref]] if ref in self.court else []) + pads
        return unary_union(gs) if gs else Point(fp["x"], fp["y"])

    def select(self, design: Design, s: dict):
        """Geometry of a selector, and the refs it names (for the report)."""
        gs, names = [], []
        m = float(s.get("margin_mm") or 0)
        lays = set(_list(s.get("layers")))
        if "parts" in s or "role" in s:
            refs = self.refs(design, s.get("parts")) if "parts" in s else []
            refs += [r for r, c in design.components.items() if "role" in s and c.get("role") == s["role"] and r in self.b["footprints"]]
            refs = [r for r in dict.fromkeys(refs) if r not in _list(s.get("except"))]
            gs += [self.part_geom(r) for r in refs]
            names += refs
        if "nets" in s or "net_class" in s:
            nets = set(_list(s.get("nets")))
            if "net_class" in s:
                nets |= {n for n in design.nets() if design.net_info(n).get("class") == s["net_class"]}
            for n, ls, g, owner in self.copper:
                if n in nets and (not lays or "*" in ls or ls & lays):
                    gs.append(g)
            names += sorted(nets)
        if "rect" in s:
            gs.append(box(*[float(x) for x in s["rect"]]))
        if "circle" in s:
            x, y, r = (float(v) for v in s["circle"])
            gs.append(Point(x, y).buffer(r))
        if "region" in s:
            r = next(r for r in (design.raw.get("floorplan") or {}).get("regions") or [] if r.get("name") == s["region"])
            if r.get("rect"):
                gs.append(box(*[float(x) for x in r["rect"]]))
            elif r.get("circle"):
                gs.append(Point(*[float(x) for x in r["circle"][:2]]).buffer(float(r["circle"][2])))
        g = unary_union(gs) if gs else None
        return (g.buffer(m) if g is not None and m else g), names


# ------------------------------------------------------------------ evaluation

def evaluate(design: Design, board: Path) -> list[dict]:
    """One record per `layout_checks` entry: {name, kind, ok, value, limit, unit, detail, req}."""
    cs = checks_of(design)
    if not cs:
        return []
    bd = _Board(board)
    out = []
    for i, c in enumerate(cs):
        kind = c.get("kind")
        name = c.get("name") or f"{kind}{i}"
        r = {"name": name, "kind": kind, "req": c.get("req"), "ok": False, "value": None, "limit": None, "unit": "mm", "detail": ""}
        try:
            _one(design, bd, c, r)
        except Exception as e:  # noqa: BLE001 - a check that cannot be measured is a failed check with its reason
            r.update(ok=False, detail=f"not measured: {type(e).__name__}: {e}")
        out.append(r)
    return out


def _one(design, bd: _Board, c: dict, r: dict):
    kind = c["kind"]
    if kind == "outline_max":
        if bd.outline is None:
            r["detail"] = "no Edge.Cuts outline on the board"
            return
        w, h = bd.outline[2] - bd.outline[0], bd.outline[3] - bd.outline[1]
        lw, lh = float(c["width_mm"]), float(c["height_mm"])
        fits = (w <= lw + 1e-3 and h <= lh + 1e-3) or (bool(c.get("rotate_ok")) and w <= lh + 1e-3 and h <= lw + 1e-3)
        r.update(ok=fits, value=[round(w, 3), round(h, 3)], limit=[lw, lh], detail=f"outline {w:.2f} x {h:.2f} mm, limit {lw:g} x {lh:g} mm")
    elif kind == "min_distance":
        a, na = bd.select(design, c["from"])
        b, nb = bd.select(design, c["to"])
        if a is None or b is None:
            r["ok"] = True   # nothing on one side (no such copper or part): nothing closer than the limit
            r["detail"] = "nothing selected on the " + ("from" if a is None else "to") + " side"
            r.update(value=None, limit=float(c["min_mm"]))
            return
        d = a.distance(b)
        lim = float(c["min_mm"])
        r.update(ok=d >= lim - 1e-3, value=round(d, 3), limit=lim,
                 detail=f"{'+'.join(map(str, na)) or 'area'} to {'+'.join(map(str, nb)) or 'area'}: {d:.2f} mm, need {lim:g} mm")
    elif kind == "keepout_free":
        s = c["area"]
        area, _ = bd.select(design, s)
        lays = set(_list(c.get("layers")) or ["F.Cu", "B.Cu"])
        skip_parts = set(_list(c.get("except_parts")))
        skip_nets = set(_list(c.get("except_nets")))
        tol = float(c.get("tolerance_mm2", 0.02))   # fractured zone fills leave slits a few thousandths of a mm2 wide
        hits = []
        for n, ls, g, owner in bd.copper:
            if (owner in skip_parts) or (n in skip_nets and n is not None) or not ("*" in ls or ls & lays):
                continue
            ov = g.intersection(area).area
            if ov > tol:
                hits.append((n or "-", sorted(ls) if "*" not in ls else ["all"], ov))
        tot = sum(h[2] for h in hits)
        nets = sorted({h[0] for h in hits})
        r.update(ok=not hits, value=round(tot, 3), limit=0.0, unit="mm2",
                 detail="keep-out free of copper" if not hits else f"copper in the keep-out: {tot:.2f} mm2 of {', '.join(nets)}")
    elif kind == "side_only":
        side = c["side"]
        want = "F.Cu" if side == "front" else "B.Cu"
        refs = bd.refs(design, c["parts"])
        bad = [x for x in refs if bd.fp_layer.get(x, "F.Cu") != want]
        r.update(ok=not bad, value=len(bad), limit=0, unit="parts",
                 detail=f"{len(refs)} parts, all on the {side}" if not bad else f"on the wrong side ({side} only): {', '.join(bad[:12])}")
