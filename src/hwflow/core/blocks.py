"""Layout blocks: pre-laid-out, verified sub-circuits (`blocks/<name>/block.yaml`) that a design binds to its parts
(`blocks:` in design.yaml). The placer moves a block's parts as one rigid unit, the board writer adds the block's
copper as pre-existing copper, and the router only joins the blocks. Format: docs/design-yaml.md, blocks/README.md."""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import yaml

from .settings import get_settings
from .units import parse_value
from .geometry import Part, Rigid, rot



def library_root() -> Path:
    """The repository's block library (settings.paths.blocks_dir)."""
    return Path(get_settings().paths.blocks_dir)


YARD = "block:"  # ref prefix of a block courtyard in the placer


# ------------------------------------------------------------------ library

def lib_dirs(design=None) -> list[Path]:
    """Where blocks are looked up: the project's own `blocks/` first, then the repository library."""
    out = [design.dir / "blocks"] if design is not None and (design.dir / "blocks").is_dir() else []
    return out + [library_root()]


def names(design=None) -> list[str]:
    seen = {}
    for d in lib_dirs(design):
        for f in sorted(d.glob("*/block.yaml")):
            seen.setdefault(f.parent.name, f)
    return sorted(seen)


def find(name: str, design=None) -> Path | None:
    for d in lib_dirs(design):
        f = d / name / "block.yaml"
        if f.exists():
            return f
    return None


@lru_cache(maxsize=64)
def _read(path: str, mtime: int) -> dict:
    b = yaml.safe_load(Path(path).read_text()) or {}
    b["dir"] = str(Path(path).parent)
    b.setdefault("name", Path(path).parent.name)
    return b


def load(name: str, design=None) -> dict:
    f = find(name, design)
    if f is None:
        raise KeyError(f"block {name!r} not found (library: {', '.join(names(design)) or 'empty'})")
    return _read(str(f), f.stat().st_mtime_ns)


def ports(block: dict) -> set[str]:
    return {str(p) for p in block.get("ports") or []}


def pins(role: dict) -> dict[str, str]:
    """pin -> block net of a role (YAML may read pin numbers as integers)."""
    return {str(k): str(v) for k, v in (role.get("pins") or {}).items()}


def block_nets(block: dict) -> set[str]:
    return {n for r in (block.get("roles") or {}).values() for n in pins(r).values()}


def drc_rules(design) -> list[str]:
    """KiCad custom rules the blocks' land patterns need (`drc_rules:` of a block, `{U}` a bound role's ref,
    `{block}` the entry name), for the .kicad_dru that DRC and the router read."""
    out = []
    for bnd in bindings(design):
        for r in bnd["block"].get("drc_rules") or []:
            out.append(str(r).format(block=bnd["name"], **bnd["parts"]))
    return out


def footprint_dirs(design) -> list[Path]:
    """`footprints/` folders of the blocks the design uses (project-specific footprints shipped with a block)."""
    out = []
    for e in (design.raw.get("blocks") or {}).values():
        f = find(str((e or {}).get("use")), design)
        if f is not None and (f.parent / "footprints").is_dir():
            out.append(f.parent / "footprints")
    return out


# ------------------------------------------------------------------ binding

def _refs(name: str, e: dict, block: dict) -> dict[str, str]:
    """role -> ref: `parts` (bind mode), else the instance refs (`refs`, else the block's own refs)."""
    if e.get("parts"):
        return {str(k): str(v) for k, v in e["parts"].items()}
    refs = e.get("refs") or {}
    return {role: str(refs.get(role) or r.get("ref") or f"{name}_{role}") for role, r in (block.get("roles") or {}).items()}


def bindings(design) -> list[dict]:
    """The design's blocks that resolve: [{name, block, parts (role -> ref), at}]. `check` reports the others."""
    out = []
    for name, e in (design.raw.get("blocks") or {}).items():
        e = e or {}
        try:
            b = load(str(e.get("use")), design)
        except KeyError:
            continue
        out.append({"name": str(name), "block": b, "parts": _refs(str(name), e, b), "at": e.get("at") or {}})
    return out


