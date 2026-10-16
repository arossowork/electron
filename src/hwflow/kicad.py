"""kicad-cli wrapper: ERC, DRC (with zone refill), exports and renders."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

_CLI = [os.environ.get("KICAD_CLI", ""), shutil.which("kicad-cli") or "",
        os.path.expanduser("~/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"),
        "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"]


def cli() -> str:
    for c in _CLI:
        if c and Path(c).exists():
            return c
    raise FileNotFoundError("kicad-cli not found; set KICAD_CLI")


def _run(args: list[str], timeout=600) -> subprocess.CompletedProcess:
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


def _violations(report: dict) -> list[dict]:
    out = []
    for v in report.get("violations", []):
        out.append({"sev": v.get("severity"), "type": v.get("type"), "msg": v.get("description"),
                    "items": [i.get("description") for i in v.get("items", [])]})
    for sheet in report.get("sheets", []):
        for v in sheet.get("violations", []):
            out.append({"sev": v.get("severity"), "type": v.get("type"), "msg": v.get("description"),
                        "items": [i.get("description") for i in v.get("items", [])]})
    for v in report.get("unconnected_items", []):
        out.append({"sev": "error", "type": "unconnected", "msg": v.get("description"),
                    "items": [i.get("description") for i in v.get("items", [])]})
    for v in report.get("schematic_parity", []):
        out.append({"sev": v.get("severity", "error"), "type": "parity:" + str(v.get("type")), "msg": v.get("description"),
                    "items": [i.get("description") for i in v.get("items", [])]})
    return out


def erc(sch: Path) -> list[dict]:
    out = sch.with_suffix(".erc.json")
    _run(["sch", "erc", "--format", "json", "--severity-all", "-o", str(out), str(sch)])
    return _violations(json.loads(out.read_text()))


def drc(pcb: Path, refill=True, parity=False) -> list[dict]:
    out = pcb.with_suffix(".drc.json")
    args = ["pcb", "drc", "--format", "json", "--severity-all", "-o", str(out)]
    if refill:
        args += ["--refill-zones", "--save-board"]
    if parity:
        args += ["--schematic-parity"]
    p = _run(args + [str(pcb)])
    if not out.exists():
        raise RuntimeError(p.stdout + p.stderr)
    return _violations(json.loads(out.read_text()))


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
