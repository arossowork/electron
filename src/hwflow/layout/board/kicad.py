"""kicad-cli wrapper: ERC, DRC (with zone refill), exports and renders."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ...core.settings import get_settings

def cli() -> str:
    c = get_settings().tools.kicad_cli  # KICAD_CLI, PATH, then the macOS app
    if c:
        return c
    raise FileNotFoundError("kicad-cli not found; set KICAD_CLI")


def _run(args: list[str], timeout=600) -> subprocess.CompletedProcess:
    # subprocess environment: the parent environment passes through to kicad-cli on purpose
    env = dict(os.environ, LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8", LANGUAGE="en")
    return subprocess.run([cli()] + args, capture_output=True, text=True, timeout=timeout, env=env)


def write_lib_tables(design, libs, outdir: Path):
    """Project-level sym-lib-table / fp-lib-table with absolute paths for every library used."""
    syms = sorted({c.get("symbol").split(":")[0] for c in design.components.values() if c.get("symbol")} | {"power"})
    fps = sorted({c.get("footprint").split(":")[0] for c in design.components.values() if c.get("footprint")})
    sl = ["(sym_lib_table", "  (version 7)"]
    for lib in syms:
        sl.append(f'  (lib (name "{lib}")(type "KiCad")(uri "{libs._sym_lib_path(lib)}")(options "")(descr ""))')
    fl = ["(fp_lib_table", "  (version 7)"]
    for lib in fps:
        p = next((b / f"{lib}.pretty" for b in libs.extra + [libs.root / "footprints"] if (b / f"{lib}.pretty").exists()), None)
        if p:
            fl.append(f'  (lib (name "{lib}")(type "KiCad")(uri "{p}")(options "")(descr ""))')
    (outdir / "sym-lib-table").write_text("\n".join(sl + [")"]) + "\n")
    (outdir / "fp-lib-table").write_text("\n".join(fl + [")"]) + "\n")


def _at(v: dict) -> list:
    """Positions (x, y in mm) of a violation's items, in the order of `items`; None where KiCad gives none."""
    return [[i["pos"]["x"], i["pos"]["y"]] if i.get("pos") else None for i in v.get("items", [])]


def _violations(report: dict) -> list[dict]:
    out = []
    for v in report.get("violations", []):
        out.append({"sev": v.get("severity"), "type": v.get("type"), "msg": v.get("description"),
                    "items": [i.get("description") for i in v.get("items", [])], "at": _at(v)})
    for sheet in report.get("sheets", []):
        for v in sheet.get("violations", []):
            out.append({"sev": v.get("severity"), "type": v.get("type"), "msg": v.get("description"),
                        "items": [i.get("description") for i in v.get("items", [])], "at": _at(v)})
    for v in report.get("unconnected_items", []):
        out.append({"sev": "error", "type": "unconnected", "msg": v.get("description"),
                    "items": [i.get("description") for i in v.get("items", [])], "at": _at(v)})
    for v in report.get("schematic_parity", []):
        out.append({"sev": v.get("severity", "error"), "type": "parity:" + str(v.get("type")), "msg": v.get("description"),
                    "items": [i.get("description") for i in v.get("items", [])], "at": _at(v)})
    return out


def erc(sch: Path) -> list[dict]:
    out = sch.with_suffix(".erc.json")
    _run(["sch", "erc", "--format", "json", "--severity-all", "-o", str(out), str(sch)])
    return _violations(json.loads(out.read_text()))


def _without_creepage(dru: str) -> str:
    """The .kicad_dru text without its creepage rules (the balanced `(rule ...)` blocks with a creepage constraint)."""
    out, i = [], 0
    while True:
        j = dru.find("(rule", i)
        if j < 0:
            break
        depth, k = 0, j
        while k < len(dru):
            depth += (dru[k] == "(") - (dru[k] == ")")
            k += 1
            if depth == 0:
                break
        out.append(dru[i:j])
        if "constraint creepage" not in dru[j:k]:
            out.append(dru[j:k])
        i = k
    return "".join(out) + dru[i:]


def drc_fast(pcb: Path, refill=True) -> list[dict]:
    """DRC without the creepage rules of the sibling .kicad_dru: 2-3 s against 15 s on a board with a creepage rule,
    because KiCad evaluates creepage over every item pair. A pre-check for a repair loop; creepage is left to the
    full `drc` of the candidate that is kept. It runs on a copy, so the board and its siblings stay as they are."""
    import tempfile
    pcb = Path(pcb)
    with tempfile.TemporaryDirectory(prefix="drcfast_", dir=pcb.parent) as td:
        tmp = Path(td) / pcb.name
        shutil.copy(pcb, tmp)
        if pcb.with_suffix(".kicad_pro").exists():
            shutil.copy(pcb.with_suffix(".kicad_pro"), tmp.with_suffix(".kicad_pro"))
        if pcb.with_suffix(".kicad_dru").exists():
            tmp.with_suffix(".kicad_dru").write_text(_without_creepage(pcb.with_suffix(".kicad_dru").read_text()))
        return [v for v in drc(tmp, refill=refill) if v["type"] != "creepage"]


def drc(pcb: Path, refill=True, parity=False, keepout_strict: bool | None = None) -> list[dict]:
    """`keepout_strict` adds the footprint keep-out errors KiCad drops when `fp-lib-table` resolves the keep-out
    footprint (see keepout.py). None reads HWFLOW_KEEPOUT_STRICT; on by default."""
    out = pcb.with_suffix(".drc.json")
    args = ["pcb", "drc", "--format", "json", "--severity-all", "-o", str(out)]
    if refill:
        args += ["--refill-zones", "--save-board"]
    if parity:
        args += ["--schematic-parity"]
    p = _run(args + [str(pcb)])
    if not out.exists():
        raise RuntimeError(p.stdout + p.stderr)
    res = _violations(json.loads(out.read_text()))
    from ..place import keepout
    if keepout.enabled(keepout_strict):
        res = keepout.merge(res, keepout.violations(pcb))
    return res