def expand(design, component):
    """Instantiate mode: a `blocks:` entry with `nets:` (block port -> design net) and no `parts:` adds the block's
    components (refs from `refs:` or the block). Internal nets become `<entry>_<net>`. Called by Design.load; the
    added components carry `from_block` and are not written back by Design.save."""
    for name, e in (design.raw.get("blocks") or {}).items():
        e = e or {}
        if e.get("parts") or e.get("nets") is None:
            continue
        try:
            b = load(str(e.get("use")), design)
        except KeyError:
            continue
        nets = {str(k): str(v) for k, v in (e.get("nets") or {}).items()}
        for role, ref in _refs(str(name), e, b).items():
            r = b["roles"][role]
            if ref in design.components:
                continue  # `check` reports the clash
            data = {k: r[k] for k in ("value", "symbol", "footprint", "mpn", "lcsc", "price_usd") if k in r}
            data["pins"] = {p: nets.get(n, f"{name}_{n}") for p, n in pins(r).items()}
            data.update(spice=False, from_block=str(name))
            design.components[ref] = component(ref, data)


def netmap(design, bnd: dict) -> tuple[dict[str, str], list[str]]:
    """block net -> design net from the bound parts' pins, and the inconsistencies found."""
    b, out, bad = bnd["block"], {}, []
    for role, r in (b.get("roles") or {}).items():
        ref = bnd["parts"].get(role)
        c = design.components.get(ref)
        if c is None:
            continue
        for pin, bn in pins(r).items():
            dn = c.pins.get(pin)
            if dn is None or dn.upper() in ("NC", "~"):
                bad.append(f"{ref}.{pin} has no net; block {b['name']} connects it to {bn}")
            elif out.setdefault(bn, dn) != dn:
                bad.append(f"block net {bn} is {out[bn]} on one pin and {dn} on {ref}.{pin}")
    return out, bad


def findings(design, libs=None) -> list[dict]:
    """`check` findings of the `blocks:` field: unknown block, roles not bound, footprints, pin nets, layers."""
    f = []

    def add(sev, msg):
        f.append({"sev": sev, "code": "block", "msg": msg})

    seen: dict[str, str] = {}
    known = {x["name"] for x in bindings(design)}
    for name, e in (design.raw.get("blocks") or {}).items():
        if str(name) not in known:
            add("error", f"blocks.{name}: block {(e or {}).get('use')!r} not found "
                         f"(library: {', '.join(names(design)) or 'empty'})")
    for bnd in bindings(design):
        name, b, refs = bnd["name"], bnd["block"], bnd["parts"]
        roles = b.get("roles") or {}
        for role in roles:
            if role not in refs:
                add("error", f"blocks.{name}: role {role} of {b['name']} is not bound (parts: {{{role}: <ref>}})")
        for role, ref in refs.items():
            if role not in roles:
                add("error", f"blocks.{name}: {b['name']} has no role {role} (roles: {', '.join(roles)})")
                continue
            c = design.components.get(ref)
            if c is None:
                add("error", f"blocks.{name}: {role} is {ref}, which is not a component")
                continue
            e = (design.raw.get("blocks") or {}).get(name) or {}
            if not e.get("parts") and c.get("from_block") != name:
                add("error", f"blocks.{name}: the block would add {ref}, which is already a component (set refs: {{{role}: <ref>}})")
            if ref in seen:
                add("error", f"blocks.{name}: {ref} is already bound in block {seen[ref]}")
            seen[ref] = name
            r = roles[role]
            if str(c.get("footprint")) != str(r.get("footprint")):
                add("error", f"blocks.{name}: {ref} has footprint {c.get('footprint')}; role {role} of {b['name']} "
                             f"needs {r.get('footprint')}")
            pl = c.get("placement") or {}
            if "x" in pl or "y" in pl or "edge" in pl:
                add("error", f"blocks.{name}: {ref} has its own placement; the block places it (remove it, or set "
                             f"blocks.{name}.at)")
            msg = _value_mismatch(c, r)
            if msg:
                add("warn", f"blocks.{name}: {ref} {msg}")
        nm, bad = netmap(design, bnd)
        for m in bad:
            add("error", f"blocks.{name}: {m}")
        P = ports(b)
        inside = {(r, p) for role, r in refs.items() for p in pins(roles.get(role, {}))}
        conns = design.nets()
        for bn, dn in nm.items():
            if bn in P:
                continue
            out = [f"{r}.{p}" for r, p in conns.get(dn, []) if (r, p) not in inside]
            if out:
                add("error", f"blocks.{name}: internal net {bn} of {b['name']} ({dn}) also reaches {', '.join(out[:4])}"
                             f"; only ports ({', '.join(sorted(P))}) may leave the block")
        by_dn: dict[str, list[str]] = {}
        for bn, dn in nm.items():
            by_dn.setdefault(dn, []).append(bn)
        for dn, bns in by_dn.items():
            if len(bns) > 1 and not all(x in P for x in bns):
                add("error", f"blocks.{name}: block nets {', '.join(sorted(bns))} of {b['name']} are all {dn} "
                             f"(a short across the block's copper)")
        at = bnd["at"]
        if str(at.get("side", "front")) != "front":
            add("error", f"blocks.{name}: side {at.get('side')} is not supported (hwflow places every part on the front)")
        if at.get("rot") is not None and float(at["rot"]) % 90:
            add("error", f"blocks.{name}: rot {at['rot']} is not a multiple of 90")
        brd = design.board
        if int(b.get("layers_min", 2)) > int(brd.get("layers", 2)):
            add("error", f"blocks.{name}: {b['name']} needs {b.get('layers_min')} layers, the board has {brd.get('layers')}")
        from .current import piece_floors
        segs = [{"net": nm[str(t["net"])], "w": float(t["w"]), "layer": str(t["layer"]), "x1": float(p[0]),
                 "y1": float(p[1]), "x2": float(q[0]), "y2": float(q[1])}
                for t in (b.get("copper") or {}).get("tracks") or [] if str(t["net"]) in nm
                for p, q in zip(t["pts"], t["pts"][1:])]
        thin: dict = {}
        for sg, fl in zip(segs, piece_floors(design, segs)):
            if sg["w"] < fl - 1e-9:
                w0, f0 = thin.get(sg["net"], (math.inf, 0.0))
                thin[sg["net"]] = (min(w0, sg["w"]), max(f0, fl))
        for dn, (w, fl) in sorted(thin.items()):
            add("warn", f"blocks.{name}: {b['name']} has {w:g} mm copper on {dn}, under its {fl:g} mm floor "
                        f"(min_width_mm, or the IPC-2221 width of current_a on a stretch longer than a "
                        f"pad-entry neck): the layout widens it to {fl:g} mm (DRC checks the widened block copper)")
        rules = b.get("rules") or {}
        for k in ("clearance_mm", "via_dia_mm"):
            if k in rules and float(brd[k]) > float(rules[k]) + 1e-9:
                add("warn", f"blocks.{name}: {b['name']} copper was checked at {k} {rules[k]}; the board uses "
                            f"{brd[k]} (DRC may flag the block copper)")
    return f


