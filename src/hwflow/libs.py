"""KiCad symbol and footprint library access (read-only, no KiCad Python API needed)."""

from __future__ import annotations

import copy
import os
import re
from functools import lru_cache
from pathlib import Path

from . import sexpr
from .sexpr import Q

CANDIDATES = [
    os.environ.get("KICAD_SHARE", ""),
    os.path.expanduser("~/Applications/KiCad/KiCad.app/Contents/SharedSupport"),
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport",
    "/usr/share/kicad",
    "/usr/local/share/kicad",
]


def share_dir() -> Path:
    for c in CANDIDATES:
        if c and (Path(c) / "symbols").exists():
            return Path(c)
    raise FileNotFoundError("KiCad libraries not found. Set KICAD_SHARE to the folder holding symbols/ and footprints/")


PROJECT_LIB_DIRS = ("lib", "libs", "kicad_share")


def project_lib_dirs(design) -> list[Path]:
    """Project libraries: `libraries:` in design.yaml plus conventional folders. Each may hold symbols/ and
    footprints/ subfolders or the .kicad_sym files and .pretty folders directly."""
    out = []
    for rel in list(design.raw.get("libraries") or []) + [d for d in PROJECT_LIB_DIRS if (design.dir / d).is_dir()]:
        base = (design.dir / rel).resolve()
        for cand in (base, base / "symbols", base / "footprints"):
            if cand.is_dir() and cand not in out:
                out.append(cand)
    return out


