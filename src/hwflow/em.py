"""Optional 3D FDTD field-solver stage (openEMS + CSXCAD). PROTOTYPE.

openEMS is not a hard dependency. It is imported lazily; `available()` tells the
caller whether the stage can run. On macOS arm64 it is built from source into a
user prefix (see docs/em-openems-spike.md); its Python bindings live in their own
venv, so `hwflow em` re-executes under that interpreter when the bindings are not
importable from the project venv (`HWFLOW_OPENEMS_PYTHON`).

Two entry points:

- `msl_line()`: straight microstrip with openEMS MSL ports. Returns the line Z0 and
  effective er from the port's E/H probes plus S11/S21. Used as a validation case
  against `postlayout.microstrip()`.
- `simulate_net()`: one routed net from a `.kicad_pcb`: its trace segments and vias,
  the filled GND zones (or a solid plane on the opposite layer when the board has no
  zone fill), and the substrate. Two lumped ports sit at two pads of the net, each
  between the F.Cu pad and the B.Cu ground directly under it. Returns S11/S21, a
  TDR impedance profile seen from port 1, run time, mesh size and peak memory.
"""

from __future__ import annotations

import json
import math
import os
import resource
import sys
import time
from pathlib import Path

import numpy as np

from .core.settings import get_settings
from .pcb import read_board
from .postlayout import _edge_dist, _ellipk, _zone_fills, cpwg_z0, microstrip  # noqa: F401

C0 = 299792458.0
EPS0 = 8.854187817e-12


def available() -> bool:
    try:
        import CSXCAD  # noqa: F401
        import openEMS  # noqa: F401
        return True
    except ImportError:
        return False


def _oems():
    from CSXCAD import ContinuousStructure
    from openEMS import openEMS
    return ContinuousStructure, openEMS


def _peak_rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 1e6 if sys.platform == "darwin" else r / 1e3   # bytes on macOS, kB on Linux


def _kappa(er: float, tand: float, f: float) -> float:
    """Conductivity that gives loss tangent `tand` at frequency f (FDTD has no tand)."""
    return 2 * math.pi * f * EPS0 * er * tand


def _run(fdtd, sim_path: Path, threads: int = 0) -> float:
    cwd = os.getcwd()
    t0 = time.perf_counter()
    try:
        fdtd.Run(str(sim_path), cleanup=True, verbose=get_settings().tools.em_verbose,
                 numThreads=threads)
    finally:
        os.chdir(cwd)
    return time.perf_counter() - t0


def _mesh_stats(mesh) -> dict:
    n = [mesh.GetQtyLines(a) for a in "xyz"]
    d = [float(np.min(np.diff(mesh.GetLines(a)))) for a in "xyz"]
    dt = 1 / (C0 * math.sqrt(sum(1 / (x * 1e-3) ** 2 for x in d)))
    return {"lines": n, "cells": int(np.prod([k - 1 for k in n])), "min_cell_mm": [round(x, 4) for x in d],
            "dt_ps": round(dt * 1e12, 4)}


def _timesteps(sim_path: Path, dt_ps: float) -> int | None:
    """Simulated time steps: last probe time over the time step (probes are oversampled)."""
    for p in sorted(Path(sim_path).glob("port_ut*")):
        rows = [r for r in p.read_text().splitlines() if r.strip() and not r.startswith("%")]
        if rows:
            return int(round(float(rows[-1].split()[0]) / (dt_ps * 1e-12)))
    return None



# ---------------------------------------------------------------- validation case