def _value_mismatch(c, r: dict) -> str | None:
    v = c.get("value")
    try:
        x = parse_value(v)
    except (ValueError, TypeError):
        x = None
    if r.get("value") is not None:
        try:
            same = math.isclose(x, parse_value(r["value"]), rel_tol=1e-6)
        except (ValueError, TypeError):  # a part number: the block's names the family (TPS61299 for "TPS61299 3.3V")
            same = str(v).upper().startswith(str(r["value"]).upper())
        if not same:
            return f"value {v} differs from the block's {r['value']}"
    for k, cmp in (("value_min", lambda a, b: a >= b * (1 - 1e-9)), ("value_max", lambda a, b: a <= b * (1 + 1e-9))):
        if r.get(k) is not None and x is not None and not cmp(x, parse_value(r[k])):
            return f"value {v} is outside the block's {k} {r[k]}"
    return None


# ------------------------------------------------------------------ geometry

def courtyard(block: dict) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = (float(v) for v in block["courtyard"])
    return x0, y0, x1, y1


def _origin(bnd: dict, parts) -> tuple[float, float, float] | None:
    """Block origin and rotation from its first bound part's position."""
    pl = bnd["block"].get("placement") or {}
    for role, ref in bnd["parts"].items():
        if ref in parts and role in pl:
            p, q = parts[ref], pl[role]
            r = (p.r - float(q.get("rot", 0))) % 360
            ox, oy = rot(float(q["x"]), float(q["y"]), r)
            return p.x - ox, p.y - oy, r
    return None


