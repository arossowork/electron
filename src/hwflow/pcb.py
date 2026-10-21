"""KiCad PCB generation, Specctra DSN export, Freerouting run and SES import."""

from __future__ import annotations

import copy
import math
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from shapely.geometry import box

from . import sexpr
from .design import Design
from .placer import Part, rot
from .sexpr import Q

FREEROUTING = os.environ.get("FREEROUTING") or str(Path(__file__).resolve().parents[2] / "tools" / "freerouting-2.5.0.jar")
JAVA = os.environ.get("JAVA") or next((j for j in ("/opt/homebrew/opt/openjdk/bin/java", shutil.which("java") or "")
                                      if j and Path(j).exists()), "java")
PCB_VERSION = "20241229"


def uid() -> Q:
    return Q(str(uuid.uuid4()))


# ------------------------------------------------------------------ track sizing

def ipc2221_width_mm(current_a: float, copper_oz: float = 1.0, dtemp: float = 10.0, external: bool = True) -> float:
    k = 0.048 if external else 0.024
    area_mil2 = (current_a / (k * dtemp ** 0.44)) ** (1 / 0.725)
    thick_mil = 1.378 * copper_oz
    return area_mil2 / thick_mil * 0.0254


def net_classes(design: Design) -> dict[str, dict]:
    """net -> {width, clearance, class}"""
    b = design.board
    out = {}
    for net in design.nets():
        info = design.net_info(net)
        w = float(info.get("width_mm", b["track_mm"]))
        if info.get("current_a"):
            w = max(w, round(ipc2221_width_mm(float(info["current_a"]), b["copper_oz"]) + 0.049, 1))
        if info.get("class") == "power":
            w = max(w, float(b.get("power_track_mm", 0.4)))
        w = max(w, (getattr(design, "budget_widths", None) or {}).get(net, 0.0))  # budgets.widths, set by the layout stage
        out[net] = {"width": w, "clearance": float(info.get("clearance_mm", b["clearance_mm"])),
                    "class": info.get("class", "signal")}
    return out


# ------------------------------------------------------------------ board writer

def _layers(n: int) -> list:
    cu = [[0, Q("F.Cu"), "signal"]]
    if n == 4:
        cu += [[4, Q("In1.Cu"), "signal"], [6, Q("In2.Cu"), "signal"]]
    cu += [[2, Q("B.Cu"), "signal"]]
    tech = [[9, Q("F.Adhes"), "user", Q("F.Adhesive")], [11, Q("B.Adhes"), "user", Q("B.Adhesive")],
            [13, Q("F.Paste"), "user"], [15, Q("B.Paste"), "user"],
            [5, Q("F.SilkS"), "user", Q("F.Silkscreen")], [7, Q("B.SilkS"), "user", Q("B.Silkscreen")],
            [1, Q("F.Mask"), "user"], [3, Q("B.Mask"), "user"],
            [17, Q("Dwgs.User"), "user", Q("User.Drawings")], [19, Q("Cmts.User"), "user", Q("User.Comments")],
            [25, Q("Edge.Cuts"), "user"], [27, Q("Margin"), "user"],
            [31, Q("F.CrtYd"), "user", Q("F.Courtyard")], [29, Q("B.CrtYd"), "user", Q("B.Courtyard")],
            [35, Q("F.Fab"), "user"], [33, Q("B.Fab"), "user"]]
    return ["layers"] + cu + tech


def cu_layers(n: int) -> list[str]:
    return ["F.Cu"] + (["In1.Cu", "In2.Cu"] if n == 4 else []) + ["B.Cu"]


def _quote_tokens(node: list) -> None:
    """Quote pad numbers and layer names in place. Older KiCad libraries write `(pad 1 smd ..)` and
    `(layers F.Cu F.Mask)` unquoted; KiCad reads both, but the router's board parser only finds quoted pad numbers
    and silently drops those pads (IC pads vanish from the router's view and nets come out unconnected)."""
    head = node[0] if node else None
    if head == "pad" and len(node) > 1 and not isinstance(node[1], list):
        node[1] = Q(str(node[1]))
    if head in ("layer", "layers"):
        for i in range(1, len(node)):
            if not isinstance(node[i], list):
                node[i] = Q(str(node[i]))
    for x in node:
        if isinstance(x, list):
            _quote_tokens(x)