def msl_line(w_mm=2.9, h_mm=1.6, t_mm=0.035, er=4.3, length_mm=30.0, fmax=3e9, tand=0.0,
             res_div=30, nz_sub=4, port_mm=20.0, sim_path: Path | None = None, threads: int = 0, end_db=-40.0,
             max_steps=200000, gap_mm: float | None = None) -> dict:
    """Straight microstrip between two MSL ports (reference planes `length_mm` apart).

    The strip is a zero-thickness PEC sheet; ground is the PEC z-min boundary. With `gap_mm`
    the line is a grounded coplanar waveguide: a same-layer ground pour `gap_mm` from each
    strip edge, tied to the plane along the substrate sides (an ideal via fence).
    """
    ContinuousStructure, openEMS = _oems()
    sim_path = Path(sim_path or Path.cwd() / "em_msl")
    P = port_mm                          # MSL port section; its measurement plane sits in its middle
    half = length_mm / 2 + P / 2         # strip ends (outside them: PML)
    side = max(10 * w_mm, 8 * h_mm, 15.0)
    air = max(8 * h_mm, 10.0)

    fdtd = openEMS(EndCriteria=10 ** (end_db / 10), NrTS=max_steps)
    fdtd.SetGaussExcite(fmax / 2, fmax / 2)
    fdtd.SetBoundaryCond(["PML_8", "PML_8", "MUR", "MUR", "PEC", "MUR"])
    csx = ContinuousStructure()
    fdtd.SetCSX(csx)
    mesh = csx.GetGrid()
    mesh.SetDeltaUnit(1e-3)
    res = C0 / (fmax * math.sqrt(er)) * 1e3 / res_div
    fine = min(res / 4, w_mm / 3, gap_mm / 3 if gap_mm else 1e9)
    third = np.array([2 * fine / 3, -fine / 3])            # "thirds rule" around the strip edges
    pml = 8 * res                        # PML_8 eats 8 cells at each x end: keep it outside the ports
    mesh.AddLine("x", [-half - pml, -half, -length_mm / 2, length_mm / 2, half, half + pml])
    mesh.SmoothMeshLines("x", res / 2)
    mesh.AddLine("y", [0])
    mesh.AddLine("y", w_mm / 2 + third)
    mesh.AddLine("y", -w_mm / 2 - third)
    if gap_mm:
        e = w_mm / 2 + gap_mm
        mesh.AddLine("y", [e - third[0], e - third[1], -e + third[0], -e + third[1]])
    mesh.SmoothMeshLines("y", fine, ratio=1.3)
    mesh.AddLine("y", [-side, side])
    mesh.SmoothMeshLines("y", res)
    mesh.AddLine("z", np.linspace(0, h_mm, nz_sub + 1))
    mesh.AddLine("z", h_mm + air)
    mesh.SmoothMeshLines("z", res)

    sub = csx.AddMaterial("FR4", epsilon=er, kappa=_kappa(er, tand, fmax / 2) if tand else 0)
    sub.AddBox([-half - pml, -side, 0], [half + pml, side, h_mm])
    pec = csx.AddMetal("PEC")
    for sgn in (-1, 1):                  # line continues into the PML (matched termination)
        pec.AddBox([sgn * half, -w_mm / 2, h_mm], [sgn * (half + pml), w_mm / 2, h_mm], priority=10)
    lo = length_mm / 2 - P / 2
    p1 = fdtd.AddMSLPort(1, pec, [-half, -w_mm / 2, h_mm], [-lo, w_mm / 2, 0], "x", "z", excite=-1,
                         FeedShift=P / 4, MeasPlaneShift=P / 2, priority=10)
    p2 = fdtd.AddMSLPort(2, pec, [half, -w_mm / 2, h_mm], [lo, w_mm / 2, 0], "x", "z",
                         MeasPlaneShift=P / 2, priority=10)
    pec.AddBox([-lo, -w_mm / 2, h_mm], [lo, w_mm / 2, h_mm], priority=10)
    if gap_mm:
        e = w_mm / 2 + gap_mm
        for sg in (-1, 1):
            pec.AddBox([-half - pml, sg * e, h_mm], [half + pml, sg * side, h_mm], priority=10)
            pec.AddBox([-half - pml, sg * side, 0], [half + pml, sg * side, h_mm], priority=10)
    stats = _mesh_stats(mesh)
    rt = _run(fdtd, sim_path, threads)

    f = np.linspace(fmax / 100, fmax, 100)
    p1.CalcPort(str(sim_path), f, ref_impedance=50)
    p2.CalcPort(str(sim_path), f, ref_impedance=50)
    s11 = p1.uf_ref / p1.uf_inc
    s21 = p2.uf_ref / p1.uf_inc
    p1.CalcPort(str(sim_path), f)                      # Z_ref -> measured line impedance
    zl = np.real(p1.Z_ref)
    eeff = (np.real(p1.beta) * C0 / (2 * math.pi * f)) ** 2
    ph = -np.unwrap(np.angle(s21))                     # cross-check: eeff from the S21 phase delay
    eeff_ph = (ph * C0 / (2 * math.pi * f * length_mm * 1e-3)) ** 2
    band = (f > 0.2 * fmax) & (f < 0.8 * fmax)
    z_cf, ee_cf = microstrip(w_mm, h_mm, t_mm, er)
    z_cf0, ee_cf0 = microstrip(w_mm, h_mm, 0.0, er) if not gap_mm else cpwg_z0(w_mm, gap_mm, h_mm, er)
    return {"z0_openems": float(np.median(zl[band])), "eeff_openems": float(np.median(eeff[band])),
            "eeff_s21_phase": float(np.median(eeff_ph[band])),
            "z0_formula_t": z_cf, "z0_formula_t0": z_cf0, "eeff_formula_t0": ee_cf0,
            "s11_db_max": float(np.max(20 * np.log10(np.abs(s11)))),
            "s21_db_min": float(np.min(20 * np.log10(np.abs(s21)))),
            "s21_db_at_fmax": float(20 * np.log10(abs(s21[-1]))),
            "f": f.tolist(), "z0_f": zl.tolist(), "s11_db": (20 * np.log10(np.abs(s11))).tolist(),
            "s21_db": (20 * np.log10(np.abs(s21))).tolist(),
            "runtime_s": rt, "peak_rss_mb": _peak_rss_mb(), "timesteps": _timesteps(sim_path, stats["dt_ps"]), "mesh": stats,
            "params": {"w_mm": w_mm, "h_mm": h_mm, "er": er, "length_mm": length_mm, "fmax": fmax,
                       "res_mm": res, "nz_sub": nz_sub, "port_mm": P, "gap_mm": gap_mm, "tand": tand}}


