"""Block authoring and the standalone block check: `extract` builds a block from a routed board, `verify` places it
alone on a small board, runs KiCad DRC and the post-layout copper extraction. The block library, bindings and copper
are `hwflow.core.blocks`."""

from __future__ import annotations

from pathlib import Path

import yaml

from ..core.blocks import connected_nets, courtyard, library_root, load, ports
from ..core.geometry import rot


# ------------------------------------------------------------------ extraction

def extract(board: Path, refs: list[str], name: str, source: str, roles: dict | None = None, design=None,
            libs=None, margin: float = 1.0) -> dict:
    """A block from a routed board: the parts' placement relative to the first one, the copper of the nets whose
    pads are all in the part set, and the port copper attached to the parts, clipped at the parts' courtyards and
    that copper grown by `margin`. The block courtyard is the box of the parts and the copper kept."""
    from shapely.geometry import LineString, Point, box

    from ..layout.board import pcb
    rb = pcb.read_board(Path(board))
    fps = rb["footprints"]
    roles = roles or {r: r for r in refs}
    missing = [r for r in roles.values() if r not in fps]
    if missing:
        raise KeyError(f"not on the board: {', '.join(missing)}")
    S = set(roles.values())
    ref0 = next(iter(roles.values()))
    ox, oy = fps[ref0]["x"], fps[ref0]["y"]
    allpads: dict[str, set] = {}
    for ref, fp in fps.items():
        for p in fp["pads"]:
            if p["net"]:
                allpads.setdefault(p["net"], set()).add(ref)
    rl, pl, P = {}, {}, set()
    yard = None
    for role, ref in roles.items():
        fp = fps[ref]
        pmap = {}
        for p in fp["pads"]:
            if p["number"] and p["net"] and p["number"] not in pmap:
                pmap[p["number"]] = p["net"]
                if allpads[p["net"]] - S:
                    P.add(p["net"])
        c = design.components.get(ref) if design is not None else None
        rl[role] = {"ref": ref, **({"symbol": c.get("symbol")} if c is not None else {}), "footprint": fp["footprint"],
                    "value": str(c.get("value")) if c is not None else None, "pins": pmap}
        rl[role] = {k: v for k, v in rl[role].items() if v is not None}
        pl[role] = {"x": round(fp["x"] - ox, 4), "y": round(fp["y"] - oy, 4), "rot": fp["r"] % 360, "side": "front"}
        if libs is not None:
            b = libs.footprint(fp["footprint"])["sides"].get("front") or libs.footprint(fp["footprint"])["bbox"]
            pts = [rot(x, y, fp["r"]) for x, y in ((b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3]))]
        else:
            pts = [(q["x"] - fp["x"] + s * q["w"] / 2, q["y"] - fp["y"] + t * q["h"] / 2)
                   for q in fp["pads"] for s in (-1, 1) for t in (-1, 1)]
        bx = box(min(x for x, _ in pts) + fp["x"], min(y for _, y in pts) + fp["y"],
                 max(x for x, _ in pts) + fp["x"], max(y for _, y in pts) + fp["y"])
        yard = bx if yard is None else yard.union(bx)
    nets = {n for r in rl.values() for n in r["pins"].values()}
    internal = nets - P
    tracks = [s for s in rb["segments"] if s["net"] in internal]
    vias = [v for v in rb["vias"] if v["net"] in internal]
    for s in tracks:
        yard = yard.union(LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])]).buffer(s["w"] / 2).envelope)
    for v in vias:
        yard = yard.union(Point(v["x"], v["y"]).buffer(0.3).envelope)
    rect = yard.envelope.buffer(margin, join_style=2)
    # port copper: what joins a member pad of the net inside the courtyard rectangle
    via_r = (design.board["via_dia_mm"] if design is not None else 0.6) / 2
    for net in sorted(P):
        shapes = [(set(p["layers"]) if p["kind"] != "thru_hole" else {"*"},
                   box(p["x"] - p["w"] / 2, p["y"] - p["h"] / 2, p["x"] + p["w"] / 2, p["y"] + p["h"] / 2))
                  for r in S for p in fps[r]["pads"] if p["net"] == net]
        cand = []
        for s in rb["segments"]:
            if s["net"] != net:
                continue
            piece = LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])]).intersection(rect)
            if piece.geom_type == "LineString" and piece.length > 1e-3:
                (x1, y1), (x2, y2) = piece.coords[0], piece.coords[-1]
                cand.append(("t", dict(s, x1=x1, y1=y1, x2=x2, y2=y2), {s["layer"]}, piece.buffer(s["w"] / 2)))
        cand += [("v", v, {"*"}, Point(v["x"], v["y"]).buffer(via_r)) for v in rb["vias"]
                 if v["net"] == net and rect.contains(Point(v["x"], v["y"]))]
        grow = True
        while grow:
            grow = False
            for c in list(cand):
                if any((c[2] & L or "*" in c[2] or "*" in L) and c[3].intersects(g) for L, g in shapes):
                    shapes.append((c[2], c[3]))
                    (tracks if c[0] == "t" else vias).append(c[1])
                    cand.remove(c)
                    grow = True

    def rel(x, y):
        return [round(x - ox, 4), round(y - oy, 4)]

    for s in tracks:
        yard = yard.union(LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])]).buffer(s["w"] / 2).envelope)
    for v in vias:
        yard = yard.union(Point(v["x"], v["y"]).buffer(via_r).envelope)
    x0, y0, x1, y1 = yard.bounds
    rules = {k: float(design.board[k]) for k in ("clearance_mm", "via_dia_mm", "via_drill_mm")} if design is not None \
        else {"clearance_mm": 0.2, "via_dia_mm": 0.6, "via_drill_mm": 0.3}
    used = {t["layer"] for t in tracks}
    return {
        "name": name,
        "description": f"extracted from {Path(board).name}: {', '.join(f'{k}={v}' for k, v in roles.items())}",
        "source": source,
        "layers_min": 4 if used & {"In1.Cu", "In2.Cu"} else 2,
        "rules": rules,
        "ports": sorted(P),
        "roles": rl,
        "placement": pl,
        "copper": {"tracks": [{"net": t["net"], "layer": t["layer"], "w": t["w"], "pts": [rel(t["x1"], t["y1"]), rel(t["x2"], t["y2"])]}
                              for t in tracks],
                   "vias": [{"net": v["net"], "x": rel(v["x"], v["y"])[0], "y": rel(v["x"], v["y"])[1]} for v in vias]},
        "courtyard": [round(x0 - ox, 4), round(y0 - oy, 4), round(x1 - ox, 4), round(y1 - oy, 4)],
    }


