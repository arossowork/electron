"""Floorplan items as parts with real copper, before the circuit exists.

An item with `footprint: Lib:Name` (a footprint chosen with `hwflow lib fp`) or with `size_mm` and `pin_rows` (a body
box with rows of pins, before a footprint exists) becomes a synthetic component of a copy of the design: the
footprint's pads (through-hole pins come through to the other face), its courtyard per face, `rot`, a fixed position
when the item has `at` (the footprint origin, as `placement: {x, y}`; the box centre for `pin_rows`), and the nets of
`pins:` (pad number -> net) or of the rows' `nets`. The floorplan check then sees what the real part will do: a
relay's coil pins under the battery holder, a terminal's pads in an isolation band, a corridor too narrow for the mains
tracks (the layout proofs run on these nets).

A footprint drawn for the other face than the item's `side` is mirrored in x (the repository's `_back` convention:
body on B.CrtYd/B.Fab/B.SilkS, through-hole pads unchanged). Without `side`, an item takes the face its footprint is
drawn for. Generated footprints go to <project>/build/fpsynth/fpsynth.pretty (regenerated on every run)."""

from __future__ import annotations

import copy
import re

from ...core import sexpr
from ...core.design import Component, Design

LIB = "fpsynth"
TAG = "~item~"  # component group of a synthetic part: the item resolves to it (floorplan.resolve, by group)
_GEO_KEYS = ("at", "start", "end", "mid", "center", "xy")


def _swap_layer(name: str) -> str:
    if name.startswith("F."):
        return "B." + name[2:]
    if name.startswith("B."):
        return "F." + name[2:]
    return name


def mirror(tree: list) -> list:
    """The footprint mirrored in x for the other face: every x negated, pad and text angles negated, F.* and B.*
    layers swapped (the plated through-hole pads stay on *.Cu)."""
    t = copy.deepcopy(tree)

    def walk(node):
        if not isinstance(node, list) or not node:
            return
        head = node[0]
        if head in _GEO_KEYS and len(node) >= 3 and _num(node[1]):
            node[1] = -float(node[1])
            if head == "at" and len(node) >= 4 and _num(node[3]):
                node[3] = (-float(node[3])) % 360
        elif head in ("layer", "layers"):
            for i in range(1, len(node)):
                if isinstance(node[i], str):
                    v = _swap_layer(str(node[i]))
                    node[i] = sexpr.Q(v) if isinstance(node[i], sexpr.Q) else v
        for x in node[1:]:
            walk(x)
    for x in t[1:]:  # the footprint itself stays on F.Cu, as the `_back` footprints of the shared library do
        if not (isinstance(x, list) and x and x[0] == "layer"):
            walk(x)
    return t


def _num(v) -> bool:
    try:
        float(v)
        return not isinstance(v, list)
    except (TypeError, ValueError):
        return False


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)


def _box_footprint(name: str, it: dict, side: str) -> str:
    """A footprint for a box placeholder with pin rows: origin at the box centre, courtyard = the box."""
    w, h = it["size"]
    L = "F" if side == "front" else "B"
    out = [f'(footprint "{name}" (layer "F.Cu")',
           f'  (fp_rect (start {-w / 2:.4f} {-h / 2:.4f}) (end {w / 2:.4f} {h / 2:.4f}) (stroke (width 0.05) '
           f'(type solid)) (fill none) (layer "{L}.CrtYd"))']
    n = 1
    for r in it["pin_rows"]:
        for i in range(r["n"]):
            dx, dy = (i * r["pitch"], 0.0) if r["dir"] == "x" else (0.0, i * r["pitch"])
            x, y = r["at"][0] + dx, r["at"][1] + dy
            if r["smd"]:
                out.append(f'  (pad "{n}" smd rect (at {x:.4f} {y:.4f}) (size {r["pad"]:.3f} {r["pad"]:.3f}) '
                           f'(layers "{L}.Cu" "{L}.Mask"))')
            else:
                out.append(f'  (pad "{n}" thru_hole circle (at {x:.4f} {y:.4f}) (size {r["pad"]:.3f} {r["pad"]:.3f}) '
                           f'(drill {r["drill"]:.3f}) (layers "*.Cu" "*.Mask"))')
            n += 1
    out.append(")")
    return "\n".join(out) + "\n"


def _row_nets(it: dict) -> dict:
    out, n = {}, 1
    for r in it["pin_rows"]:
        for i in range(r["n"]):
            if i < len(r["nets"]) and r["nets"][i]:
                out[str(n)] = r["nets"][i]
            n += 1
    return out