class Libs:
    def __init__(self, extra_dirs: list[Path] | None = None, design=None):
        self.root = share_dir()
        self.extra = [Path(p) for p in (extra_dirs or [])] + (project_lib_dirs(design) if design is not None else [])

    # ------------------------------------------------------------ symbols
    def _sym_lib_path(self, lib: str) -> Path:
        for base in self.extra + [self.root / "symbols"]:
            for cand in (base / f"{lib}.kicad_sym", base / f"{lib}.kicad_symdir"):
                if cand.exists():
                    return cand
        raise FileNotFoundError(f"symbol library {lib!r} not found")

    @lru_cache(maxsize=256)
    def _sym_lib(self, lib: str) -> dict[str, list]:
        p = self._sym_lib_path(lib)
        files = sorted(p.glob("*.kicad_sym")) if p.is_dir() else [p]
        out = {}
        for f in files:
            tree = sexpr.loads(f.read_text())
            for s in sexpr.find_all(tree, "symbol"):
                out[str(s[1])] = s
        return out

    def symbol_tree(self, lib_id: str) -> list:
        """Flattened symbol S-expression named `lib:name` ready for embedding in lib_symbols."""
        lib, name = lib_id.split(":", 1)
        syms = self._sym_lib(lib)
        if name not in syms:
            close = [s for s in syms if name.lower() in s.lower()][:8]
            raise KeyError(f"symbol {lib_id} not found" + (f"; similar: {close}" if close else ""))
        s = copy.deepcopy(syms[name])
        ext = sexpr.find(s, "extends")
        if ext:
            parent = copy.deepcopy(self.symbol_tree(f"{lib}:{ext[1]}"))
            pname = str(ext[1])
            child_props = {str(p[1]): p for p in sexpr.find_all(s, "property")}
            flat = [s[0], Q(name)]
            for item in parent[2:]:
                if isinstance(item, list) and item[0] == "property" and str(item[1]) in child_props:
                    flat.append(child_props.pop(str(item[1])))
                elif isinstance(item, list) and item[0] == "symbol":
                    sub = copy.deepcopy(item)
                    sub[1] = Q(str(sub[1]).replace(pname.split(":")[-1], name, 1))
                    flat.append(sub)
                else:
                    flat.append(item)
            # child-only properties go before the unit symbols
            idx = next(i for i, x in enumerate(flat) if isinstance(x, list) and x[0] == "symbol")
            for p in child_props.values():
                flat.insert(idx, p)
                idx += 1
            s = flat
        s[1] = Q(f"{lib}:{name}" if ":" not in str(s[1]) else str(s[1]))
        return s

    def symbol(self, lib_id: str) -> dict:
        tree = self.symbol_tree(lib_id)
        name = lib_id.split(":", 1)[1]
        pins = []
        units = set()
        for sub in sexpr.find_all(tree, "symbol"):
            m = re.match(rf"^{re.escape(name)}_(\d+)_(\d+)$", str(sub[1]))
            if not m:
                continue
            unit, style = int(m.group(1)), int(m.group(2))
            if style == 2:
                continue
            if unit:
                units.add(unit)
            for p in sexpr.find_all(sub, "pin"):
                at = sexpr.find(p, "at")
                pins.append({
                    "type": str(p[1]), "number": str(sexpr.find(p, "number")[1]),
                    "name": str(sexpr.find(p, "name")[1]), "unit": unit,
                    "x": float(at[1]), "y": float(at[2]), "angle": float(at[3]) if len(at) > 3 else 0.0,
                    "length": float(sexpr.value(p, "length", 0)), "hidden": "hide" in [str(x) for x in p],
                })
        props = {str(p[1]): str(p[2]) for p in sexpr.find_all(tree, "property")}
        return {"lib_id": lib_id, "pins": pins, "units": sorted(units) or [1], "props": props, "tree": tree}

    def _symbol_index(self) -> list[tuple[str, str]]:
        """[(lib:name, description keywords)] cached in ~/.cache/hwflow, rebuilt when KiCad changes."""
        import json
        cache = Path.home() / ".cache" / "hwflow" / "symbol_index.json"
        sym_dir = self.root / "symbols"
        stamp = f"{sym_dir}:{sym_dir.stat().st_mtime}"
        if cache.exists():
            data = json.loads(cache.read_text())
            if data.get("stamp") == stamp:
                return [tuple(x) for x in data["items"]]
        items = []
        for f in sorted(sym_dir.iterdir()):
            if f.suffix not in (".kicad_sym", ".kicad_symdir"):
                continue
            lib = f.name.split(".")[0]
            try:
                names = self._sym_lib(lib)
            except Exception:  # noqa: BLE001
                continue
            for n, s in names.items():
                desc = " ".join(str(p[2]) for p in sexpr.find_all(s, "property")
                                if str(p[1]) in ("Description", "ki_description", "ki_keywords"))
                items.append((f"{lib}:{n}", desc))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"stamp": stamp, "items": items}))
        return items

    def search_symbols(self, query: str, limit: int = 30) -> list[tuple[str, str]]:
        q = query.lower().split()
        res = []
        for lid, desc in self._symbol_index():
            if all(t in f"{lid} {desc}".lower() for t in q):
                res.append((lid, desc[:110]))
                if len(res) >= limit:
                    break
        return res

    # ------------------------------------------------------------ footprints
    def footprint_path(self, fp_id: str) -> Path:
        lib, name = fp_id.split(":", 1)
        for base in self.extra + [self.root / "footprints"]:
            p = base / f"{lib}.pretty" / f"{name}.kicad_mod"
            if p.exists():
                return p
        d = self.root / "footprints" / f"{lib}.pretty"
        close = [f.stem for f in d.glob("*.kicad_mod") if name.lower()[:8] in f.stem.lower()][:8] if d.exists() else []
        raise FileNotFoundError(f"footprint {fp_id} not found" + (f"; similar: {close}" if close else ""))

    @lru_cache(maxsize=512)
    def footprint_tree(self, fp_id: str) -> list:
        return sexpr.loads(self.footprint_path(fp_id).read_text())

    def footprint(self, fp_id: str) -> dict:
        t = self.footprint_tree(fp_id)
        pads = []
        for p in sexpr.find_all(t, "pad"):
            at = sexpr.find(p, "at")
            size = sexpr.find(p, "size")
            layers = [str(x) for x in (sexpr.find(p, "layers") or [])[1:]]
            drill = sexpr.find(p, "drill")
            dval = None
            if drill:
                nums = [x for x in drill[1:] if not isinstance(x, list) and re.match(r"^[\d.]+$", str(x))]
                dval = float(nums[0]) if nums else None
            pads.append({
                "number": str(p[1]), "kind": str(p[2]), "shape": str(p[3]),
                "x": float(at[1]), "y": float(at[2]), "rot": float(at[3]) if len(at) > 3 else 0.0,
                "w": float(size[1]), "h": float(size[2]), "layers": layers, "drill": dval,
                "rratio": float(sexpr.value(p, "roundrect_rratio", 0.25)),
                "hull": sexpr.custom_pad_hull(p),
            })
        xs, ys = [], []
        side_pts = {"F.CrtYd": ([], []), "B.CrtYd": ([], [])}
        for kind in ("fp_line", "fp_rect", "fp_circle", "fp_poly", "fp_arc"):
            for g in sexpr.find_all(t, kind):
                layer = str(sexpr.value(g, "layer", ""))
                if layer not in ("F.CrtYd", "B.CrtYd"):
                    continue
                n0 = len(xs)
                for key in ("start", "end", "mid", "center"):
                    e = sexpr.find(g, key)
                    if e:
                        xs.append(float(e[1]))
                        ys.append(float(e[2]))
                pts = sexpr.find(g, "pts")
                if pts:
                    for xy in sexpr.find_all(pts, "xy"):
                        xs.append(float(xy[1]))
                        ys.append(float(xy[2]))
                if kind == "fp_circle":
                    c, e = sexpr.find(g, "center"), sexpr.find(g, "end")
                    r = ((float(c[1]) - float(e[1])) ** 2 + (float(c[2]) - float(e[2])) ** 2) ** 0.5
                    xs += [float(c[1]) - r, float(c[1]) + r]
                    ys += [float(c[2]) - r, float(c[2]) + r]
                side_pts[layer][0].extend(xs[n0:])
                side_pts[layer][1].extend(ys[n0:])
        if not xs:
            for p in pads:
                xs += [p["x"] - p["w"] / 2, p["x"] + p["w"] / 2]
                ys += [p["y"] - p["h"] / 2, p["y"] + p["h"] / 2]
        sides = {}
        for layer, side in (("F.CrtYd", "front"), ("B.CrtYd", "back")):
            sx, sy = side_pts[layer]
            if sx:
                sides[side] = (min(sx), min(sy), max(sx), max(sy))
        if not sides:
            sides["front"] = (min(xs), min(ys), max(xs), max(ys))
        return {"id": fp_id, "pads": pads, "bbox": (min(xs), min(ys), max(xs), max(ys)), "sides": sides, "tree": t,
                "smd": all(p["kind"] == "smd" for p in pads if p["number"])}

    def search_footprints(self, query: str, limit: int = 40) -> list[str]:
        q = query.lower().split()
        res = []
        for d in sorted((self.root / "footprints").glob("*.pretty")):
            for f in sorted(d.glob("*.kicad_mod")):
                hay = f"{d.stem}:{f.stem}".lower()
                if all(t in hay for t in q):
                    res.append(f"{d.stem}:{f.stem}")
                    if len(res) >= limit:
                        return res
        return res