# ---------------------------------------------------------------- real board

def _stadium(x1, y1, x2, y2, w, n=6) -> np.ndarray:
    """Polygon (2xN) of a track segment with round ends."""
    a = math.atan2(y2 - y1, x2 - x1)
    r = w / 2
    pts = []
    for cx, cy, a0 in ((x2, y2, a - math.pi / 2), (x1, y1, a + math.pi / 2)):
        for k in range(n + 1):
            t = a0 + math.pi * k / n
            pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    return np.array(pts).T


def _grid(a: float, b: float, step: float, fixed: list[float]) -> list[float]:
    """Uniform lines from a to b plus `fixed` lines; base lines closer than step/2 to a fixed one are dropped,
    so the smallest cell (and so the FDTD time step) stays >= step/2."""
    base = [v for v in np.arange(a, b + step / 2, step) if all(abs(v - f) >= step / 2 for f in fixed)]
    return sorted({round(v, 6) for v in base + list(fixed)})


def _tdr(f: np.ndarray, s11: np.ndarray, z_ref: float, eeff: float):
    """Step-response TDR from S11 on a uniform grid starting at df. Returns (t_ns, dist_mm, Z)."""
    df = f[1] - f[0]
    n = len(f)
    s = np.concatenate([[np.real(s11[0])], s11])                  # DC by extrapolation
    win = np.cos(np.linspace(0, math.pi / 2, n + 1)) ** 2          # raised-cosine roll-off: ~1/fmax rise
    nfft = 8 * (n + 1)
    h = np.fft.irfft(s * win, nfft)
    # the band-limited impulse is symmetric around t=0: its t<0 half sits at the end of the record
    half = nfft // 2
    rho = np.cumsum(np.roll(h, half))[half:]
    t = np.arange(len(rho)) / (nfft * df)
    z = z_ref * (1 + rho) / np.clip(1 - rho, 1e-6, None)
    dist = t * C0 / math.sqrt(eeff) / 2 * 1e3                      # round trip -> one-way mm
    return t * 1e9, dist, z