def export_fab(pcb: Path, sch: Path | None, outdir: Path) -> list[str]:
    outdir.mkdir(parents=True, exist_ok=True)
    g = outdir / "gerbers"
    g.mkdir(exist_ok=True)
    steps = [
        ["pcb", "export", "gerbers", "-o", str(g) + "/", str(pcb)],
        ["pcb", "export", "drill", "-o", str(g) + "/", str(pcb)],
        ["pcb", "export", "pos", "--format", "csv", "--units", "mm", "-o", str(outdir / "positions.csv"), str(pcb)],
        ["pcb", "export", "svg", "--mode-single", "--layers", "F.Cu,F.SilkS,F.Mask,Edge.Cuts", "--page-size-mode", "2",
         "--exclude-drawing-sheet", "-o", str(outdir / "pcb.svg"), str(pcb)],
        ["pcb", "export", "svg", "--mode-single", "--layers", "B.Cu,B.SilkS,Edge.Cuts", "--page-size-mode", "2",
         "--mirror", "--exclude-drawing-sheet", "-o", str(outdir / "pcb_bottom.svg"), str(pcb)],
        ["pcb", "render", "--output", str(outdir / "pcb_3d_top.png"), "--width", "1600", "--height", "1200",
         "--quality", "basic", str(pcb)],
    ]
    if sch:
        steps += [["sch", "export", "pdf", "-o", str(outdir / "schematic.pdf"), str(sch)],
                  ["sch", "export", "svg", "--exclude-drawing-sheet", "-o", str(outdir) + "/", str(sch)]]
    msgs = []
    for s in steps:
        p = _run(s)
        msgs.append(f"{' '.join(s[:3])}: {'ok' if p.returncode == 0 else 'FAILED ' + (p.stderr or p.stdout)[-300:]}")
    return msgs


def mirrored_back_parts(pcb: Path) -> dict[str, dict]:
    """Footprints drawn for the back face on F.Cu: a `_back` footprint is the front one mirrored in x, with its body
    (courtyard, fab) on B.* layers, placed on F.Cu. KiCad's position file calls them top. ref -> {rot (KiCad
    orientation on F.Cu)}. A footprint with F.CrtYd or F.Fab graphics, or one on B.Cu, is not one."""
    from ...core import sexpr
    t = sexpr.loads(Path(pcb).read_text())
    out = {}
    for fp in sexpr.find_all(t, "footprint"):
        if str(sexpr.value(fp, "layer")) != "F.Cu":
            continue
        lays = {str(sexpr.value(g, "layer")) for k in ("fp_line", "fp_rect", "fp_circle", "fp_poly", "fp_arc")
                for g in sexpr.find_all(fp, k)}
        if not lays & {"B.CrtYd", "B.Fab"} or lays & {"F.CrtYd", "F.Fab"}:
            continue
        ref = next((str(p[2]) for p in sexpr.find_all(fp, "property") if str(p[1]) == "Reference"), None)
        at = sexpr.find(fp, "at")
        if ref:
            out[ref] = {"rot": float(at[3]) if len(at) > 3 else 0.0}
    return out


def _deg(a: float) -> float:
    """An angle in KiCad's position-file range (-180, 180]."""
    a = (a + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


def fix_back_positions(pcb: Path, positions: Path, cpl: Path) -> dict:
    """Rewrite the KiCad position file so mirrored back-face footprints (mirrored_back_parts) are bottom side with the
    rotation KiCad writes for the same part really flipped to B.Cu: a `_back` footprint at orientation r on F.Cu has
    the pads of the front footprint flipped to B.Cu at r + 180 (checked against pcbnew's Flip). X and Y stay the board
    coordinates of the part (KiCad does not negate X on the bottom by default, and JLCPCB reads the KiCad file that
    way). Writes `cpl` in the JLCPCB column names (Designator, Mid X, Mid Y, Layer, Rotation) for every part.
    Returns {bottom: refs, tht: through-hole refs}."""
    import csv
    back = mirrored_back_parts(pcb)
    with open(positions, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return {"bottom": [], "tht": []}
    fields = list(rows[0])
    for r in rows:
        b = back.get(r["Ref"])
        if b is not None and r["Side"] == "top":
            r["Side"] = "bottom"
            r["Rot"] = f"{_deg(float(r['Rot']) + 180.0):.6f}"
    with open(positions, "w", newline="") as fh:
        w = csv.DictWriter(fh, fields)
        w.writeheader()
        w.writerows(rows)
    with open(cpl, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for r in rows:
            w.writerow([r["Ref"], f"{float(r['PosX']):.4f}mm", f"{float(r['PosY']):.4f}mm",
                        "Bottom" if r["Side"] == "bottom" else "Top", f"{float(r['Rot']):g}"])
    tht = sorted(_tht_refs(pcb))
    return {"bottom": sorted(r["Ref"] for r in rows if r["Side"] == "bottom"), "tht": tht}


def _tht_refs(pcb: Path) -> set[str]:
    from ...core import sexpr
    out = set()
    for fp in sexpr.find_all(sexpr.loads(Path(pcb).read_text()), "footprint"):
        attr = sexpr.find(fp, "attr")
        ref = next((str(p[2]) for p in sexpr.find_all(fp, "property") if str(p[1]) == "Reference"), None)
        if ref and attr and "through_hole" in [str(x) for x in attr[1:]]:
            out.add(ref)
    return out