def units(design, libs, parts, margin: float) -> list:
    """The placer units of the design's blocks (placer.Rigid). A block with `at: {x, y}` is fixed (unless
    `fixed: false`) and its members get their positions; members of a movable block are movable."""
    out = []
    for bnd in bindings(design):
        b, refs = bnd["block"], bnd["parts"]
        pl = b.get("placement") or {}
        if not refs or any(r not in parts for r in refs.values()) or any(role not in pl for role in refs):
            continue
        members = [(ref, float(pl[role]["x"]), float(pl[role]["y"]), float(pl[role].get("rot", 0)))
                   for role, ref in refs.items()]
        x0, y0, x1, y1 = courtyard(b)
        w, h, cx, cy = x1 - x0 + 2 * margin, y1 - y0 + 2 * margin, (x0 + x1) / 2, (y0 + y1) / 2
        yard = Part(YARD + bnd["name"], w, h, cx, cy, {}, sides={"front": (w, h, cx, cy)})
        u = Rigid(bnd["name"], yard.ref, members, yard)
        at = bnd["at"]
        if "x" in at and "y" in at:
            u.x, u.y, u.r = float(at["x"]), float(at["y"]), float(at.get("rot", 0))
            u.fixed = bool(at.get("fixed", True))
        elif "rot" in at:
            u.r = float(at["rot"])
        yard.fixed = u.fixed
        for ref in refs.values():
            parts[ref].fixed = u.fixed
        u.sync(parts)
        out.append(u)
    return out


def anchor(design, ref: str) -> dict | None:
    """Board placement {x, y, rot} of a member of a fixed block (from `at`), else None."""
    for bnd in bindings(design):
        at = bnd["at"]
        if "x" not in at or "y" not in at or not at.get("fixed", True):
            continue
        for role, r in bnd["parts"].items():
            q = (bnd["block"].get("placement") or {}).get(role)
            if r == ref and q is not None:
                a = float(at.get("rot", 0))
                dx, dy = rot(float(q["x"]), float(q["y"]), a)
                return {"x": float(at["x"]) + dx, "y": float(at["y"]) + dy, "rot": (float(q.get("rot", 0)) + a) % 360}
    return None


def copper(design, parts) -> tuple[list[dict], list[dict]]:
    """The blocks' copper in board coordinates and design nets: (tracks, vias) as the board writer takes them. A track
    under the floor of its design net gets that floor (current.piece_floors: min_width_mm everywhere, the IPC-2221
    width of current_a on a stretch longer than a pad-entry neck): block copper never carries a net's current on
    less copper than the router may use."""
    from .current import piece_floors
    tracks, vias = [], []
    for bnd in bindings(design):
        o = _origin(bnd, parts)
        if o is None or any(r not in parts for r in bnd["parts"].values()):
            continue
        ox, oy, a = o
        nm, _ = netmap(design, bnd)
        cu = bnd["block"].get("copper") or {}

        def xy(x, y):
            dx, dy = rot(float(x), float(y), a)
            return round(ox + dx, 4), round(oy + dy, 4)

        for t in cu.get("tracks") or []:
            net = nm.get(str(t["net"]))
            if net is None:
                continue
            pts = [xy(*p) for p in t["pts"]]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                tracks.append({"net": net, "layer": str(t["layer"]), "w": float(t["w"]),
                               "x1": x1, "y1": y1, "x2": x2, "y2": y2, "block": bnd["name"]})
        for v in cu.get("vias") or []:
            net = nm.get(str(v["net"]))
            if net is not None:
                x, y = xy(v["x"], v["y"])
                vias.append({"net": net, "x": x, "y": y, "block": bnd["name"]})
    for t, f in zip(tracks, piece_floors(design, tracks)):
        t["w"] = max(t["w"], f)
    return tracks, vias


