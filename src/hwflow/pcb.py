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
                vias: list | None = None) -> list:
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
                             ["fill", "yes", ["thermal_gap", 0.3], ["thermal_bridge_width", 0.4], ["island_removal_mode", 0]],
                             ["polygon", ["pts", ["xy", 0.3, 0.3], ["xy", W - 0.3, 0.3], ["xy", W - 0.3, H - 0.3],
                                          ["xy", 0.3, H - 0.3]]]])
    return tree


def write_board(path: Path, tree: list):
    path.write_text(sexpr.dumps(tree) + "\n")


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
    import json as _json
    from . import kicad as _kicad
    route_py = KRT_DIR / "py_router" / "route.py"
    if not route_py.exists():
        raise FileNotFoundError(f"KiCadRoutingTools not found at {KRT_DIR}; run scripts/setup.sh or set KRT_DIR")
    b = design.board
    name = design.name
    workdir = Path(workdir).resolve()  # the router runs with cwd=KRT_DIR, so relative paths would not be found
    src = workdir / f"{name}_unrouted.kicad_pcb"
    write_board(src, build_board(design, libs, parts))
    pro = workdir / f"{name}.kicad_pro"
    classes = net_classes(design)
    gnds = design.ground_nets() if b["gnd_pour"] else set()
    groups: dict[float, list[str]] = {}
    for net, cl in classes.items():
        if net not in gnds:
            groups.setdefault(cl["width"], []).append(net)
    env = dict(os.environ, KICAD_CLI=_kicad.cli())
    common = ["--clearance", str(b["clearance_mm"]), "--via-size", str(b["via_dia_mm"]),
              "--via-drill", str(b["via_drill_mm"]), "--same-net-pad-clearance", "0.15", "--fab-tier", "standard",
              "--escalation", "off", "--no-fix-drc-settings", "--strict-sizes"]
    cur, log, last = src, [], {}
    for i, (w, nets) in enumerate(sorted(groups.items(), reverse=True)):
        out = workdir / f"{name}_krt{i}.kicad_pcb"
        if pro.exists():
            shutil.copy(pro, cur.with_suffix(".kicad_pro"))
        cmd = [sys.executable, str(route_py), str(cur), str(out), "--nets", *nets, "--track-width", str(w), *common]
        if i:
            cmd.append("--keep-input-copper")
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=KRT_DIR, env=env)
        log.append(p.stdout + p.stderr)
        for ln in (p.stdout + p.stderr).splitlines():
            if ln.startswith("JSON_SUMMARY_MIN:"):
                last[w] = _json.loads(ln.split(":", 1)[1])
        if not out.exists():
            (workdir / "krt.log").write_text("\n".join(log))
            raise RuntimeError(f"KiCadRoutingTools failed on width class {w} mm (exit {p.returncode}); see krt.log")
        cur = out
    (workdir / "krt.log").write_text("\n".join(log))
    routed = read_board(cur)
    tracks = [dict(s) for s in routed["segments"] if s["net"]]
    vias = [dict(v) for v in routed["vias"] if v["net"]]
    unrouted = sum(int(v.get("failed", 0)) + int(v.get("multipoint_deficit", 0)) + len(v.get("open_single") or [])
                   for v in last.values())
    return tracks, vias, f"{unrouted} unrouted, krt {len(groups)} pass(es)", unrouted


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
        ref = next((str(p[2]) for p in sexpr.find_all(fp, "property") if str(p[1]) == "Reference"), "?")
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
                         "kind": str(p[2]), "layers": [str(x) for x in (sexpr.find(p, "layers") or [])[1:]]})
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