def simulate_net(pcb: Path, net: str, ports: tuple[str, str] | None = None, *, h_mm=1.6, er=4.3, t_mm=0.035,
                 tand=0.02, fmax=6e9, cell_mm=0.1, via_drill_mm=0.3, via_dia_mm=0.6, gnd_nets=("GND",),
                 use_zone_fill=True, sim_path: Path | None = None, threads: int = 0, z_port=50.0,
                 end_db=-40.0, max_steps=300000, boundary="MUR", clearance_mm=0.2, keepout=True,
                 fill_layers=("F.Cu", "B.Cu")) -> dict:
    """Full-wave model of one routed net between two of its pads (2-layer boards).

    ports: ("J1.3", "U2.1"); default: the two pads of the net that are farthest apart.
    Pads of the net that are not ports are left open (as a high-impedance pin would be).
    """
    ContinuousStructure, openEMS = _oems()
    sim_path = Path(sim_path or Path.cwd() / f"em_{net}")
    b = read_board(Path(pcb))
    segs = [s for s in b["segments"] if s["net"] == net]
    vias = [v for v in b["vias"] if v["net"] == net]
    pads = {f"{ref}.{p['number']}": p for ref, fp in b["footprints"].items() for p in fp["pads"]
            if p["net"] == net}
    if len(pads) < 2:
        raise ValueError(f"net {net} has fewer than two pads")
    if ports is None:
        keys = list(pads)
        ports = max(((a, c) for i, a in enumerate(keys) for c in keys[i + 1:]),
                    key=lambda ac: math.hypot(pads[ac[0]]["x"] - pads[ac[1]]["x"], pads[ac[0]]["y"] - pads[ac[1]]["y"]))
    zcu = {"F.Cu": h_mm, "B.Cu": 0.0}
    fills = _zone_fills(Path(pcb)) if use_zone_fill else []
    fills = [z for z in fills if z["net"] in gnd_nets and z["layer"] in zcu and z["layer"] in fill_layers]
    gnd_segs = [s for s in b["segments"] if s["net"] in gnd_nets]
    gnd_pads = [(ref, p) for ref, fp in b["footprints"].items() for p in fp["pads"] if p["net"] in gnd_nets]

    xs = [x for s in segs for x in (s["x1"], s["x2"])] + [p["x"] for p in pads.values()]
    ys = [y for s in segs for y in (s["y1"], s["y2"])] + [p["y"] for p in pads.values()]
    if fills:
        xs += [float(z["pts"][0].min()) for z in fills] + [float(z["pts"][0].max()) for z in fills]
        ys += [float(z["pts"][1].min()) for z in fills] + [float(z["pts"][1].max()) for z in fills]
    x0, x1, y0, y1 = min(xs) - 1, max(xs) + 1, min(ys) - 1, max(ys) + 1

    fdtd = openEMS(EndCriteria=10 ** (end_db / 10), NrTS=max_steps)
    fdtd.SetGaussExcite(fmax / 2, fmax / 2)
    fdtd.SetBoundaryCond([boundary] * 6)
    csx = ContinuousStructure()
    fdtd.SetCSX(csx)
    mesh = csx.GetGrid()
    mesh.SetDeltaUnit(1e-3)
    lam = C0 / fmax * 1e3
    margin = max(5.0, lam / 10)

    # xy: uniform fine grid over the copper, graded out into the air box
    q = cell_mm / 10                      # snap grid: ports must sit exactly on mesh lines
    px = [round(round(pads[k]["x"] / q) * q, 6) for k in ports]
    py = [round(round(pads[k]["y"] / q) * q, 6) for k in ports]
    mesh.AddLine("x", _grid(x0, x1, cell_mm, px))
    mesh.AddLine("y", _grid(y0, y1, cell_mm, py))
    mesh.AddLine("x", [x0 - margin, x1 + margin])
    mesh.AddLine("y", [y0 - margin, y1 + margin])
    mesh.SmoothMeshLines("x", lam / 20, ratio=1.4)
    mesh.SmoothMeshLines("y", lam / 20, ratio=1.4)
    # z: 4 cells in the substrate, finer next to F.Cu, air above and a little below
    mesh.AddLine("z", [-margin / 2, 0, h_mm * 0.25, h_mm * 0.5, h_mm * 0.75, h_mm - 0.1, h_mm, h_mm + 0.1,
                       h_mm + margin])
    mesh.SmoothMeshLines("z", lam / 20, ratio=1.4)

    sub = csx.AddMaterial("FR4", epsilon=er, kappa=_kappa(er, tand, fmax / 2) if tand else 0)
    sub.AddBox([x0, y0, 0], [x1, y1, h_mm], priority=0)
    cu = csx.AddMetal("net")
    gnd = csx.AddMetal("gnd")
    for s in segs:
        if s["layer"] in zcu:
            cu.AddPolygon(_stadium(s["x1"], s["y1"], s["x2"], s["y2"], s["w"]), "z", zcu[s["layer"]], priority=10)
    for v in vias:
        cu.AddCylinder([v["x"], v["y"], 0], [v["x"], v["y"], h_mm], via_drill_mm / 2 + 0.025, priority=10)
        for z in zcu.values():
            cu.AddCylinder([v["x"], v["y"], z], [v["x"], v["y"], z], via_dia_mm / 2, priority=10)

    def pad_box(prop, p, prio):
        layers = list(zcu) if p["kind"] == "thru_hole" or "*.Cu" in p["layers"] else \
            [l for l in p["layers"] if l in zcu]
        for l in layers:
            prop.AddBox([p["x"] - p["w"] / 2, p["y"] - p["h"] / 2, zcu[l]],
                        [p["x"] + p["w"] / 2, p["y"] + p["h"] / 2, zcu[l]], priority=prio)
        if p["kind"] == "thru_hole":
            r = min(p["w"], p["h"]) / 2 * 0.6
            prop.AddCylinder([p["x"], p["y"], 0], [p["x"], p["y"], h_mm], r, priority=prio)

    for key, p in pads.items():
        if key in ports and p["kind"] == "thru_hole":
            p = dict(p, kind="smd", layers=["F.Cu"])     # barrel would short the vertical port
        pad_box(cu, p, 10)
    if fills:
        for z in fills:
            gnd.AddPolygon(z["pts"], "z", zcu[z["layer"]], priority=5)
        if keepout:
            # The pour sits `clearance_mm` from the net. On a mesh not much finer than that, the staircased
            # pour and trace touch and short the net. A higher-priority dielectric sheet over the clearance
            # band keeps at least one cell of gap (it slightly widens gaps below one cell instead).
            ko = csx.AddMaterial("keepout", epsilon=(er + 1) / 2)
            c = max(0.9 * clearance_mm, 1.5 * cell_mm)        # >= 1.5 cells, or staircased edges touch
            for sg in segs:
                if sg["layer"] in zcu:
                    ko.AddPolygon(_stadium(sg["x1"], sg["y1"], sg["x2"], sg["y2"], sg["w"] + 2 * c), "z",
                                  zcu[sg["layer"]], priority=8)
            for p in pads.values():
                for l in (list(zcu) if p["kind"] == "thru_hole" else [l for l in p["layers"] if l in zcu]):
                    ko.AddBox([p["x"] - p["w"] / 2 - c, p["y"] - p["h"] / 2 - c, zcu[l]],
                              [p["x"] + p["w"] / 2 + c, p["y"] + p["h"] / 2 + c, zcu[l]], priority=8)
    else:  # no zone fill in the file: solid plane on the layer the net does not use
        used = {s["layer"] for s in segs}
        lay = "B.Cu" if "B.Cu" not in used else "F.Cu"
        gnd.AddBox([x0, y0, zcu[lay]], [x1, y1, zcu[lay]], priority=5)
    for s in gnd_segs:
        if s["layer"] in zcu:
            gnd.AddPolygon(_stadium(s["x1"], s["y1"], s["x2"], s["y2"], s["w"]), "z", zcu[s["layer"]], priority=6)
    for _, p in gnd_pads:
        pad_box(gnd, p, 6)
    # probe ground: a B.Cu patch under each port, larger than the pad, so the port always lands on ground
    port_objs = []
    for i, k in enumerate(ports):
        p = pads[k]
        e = 0.6                                         # overlap the pour across a THT antipad
        gnd.AddBox([p["x"] - p["w"] / 2 - e, p["y"] - p["h"] / 2 - e, 0],
                   [p["x"] + p["w"] / 2 + e, p["y"] + p["h"] / 2 + e, 0], priority=9)
        port_objs.append(fdtd.AddLumpedPort(i + 1, z_port, [px[i], py[i], 0], [px[i], py[i], h_mm], "z",
                                            excite=1 if i == 0 else 0, priority=20))
    stats = _mesh_stats(mesh)
    rt = _run(fdtd, sim_path, threads)

    n = 400
    f = np.linspace(fmax / n, fmax, n)
    for p in port_objs:
        p.CalcPort(str(sim_path), f, ref_impedance=z_port)
    s11 = port_objs[0].uf_ref / port_objs[0].uf_inc
    s21 = port_objs[1].uf_ref / port_objs[0].uf_inc

    # closed form, segment by segment, as hwflow.postlayout does it
    z_cf, ee_cf = zip(*[microstrip(s["w"], h_mm, t_mm, er) for s in segs]) if segs else ((None,), (er,))
    # same-layer pour gap per segment (midpoint to nearest fill edge) -> grounded-CPW closed form
    cpw = []
    for sg in segs:
        same = [z for z in fills if z["layer"] == sg["layer"]]
        if not same:
            continue
        mx, my = (sg["x1"] + sg["x2"]) / 2, (sg["y1"] + sg["y2"]) / 2
        gap = min(_edge_dist(mx, my, z["pts"]) for z in same) - sg["w"] / 2
        L = math.hypot(sg["x2"] - sg["x1"], sg["y2"] - sg["y1"])
        if gap > 0.05 and L > 0.3:
            cpw.append((L, gap, *cpwg_z0(sg["w"], gap, h_mm, er)))
    lsum = sum(c[0] for c in cpw)
    z_cpw = sum(c[0] * c[2] for c in cpw) / lsum if cpw else None
    eeff = sum(c[0] * c[3] for c in cpw) / lsum if cpw else float(np.mean(ee_cf))   # for TDR distance only
    t_ns, dist, ztdr = _tdr(f, s11, z_port, eeff)
    length = sum(math.hypot(s["x2"] - s["x1"], s["y2"] - s["y1"]) for s in segs)
    # plateau: median Z over the middle of the trace (skip the port discontinuity and the far end)
    sel = (dist > min(2.0, 0.2 * length)) & (dist < 0.8 * length)
    z_plateau = float(np.median(ztdr[sel])) if sel.any() else None

    def at(fq):
        i = int(np.argmin(abs(f - fq)))
        return {"f_ghz": round(f[i] / 1e9, 3), "s11_db": round(float(20 * np.log10(abs(s11[i]))), 2),
                "s21_db": round(float(20 * np.log10(abs(s21[i]))), 2)}

    keep = dist < 2 * length + 5
    return {"net": net, "ports": list(ports), "segments": len(segs), "vias": len(vias), "length_mm": round(length, 2),
            "zone_fill": bool(fills), "z0_closed_form": sorted({round(z, 1) for z in z_cf if z}),
            "eeff_closed_form": round(eeff, 3),
            "z0_cpwg_closed_form_lenavg": round(z_cpw, 1) if z_cpw else None,
            "pour_gap_mm": sorted({round(c[1], 2) for c in cpw}), "z_tdr_plateau": z_plateau,
            "z_tdr_min": float(np.min(ztdr[sel])) if sel.any() else None,
            "z_tdr_max": float(np.max(ztdr[sel])) if sel.any() else None,
            "s_at": [at(x) for x in (0.1e9, 0.5e9, 1e9, 2e9, 3e9, fmax) if x <= fmax],
            "f": f.tolist(), "s11_db": (20 * np.log10(np.abs(s11))).tolist(),
            "s21_db": (20 * np.log10(np.abs(s21))).tolist(),
            "s11_ri": [np.real(s11).tolist(), np.imag(s11).tolist()],
            "s21_ri": [np.real(s21).tolist(), np.imag(s21).tolist()],
            "tdr": {"t_ns": t_ns[keep].tolist(), "dist_mm": dist[keep].tolist(), "z": ztdr[keep].tolist()},
            "runtime_s": rt, "peak_rss_mb": _peak_rss_mb(), "timesteps": _timesteps(sim_path, stats["dt_ps"]), "mesh": stats,
            "params": {"h_mm": h_mm, "er": er, "tand": tand, "fmax": fmax, "cell_mm": cell_mm, "z_port": z_port}}