def local_copper(design, name: str, via_dia: float = 0.6) -> list[tuple]:
    """The copper of block binding `name` in block coordinates (origin and rotation of the placer unit), as boxes
    with their design net: [(net, (x0, y0, x1, y1))]. A track piece is its bounding box grown by half its width (a
    bound for a diagonal piece), a via its pad square. The placer's isolation and clearance terms measure these like
    pads: block copper is on the board before the router runs, so it keeps the same distances as a pad."""
    for bnd in bindings(design):
        if bnd["name"] != name:
            continue
        nm, _ = netmap(design, bnd)
        cu = bnd["block"].get("copper") or {}
        from .current import piece_floors
        ts = [dict(t, net=nm.get(str(t["net"]))) for t in cu.get("tracks") or []]
        segs = [{"net": t["net"], "w": float(t["w"]), "layer": str(t["layer"]), "x1": float(a[0]), "y1": float(a[1]),
                 "x2": float(c[0]), "y2": float(c[1])} for t in ts if t["net"] is not None
                for a, c in zip(t["pts"], t["pts"][1:])]
        wide = {(s["net"], s["layer"], s["x1"], s["y1"], s["x2"], s["y2"]): max(s["w"], f)
                for s, f in zip(segs, piece_floors(design, segs))}  # the width copper() gives the piece
        out = []
        for t in ts:
            net = t["net"]
            if net is None:
                continue
            pts = [(float(x), float(y)) for x, y in t["pts"]]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                h = wide.get((net, str(t["layer"]), x1, y1, x2, y2), float(t["w"])) / 2
                out.append((net, (min(x1, x2) - h, min(y1, y2) - h, max(x1, x2) + h, max(y1, y2) + h)))
        for v in cu.get("vias") or []:
            net = nm.get(str(v["net"]))
            if net is not None:
                x, y, h = float(v["x"]), float(v["y"]), via_dia / 2
                out.append((net, (x - h, y - h, x + h, y + h)))
        return out
    return []


def _tkey(t: dict) -> tuple:
    a, b = (round(t["x1"], 4), round(t["y1"], 4)), (round(t["x2"], 4), round(t["y2"], 4))
    return (t["net"], t["layer"], round(float(t["w"]), 4)) + tuple(sorted((a, b)))


def merge(design, parts, tracks, vias) -> tuple[list, list]:
    """The block copper plus `tracks` and `vias` without the items that repeat it (a routed board read back keeps
    the block copper it was given): the block copper appears once on any board."""
    bt, bv = copper(design, parts)
    if not bt and not bv:
        return list(tracks or []), list(vias or [])
    tk = {_tkey(t) for t in bt}
    vk = {(v["net"], round(v["x"], 4), round(v["y"], 4)) for v in bv}
    return (bt + [t for t in tracks or [] if _tkey(t) not in tk],
            bv + [v for v in vias or [] if (v["net"], round(v["x"], 4), round(v["y"], 4)) not in vk])