def synth(design, libs, fp: dict, findings: list | None = None):
    """(design copy with a synthetic component per footprint or pin-row item that names no real part yet, libs that
    also read the generated footprints, {item name: synthetic refs}). Mutates the parsed items of `fp` (side, groups)
    to match. A footprint that cannot be read is a finding ({name, ok, msg}) in `findings`, and the item stays a box
    placeholder of its bounding box (or 1 x 1 mm)."""
    from ...core.floorplan import resolve
    todo = [it for it in fp["items"] if it["footprint"] or it["pin_rows"]]
    if not todo:
        return design, libs, {}
    res = resolve(fp, design, list(design.components))
    raw = copy.deepcopy(design.raw)
    d2 = Design(path=design.path, raw=raw)
    d2.components = dict(design.components)
    d2.budget_widths = dict(design.budget_widths)
    out_dir = design.path.parent / "build" / "fpsynth" / f"{LIB}.pretty"
    out_dir.mkdir(parents=True, exist_ok=True)
    libs2 = copy.copy(libs)
    libs2.extra = [out_dir.parent] + list(libs.extra)
    raw_items = {str(r.get("name") or r.get("ref") or f"item{i + 1}"): r
                 for i, r in enumerate((raw.get("floorplan") or {}).get("items") or [])}
    made: dict[str, list[str]] = {}
    for it in todo:
        names = res.get(it["name"]) or []
        if names and names[0] in design.components:
            continue  # the circuit already names the real part
        if it["pin_rows"]:
            side = it["side"]
            fid = f"{LIB}:item_{_safe(it['name'])}"
            (out_dir / f"item_{_safe(it['name'])}.kicad_mod").write_text(_box_footprint(f"item_{_safe(it['name'])}", it, side))
            pins = {**_row_nets(it), **it["pins"]}
        else:
            try:
                f = libs.footprint(it["footprint"])
            except Exception as e:  # noqa: BLE001 - a missing footprint is a finding, not a crash
                if findings is not None:
                    findings.append({"name": "field", "ok": False, "msg": f"item {it['name']}: footprint "
                                     f"{it['footprint']} not found ({type(e).__name__}: {e}); `hwflow lib fp <words>` "
                                     f"lists the library footprints"})
                if not it["size"]:
                    it["size"] = (1.0, 1.0)
                it["footprint"] = None
                continue
            have = set(f["sides"])
            side = it["side"] if it["side_given"] or len(have) != 1 else next(iter(have))
            fid = it["footprint"]
            if side not in have and len(have) == 1:  # drawn for the other face: mirror it, as a `_back` footprint
                fid = f"{LIB}:{_safe(it['footprint'])}__{side}"
                (out_dir / f"{fid.split(':', 1)[1]}.kicad_mod").write_text(sexpr.dumps(mirror(f["tree"])) + "\n")
            pins = dict(it["pins"])
            nums = {p["number"] for p in f["pads"] if p["number"]}
            bad = sorted(set(pins) - nums)
            if bad and findings is not None:
                findings.append({"name": "field", "ok": False, "msg": f"item {it['name']}: pins {', '.join(bad)} are "
                                 f"not pads of {it['footprint']} (pads: {', '.join(sorted(nums)) or 'none'}; "
                                 f"`hwflow lib pads {it['footprint']}`)"})
                pins = {k: v for k, v in pins.items() if k in nums}
        it["side"] = side
        tag = f"{TAG}{it['name']}"
        it["groups"] = list(it["groups"]) + [tag]
        ri = raw_items.get(it["name"])
        if ri is not None:
            ri["side"] = side
            ri["groups"] = list(ri.get("groups") or []) + [tag]
        plain = [r for r in it["refs"] if re.fullmatch(r"[A-Za-z]+[A-Za-z0-9_]*", r)]
        base = plain[0] if len(plain) == 1 and it["count"] <= 1 else re.sub(r"[^A-Za-z0-9_]", "_", it["name"])
        if not re.match(r"[A-Za-z]", base):
            base = "X" + base
        refs = [base] if it["count"] <= 1 else [f"{base}_{i + 1}" for i in range(it["count"])]
        for ref in refs:
            k = ref
            while k in d2.components:
                k += "_"
            data = {"footprint": fid, "pins": pins, "group": tag, "side": side, "floorplan_item": it["name"],
                    "role": "none"}
            if it["height"] is not None:
                data["height_mm"] = it["height"]
            if it["at"] and len(refs) == 1:
                b = next(b for b in fp["boards"] if b["name"] == it["board"])
                data["placement"] = {"x": b["x"] + float(it["at"][0]), "y": b["y"] + float(it["at"][1]),
                                     "rot": it["rot"], "fixed": True}
                if it["edge"]:
                    data["placement"]["edge"] = it["edge"]
            elif it["rot"] or it["edge"]:
                data["placement"] = {k2: v for k2, v in (("rot", it["rot"]), ("edge", it["edge"])) if v}
            d2.components[k] = Component(k, data)
            made.setdefault(it["name"], []).append(k)
    return d2, libs2, made


def is_synthetic(design, ref: str) -> bool:
    c = design.components.get(ref)
    return bool(c is not None and c.get("floorplan_item"))