# ---------------------------------------------------------------- project stage

def em_nets(design) -> dict[str, dict]:
    """Nets that opt in with `nets.<NET>.em: true` or `em: {fmax_ghz, ports, cell_mm, z_target, z_tol}`."""
    out = {}
    for net in design.nets():
        cfg = design.net_info(net).get("em")
        if cfg:
            out[net] = cfg if isinstance(cfg, dict) else {}
    return out


def run_project(design, nets: list[str] | None = None, fmax_ghz: float | None = None,
                cell_mm: float | None = None) -> dict:
    """Run `simulate_net` on the opted-in nets of a routed 2-layer project; write build/em/<net>.json."""
    b = design.board
    if b["layers"] != 2:
        raise NotImplementedError("the prototype handles 2-layer boards only")
    pcb = design.dir / "build" / f"{design.name}.kicad_pcb"
    out_dir = design.dir / "build" / "em"
    out_dir.mkdir(parents=True, exist_ok=True)
    cfgs = em_nets(design)
    if nets:
        cfgs = {n: cfgs.get(n, {}) for n in nets}
    rows = []
    for net, cfg in cfgs.items():
        ports = tuple(cfg["ports"]) if cfg.get("ports") else None
        r = simulate_net(pcb, net, ports, h_mm=float(b.get("ref_plane_h_mm", b["thickness_mm"])), er=float(b["er"]),
                         t_mm=0.035 * b["copper_oz"], fmax=float(fmax_ghz or cfg.get("fmax_ghz", 6)) * 1e9,
                         cell_mm=float(cell_mm or cfg.get("cell_mm", 0.1)), via_drill_mm=b["via_drill_mm"],
                         via_dia_mm=b["via_dia_mm"], gnd_nets=tuple(design.ground_nets()),
                         clearance_mm=float(b["clearance_mm"]), sim_path=out_dir / f"sim_{net}",
                         max_steps=int(cfg.get("max_steps", 60000)))
        (out_dir / f"{net}.json").write_text(json.dumps(r))
        z_t = design.net_info(net).get("impedance_ohm") or cfg.get("z_target")
        row = {k: r[k] for k in ("net", "ports", "length_mm", "z0_closed_form", "z0_cpwg_closed_form_lenavg",
                                 "z_tdr_plateau", "z_tdr_min", "z_tdr_max", "s_at", "runtime_s", "peak_rss_mb",
                                 "timesteps")}
        row["cells"] = r["mesh"]["cells"]
        row["z_target"] = z_t
        row["ok"] = (z_t is None or r["z_tdr_plateau"] is None
                     or abs(r["z_tdr_plateau"] - z_t) / z_t <= float(cfg.get("z_tol", 0.1)))
        rows.append(row)
    return {"ok": all(x["ok"] for x in rows), "nets": rows}