# ------------------------------------------------------------------ standalone check

def verify(name: str, workdir: Path, design=None) -> dict:
    """Place the block alone on a small board (instantiate mode, ports as single nets) and run KiCad DRC on the
    unrouted board, which holds only the block copper. Returns {ok, errors, warnings, drc, board, joined}."""
    from ..layout.board import kicad, pcb
    from ..layout.place import placer
    from . import postlayout
    from ..core.design import Design
    from ..core.libs import Libs
    b = load(name, design)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    x0, y0, x1, y1 = courtyard(b)
    m = 3.0
    rules = b.get("rules") or {}
    P = ports(b)
    gnd = "GND" if "GND" in P else None
    board = {"width_mm": round(x1 - x0 + 2 * m, 2), "height_mm": round(y1 - y0 + 2 * m, 2),
             "layers": int(b.get("layers_min", 2)), "gnd_pour": gnd is not None, "margin_mm": 0.5,
             **{k: float(v) for k, v in rules.items()}}
    nets = {n: {"allow_single": True, **({"class": "ground"} if n == gnd else {})} for n in sorted(P)}
    raw = {"project": f"block_{name}", "board": board, "nets": nets,
           "blocks": {"t": {"use": name, "nets": {n: n for n in sorted(P)},
                            "at": {"x": round(m - x0, 4), "y": round(m - y0, 4), "rot": 0, "fixed": True}}}}
    (workdir / "design.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    if b.get("dir") and (Path(b["dir"]).parent != library_root()):  # a project block: look it up from the project
        (workdir / "blocks").mkdir(exist_ok=True)
        link = workdir / "blocks" / name
        if not link.exists():
            link.symlink_to(Path(b["dir"]).resolve())
    d = Design.load(workdir)
    libs = Libs(design=d)
    parts = placer.build_parts(d, libs)
    out = workdir / "build"
    out.mkdir(exist_ok=True)
    pcb.write_project(out, d.name, d)
    f = out / f"{d.name}.kicad_pcb"
    pcb.write_board(f, pcb.build_board(d, libs, parts))
    drc = kicad.drc(f, refill=True)
    errs = [x for x in drc if x["sev"] == "error"]
    # the block copper must also be one piece per net for the post-layout extraction (copper overlap, as KiCad)
    split = {n: k for n, k in postlayout.Extraction(d, f).islands().items() if k > 1}
    return {"ok": not errs and not split, "errors": len(errs), "warnings": len(drc) - len(errs), "drc": drc,
            "board": str(f), "split": split, "joined": sorted(connected_nets(b, libs, float(d.board["via_dia_mm"])))}