def keepout_zones(design, parts) -> list:
    """Rule areas of the blocks' `keepouts` in board coordinates (KiCad zone nodes)."""
    import uuid

    from .geometry import cu_layers
    from .sexpr import Q
    out, board = [], set(cu_layers(int(design.board["layers"])))
    for bnd in bindings(design):
        o = _origin(bnd, parts)
        if o is None:
            continue
        ox, oy, a = o
        for i, k in enumerate(bnd["block"].get("keepouts") or []):
            lay = [str(L) for L in k.get("layers") or ["F.Cu", "B.Cu"] if str(L) in board]
            if not lay:
                continue  # inner-layer keep-outs on a 2-layer board
            x0, y0, x1, y1 = (float(v) for v in k["rect"])
            pts = [rot(x, y, a) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
            forbid = {str(x) for x in k.get("forbids") or ["tracks", "vias"]}
            rules = [[n, "not_allowed" if n in forbid else "allowed"] for n in ("tracks", "vias", "pads", "copperpour", "footprints")]
            out.append(["zone", ["net", 0], ["net_name", Q("")], ["layers"] + [Q(L) for L in lay],
                        ["uuid", Q(str(uuid.uuid4()))], ["name", Q(f"block {bnd['name']} keepout {i + 1}")],
                        ["hatch", "edge", 0.5], ["connect_pads", ["clearance", 0]], ["min_thickness", 0.25],
                        ["filled_areas_thickness", "no"], ["keepout"] + rules,
                        ["fill", ["thermal_gap", 0.5], ["thermal_bridge_width", 0.5]],
                        ["polygon", ["pts"] + [["xy", round(ox + x, 4), round(oy + y, 4)] for x, y in pts]]])
    return out


def _pad_shapes(fp: dict, x: float, y: float, r: float, layers_all: list[str]):
    """(pad number, layers, polygon) of a footprint placed at x, y, r (pads as rectangles)."""
    from shapely.geometry import box
    out = []
    for p in fp["pads"]:
        if not p["number"]:
            continue
        w, h = (p["w"], p["h"]) if (p["rot"] + r) % 180 == 0 else (p["h"], p["w"])
        dx, dy = rot(p["x"], p["y"], r)
        lay = layers_all if p["kind"] == "thru_hole" else [L for L in p["layers"] if L in layers_all]
        out.append((p["number"], lay, box(x + dx - w / 2, y + dy - h / 2, x + dx + w / 2, y + dy + h / 2)))
    return out


def connected_nets(block: dict, libs, via_dia: float = 0.6) -> set[str]:
    """Block nets whose pads the block copper joins into one piece (nets the router need not touch)."""
    from shapely.geometry import LineString, Point
    layers = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    items: dict[str, list] = {}
    for role, r in (block.get("roles") or {}).items():
        q = block["placement"][role]
        fp = libs.footprint(r["footprint"])
        for num, lay, g in _pad_shapes(fp, float(q["x"]), float(q["y"]), float(q.get("rot", 0)), layers):
            n = pins(r).get(num)
            if n is not None:
                items.setdefault(n, []).append(("pad", set(lay), g))
    cu = block.get("copper") or {}
    for t in cu.get("tracks") or []:
        g = LineString([tuple(map(float, p)) for p in t["pts"]]).buffer(float(t["w"]) / 2)
        items.setdefault(str(t["net"]), []).append(("cu", {str(t["layer"])}, g))
    for v in cu.get("vias") or []:
        items.setdefault(str(v["net"]), []).append(("cu", set(layers), Point(float(v["x"]), float(v["y"])).buffer(via_dia / 2)))
    out = set()
    for net, its in items.items():
        pads = [i for i, x in enumerate(its) if x[0] == "pad"]
        if len(pads) < 2:
            continue
        seen, todo = {pads[0]}, [pads[0]]
        while todo:
            i = todo.pop()
            for j, x in enumerate(its):
                if j not in seen and its[i][1] & x[1] and its[i][2].intersects(x[2]):
                    seen.add(j)
                    todo.append(j)
        if all(i in seen for i in pads):
            out.add(net)
    return out


def closed_nets(design, libs) -> set[str]:
    """Design nets whose every pad is in one block and joined by its copper: the router leaves them alone."""
    conns = design.nets()
    out = set()
    for bnd in bindings(design):
        b, refs = bnd["block"], bnd["parts"]
        nm, bad = netmap(design, bnd)
        if bad:
            continue
        joined = connected_nets(b, libs, float(design.board["via_dia_mm"]))
        inside = {(r, p) for role, r in refs.items() for p in pins((b.get("roles") or {}).get(role, {}))}
        for bn, dn in nm.items():
            same = [x for x, y in nm.items() if y == dn]
            if all(x in joined for x in same) and len(same) == 1 and all((r, p) in inside for r, p in conns.get(dn, [])):
                out.add(dn)
    return out


# ------------------------------------------------------------------ matching (reference layouts as presets)

def match(design, block: dict, exclude: set[str] | None = None, limit: int = 4) -> list[dict]:
    """Bindings of `block` onto parts of `design` that `check` would accept on the netlist: every role on a distinct
    part with the role's footprint, block net -> design net consistent on every pin, two block nets on one design net
    only when both are ports, and no internal block net reaching a part outside the binding. Values are compared but
    do not stop a match (`value_diffs`). Returns up to `limit` [{parts: role -> ref, netmap, value_diffs}]."""
    roles = {str(k): v for k, v in (block.get("roles") or {}).items()}
    if not roles:
        return []
    P = ports(block)
    exclude = exclude or set()
    by_fp: dict[str, list] = {}
    for c in design.components.values():
        if c.ref not in exclude and not c.get("virtual"):
            by_fp.setdefault(str(c.get("footprint")), []).append(c)
    cands = {role: by_fp.get(str(r.get("footprint")), []) for role, r in roles.items()}
    if any(not v for v in cands.values()):
        return []
    order = sorted(roles, key=lambda role: (len(cands[role]), -len(pins(roles[role]))))
    conns = design.nets()
    out: list[dict] = []

    def extend(nm: dict, role: str, c) -> dict | None:
        nm = dict(nm)
        for pin, bn in pins(roles[role]).items():
            dn = c.pins.get(pin)
            if dn is None or dn.upper() in ("NC", "~"):
                return None
            if nm.setdefault(bn, dn) != dn:
                return None
        rev: dict[str, set] = {}
        for bn, dn in nm.items():
            rev.setdefault(dn, set()).add(bn)
        if any(len(b) > 1 and not b <= P for b in rev.values()):
            return None
        return nm

    def closed(nm: dict, parts: dict) -> bool:
        inside = {(ref, p) for role, ref in parts.items() for p in pins(roles[role])}
        return all(all((r, p) in inside for r, p in conns.get(dn, [])) for bn, dn in nm.items() if bn not in P)

    def walk(i: int, nm: dict, parts: dict):
        if len(out) >= limit:
            return
        if i == len(order):
            if closed(nm, parts):
                diffs = {role: msg for role, ref in parts.items()
                         if (msg := _value_mismatch(design.components[ref], roles[role]))}
                out.append({"parts": dict(parts), "netmap": nm, "value_diffs": diffs})
            return
        role = order[i]
        for c in cands[role]:
            if c.ref in parts.values():
                continue
            nm2 = extend(nm, role, c)
            if nm2 is not None:
                walk(i + 1, nm2, {**parts, role: c.ref})

    walk(0, {}, {})
    return out


def suggest(design) -> list[dict]:
    """Library blocks that match unbound parts of the design (one binding per block and part set): the preset a design
    can take with `blocks: {<entry>: {use: <block>, parts: {...}}}`. [{block, parts, value_diffs, source}]"""
    bound = {ref for b in bindings(design) for ref in b["parts"].values()}
    back = {r for r, c in design.components.items() if str((c.get("placement") or {}).get("side", "front")) != "front"}
    out, used = [], set(bound) | back  # blocks are front side only
    for n in names(design):
        b = load(n, design)
        for m in match(design, b, exclude=used, limit=32):
            if set(m["parts"].values()) & used:
                continue
            used |= set(m["parts"].values())
            placed = [ref for ref in m["parts"].values()
                      if {"x", "y", "edge"} & set((design.components[ref].get("placement") or {}))]
            out.append({"block": n, "entry": n, "parts": m["parts"], "value_diffs": m["value_diffs"], "placed": placed,
                        "source": " ".join(str(b.get("source") or "").split())[:160]})
    taken = set((design.raw.get("blocks") or {}))
    for m in out:  # entry names unique in `blocks:` (a block bound twice gets its first part's ref)
        if sum(x["block"] == m["block"] for x in out) > 1 or m["entry"] in taken:
            m["entry"] = f"{m['block']}_{next(iter(m['parts'].values())).lower()}"
    return out



def write(block: dict, root: Path | None = None) -> Path:
    """blocks/<name>/block.yaml, leaf lists and maps in flow style."""
    d = Path(root or library_root()) / block["name"]
    d.mkdir(parents=True, exist_ok=True)
    f = d / "block.yaml"
    f.write_text(yaml.safe_dump(block, sort_keys=False, default_flow_style=None, width=120))
    return f


def describe(name: str, design=None) -> str:
    b = load(name, design)
    x0, y0, x1, y1 = courtyard(b)
    cu = b.get("copper") or {}
    L = [f"{b['name']}: {str(b.get('description', '')).strip()}",
         f"  source: {str(b.get('source', '')).strip()}",
         f"  ports: {', '.join(sorted(ports(b)))}; internal nets: {', '.join(sorted(block_nets(b) - ports(b))) or '-'}",
         f"  courtyard {x1 - x0:.2f} x {y1 - y0:.2f} mm, layers_min {b.get('layers_min', 2)}, "
         f"{len(cu.get('tracks') or [])} tracks, {len(cu.get('vias') or [])} vias, rules {b.get('rules') or {}}"]
    for role, r in (b.get("roles") or {}).items():
        q = (b.get("placement") or {}).get(role, {})
        val = r.get("value") or "/".join(f"{k} {r[k]}" for k in ("value_min", "value_max") if k in r)
        L.append(f"  {role:<6} {r.get('ref', ''):<5} {r.get('footprint')} {val} at ({q.get('x')}, {q.get('y')}) "
                 f"rot {q.get('rot', 0)}  pins {r.get('pins')}")
    for k, v in (b.get("resistance_mohm") or {}).items():
        L.append(f"  copper resistance {k}: {v} mOhm")
    return "\n".join(L)