def _place_footprint(fp_tree: list, ref: str, value: str, part: Part, netidx: dict, pin_nets: dict,
                     fields: dict | None = None) -> list:
    t = copy.deepcopy(fp_tree)
    t[0] = "footprint"
    fp_name = str(t[1])
    out = [t[0], Q(fp_name if ":" in fp_name else fp_name)]
    out.append(["layer", Q("F.Cu")])
    out.append(["uuid", uid()])
    out.append(["at", round(part.x, 4), round(part.y, 4)] + ([part.r] if part.r else []))
    for k, v in (fields or {}).items():
        out.append(["property", Q(k), Q(str(v)), ["at", 0, 0, 0], ["layer", Q("F.Fab")], ["hide", "yes"],
                    ["uuid", uid()], ["effects", ["font", ["size", 1, 1], ["thickness", 0.15]]]])
    for item in t[2:]:
        if not isinstance(item, list):
            continue
        head = item[0]
        if head in ("layer", "uuid", "at", "version", "generator", "generator_version", "tedit"):
            continue
        if head == "property":
            item = copy.deepcopy(item)
            if str(item[1]) == "Reference":
                item[2] = Q(ref)
            elif str(item[1]) == "Value":
                item[2] = Q(value)
            _rotate_text(item, part.r)
        elif head == "fp_text":
            item = copy.deepcopy(item)
            if str(item[1]) == "reference":
                item[2] = Q(ref)
            elif str(item[1]) == "value":
                item[2] = Q(value)
            _rotate_text(item, part.r)
        elif head == "pad":
            item = copy.deepcopy(item)
            _quote_tokens(item)
            at = sexpr.find(item, "at")
            prot = float(at[3]) if len(at) > 3 else 0.0
            ang = (prot + part.r) % 360
            del at[3:]
            if ang:
                at.append(ang)
            net = pin_nets.get(str(item[1]))
            item = [x for x in item if not (isinstance(x, list) and x and x[0] in ("net", "uuid"))]
            if net is not None and str(item[1]):
                item.append(["net", netidx[net], Q(net)])
            item.append(["uuid", uid()])
        elif head == "model":
            item = copy.deepcopy(item)
            for k in ("path",):
                pass
            mp = str(item[1]).replace("${KICAD9_3DMODEL_DIR}", "${KICAD10_3DMODEL_DIR}")
            item[1] = Q(mp)
        elif head == "zone":
            # .kicad_mod zones are in footprint coordinates, .kicad_pcb zones in board coordinates
            item = copy.deepcopy(item)
            for poly in (x for x in item if isinstance(x, list) and x and x[0] == "polygon"):
                for xy in (p for p in sexpr.find(poly, "pts")[1:] if isinstance(p, list) and p[0] == "xy"):
                    dx, dy = rot(float(xy[1]), float(xy[2]), part.r)
                    xy[1], xy[2] = round(part.x + dx, 4), round(part.y + dy, 4)
        elif head not in ("pad", "property", "fp_text"):
            item = copy.deepcopy(item)
        _quote_tokens(item)
        out.append(item)
    return out


def _rotate_text(item: list, r: float):
    at = sexpr.find(item, "at")
    if at is None:
        return
    base = float(at[3]) if len(at) > 3 else 0.0
    del at[3:]
    a = (base + r) % 360
    if a:
        at.append(a)


def thermal_vias(design: Design, libs, parts: dict[str, Part]) -> list[dict]:
    """Via grids inside thermal pads, from `thermal_vias: {pad, count, pitch_mm}` on a component."""
    b = design.board
    out = []
    for ref, part in parts.items():
        c = design.components[ref]
        tv = c.get("thermal_vias")
        if not tv:
            continue
        pad_no = str(tv.get("pad", ""))
        net = c.pins.get(pad_no)
        pad = next((p for p in libs.footprint(c.get("footprint"))["pads"] if p["number"] == pad_no), None)
        if not net or pad is None:
            continue
        w, h = pad["w"], pad["h"]
        if pad.get("hull"):
            xs, ys = [q[0] for q in pad["hull"]], [q[1] for q in pad["hull"]]
            cx0, cy0 = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
            w, h = max(xs) - min(xs), max(ys) - min(ys)
        else:
            cx0, cy0 = 0.0, 0.0
        if pad["rot"] % 180:
            w, h = h, w
        pitch = float(tv.get("pitch_mm", max(b["via_dia_mm"] + 0.4, 1.0)))
        r = b["via_dia_mm"] / 2
        nx = max(1, int((w - 2 * r) // pitch) + 1)
        ny = max(1, int((h - 2 * r) // pitch) + 1)
        pts = [((i - (nx - 1) / 2) * pitch + cx0, (j - (ny - 1) / 2) * pitch + cy0) for i in range(nx) for j in range(ny)]
        pts = pts[: int(tv.get("count", len(pts)))]
        for lx, ly in pts:
            dx, dy = rot(pad["x"] + lx, pad["y"] + ly, part.r)
            out.append({"net": net, "x": part.x + dx, "y": part.y + dy, "thermal": f"{ref}.{pad_no}"})
    return out


def build_board(design: Design, libs, parts: dict[str, Part], tracks: list | None = None,
                vias: list | None = None, island_removal: int = 0) -> list:
    b = design.board
    nets = list(design.nets())
    netidx = {n: i + 1 for i, n in enumerate(nets)}
    classes = net_classes(design)
    nl = b["layers"]
    tree = ["kicad_pcb", ["version", PCB_VERSION], ["generator", Q("hwflow")], ["generator_version", Q("10.0")],
            ["general", ["thickness", b["thickness_mm"]], ["legacy_teardrops", "no"]],
            ["paper", Q("A4")], _layers(nl),
            ["setup", ["pad_to_mask_clearance", 0], ["allow_soldermask_bridges_in_footprints", "no"],
             ["pcbplotparams", ["layerselection", Q("0x00010fc_ffffffff")], ["outputformat", 1],
              ["outputdirectory", Q("gerbers/")]]],
            ["net", 0, Q("")]]
    for n in nets:
        tree.append(["net", netidx[n], Q(n)])
    W, H = b["width_mm"], b["height_mm"]
    tree.append(["gr_rect", ["start", 0, 0], ["end", W, H], ["stroke", ["width", 0.1], ["type", "default"]],
                 ["fill", "no"], ["layer", Q("Edge.Cuts")], ["uuid", uid()]])
    tree.append(["gr_text", Q(f"{design.name} rev {design.raw.get('revision', 'A')}"), ["at", 1.5, H - 1.2, 0],
                 ["layer", Q("F.SilkS")], ["uuid", uid()],
                 ["effects", ["font", ["size", 0.8, 0.8], ["thickness", 0.12]], ["justify", "left"]]])
    for ref, part in parts.items():
        c = design.components[ref]
        fp = libs.footprint_tree(c.get("footprint"))
        fp = copy.deepcopy(fp)
        fp[1] = Q(c.get("footprint"))
        fields = {k.upper() if k != "manufacturer" else "Manufacturer": c.get(k)
                  for k in ("mpn", "lcsc", "manufacturer") if c.get(k)}
        tree.append(_place_footprint(fp, ref, str(c.get("value")), part, netidx, c.pins, fields))
    for t in tracks or []:
        tree.append(["segment", ["start", round(t["x1"], 4), round(t["y1"], 4)], ["end", round(t["x2"], 4), round(t["y2"], 4)],
                     ["width", t["w"]], ["layer", Q(t["layer"])], ["net", netidx[t["net"]]], ["uuid", uid()]])
    if tracks is not None:  # routed board: add declared thermal via grids inside their pads
        vias = list(vias or []) + thermal_vias(design, libs, parts)
    for v in vias or []:
        tree.append(["via", ["at", round(v["x"], 4), round(v["y"], 4)], ["size", b["via_dia_mm"]],
                     ["drill", b["via_drill_mm"]], ["layers", Q("F.Cu"), Q("B.Cu")], ["net", netidx[v["net"]]],
                     ["uuid", uid()]])
    if b["gnd_pour"]:
        for g in design.ground_nets():
            if g not in netidx:
                continue
            for layer in cu_layers(nl):
                if layer in b.get("pour_skip_layers", []):
                    continue
                tree.append(["zone", ["net", netidx[g]], ["net_name", Q(g)], ["layer", Q(layer)], ["uuid", uid()],
                             ["hatch", "edge", 0.5],
                             ["connect_pads", "yes", ["clearance", b["clearance_mm"]]],
                             ["min_thickness", 0.25], ["filled_areas_thickness", "no"],
                             ["fill", "yes", ["thermal_gap", 0.3], ["thermal_bridge_width", 0.4], ["island_removal_mode", island_removal]],
                             ["polygon", ["pts", ["xy", 0.3, 0.3], ["xy", W - 0.3, 0.3], ["xy", W - 0.3, H - 0.3],
                                          ["xy", 0.3, H - 0.3]]]])
    return tree


def board_text(tree: list) -> str:
    """The board as text, its UUIDs renamed in order of appearance: the same input gives the same bytes."""
    import re
    ids: dict[str, str] = {}
    text = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                  lambda m: ids.setdefault(m.group(0), str(uuid.uuid5(uuid.NAMESPACE_URL, f"hwflow/{len(ids)}"))),
                  sexpr.dumps(tree))
    return text + "\n"


def write_board(path: Path, tree: list):
    path.write_text(board_text(tree))


def isolation_groups(design: Design) -> list[dict]:
    """board.isolation (backlog 38): each entry names a net group (`nets:` glob or list of globs, and/or `class:`)
    that keeps `clearance_mm` and `creepage_mm` to every other net. Returns the member nets and the KiCad custom
    rules (clearance, creepage) written to the .kicad_dru, so DRC enforces them in the layout stage."""
    import fnmatch
    out = []
    nets = sorted(set(design.nets()) | set((design.raw.get("nets") or {})))
    for i, g in enumerate(design.board.get("isolation") or []):
        pats = g.get("nets") or []
        pats = [pats] if isinstance(pats, str) else list(pats)
        cls = g.get("class")
        members = [n for n in nets if any(fnmatch.fnmatchcase(n, p) for p in pats)
                   or (cls and design.net_info(n).get("class") == cls)]
        name = str(g.get("name") or g.get("req") or f"isolation{i + 1}")
        row = {"name": name, "members": members, "req": g.get("req"), "kicad": [],
               "clearance_mm": g.get("clearance_mm"), "creepage_mm": g.get("creepage_mm")}
        out.append(row)
        if not members:
            continue
        a = " || ".join(f"A.NetName == '{n}'" for n in members)
        cond = (f"({a})" if len(members) > 1 else a) + " && " + " && ".join(f"B.NetName != '{n}'" for n in members)
        for kind in ("clearance", "creepage"):
            v = g.get(f"{kind}_mm")
            if v is not None:
                row["kicad"].append(f'(rule "{name} {kind}" (constraint {kind} (min {float(v):g}mm)) '
                                    f'(condition "{cond}"))')
    return out


def isolation_needs(design: Design) -> list[dict]:
    """Distance a net group keeps to every other net, as [{members, mm}] for the placer: `board.isolation` entries
    (creepage, else clearance) and `board.drc_rules` clearance or creepage rules shaped
    `A.NetName == 'P' && B.NetName != 'P'` (P a glob; the largest value per P)."""
    import fnmatch
    import re
    out = [{"members": g["members"], "mm": float(g["creepage_mm"] if g["creepage_mm"] is not None else g["clearance_mm"])}
           for g in isolation_groups(design)
           if g["members"] and (g["creepage_mm"] is not None or g["clearance_mm"] is not None)]
    nets = sorted(set(design.nets()) | set((design.raw.get("nets") or {})))
    pat = re.compile(r"A\.NetName\s*==\s*'([^']+)'\s*&&\s*B\.NetName\s*!=\s*'([^']+)'")
    raw: dict = {}
    for r in design.board.get("drc_rules") or []:
        mm = re.search(r"\(constraint\s+(?:clearance|creepage)\s*\(min\s+([0-9.]+)\s*mm\)", r)
        cond = re.search(r'\(condition\s+"([^"]*)"', r)
        m = pat.fullmatch(cond.group(1).strip()) if cond else None
        if mm and m and m.group(1) == m.group(2):
            raw[m.group(1)] = max(raw.get(m.group(1), 0.0), float(mm.group(1)))
    out += [{"members": [n for n in nets if fnmatch.fnmatchcase(n, p)], "mm": mm} for p, mm in raw.items()]
    return [g for g in out if g["members"]]


def isolation_router_rules(design: Design) -> list[dict]:
    """Creepage as a clearance the router honours (L11). KiCadRoutingTools reads no net-name condition on a
    clearance rule; it reads a track-to-track rule scoped to a net class, `A.Type=='track' && B.Type=='track' &&
    A.NetClass=='C' && B.NetClass!='C'` (member against non-member only, so a group keeps its own spacing). Every
    creepage requirement between a net group and all other nets becomes such a rule at the creepage value, with a
    class `iso_<name>` that holds the group nets. Sources: `board.isolation` entries with `creepage_mm`, and
    `board.drc_rules` creepage rules shaped `A.NetName == 'P' && B.NetName != 'P'` (P a glob). Returns
    [{class, members, mm, rule}]. Other condition shapes stay with KiCad DRC and the repair loop."""
    import fnmatch
    import re
    nets = sorted(set(design.nets()) | set((design.raw.get("nets") or {})))
    out, seen = [], set()

    def add(name, members, mm):
        cname = "iso_" + re.sub(r"\W+", "_", name).strip("_")
        if not members or cname in seen:
            return
        seen.add(cname)
        cond = f"A.Type == 'track' && B.Type == 'track' && A.NetClass == '{cname}' && B.NetClass != '{cname}'"
        out.append({"class": cname, "members": members, "mm": mm,
                    "rule": f'(rule "{name} (router clearance)" (constraint clearance (min {mm:g}mm)) '
                            f'(condition "{cond}"))'})

    for g in isolation_groups(design):
        if g["creepage_mm"] is not None:
            add(g["name"], g["members"], float(g["creepage_mm"]))
    pat = re.compile(r"A\.NetName\s*==\s*'([^']+)'\s*&&\s*B\.NetName\s*!=\s*'([^']+)'")
    for r in design.board.get("drc_rules") or []:
        nm = re.search(r'\(rule\s+"([^"]+)"', r)
        mm = re.search(r"\(constraint\s+creepage\s*\(min\s+([0-9.]+)\s*mm\)", r)
        cond = re.search(r'\(condition\s+"([^"]*)"', r)
        m = pat.fullmatch(cond.group(1).strip()) if cond else None
        if nm and mm and m and m.group(1) == m.group(2):
            add(nm.group(1), [n for n in nets if fnmatch.fnmatchcase(n, m.group(1))], float(mm.group(1)))
    return out


def write_project(dirpath: Path, name: str, design: Design):
    """Minimal .kicad_pro with net classes so DRC uses our widths and clearances."""
    import json
    classes = net_classes(design)
    b = design.board
    nc = [{"name": "Default", "clearance": b["clearance_mm"], "track_width": b["track_mm"],
           "via_diameter": b["via_dia_mm"], "via_drill": b["via_drill_mm"], "microvia_diameter": 0.3,
           "microvia_drill": 0.1, "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "pcb_color": "rgba(0,0,0,0.000)",
           "schematic_color": "rgba(0,0,0,0.000)", "wire_width": 6, "bus_width": 12, "line_style": 0}]
    assignments = []
    widths = sorted({(v["width"], v["clearance"]) for v in classes.values()})
    for w, cl in widths:
        if w == b["track_mm"] and cl == b["clearance_mm"]:
            continue
        cname = f"W{w:g}_C{cl:g}"
        nc.append(dict(nc[0], name=cname, track_width=w, clearance=cl))
        for n, v in classes.items():
            if (v["width"], v["clearance"]) == (w, cl):
                assignments.append({"netclass": cname, "pattern": n})
    iso = isolation_router_rules(design)
    for x in iso:  # membership only (no size fields): the aggregate class keeps the width and clearance of the net's own class
        nc.append({"name": x["class"], "priority": 100})
        assignments += [{"netclass": x["class"], "pattern": n} for n in x["members"]]
    pro = {
        "board": {"design_settings": {"rules": {"min_clearance": min(b["clearance_mm"], 0.15),
                                                "min_track_width": 0.15, "min_via_diameter": 0.45,
                                                "min_through_hole_diameter": 0.2, "min_copper_edge_clearance": 0.3,
                                                "min_hole_clearance": 0.25, "min_hole_to_hole": 0.25,
                                                "min_via_annular_width": 0.1}}},
        "meta": {"filename": f"{name}.kicad_pro", "version": 3},
        "net_settings": {"classes": nc, "meta": {"version": 4}, "net_colors": None,
                         "netclass_assignments": None, "netclass_patterns": assignments},
        "sheets": [], "text_variables": {},
    }
    (dirpath / f"{name}.kicad_pro").write_text(json.dumps(pro, indent=2))
    # board.drc_rules: KiCad custom rules (class-pair clearance, creepage), applied by DRC from <name>.kicad_dru;
    # board.isolation: the same rules generated from data (backlog 38)
    dru = dirpath / f"{name}.kicad_dru"
    rules = list(b.get("drc_rules") or []) + [r for g in isolation_groups(design) for r in g["kicad"]]
    rules += [x["rule"] for x in iso]
    if rules:
        dru.write_text("(version 1)\n" + "\n".join(rules) + "\n")
    elif dru.exists():
        dru.unlink()


# ------------------------------------------------------------------ Specctra DSN

def _um(v: float) -> float:
    return round(v * 1000, 1)


def _padstack(p: dict, layers_all: list[str]) -> tuple[str, list]:
    if p["kind"] in ("thru_hole", "np_thru_hole"):
        lays = layers_all
    else:
        lays = [l for l in p["layers"] if l.endswith(".Cu")]
        lays = ["F.Cu"] if "F.Cu" in lays else lays
    w, h = _um(p["w"]), _um(p["h"])
    shape = p["shape"]
    shapes = []
    hull = p.get("hull")
    for L in lays:
        if hull:  # custom pad: full outline (anchor + primitives), y flipped for Specctra
            coords = []
            for x, y in hull:
                coords += [_um(x), -_um(y)]
            shapes.append(["shape", ["polygon", L, 0] + coords])
        elif shape == "circle":
            shapes.append(["shape", ["circle", L, w]])
        elif shape == "oval":
            if w > h:
                shapes.append(["shape", ["path", L, h, -(w - h) / 2, 0, (w - h) / 2, 0]])
            else:
                shapes.append(["shape", ["path", L, w, 0, -(h - w) / 2, 0, (h - w) / 2]])
        else:
            shapes.append(["shape", ["rect", L, -w / 2, -h / 2, w / 2, h / 2]])
    name = f"{shape}_{p['kind']}_{w:g}x{h:g}_{'-'.join(lays)}".replace(".", "")
    if hull:
        name += "_h" + str(abs(hash(tuple(hull))) % 100000)
    return name, ["padstack", Q(name)] + shapes + [["attach", "off"]]


def export_dsn(design: Design, libs, parts: dict[str, Part], path: Path):
    b = design.board
    layers = cu_layers(b["layers"])
    classes = net_classes(design)
    W, H = b["width_mm"], b["height_mm"]
    structure = ["structure"]
    for i, L in enumerate(layers):
        structure.append(["layer", L, ["type", "power" if False else "signal"], ["property", ["index", i]]])
    structure.append(["boundary", ["path", "pcb", 0, 0, 0, _um(W), 0, _um(W), -_um(H), 0, -_um(H), 0, 0]])
    via_name = f"Via[0-{len(layers) - 1}]_{_um(b['via_dia_mm']):g}:{_um(b['via_drill_mm']):g}_um"
    structure.append(["via", Q(via_name)])
    structure.append(["rule", ["width", _um(b["track_mm"])], ["clearance", _um(b["clearance_mm"])]])
    placement = ["placement"]
    library = ["library"]
    padstacks = {}
    for ref, part in parts.items():
        c = design.components[ref]
        fp = libs.footprint(c.get("footprint"))
        img = f"{c.get('footprint')}__{ref}"
        placement.append(["component", Q(img), ["place", ref, _um(part.x), -_um(part.y), "front", part.r]])
        image = ["image", Q(img)]
        for p in fp["pads"]:
            if not p["number"] or p["kind"] == "np_thru_hole":
                continue
            name, ps = _padstack(p, layers)
            padstacks[name] = ps
            pin = ["pin", Q(name)]
            if p["rot"]:
                pin.append(["rotate", p["rot"]])
            pin += [Q(p["number"]), _um(p["x"]), -_um(p["y"])]
            image.append(pin)
        # keepout for non-plated holes
        for p in fp["pads"]:
            if p["kind"] == "np_thru_hole":
                image.append(["keepout", Q(""), ["circle", "signal", _um(p["w"]), _um(p["x"]), -_um(p["y"])]])
        library.append(image)
    library += list(padstacks.values())
    vd = _um(b["via_dia_mm"])
    library.append(["padstack", Q(via_name)] + [["shape", ["circle", L, vd]] for L in layers] + [["attach", "off"]])
    network = ["network"]
    nets = design.nets()
    for net, conns in nets.items():
        pins = [f"{r}-{p}" for r, p in conns if r in parts]
        network.append(["net", Q(net), ["pins"] + pins])
    groups: dict[tuple, list] = {}
    for net, cl in classes.items():
        groups.setdefault((cl["width"], cl["clearance"]), []).append(net)
    for i, ((w, cl), ns) in enumerate(sorted(groups.items())):
        network.append(["class", Q(f"c{i}")] + [Q(n) for n in ns] +
                       [["circuit", ["use_via", Q(via_name)]], ["rule", ["width", _um(w)], ["clearance", _um(cl)]]])
    tree = ["pcb", Q(str(path)), ["parser", ["string_quote", Q('"')], ["space_in_quoted_tokens", "on"],
                                  ["host_cad", Q("hwflow")], ["host_version", Q("0.1")]],
            ["resolution", "um", 10], ["unit", "um"], structure, placement, library, network, ["wiring"]]
    text = sexpr.dumps(tree).replace('(string_quote "\\"")', "(string_quote \")")
    path.write_text(text + "\n")


def run_freerouting(dsn: Path, ses: Path, passes: int = 30, timeout: int = 900) -> str:
    if not Path(FREEROUTING).exists():
        raise FileNotFoundError(f"freerouting jar not found at {FREEROUTING}; set FREEROUTING")
    cmd = [JAVA, "-Djava.awt.headless=true", "-jar", FREEROUTING, "--gui.enabled=false",
           "-de", str(dsn.resolve()), "-do", str(ses.resolve()), "--router.autorouter.max_passes", str(passes)]
    ses.unlink(missing_ok=True)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=dsn.parent, stdin=subprocess.DEVNULL)
        log = p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        log = f"TIMEOUT after {timeout}s\n" + (e.stdout or b"").decode(errors="replace")
    (dsn.parent / "freerouting.log").write_text(log)
    if not ses.exists():
        raise RuntimeError("freerouting produced no session file:\n" + log[-2000:])
    return log


KRT_DIR = Path(os.environ.get("KRT_DIR") or Path(__file__).resolve().parents[2] / "tools" / "KiCadRoutingTools")


def run_krt(design: Design, libs, parts: dict[str, Part], workdir: Path, timeout: int = 1800) -> tuple[list, list, str, int]:
    """Route with KiCadRoutingTools: one pass per track-width class (widest first), keeping earlier copper.

    Power-tap mode is not used, because it places vias inside same-net SMD pads.
    Returns (tracks, vias, summary, unrouted) like the Freerouting path.
    """
    workdir = Path(workdir).resolve()  # the router runs with cwd=KRT_DIR, so relative paths would not be found
    name = design.name
    src = workdir / f"{name}_unrouted.kicad_pcb"
    write_board(src, build_board(design, libs, parts))
    (workdir / "krt.log").unlink(missing_ok=True)  # krt_route appends: repair rounds add to the same log
    write_project(workdir, name, design)  # the rules DRC will use: net classes (.kicad_pro) and .kicad_dru
    cur, last, retried, ngroups = krt_route(design, src, workdir, f"{name}_krt", timeout=timeout)
    routed = read_board(cur)
    tracks = [dict(s) for s in routed["segments"] if s["net"]]
    vias = [dict(v) for v in routed["vias"] if v["net"]]
    unrouted = sum(krt_unrouted(v) for v in last.values())
    extra = f"; retried in another net order ({'; '.join(retried)})" if retried else ""
    return tracks, vias, f"{unrouted} unrouted, krt {ngroups} pass(es){extra}", unrouted


def krt_route(design: Design, src: Path, workdir: Path, stem: str, nets=None, keep_first: bool = False,
              variants: list | None = None, timeout: int = 1800) -> tuple[Path, dict, list, int]:
    """The router invocation: one KiCadRoutingTools pass per track-width class (widest first) on the board `src`.
    `nets` limits the nets to route (default: every non-pour net), `keep_first` keeps the copper already in `src`
    in the first pass too (repair), `variants` replaces the net orders tried (KRT_VARIANTS).
    The rules files `<name>.kicad_pro` and `.kicad_dru` in `workdir` are copied next to every pass input.

    With `board.isolation` groups, the member nets route first, and every pass input gets the isolation keep-outs
    of routing.isolation_keepouts. A net a pass wider than the board track width leaves open is routed again as a
    router power net (neck-down for at most 2 mm at a pad entry), then at the track width and widened where it fits
    (routing.widen); a net walled in by the others of its pass is routed first. HWFLOW_NECKDOWN=0 switches the
    neck-down off.
    Returns (last board, {pass: summary}, [notes: other net orders, neck-downs], number of passes)."""
    from . import keepout, routing
    b = design.board
    name = design.name
    src, workdir = Path(src).resolve(), Path(workdir).resolve()  # the router runs with cwd=KRT_DIR (a relative project path would not be found)
    rule_files = [workdir / f"{name}.kicad_pro", workdir / f"{name}.kicad_dru"]
    classes = net_classes(design)
    gnds = design.ground_nets() if b["gnd_pour"] else set()
    iso = isolation_needs(design)
    members = {n for g in iso for n in g["members"]}
    groups: dict[tuple, list[str]] = {}
    for net, cl in classes.items():
        if net not in gnds and (nets is None or net in nets):
            groups.setdefault((net not in members, -cl["width"]), []).append(net)
    passes = [(-k[1], groups[k]) for k in sorted(groups)]  # isolation members first, then widest first
    neck = float(b["track_mm"])
    neckdown = os.environ.get("HWFLOW_NECKDOWN", "1") != "0"
    copper = cu_layers(b["layers"])
    env = dict(os.environ, KICAD_CLI=_kicad_cli())
    # Pre-existing copper from the earlier (wider) passes as rip candidates boxes in the pads of the narrow pass:
    # home-thermostat-3 0.25 mm pass 5 of 26 nets routed with it, 25 without. Set to 1 to restore the router default.
    env.setdefault("KICAD_RIP_PREEXISTING", "0")
    common = ["--clearance", str(b["clearance_mm"]), "--via-size", str(b["via_dia_mm"]),
              "--via-drill", str(b["via_drill_mm"]), "--same-net-pad-clearance", "0.15", "--fab-tier", "standard",
              "--escalation", "off", "--no-fix-drc-settings", "--strict-sizes"]
    cur, log, last, retried = src, [], {}, []
    retries = int(b.get("router_retries", 3))
    route_py = KRT_DIR / "py_router" / "route.py"
    if not route_py.exists():
        raise FileNotFoundError(f"KiCadRoutingTools not found at {KRT_DIR}; run scripts/setup.sh or set KRT_DIR")

    def run(args):
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=KRT_DIR, env=env)
        return p.stdout + p.stderr, p.returncode

    def pass_input(cur, tag, pnets):
        """The pass input: `cur` with the isolation keep-outs of this pass, and the rules files beside it."""
        rules = []
        if iso:
            zones, rules = routing.isolation_keepouts(iso, read_board(cur), copper, b["via_dia_mm"],
                                                      b["clearance_mm"], set(pnets))
            cur = routing.pass_input(cur, workdir / f"{stem}{tag}_in.kicad_pcb", zones)
        for f in rule_files:  # the router reads net-class clearances and custom rules from the input's siblings
            dst = f.with_name(cur.stem + f.suffix)
            if rules and f.suffix == ".kicad_dru":
                text = f.read_text().rstrip("\n") if f.exists() else "(version 1)"
                dst.write_text(text + "\n" + "\n".join(rules) + "\n")
            elif not f.exists():
                dst.unlink(missing_ok=True)
            elif dst != f:
                shutil.copy(f, dst)
        return cur

    def run_pass(cur, tag, pnets, args, i):
        base = [sys.executable, str(route_py), str(pass_input(cur, tag, pnets)), None, "--nets", *pnets, *args, *common]
        if i or keep_first:
            base.append("--keep-input-copper")
        best, tried, logs = route_class(base, workdir / f"{stem}{tag}", retries, run, variants)
        log.extend(logs)
        if best is None:
            with (workdir / "krt.log").open("a") as f:
                f.write("\n".join(log))
            raise RuntimeError(f"KiCadRoutingTools failed on pass {tag} ({' '.join(args)}); see krt.log")
        return best, tried

    def route_group(cur, tag, pnets, w, i):
        """Route `pnets` at width `w`: as router power nets when wider than the track width, then the nets left open
        at the track width, widened back where they fit. Returns ((board, summary, unrouted), notes)."""
        wide = neckdown and w > neck + 1e-9
        best, tried = run_pass(cur, tag, pnets, ["--track-width", str(w)], i)
        notes = [f"{w} mm: " + ", ".join(f"{v} {n}" for v, n in tried)] if len(tried) > 1 else []
        # neck-down, only for the nets left open (a board that routes at full width keeps that copper): first the
        # router's own pad-entry neck (power nets, at most 2 mm narrow), then the nets at the track width (then at
        # the board's minimum width), back to their width wherever it fits
        open_nets = sorted(set(krt_open_nets(best[1])) & set(pnets)) if wide and best[2] else []
        if open_nets:
            pb, _ = run_pass(best[0], f"{tag}w", open_nets, ["--track-width", str(neck), "--power-nets", *open_nets,
                                                           "--power-nets-widths", *[str(w)] * len(open_nets)], 1)
            if pb[2] < best[2]:
                best = pb
                notes.append(f"{w} mm: {', '.join(open_nets)} routed with the router's pad-entry neck-down")
        for k, nw in enumerate(x for x in dict.fromkeys((neck, routing.NECK_FLOOR)) if x <= neck):
            open_nets = sorted(set(krt_open_nets(best[1])) & set(pnets)) if wide and best[2] else []
            if not open_nets:
                break
            nb, _ = run_pass(best[0], f"{tag}n{k}", open_nets, ["--track-width", str(nw)], 1)
            if nb[2] >= best[2]:
                continue
            ko = {}
            for a in keepout._rule_areas(sexpr.loads(nb[0].read_text()), ("tracks",)):
                for L in routing.pad_layers(a["layers"], copper):
                    ko.setdefault(L, []).append(a["poly"])
            edge = 0.3  # min_copper_edge_clearance written by write_project
            outline = box(edge, edge, b["width_mm"] - edge, b["height_mm"] - edge)
            stats = routing.widen(nb[0], read_board(nb[0]), {n: w for n in open_nets},
                                  _pair_clearance(design, classes, iso), copper, b["via_dia_mm"], outline, ko)
            text = ", ".join(f"{n} {s[0]:.1f} of {s[1]:.1f} mm narrow" for n, s in sorted(stats.items()))
            if all(s[0] <= routing.NECK_MAX_MM for s in stats.values()):
                best = nb
                notes.append(f"{w} mm: neck-down to {nw:g} mm: {text}")
            else:  # a long narrow stretch is a thinner net, not a neck: leave the net open and say so
                notes.append(f"{w} mm: neck-down to {nw:g} mm rejected (over {routing.NECK_MAX_MM:g} mm narrow): {text}")
        return best, notes

    for i, (w, gnets) in enumerate(passes):
        best, notes = route_group(cur, i, gnets, w, i)
        got = {f"{i}: {w} mm": best[1]}
        open_nets = sorted(set(krt_open_nets(best[1])) & set(gnets)) if best[2] else []
        if open_nets and len(open_nets) < len(gnets):
            # a net walled in by the others of its pass (a pin between two pins of a wider net): route it first
            first, n1 = route_group(cur, f"{i}p", open_nets, w, i)
            rest, n2 = route_group(first[0], f"{i}q", [n for n in gnets if n not in open_nets], w, 1)
            if first[2] + rest[2] < best[2]:
                best = (rest[0], rest[1], first[2] + rest[2])
                got = {f"{i}: {w} mm, open nets first": first[1], f"{i}: {w} mm, the others": rest[1]}
                notes = n1 + n2 + [f"{w} mm: {', '.join(open_nets)} routed before the other nets of the pass"]
        cur = best[0]
        last.update(got)
        retried += notes
    with (workdir / "krt.log").open("a") as f:
        f.write("\n".join(log))
    return cur, last, retried, len(passes)


def krt_open_nets(summary: dict) -> list[str]:
    """Nets a router summary reports as not fully connected."""
    out = list(summary.get("failed_single") or []) + list(summary.get("open_single") or [])
    out += [x.get("net_name") for x in summary.get("failed_multipoint") or [] if isinstance(x, dict)]
    out += list((summary.get("pad_pairs_open") or {}).get("nets") or [])
    return [n for n in dict.fromkeys(out) if n]


def _pair_clearance(design: Design, classes: dict, iso: list[dict]):
    """clearance(a, b): the copper distance nets a and b keep (net classes, and the distance of an isolation group
    between a member and a non-member)."""
    default = float(design.board["clearance_mm"])

    def clearance(a, b):
        c = max(classes.get(a, {}).get("clearance", default), classes.get(b, {}).get("clearance", default))
        for g in iso:
            if (a in g["members"]) != (b in g["members"]):
                c = max(c, float(g["mm"]))
        return c
    return clearance


_ROUTER_VERSION: dict[str, str] = {}


def router_version(design: Design) -> str:
    """What the routing result depends on besides the input: a hash of the KiCadRoutingTools sources and its compiled
    extension (or the Freerouting jar), so a cached route is dropped when the router changes."""
    import hashlib
    krt = design.board.get("router", "krt") == "krt" and (KRT_DIR / "py_router" / "route.py").exists()
    key = f"krt:{KRT_DIR}" if krt else f"freerouting:{FREEROUTING}"
    if key not in _ROUTER_VERSION:
        h = hashlib.sha256()
        files = sorted(list((KRT_DIR / "py_router").rglob("*.py")) + list((KRT_DIR / "rust_router").glob("*.so"))) if krt \
            else [Path(FREEROUTING)]
        for f in files:
            if f.exists():
                h.update(str(f.name).encode() + f.read_bytes())
        _ROUTER_VERSION[key] = h.hexdigest()[:16]
    return _ROUTER_VERSION[key]


def _kicad_cli() -> str:
    from . import kicad
    return kicad.cli()


# net orders tried, in turn, when a width class leaves nets unrouted (backlog 40)
KRT_VARIANTS = [[], ["--ordering", "inside_out"], ["--mps-reverse-rounds"], ["--direction", "backward"],
                ["--ordering", "original"]]


def krt_unrouted(summary: dict) -> int:
    return int(summary.get("failed", 0)) + int(summary.get("multipoint_deficit", 0)) + len(summary.get("open_single") or [])


def route_class(base: list, stem: Path, retries: int, run, variants: list | None = None) -> tuple:
    """Route one width class; while nets stay unrouted, retry in another net order (up to `retries` times).
    Returns ((board, summary, unrouted) of the best attempt or None, [(order, unrouted)], logs)."""
    import json as _json
    best, tried, logs = None, [], []
    for j, extra in enumerate(variants or KRT_VARIANTS[:1 + max(retries, 0)]):
        out = stem.with_name(stem.name + (f"_r{j}" if j else "") + ".kicad_pcb")
        args = [str(out) if a is None else a for a in base] + extra  # None marks the output board
        out.unlink(missing_ok=True)
        text, rc = run(args)
        logs.append(text)
        summ = {}
        for ln in text.splitlines():
            if ln.startswith("JSON_SUMMARY_MIN:"):
                summ = _json.loads(ln.split(":", 1)[1])
        label = " ".join(extra) or "default order"
        if not out.exists():
            tried.append((label, f"failed (exit {rc})"))
            continue
        n = krt_unrouted(summ)
        tried.append((label, f"{n} unrouted"))
        if best is None or n < best[2]:
            best = (out, summ, n)
        if n == 0:
            break
    return best, tried, logs


def import_ses(ses: Path, design: Design) -> tuple[list, list]:
    tree = sexpr.loads(ses.read_text())
    routes = sexpr.find(tree, "routes")
    res = sexpr.find(routes, "resolution")
    unit, per = str(res[1]), float(res[2])
    scale = {"um": 1e-3, "mm": 1.0, "mil": 0.0254, "inch": 25.4}[unit] / per
    tracks, vias = [], []
    for net in sexpr.find_all(sexpr.find(routes, "network_out"), "net"):
        name = str(net[1])
        for w in sexpr.find_all(net, "wire"):
            path = sexpr.find(w, "path")
            layer, width = str(path[1]), float(path[2]) * scale
            coords = [float(x) for x in path[3:] if not isinstance(x, list)]
            pts = [(coords[i] * scale, -coords[i + 1] * scale) for i in range(0, len(coords) - 1, 2)]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                tracks.append({"net": name, "layer": layer, "w": round(width, 4), "x1": x1, "y1": y1, "x2": x2, "y2": y2})
        for v in sexpr.find_all(net, "via"):
            vias.append({"net": name, "x": float(v[2]) * scale, "y": -float(v[3]) * scale})
    return tracks, vias


# ------------------------------------------------------------------ read back

def read_board(path: Path) -> dict:
    """Parse our own .kicad_pcb: footprints with absolute pads, segments, vias."""
    t = sexpr.loads(path.read_text())
    nets = {int(n[1]): str(n[2]) for n in sexpr.find_all(t, "net") if len(n) > 2}

    def netname(node):
        """KiCad <=9 writes (net 3 "X") or (net 3); KiCad 10 writes (net "X")."""
        if node is None or len(node) < 2:
            return None
        if len(node) > 2:
            return str(node[2])
        v = node[1]
        return nets.get(int(v)) if str(v).isdigit() and not isinstance(v, Q) else str(v)

    fps = {}
    for fp in sexpr.find_all(t, "footprint"):
        at = sexpr.find(fp, "at")
        fx, fy, fr = float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0
        ref = next((str(p[2]) for p in sexpr.find_all(fp, "property") if str(p[1]) == "Reference"), None)
        ref = ref or next((str(p[2]) for p in sexpr.find_all(fp, "fp_text") if str(p[1]) == "reference"), None)
        ref = ref if ref and ref not in fps else f"?{len(fps)}"  # older footprints keep the reference in fp_text
        pads = []
        for p in sexpr.find_all(fp, "pad"):
            pat = sexpr.find(p, "at")
            dx, dy = rot(float(pat[1]), float(pat[2]), fr)
            size = sexpr.find(p, "size")
            n = sexpr.find(p, "net")
            ang = float(pat[3]) if len(pat) > 3 else 0.0
            w, h = float(size[1]), float(size[2])
            hull = sexpr.custom_pad_hull(p)
            off = (0.0, 0.0)
            if hull:  # custom pad: bounding box of the full outline, centred where the copper really is
                hx, hy = [q[0] for q in hull], [q[1] for q in hull]
                w, h = max(hx) - min(hx), max(hy) - min(hy)
                ox, oy = (max(hx) + min(hx)) / 2, (max(hy) + min(hy)) / 2
                off = rot(ox, oy, ang)
            if ang % 180 == 90:
                w, h = h, w
            pads.append({"number": str(p[1]), "x": fx + dx + off[0], "y": fy + dy + off[1], "w": w, "h": h,
                         "net": netname(n),
                         "kind": str(p[2]), "shape": "custom" if hull else str(p[3]) if len(p) > 3 else "rect",
                         "layers": [str(x) for x in (sexpr.find(p, "layers") or [])[1:]]})
        fps[ref] = {"x": fx, "y": fy, "r": fr, "pads": pads, "footprint": str(fp[1])}
    segs = []
    for s in sexpr.find_all(t, "segment"):
        st, en = sexpr.find(s, "start"), sexpr.find(s, "end")
        segs.append({"x1": float(st[1]), "y1": float(st[2]), "x2": float(en[1]), "y2": float(en[2]),
                     "w": float(sexpr.value(s, "width")), "layer": str(sexpr.value(s, "layer")),
                     "net": netname(sexpr.find(s, "net"))})
    vias = []
    for v in sexpr.find_all(t, "via"):
        at = sexpr.find(v, "at")
        vias.append({"x": float(at[1]), "y": float(at[2]), "net": netname(sexpr.find(v, "net"))})
    return {"footprints": fps, "segments": segs, "vias": vias, "nets": nets}
